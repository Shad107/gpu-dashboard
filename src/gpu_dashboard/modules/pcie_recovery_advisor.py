"""Module pcie_recovery_advisor — GPU link/driver recovery wizard (F4).

Detects when the NVIDIA GPU is in a "stuck" state and builds an
ordered escalation of recovery commands the user can copy-paste.
The exact ordering adapts to whether we're running bare-metal,
inside a VM with passthrough, or in a container.

Why this exists: when the PCIe link drops or the driver loses the
device handle, every userspace tool that talks to the card (nvidia-
smi, nvidia-settings, NVML clients) returns a cryptic "Unable to
determine the device handle". The standard recovery is a checklist
of escalating actions, and most operators waste 30+ minutes
googling the same StackOverflow thread every time. This module
codifies the checklist.

Signals checked:
  * `_nvml.status()` verdict — `device_handle_unavailable` is the
    canonical "driver is stuck" marker.
  * `/sys/bus/pci/devices/<bdf>/current_link_speed` == "Unknown"
    → PCIe link not trained.
  * `current_link_width` == 63 (0x3F, register uninitialised)
    → link physically down.
  * `power_state` stuck in D3hot / D3cold while the workload
    expects D0.
  * AER fatal / nonfatal error counters non-zero → real PCIe
    integrity problem (cable, slot, signal).
  * Recent dmesg lines matching "link down", "AER", "nvrm: ".

Recovery steps in escalating safety order. Steps marked `kills_workloads`
will free /dev/nvidia* — kill GPU consumers first.

stdlib only.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import List, Optional


NAME = "pcie_recovery_advisor"


_NVIDIA_VENDOR_ID = "0x10de"
_PCI_DEVICES = "/sys/bus/pci/devices"


# ── detection ───────────────────────────────────────────────────────


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except OSError:
        return None


def find_nvidia_bdf(pci_root: str = _PCI_DEVICES) -> Optional[str]:
    """Return first BDF (e.g. '0000:01:00.0') of an NVIDIA GPU
    visible in PCIe space. None if no NVIDIA device present."""
    if not os.path.isdir(pci_root):
        return None
    try:
        entries = sorted(os.listdir(pci_root))
    except OSError:
        return None
    for bdf in entries:
        vendor = _read(os.path.join(pci_root, bdf, "vendor"))
        if vendor and vendor.lower() == _NVIDIA_VENDOR_ID:
            # Skip non-GPU NVIDIA functions (e.g. HDA audio at .1)
            class_ = _read(os.path.join(pci_root, bdf, "class"))
            # GPU class codes: 0x030000 (VGA), 0x030200 (3D)
            if class_ and (class_.startswith("0x0300") or
                            class_.startswith("0x0302")):
                return bdf
    return None


def detect_virt() -> str:
    """Return 'bare' | 'kvm' | 'lxc' | 'docker' | 'other' | 'unknown'.
    Uses systemd-detect-virt when available."""
    try:
        r = subprocess.run(["systemd-detect-virt"],
                          capture_output=True, text=True, timeout=2)
        out = (r.stdout or "").strip()
        if r.returncode != 0 or not out or out == "none":
            return "bare"
        return out
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"


def gather_pci_state(bdf: str,
                       pci_root: str = _PCI_DEVICES) -> dict:
    """Read every field that matters for the diagnosis."""
    base = os.path.join(pci_root, bdf)
    state: dict = {"bdf": bdf}
    for f in ("power_state",
              "current_link_speed", "max_link_speed",
              "current_link_width", "max_link_width",
              "d3cold_allowed"):
        state[f] = _read(os.path.join(base, f))
    state["flr_supported"] = os.path.exists(os.path.join(base, "reset"))
    # AER counters — sum of all fatal+nonfatal+correctable.
    aer = {}
    for kind in ("aer_dev_correctable",
                 "aer_dev_fatal", "aer_dev_nonfatal"):
        text = _read(os.path.join(base, kind)) or ""
        total = 0
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                total += int(parts[1])
        aer[kind] = total
    state["aer"] = aer
    return state


_INVALID_LINK_WIDTH = 63  # 0x3F = register uninitialised
_DRIVER_STUCK_LINK_SPEED = "Unknown"


def classify_state(pci: dict, nvml_verdict: Optional[str]) -> dict:
    """Translate raw readings into a diagnosis. Returns:
        {broken: bool,
         severity: 'ok'|'warn'|'err',
         signals: [strings the user can read]}"""
    signals: List[str] = []
    if nvml_verdict == "device_handle_unavailable":
        signals.append("nvml_handle_unavailable")
    speed = pci.get("current_link_speed")
    if speed and speed == _DRIVER_STUCK_LINK_SPEED:
        signals.append("link_speed_unknown")
    try:
        cur_w = int(pci.get("current_link_width") or 0)
    except ValueError:
        cur_w = 0
    if cur_w == _INVALID_LINK_WIDTH:
        signals.append("link_width_invalid")
    aer = pci.get("aer", {})
    if aer.get("aer_dev_fatal", 0) > 0:
        signals.append("aer_fatal_errors")
    if aer.get("aer_dev_nonfatal", 0) > 0:
        signals.append("aer_nonfatal_errors")
    power = pci.get("power_state")
    if power and power != "D0":
        signals.append(f"power_state_{power}")
    broken = bool(signals)
    severity = "ok"
    if "aer_fatal_errors" in signals:
        severity = "err"
    elif "nvml_handle_unavailable" in signals or \
         "link_width_invalid" in signals or \
         "link_speed_unknown" in signals:
        severity = "err"
    elif "aer_nonfatal_errors" in signals or signals:
        severity = "warn"
    return {"broken": broken,
            "severity": severity,
            "signals": signals}


# ── recovery plan ───────────────────────────────────────────────────


def _step(id_, label, command, *, scope, safety, why):
    return {"id": id_, "label": label, "command": command,
            "scope": scope, "safety": safety, "why": why}


def build_recovery_plan(bdf: str, virt: str,
                          flr_supported: bool) -> List[dict]:
    """Return ordered recovery steps. The frontend renders them in
    this order; user picks whichever is appropriate.

    `scope`  = 'guest' (run inside this VM/host) | 'host'
               (Proxmox/hypervisor) | 'physical' (re-seat cable)
    `safety` = 'safe' (read-only / soft reset)
             | 'kills_workloads' (kicks every GPU consumer)
             | 'needs_host_access' (SSH or web UI to Proxmox)
             | 'manual' (operator must physically intervene)
    """
    plan: List[dict] = [
        _step("persistence_restart",
              "Restart nvidia-persistenced",
              "sudo systemctl restart nvidia-persistenced "
              "2>/dev/null; sudo nvidia-smi -pm 1; nvidia-smi",
              scope="guest", safety="safe",
              why="Some driver state issues resolve when the "
                  "persistence daemon re-establishes a session "
                  "with the GPU."),
        _step("module_reload",
              "Reload the nvidia kernel module",
              "sudo fuser -k /dev/nvidia*; "
              "sudo rmmod nvidia_uvm nvidia_drm nvidia_modeset "
              "nvidia; sudo modprobe nvidia nvidia_modeset "
              "nvidia_uvm nvidia_drm; nvidia-smi",
              scope="guest", safety="kills_workloads",
              why="Forces the userspace ↔ kernel-module ↔ GPU "
                  "session to rebuild from scratch. Fails if any "
                  "process is holding /dev/nvidia* — fuser -k "
                  "kicks them first."),
        _step("pcie_remove_rescan",
              "PCIe soft remove + rescan",
              f"echo 1 | sudo tee /sys/bus/pci/devices/{bdf}/remove; "
              f"sleep 2; echo 1 | sudo tee /sys/bus/pci/rescan; "
              f"sleep 3; nvidia-smi",
              scope="guest", safety="kills_workloads",
              why="Re-enumerates the device at the kernel PCIe "
                  "layer. Useful when the driver is fine but the "
                  "device descriptor is stale."),
    ]
    if flr_supported:
        plan.append(_step(
            "flr",
            "Function Level Reset (FLR)",
            f"echo 1 | sudo tee "
            f"/sys/bus/pci/devices/{bdf}/reset; "
            f"sleep 2; nvidia-smi",
            scope="guest", safety="kills_workloads",
            why="Asks the GPU silicon itself to reset. Most "
                "effective single-command soft reset when the "
                "link is physically up but logically stuck. "
                "Will appear as a brief device-gone in dmesg."))
    if virt in ("kvm", "qemu", "vmware", "microsoft", "xen"):
        plan.append(_step(
            "vm_restart",
            "Restart this VM (not the host)",
            "# From the Proxmox shell:\n"
            "qm shutdown <VMID>\n"
            "qm start <VMID>\n"
            "# (VMID is the integer in `qm list`)",
            scope="host", safety="needs_host_access",
            why="Most reliable when guest-side recovery fails. "
                "VFIO unbinds the GPU on the host at VM shutdown "
                "and re-binds at VM start — equivalent to a cold "
                "reset without rebooting the hypervisor."))
        plan.append(_step(
            "host_vfio_rebind",
            "Host-side VFIO unbind/rebind",
            "# From Proxmox shell — replace HOST_BDF with the BDF "
            "on the HOST (lspci -nn | grep -i nvidia)\n"
            "HOST_BDF=\"0000:0X:00.0\"\n"
            "echo \"$HOST_BDF\" | sudo tee "
            "/sys/bus/pci/drivers/vfio-pci/unbind\n"
            "echo \"$HOST_BDF\" | sudo tee "
            "/sys/bus/pci/drivers/vfio-pci/bind",
            scope="host", safety="needs_host_access",
            why="More surgical than restarting the whole VM. "
                "VFIO releases its grip on the GPU, the device "
                "resets, then VFIO re-acquires it. Faster than "
                "VM restart when it works."))
    plan.append(_step(
        "reseat_cable",
        "Re-seat the OcuLink / PCIe cable",
        "# Power off the GPU enclosure, unplug the OcuLink cable "
        "from BOTH ends, re-seat firmly, power back on.",
        scope="physical", safety="manual",
        why="OcuLink signal integrity is less forgiving than a "
            "PCIe slot. If the link drops repeatedly without an "
            "obvious software cause, the cable connector is the "
            "first suspect."))
    return plan


# ── module-level entry point ────────────────────────────────────────


def status(cfg=None,
            pci_root: str = _PCI_DEVICES) -> dict:
    bdf = find_nvidia_bdf(pci_root)
    if bdf is None:
        return {"ok": False, "available": False,
                "verdict": {"verdict": "no_nvidia_gpu",
                            "reason": ("No NVIDIA GPU visible in "
                                       "PCIe space. Recovery wizard "
                                       "is a no-op."),
                            "recommendation": ""}}
    virt = detect_virt()
    pci = gather_pci_state(bdf, pci_root)
    # Pull the NVML verdict if available — best-effort, never crashes.
    nvml_verdict = None
    try:
        from . import _nvml
        nvml_status = _nvml.status()
        nvml_verdict = nvml_status.get("verdict", {}).get("verdict")
    except Exception:  # noqa: BLE001 — best-effort
        pass
    diagnosis = classify_state(pci, nvml_verdict)
    plan = build_recovery_plan(bdf, virt, pci.get("flr_supported", False))
    if diagnosis["broken"]:
        verdict = {"verdict": "recovery_recommended",
                   "reason": (f"GPU at {bdf} appears stuck. Signals: "
                              f"{', '.join(diagnosis['signals'])}. "
                              f"Virt: {virt}. {len(plan)} recovery "
                              f"step(s) suggested below."),
                   "recommendation": ""}
    else:
        verdict = {"verdict": "ok",
                   "reason": (f"GPU at {bdf} healthy. Virt: {virt}. "
                              f"Recovery plan is documented but no "
                              f"action recommended right now."),
                   "recommendation": ""}
    return {"ok": True,
            "bdf": bdf,
            "virt": virt,
            "pci_state": pci,
            "diagnosis": diagnosis,
            "plan": plan,
            "verdict": verdict}
