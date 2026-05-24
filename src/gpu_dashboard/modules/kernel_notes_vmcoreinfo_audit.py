"""Module kernel_notes_vmcoreinfo_audit — kernel notes /
vmcoreinfo / kexec readiness audit (R&D #73.4).

When a GPU driver wedges the box (NVIDIA XID 79, GSP hangs,
NVRM oopses) the only forensic record you'll get is a kdump
capture. Kdump silently fails if :

  * `crashkernel=` was never reserved at boot — meaning
    `/sys/kernel/kexec_crash_size` = 0 ; the kexec_load_image
    syscall has nowhere to copy the crash kernel.
  * `/sys/kernel/vmcoreinfo` is empty — the running kernel has
    no metadata blob for crash-utility / makedumpfile to
    decode physical memory.
  * `/sys/kernel/notes` is missing — userspace can't confirm
    the running vmlinux build-id matches the on-disk one (a
    stale kernel-headers package quietly poisons every bug
    report).

Reads :
  /sys/kernel/notes              ELF notes section (build-id +
                                   GNU notes ; world-readable)
  /sys/kernel/vmcoreinfo          PA + size of the in-kernel
                                   vmcoreinfo elf note
  /sys/kernel/kexec_loaded        1 if a normal kexec image is
                                   currently queued
  /sys/kernel/kexec_crash_loaded  1 if the crash kernel image
                                   is queued (this is the one
                                   kdump needs)
  /sys/kernel/kexec_crash_size    bytes reserved at boot via
                                   crashkernel= cmdline

Verdicts (priority order) :
  crash_kernel_not_reserved   kexec_crash_size = 0 OR
                                 kexec_crash_loaded = 0.
  vmcoreinfo_unreadable        vmcoreinfo present but reads
                                 zero bytes.
  kexec_loaded_unexpectedly    kexec_loaded = 1 (a non-crash
                                 image queued — kexec reboot
                                 in progress or a panic-test
                                 left over).
  kernel_notes_missing         /sys/kernel/notes absent OR
                                 empty.
  requires_root                files exist but unreadable.
  ok                           kdump is armed and ready.
  unknown                      none of the surfaces present.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "kernel_notes_vmcoreinfo_audit"


_SYS_NOTES = "/sys/kernel/notes"
_SYS_VMCOREINFO = "/sys/kernel/vmcoreinfo"
_SYS_KEXEC_LOADED = "/sys/kernel/kexec_loaded"
_SYS_KEXEC_CRASH_LOADED = "/sys/kernel/kexec_crash_loaded"
_SYS_KEXEC_CRASH_SIZE = "/sys/kernel/kexec_crash_size"


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def _read_bytes_len(path: str) -> Optional[int]:
    """Return number of bytes actually read (not file stat size)."""
    try:
        with open(path, "rb") as f:
            return len(f.read())
    except OSError:
        return None


def classify(notes_size: Optional[int],
              vmcoreinfo_size: Optional[int],
              vmcoreinfo_present: bool,
              kexec_loaded: Optional[int],
              kexec_crash_loaded: Optional[int],
              kexec_crash_size: Optional[int],
              any_present: bool,
              any_readable: bool) -> dict:
    if not any_present:
        return {"verdict": "unknown",
                "reason": ("Neither /sys/kernel/notes, "
                          "/sys/kernel/vmcoreinfo nor any "
                          "kexec_* knob is present."),
                "recommendation": ""}

    if not any_readable:
        return {"verdict": "requires_root",
                "reason": ("kernel notes / vmcoreinfo / kexec "
                          "knobs present but none readable from "
                          "this process."),
                "recommendation": _recipe_requires_root()}

    # 1) crash_kernel_not_reserved
    if (kexec_crash_size is not None
            and kexec_crash_size == 0) or (
                kexec_crash_loaded is not None
                and kexec_crash_loaded == 0):
        return {"verdict": "crash_kernel_not_reserved",
                "reason": (f"kexec_crash_size = "
                          f"{kexec_crash_size} bytes ; "
                          f"kexec_crash_loaded = "
                          f"{kexec_crash_loaded}. Kdump cannot "
                          f"capture on panic."),
                "recommendation": _recipe_crashkernel()}

    # 2) vmcoreinfo_unreadable
    if vmcoreinfo_present and (vmcoreinfo_size or 0) == 0:
        return {"verdict": "vmcoreinfo_unreadable",
                "reason": ("/sys/kernel/vmcoreinfo present but "
                          "zero bytes — crash-utility / make"
                          "dumpfile cannot decode the vmcore."),
                "recommendation": _recipe_vmcoreinfo()}

    # 3) kexec_loaded_unexpectedly
    if kexec_loaded is not None and kexec_loaded == 1:
        return {"verdict": "kexec_loaded_unexpectedly",
                "reason": ("/sys/kernel/kexec_loaded = 1 — a "
                          "non-crash kexec image is queued. "
                          "Either a kexec-reboot is in progress "
                          "or a panic-test left state behind."),
                "recommendation": _recipe_kexec_loaded()}

    # 4) kernel_notes_missing
    if (notes_size or 0) == 0:
        return {"verdict": "kernel_notes_missing",
                "reason": ("/sys/kernel/notes empty — userspace "
                          "cannot verify vmlinux build-id."),
                "recommendation": _recipe_notes()}

    return {"verdict": "ok",
            "reason": (f"notes={notes_size}B ; "
                      f"vmcoreinfo={vmcoreinfo_size}B ; "
                      f"crash_loaded={kexec_crash_loaded} ; "
                      f"crash_size="
                      f"{(kexec_crash_size or 0) >> 20} MiB."),
            "recommendation": ""}


def status(config=None,
            sys_notes: str = _SYS_NOTES,
            sys_vmcoreinfo: str = _SYS_VMCOREINFO,
            sys_kexec_loaded: str = _SYS_KEXEC_LOADED,
            sys_kexec_crash_loaded: str = _SYS_KEXEC_CRASH_LOADED,
            sys_kexec_crash_size: str = _SYS_KEXEC_CRASH_SIZE
            ) -> dict:
    notes_present = os.path.exists(sys_notes)
    vmci_present = os.path.exists(sys_vmcoreinfo)
    notes_size = (_read_bytes_len(sys_notes)
                       if notes_present else None)
    vmci_size = (_read_bytes_len(sys_vmcoreinfo)
                    if vmci_present else None)
    kex_loaded = _read_int(sys_kexec_loaded)
    kex_crash_loaded = _read_int(sys_kexec_crash_loaded)
    kex_crash_size = _read_int(sys_kexec_crash_size)

    any_present = (notes_present or vmci_present
                          or kex_loaded is not None
                          or kex_crash_loaded is not None
                          or kex_crash_size is not None)
    any_readable = (notes_size is not None
                          or vmci_size is not None
                          or kex_loaded is not None
                          or kex_crash_loaded is not None
                          or kex_crash_size is not None)

    verdict = classify(notes_size, vmci_size, vmci_present,
                          kex_loaded, kex_crash_loaded,
                          kex_crash_size, any_present,
                          any_readable)

    return {"ok": any_present,
              "notes_present": notes_present,
              "notes_size": notes_size,
              "vmcoreinfo_present": vmci_present,
              "vmcoreinfo_size": vmci_size,
              "kexec_loaded": kex_loaded,
              "kexec_crash_loaded": kex_crash_loaded,
              "kexec_crash_size": kex_crash_size,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_crashkernel() -> str:
    return ("# Reserve crashkernel and load kdump kernel :\n"
            "# 1) Add 'crashkernel=512M' (or more) to kernel\n"
            "#    cmdline via your bootloader :\n"
            "sudo $EDITOR /etc/default/grub  # GRUB_CMDLINE_LINUX\n"
            "sudo update-grub  # Debian/Ubuntu\n"
            "# 2) Install kdump tooling :\n"
            "sudo apt install linux-crashdump  # Debian/Ubuntu\n"
            "sudo dnf install kexec-tools       # Fedora/RHEL\n"
            "# 3) Enable + start :\n"
            "sudo systemctl enable --now kdump\n"
            "# Verify on next boot :\n"
            "cat /sys/kernel/kexec_crash_loaded  # expect 1\n")


def _recipe_vmcoreinfo() -> str:
    return ("# vmcoreinfo empty — kernel not built with\n"
            "# CONFIG_CRASH_DUMP. Reinstall matching kernel :\n"
            "sudo apt install --reinstall linux-image-$(uname -r)\n")


def _recipe_kexec_loaded() -> str:
    return ("# A non-crash kexec image is queued. Inspect :\n"
            "ls -l /sys/kernel/kexec_loaded\n"
            "# Drop the queued image :\n"
            "sudo kexec -u\n")


def _recipe_notes() -> str:
    return ("# Kernel notes missing — likely a stripped kernel.\n"
            "# Confirm running build-id :\n"
            "sudo strings /boot/vmlinuz-$(uname -r) | head\n"
            "sudo file /boot/vmlinuz-$(uname -r)\n")


def _recipe_requires_root() -> str:
    return ("# kexec / vmcoreinfo files normally world-readable.\n"
            "# Confirm permissions :\n"
            "ls -l /sys/kernel/kexec_* /sys/kernel/vmcoreinfo\n"
            "sudo cat /sys/kernel/vmcoreinfo\n")
