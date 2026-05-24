"""Module misc_chardev_audit — misc char-device inventory
(R&D #75.2).

The kernel's MAJOR-10 ("misc") devices live under
/proc/misc + /sys/class/misc/. They expose powerful primitives :

  kvm           hypervisor ioctl interface
  vhost-net     in-kernel virtio-net helper
  uinput        synthetic input injection (keyboard/mouse)
  hpet          high-precision event timer
  kmsg          kernel ring buffer write/read
  nvram         CMOS / BIOS NVRAM
  rfkill        wireless kill switch
  userfaultfd   page-fault userspace handler
  fuse          filesystem in userspace (intentionally
                  world-writable)
  vfio          PCI passthrough

A world-writable /dev/<name> for any of the privileged minors
above is a privilege-escalation surface. Conversely, missing
/dev/kvm on a host that loaded the KVM module breaks every
local VM.

Reads :
  /proc/misc                       minor → name mapping
  /sys/class/misc/                 per-device classes
  /dev/<name>                      mode-bit check for watched
                                     devices

Verdicts (priority order) :
  world_writable_node    ≥1 watched /dev/<name> has mode &
                           0o002 AND name not in safe-list
                           ({fuse}).
  orphan_minor           /proc/misc lists a minor whose
                           /sys/class/misc/<name> is absent
                           (driver dropped, registry stale).
  kvm_node_missing       /sys/module/kvm/ exists but
                           /dev/kvm is absent.
  requires_root          ≥1 watched device is 0600 root and
                           a userspace tool the user might
                           run unprivileged needs it (uinput,
                           rfkill).
  ok                     watched devices have sane perms.
  unknown                /proc/misc + /sys/class/misc both
                           absent.

stdlib only.
"""
from __future__ import annotations

import os
import stat
from typing import List, Optional


NAME = "misc_chardev_audit"


_PROC_MISC = "/proc/misc"
_SYS_CLASS_MISC = "/sys/class/misc"
_SYS_MODULE_KVM = "/sys/module/kvm"
_DEV_ROOT = "/dev"


# Privileged misc devices we care about.
_WATCHED = {"kvm", "vhost-net", "uinput", "hpet", "kmsg",
              "nvram", "rfkill", "userfaultfd", "vfio",
              "fuse", "tun"}

# fuse is intentionally world-writable on most distros
# (non-root mount). Excluding it from the world-write alert.
_WORLD_WRITE_OK = {"fuse"}


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def parse_proc_misc(text: Optional[str]) -> List[dict]:
    out: List[dict] = []
    if not text:
        return out
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) < 2:
            continue
        try:
            minor = int(parts[0])
        except ValueError:
            continue
        out.append({"minor": minor, "name": parts[1]})
    return out


def list_sysfs_misc(class_path: str = _SYS_CLASS_MISC
                         ) -> List[str]:
    if not os.path.isdir(class_path):
        return []
    try:
        return sorted(os.listdir(class_path))
    except OSError:
        return []


def watched_dev_state(dev_root: str = _DEV_ROOT) -> List[dict]:
    out: List[dict] = []
    for n in sorted(_WATCHED):
        full = os.path.join(dev_root, n)
        try:
            st = os.stat(full)
            out.append({"name": n,
                          "present": True,
                          "mode": stat.S_IMODE(st.st_mode)})
        except OSError:
            out.append({"name": n, "present": False,
                          "mode": None})
    return out


def classify(misc_entries: List[dict],
              sysfs_misc: List[str],
              dev_state: List[dict],
              kvm_module_loaded: bool,
              surfaces_present: bool) -> dict:
    if not surfaces_present:
        return {"verdict": "unknown",
                "reason": ("Neither /proc/misc nor "
                          "/sys/class/misc present."),
                "recommendation": ""}

    # 1) world_writable_node
    ww = [d for d in dev_state
            if d.get("present")
              and d.get("mode") is not None
              and (d["mode"] & 0o002)
              and d["name"] not in _WORLD_WRITE_OK]
    if ww:
        sample = ", ".join(
            f"{d['name']} mode=0o{d['mode']:03o}"
                for d in ww[:3])
        return {"verdict": "world_writable_node",
                "reason": (f"{len(ww)} privileged misc dev node(s) "
                          f"are world-writable : {sample}."),
                "recommendation": _recipe_ww()}

    # 2) orphan_minor — /proc/misc has names absent from sysfs
    sysfs_set = set(sysfs_misc)
    orphans = [e for e in misc_entries
                   if e["name"] not in sysfs_set]
    if orphans and sysfs_set:
        # Only flag when sysfs is populated (avoids false positive
        # on kernels without /sys/class/misc).
        sample = ", ".join(e["name"] for e in orphans[:5])
        return {"verdict": "orphan_minor",
                "reason": (f"{len(orphans)} /proc/misc entry/"
                          f"entries with no /sys/class/misc/"
                          f"<name> : {sample}."),
                "recommendation": _recipe_orphan()}

    # 3) kvm_node_missing
    if kvm_module_loaded:
        kvm_state = next(
            (d for d in dev_state if d["name"] == "kvm"), None)
        if kvm_state and not kvm_state["present"]:
            return {"verdict": "kvm_node_missing",
                    "reason": ("/sys/module/kvm is loaded but "
                              "/dev/kvm is missing. Local VMs "
                              "(qemu, libvirt) will fail to "
                              "start."),
                    "recommendation": _recipe_kvm_missing()}

    # 4) requires_root — uinput / rfkill 0600 (typical lock)
    root_only = [d for d in dev_state
                    if d.get("present")
                      and d.get("mode") == 0o600
                      and d["name"] in ("uinput", "rfkill",
                                                  "userfaultfd")]
    if root_only:
        sample = ", ".join(d["name"] for d in root_only[:3])
        return {"verdict": "requires_root",
                "reason": (f"{len(root_only)} userspace-facing "
                          f"misc node(s) are 0600 root : "
                          f"{sample}. Unprivileged tools fall "
                          f"back to slow paths."),
                "recommendation": _recipe_root_only()}

    present_count = sum(1 for d in dev_state if d.get("present"))
    return {"verdict": "ok",
            "reason": (f"{len(misc_entries)} misc minors ; "
                      f"{len(sysfs_misc)} sysfs entries ; "
                      f"{present_count} watched dev nodes "
                      f"present."),
            "recommendation": ""}


def status(config=None,
            proc_misc: str = _PROC_MISC,
            sys_misc: str = _SYS_CLASS_MISC,
            sys_module_kvm: str = _SYS_MODULE_KVM,
            dev_root: str = _DEV_ROOT) -> dict:
    misc_entries = parse_proc_misc(_read(proc_misc))
    sysfs_misc = list_sysfs_misc(sys_misc)
    dev_state = watched_dev_state(dev_root)
    kvm_module_loaded = os.path.isdir(sys_module_kvm)
    surfaces_present = (misc_entries or sysfs_misc)
    verdict = classify(misc_entries, sysfs_misc, dev_state,
                          kvm_module_loaded,
                          bool(surfaces_present))
    return {"ok": bool(surfaces_present),
              "misc_count": len(misc_entries),
              "sysfs_misc_count": len(sysfs_misc),
              "kvm_module_loaded": kvm_module_loaded,
              "watched_devices": dev_state,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_ww() -> str:
    return ("# A privileged misc dev node is world-writable.\n"
            "# Lock down via udev rule :\n"
            "for n in kvm vhost-net uinput hpet rfkill; do\n"
            "  echo \"KERNEL==\\\"$n\\\", MODE=\\\"0660\\\"\"\n"
            "done | sudo tee /etc/udev/rules.d/99-misc.rules\n"
            "sudo udevadm trigger\n")


def _recipe_orphan() -> str:
    return ("# /proc/misc entries without matching /sys/class\n"
            "# /misc/ — driver dropped its sysfs link but kept\n"
            "# the minor. Reload the responsible module :\n"
            "diff <(awk '{print $2}' /proc/misc | sort) \\\n"
            "     <(ls /sys/class/misc | sort)\n")


def _recipe_kvm_missing() -> str:
    return ("# /sys/module/kvm loaded but /dev/kvm missing.\n"
            "# Re-create the node via udev :\n"
            "sudo modprobe -r kvm_intel kvm_amd kvm 2>/dev/null\n"
            "sudo modprobe kvm\n"
            "sudo modprobe kvm_intel  # or kvm_amd\n"
            "ls -l /dev/kvm\n")


def _recipe_root_only() -> str:
    return ("# uinput / rfkill node is 0600. To expose to a\n"
            "# group :\n"
            "echo 'KERNEL==\"uinput\", MODE=\"0660\", "
            "GROUP=\"input\"' \\\n"
            "  | sudo tee /etc/udev/rules.d/99-uinput.rules\n"
            "sudo udevadm trigger\n")
