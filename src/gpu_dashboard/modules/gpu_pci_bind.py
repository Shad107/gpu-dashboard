"""Module gpu_pci_bind — GPU PCIe driver-binding inventory (R&D #40.1).

When a homelab user wants to hot-swap a GPU between the host (for
llama-server inference) and a Proxmox / qemu VM (for a passthrough
Windows gaming guest or a sandboxed CUDA fuzzing VM), or wants to
recover a stuck GPU after a CUDA error 999 without rebooting, the
*correct* answer is to write a few specific paths under
/sys/bus/pci/ :

  echo 0000:01:00.0 > /sys/bus/pci/drivers/nvidia/unbind
  echo 10de 2204    > /sys/bus/pci/drivers/vfio-pci/new_id
  echo 0000:01:00.0 > /sys/bus/pci/devices/<bdf>/driver/unbind
  echo 1            > /sys/bus/pci/devices/<bdf>/remove
  echo 1            > /sys/bus/pci/rescan

Shipped vfio_sentinel watches *whether* vfio-pci is bound to a GPU
it shouldn't be, and shipped gpu_reset handles the nvidia-side reset
paths — but neither *enumerates* the full per-PCI-function driver-
binding state per GPU nor surfaces the paste-ready transition
recipes (host→VM, VM→host, stuck→reset) with the right sequence
of writes.

Verdicts :
  host_bound              GPU function bound to nvidia or nouveau —
                          ready for host-side inference.
  vfio_bound              GPU function bound to vfio-pci — passthrough
                          configured ; ready for VM use.
  stuck_or_orphaned       GPU function has no driver and enable=0 —
                          recovery needed (remove + rescan).
  mixed_function_bind     GPU + its sibling audio/USB-C functions
                          bound to different drivers (e.g. GPU on
                          vfio-pci, audio on snd_hda_intel) → IOMMU
                          group split, passthrough will fail.
  no_nvidia_gpu           no 0x10de PCIe device found.
  unknown                 /sys/bus/pci unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "gpu_pci_bind"


_SYS_PCI = "/sys/bus/pci"


VID_NVIDIA = "0x10de"


_KNOWN_PASSTHROUGH_DRIVERS = ("vfio-pci",)
_KNOWN_HOST_GPU_DRIVERS = ("nvidia", "nouveau")
_KNOWN_AUDIO_DRIVERS = ("snd_hda_intel", "snd-hda-intel")
_KNOWN_USB_DRIVERS = ("xhci_hcd",)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip(), 0)  # autobase — handles 0x prefix
    except ValueError:
        return None


def _resolve_driver(device_dir: str) -> Optional[str]:
    drv = os.path.join(device_dir, "driver")
    try:
        target = os.readlink(drv)
    except OSError:
        return None
    return os.path.basename(target)


def classify_function(class_int: Optional[int]) -> str:
    """Map PCI class to a coarse function role.

    Class is a 24-bit int : (base_class << 16) | (subclass << 8) |
    progif. Examples :
      0x030000 / 0x030200 → display / 3D controller (GPU)
      0x040300            → HDMI audio
      0x0c0330            → USB-C XHCI
      0x0c8000            → Serial Bus (NVIDIA USB-C UCSI)
    """
    if class_int is None:
        return "unknown"
    base = (class_int >> 16) & 0xff
    if base == 0x03:
        return "display"
    if base == 0x04:
        return "audio"
    if base == 0x0c:
        return "serial"
    return "other"


def list_nvidia_devices(sys_pci: str = _SYS_PCI) -> list:
    devs_dir = os.path.join(sys_pci, "devices")
    if not os.path.isdir(devs_dir):
        return []
    out: list = []
    for bdf in sorted(os.listdir(devs_dir)):
        device_dir = os.path.join(devs_dir, bdf)
        vendor = (_read(os.path.join(device_dir, "vendor")) or "").strip()
        if vendor.lower() != VID_NVIDIA:
            continue
        device_id = (_read(os.path.join(device_dir, "device")) or "").strip()
        class_int = _read_int(os.path.join(device_dir, "class"))
        function_role = classify_function(class_int)
        driver = _resolve_driver(device_dir)
        enable = _read_int(os.path.join(device_dir, "enable"))
        driver_override = (
            (_read(os.path.join(device_dir, "driver_override")) or "")
            .strip()
        )
        if driver_override in ("(null)", ""):
            driver_override_clean: Optional[str] = None
        else:
            driver_override_clean = driver_override
        numa_node = _read_int(os.path.join(device_dir, "numa_node"))
        iommu_group_path = os.path.join(device_dir, "iommu_group")
        iommu_group: Optional[int] = None
        try:
            iommu_group = int(os.path.basename(
                os.readlink(iommu_group_path)))
        except (OSError, ValueError):
            pass
        power_control = (
            (_read(os.path.join(device_dir, "power/control")) or "")
            .strip() or None
        )
        out.append({
            "bdf": bdf,
            "vendor": vendor,
            "device_id": device_id,
            "class_int": class_int,
            "function_role": function_role,
            "driver": driver,
            "enable": enable,
            "driver_override": driver_override_clean,
            "numa_node": numa_node,
            "iommu_group": iommu_group,
            "power_control": power_control,
        })
    return out


def group_by_slot(devices: list) -> dict:
    """Group functions sharing a BDF slot (0000:01:00.X)."""
    out: dict = {}
    for d in devices:
        bdf = d["bdf"]
        # 0000:01:00.0 → slot "0000:01:00"
        slot = bdf.rsplit(".", 1)[0]
        out.setdefault(slot, []).append(d)
    return out


def list_drivers_present(sys_pci: str = _SYS_PCI) -> dict:
    """Map driver name → present? (True if /sys/bus/pci/drivers/<x>)"""
    drv_root = os.path.join(sys_pci, "drivers")
    out: dict = {}
    candidates = list(_KNOWN_PASSTHROUGH_DRIVERS) + list(
        _KNOWN_HOST_GPU_DRIVERS)
    for c in candidates:
        out[c] = os.path.isdir(os.path.join(drv_root, c))
    return out


def _is_passthrough(driver: Optional[str]) -> bool:
    return driver in _KNOWN_PASSTHROUGH_DRIVERS


def _is_host_gpu(driver: Optional[str]) -> bool:
    return driver in _KNOWN_HOST_GPU_DRIVERS


_RECIPE_HOST_TO_VM = (
    "# Rebind GPU + sibling functions from nvidia to vfio-pci for VM\n"
    "# passthrough. Run as root :\n"
    "sudo modprobe vfio-pci\n"
    "# List BDFs to release (GPU + audio + USB-C if present) :\n"
    "for bdf in {bdfs}; do\n"
    "  echo vfio-pci | sudo tee /sys/bus/pci/devices/$bdf/driver_override\n"
    "  echo $bdf | sudo tee /sys/bus/pci/devices/$bdf/driver/unbind\n"
    "  echo $bdf | sudo tee /sys/bus/pci/drivers_probe\n"
    "done\n"
    "# Verify : cat /sys/bus/pci/devices/<bdf>/driver should symlink\n"
    "# to /sys/bus/pci/drivers/vfio-pci."
)

_RECIPE_VM_TO_HOST = (
    "# Rebind GPU + sibling functions from vfio-pci back to nvidia.\n"
    "# WARNING : stop any VM using the GPU first.\n"
    "for bdf in {bdfs}; do\n"
    "  echo | sudo tee /sys/bus/pci/devices/$bdf/driver_override\n"
    "  echo $bdf | sudo tee /sys/bus/pci/devices/$bdf/driver/unbind\n"
    "  echo $bdf | sudo tee /sys/bus/pci/drivers_probe\n"
    "done\n"
    "# May also need : sudo modprobe nvidia"
)

_RECIPE_STUCK_RESET = (
    "# Orphaned GPU recovery — remove from bus + rescan.\n"
    "# This is the kernel-level equivalent of unplug + replug.\n"
    "for bdf in {bdfs}; do\n"
    "  echo 1 | sudo tee /sys/bus/pci/devices/$bdf/remove\n"
    "done\n"
    "sleep 1\n"
    "echo 1 | sudo tee /sys/bus/pci/rescan\n"
    "# Then sudo modprobe nvidia (or vfio-pci)."
)

_RECIPE_MIXED = (
    "# GPU + sibling functions bound to different drivers ; an IOMMU\n"
    "# group is atomic — passthrough requires ALL functions on the\n"
    "# same driver (typically vfio-pci). Unify with one of the host→VM\n"
    "# / VM→host recipes above, applied to ALL functions in the slot."
)


def _bdfs_to_str(bdfs: list) -> str:
    return " ".join(bdfs)


def classify(devices: list, drivers_present: dict) -> dict:
    if not devices:
        return {"verdict": "no_nvidia_gpu",
                "reason": "No PCIe device with vendor 0x10de found.",
                "recommendation": ""}
    slots = group_by_slot(devices)
    # Inspect each slot ; pick the worst-case verdict.
    # Prefer slots that actually contain a display function for
    # reason/recommendation continuity ; a lone audio function on
    # a different BDF would otherwise overwrite the GPU slot's
    # reason with confusing wording.
    verdict_now = "host_bound"
    reason = ""
    recommendation = ""
    has_display_for_reason = False
    rank_order = ("host_bound", "vfio_bound",
                  "stuck_or_orphaned", "mixed_function_bind")
    rank = {v: i for i, v in enumerate(rank_order)}
    for slot, funcs in slots.items():
        slot_has_display = any(f["function_role"] == "display"
                                 for f in funcs)
        # Pick the primary (display function) for the slot bdf list,
        # but emit recipes referencing every function.
        all_bdfs = [f["bdf"] for f in funcs]
        drivers = {f["driver"] for f in funcs if f["driver"]}
        no_driver = [f for f in funcs if not f["driver"]]
        # 1) stuck / orphaned
        if no_driver and any(f.get("enable") == 0 for f in no_driver):
            cand = "stuck_or_orphaned"
            cand_reason = (f"Slot {slot} has function(s) with no driver "
                           f"and enable=0 — orphaned. Recovery needed.")
            cand_recipe = _RECIPE_STUCK_RESET.replace(
                "{bdfs}", _bdfs_to_str(all_bdfs))
        # 2) mixed bind (problem when ≥2 distinct host/vfio drivers)
        elif (len(drivers) > 1
              and any(_is_passthrough(d) for d in drivers)
              and any(_is_host_gpu(d) or d in _KNOWN_AUDIO_DRIVERS
                       or d in _KNOWN_USB_DRIVERS for d in drivers)):
            cand = "mixed_function_bind"
            cand_reason = (f"Slot {slot} has functions split between "
                           f"vfio-pci and a host driver ({sorted(drivers)})"
                           f" — IOMMU group is atomic, passthrough "
                           f"will fail.")
            cand_recipe = _RECIPE_MIXED
        # 3) vfio bound (passthrough configured)
        elif any(_is_passthrough(f["driver"]) for f in funcs
                 if f["function_role"] == "display"):
            cand = "vfio_bound"
            cand_reason = (f"Slot {slot} GPU function is bound to "
                           f"vfio-pci — passthrough configured. "
                           f"Not available to host inference.")
            cand_recipe = _RECIPE_VM_TO_HOST.replace(
                "{bdfs}", _bdfs_to_str(all_bdfs))
        # 4) host bound (default)
        else:
            cand = "host_bound"
            host_drv = next((f["driver"] for f in funcs
                              if f["function_role"] == "display"
                              and _is_host_gpu(f["driver"])), None)
            cand_reason = (f"Slot {slot} GPU function bound to "
                           f"{host_drv or 'host driver'} — ready for "
                           f"local inference.")
            cand_recipe = _RECIPE_HOST_TO_VM.replace(
                "{bdfs}", _bdfs_to_str(all_bdfs))
        cand_rank = rank.get(cand, 0)
        cur_rank = rank.get(verdict_now, 0)
        # Strictly higher rank → take it.
        # Equal rank → only take if current reason came from a
        # non-display slot and this one has a display function
        # (prefer display-bearing slot for the reason text).
        if (cand_rank > cur_rank
                or (cand_rank == cur_rank and slot_has_display
                     and not has_display_for_reason)):
            verdict_now = cand
            reason = cand_reason
            recommendation = cand_recipe
            has_display_for_reason = slot_has_display
    if verdict_now == "host_bound" and not drivers_present.get("vfio-pci"):
        # Append a note that vfio-pci isn't loaded — recipe is OK but
        # the user needs modprobe first.
        recommendation = (
            "# Note : vfio-pci kernel module is not loaded yet.\n"
            + recommendation
        )
    return {"verdict": verdict_now, "reason": reason,
            "recommendation": recommendation}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_PCI):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/sys/bus/pci unreadable.",
                         "recommendation": ""},
            "devices": [], "slots": {}, "drivers_present": {},
        }
    devices = list_nvidia_devices(_SYS_PCI)
    slots = group_by_slot(devices)
    drivers_present = list_drivers_present(_SYS_PCI)
    verdict = classify(devices, drivers_present)
    return {
        "ok": True,
        "device_count": len(devices),
        "slot_count": len(slots),
        "devices": devices,
        "slots": {k: [f["bdf"] for f in v] for k, v in slots.items()},
        "drivers_present": drivers_present,
        "verdict": verdict,
    }
