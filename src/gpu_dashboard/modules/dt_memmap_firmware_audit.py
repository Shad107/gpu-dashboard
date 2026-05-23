"""Module dt_memmap_firmware_audit — devicetree / memmap /
vmcoreinfo audit (R&D #68.4).

Three small firmware-handoff surfaces that catch obscure boot-
time misconfig :

  /sys/firmware/devicetree/base/compatible
      Should be absent on a normal x86 machine. Its presence
      means the initramfs or kernel pulled a DT in — usually
      a broken multi-arch initramfs that was meant for ARM.

  /sys/firmware/memmap/<n>/{start,end,type}
      The kernel-imported E820 / EFI memory map. Each entry is
      one contiguous range with a type string ('System RAM',
      'Reserved', 'ACPI Non-volatile Storage', etc.). If the
      list has *only* 'System RAM' entries, the kernel didn't
      import the EFI memory map and downstream subsystems
      (kdump, EFI runtime, lockdown) silently lose context.

  /sys/kernel/vmcoreinfo
      World-readable file containing the kdump bridge metadata
      (physical address + size of the vmcoreinfo elf note).
      If this is unreadable or zero-sized, kexec/kdump is
      misconfigured and a kernel panic will produce no usable
      crash dump.

Why on a homelab :

* Mis-cooked initramfs (Debian + Raspberry Pi build host shared
  cache) silently injects a DT on x86 — kernel boots but extra
  subsystems break.
* OVMF firmware (Proxmox VM) sometimes truncates the EFI
  memory map ; missing 'Reserved' entries trip kdump.
* kexec_load tooling forgets to populate vmcoreinfo on custom-
  built kernels — first panic = lost dump = lost day.

Verdicts (priority order) :
  vmcoreinfo_unreadable       /sys/kernel/vmcoreinfo absent or
                                zero-byte ; kdump useless.
  efi_reserved_regions_zero   /sys/firmware/memmap exists but no
                                entry has a 'Reserved' /
                                'ACPI' / 'Unusable' type.
  devicetree_present_on_x86   /sys/firmware/devicetree/base
                                present on an x86_64 host.
  ok                          memmap healthy, vmcoreinfo ready,
                                no spurious DT.
  unknown                     none of the three surfaces present.

stdlib only.
"""
from __future__ import annotations

import os
import platform
from typing import List, Optional


NAME = "dt_memmap_firmware_audit"


_SYS_DT = "/sys/firmware/devicetree/base"
_SYS_MEMMAP = "/sys/firmware/memmap"
_SYS_VMCOREINFO = "/sys/kernel/vmcoreinfo"


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def is_devicetree_present(dt_path: str = _SYS_DT) -> bool:
    return os.path.isdir(dt_path)


def host_arch() -> str:
    try:
        return platform.machine()
    except Exception:
        return ""


def list_memmap_entries(memmap_path: str = _SYS_MEMMAP
                              ) -> List[dict]:
    if not os.path.isdir(memmap_path):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(memmap_path),
                          key=lambda n: int(n) if n.isdigit() else 0)
    except OSError:
        return []
    for n in names:
        d = os.path.join(memmap_path, n)
        if not os.path.isdir(d):
            continue
        out.append({
            "id": n,
            "start": _read(os.path.join(d, "start")),
            "end": _read(os.path.join(d, "end")),
            "type": _read(os.path.join(d, "type")),
        })
    return out


def vmcoreinfo_state(path: str = _SYS_VMCOREINFO) -> dict:
    """{present, readable, bytes_read}."""
    if not os.path.exists(path):
        return {"present": False, "readable": False,
                  "bytes_read": 0}
    blob = _read_bytes(path)
    if blob is None:
        return {"present": True, "readable": False,
                  "bytes_read": 0}
    return {"present": True, "readable": True,
              "bytes_read": len(blob.strip())}


_RESERVED_TYPES = {"Reserved", "ACPI Tables",
                       "ACPI Non-volatile Storage",
                       "ACPI NVS", "Unusable memory",
                       "Persistent Memory", "PRAM"}


def classify(arch: str,
              dt_present: bool,
              memmap: List[dict],
              vmcoreinfo: dict) -> dict:
    surfaces_present = (dt_present
                              or memmap
                              or vmcoreinfo["present"])
    if not surfaces_present:
        return {"verdict": "unknown",
                "reason": ("/sys/firmware/devicetree, "
                          "/sys/firmware/memmap and "
                          "/sys/kernel/vmcoreinfo are all "
                          "absent."),
                "recommendation": ""}

    # 1) vmcoreinfo_unreadable
    if (not vmcoreinfo["present"]
            or (vmcoreinfo["present"]
                and vmcoreinfo["readable"]
                and vmcoreinfo["bytes_read"] == 0)):
        return {"verdict": "vmcoreinfo_unreadable",
                "reason": ("/sys/kernel/vmcoreinfo is absent or "
                          "empty — kdump cannot capture a usable "
                          "crash dump."),
                "recommendation": _recipe_vmcoreinfo()}

    # 2) efi_reserved_regions_zero
    if memmap:
        types = {e.get("type") for e in memmap if e.get("type")}
        if not (types & _RESERVED_TYPES):
            return {"verdict": "efi_reserved_regions_zero",
                    "reason": (f"/sys/firmware/memmap has "
                              f"{len(memmap)} entries but none "
                              f"are 'Reserved' / 'ACPI' / "
                              f"'Unusable'. Kernel did not "
                              f"import the EFI memory map."),
                    "recommendation": _recipe_memmap()}

    # 3) devicetree_present_on_x86
    if dt_present and arch.startswith(("x86", "amd64")):
        return {"verdict": "devicetree_present_on_x86",
                "reason": (f"Host arch is {arch} but "
                          f"/sys/firmware/devicetree/base is "
                          f"present — initramfs likely pulled a "
                          f"foreign DT in."),
                "recommendation": _recipe_dt_on_x86()}

    return {"verdict": "ok",
            "reason": (f"{len(memmap)} memmap entries ; "
                      f"vmcoreinfo {vmcoreinfo['bytes_read']} "
                      f"bytes ; "
                      f"dt_present={dt_present} on {arch}."),
            "recommendation": ""}


def status(config=None,
            dt_path: str = _SYS_DT,
            memmap_path: str = _SYS_MEMMAP,
            vmcoreinfo_path: str = _SYS_VMCOREINFO) -> dict:
    arch = host_arch()
    dt = is_devicetree_present(dt_path)
    memmap = list_memmap_entries(memmap_path)
    vmci = vmcoreinfo_state(vmcoreinfo_path)
    verdict = classify(arch, dt, memmap, vmci)
    return {"ok": dt or bool(memmap) or vmci["present"],
              "arch": arch,
              "devicetree_present": dt,
              "memmap_entry_count": len(memmap),
              "memmap_sample": memmap[:8],
              "vmcoreinfo_present": vmci["present"],
              "vmcoreinfo_readable": vmci["readable"],
              "vmcoreinfo_bytes": vmci["bytes_read"],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_vmcoreinfo() -> str:
    return ("# vmcoreinfo is required for kdump crash captures.\n"
            "# Verify kexec_load support :\n"
            "ls /sys/kernel/kexec_loaded\n"
            "# Install and configure kdump :\n"
            "sudo apt install linux-crashdump  # Debian/Ubuntu\n"
            "sudo dnf install kexec-tools       # Fedora/RHEL\n"
            "sudo systemctl enable --now kdump\n"
            "# Verify :\n"
            "sudo cat /sys/kernel/vmcoreinfo\n")


def _recipe_memmap() -> str:
    return ("# Kernel didn't import the EFI memory map.\n"
            "# Confirm boot mode :\n"
            "[ -d /sys/firmware/efi ] && echo UEFI || echo BIOS\n"
            "# Check kernel cmdline for 'noefi' or 'efi=...' :\n"
            "cat /proc/cmdline | tr ' ' '\\n' | grep -i efi\n"
            "# Compare with what BIOS exposes :\n"
            "sudo dmidecode -t memory | head\n")


def _recipe_dt_on_x86() -> str:
    return ("# Spurious devicetree on x86_64 — rebuild initramfs:\n"
            "sudo update-initramfs -u  # Debian/Ubuntu\n"
            "sudo dracut --force        # Fedora/RHEL\n"
            "# Check what got pulled in :\n"
            "find /sys/firmware/devicetree/base/ -name compatible \\\n"
            "  -exec cat {} \\;\n")
