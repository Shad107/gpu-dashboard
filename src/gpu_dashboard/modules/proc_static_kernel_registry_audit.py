"""Module proc_static_kernel_registry_audit — /proc kernel
registry sweep (R&D #69.4).

Five small text-format files under /proc that together describe
the kernel's *static* registry of loaded modules, character /
block major numbers, miscellaneous-device minors, registered
filesystem types, and the currently-attached console devices.
None of the other audit modules read these together — and the
cross-references they enable are independently valuable :

  /proc/modules        loaded LKMs, with size, refcount and
                          taint flags (O / OE / F / N / E / X / U).
  /proc/devices        Character + Block major-number registry.
  /proc/misc           Misc-device minor numbers under MAJOR 10.
  /proc/filesystems    registered filesystem types.
  /proc/consoles       active console devices, enabled flags.

Why on a homelab :

* `(O)` flag in /proc/modules tells you the running kernel is
  tainted by out-of-tree modules (NVIDIA proprietary driver is
  the canonical case). Knowing which exact modules carry the
  flag avoids assuming "tainted" means "stock kernel is broken".
* Duplicate major numbers in /proc/devices = misregistered
  driver — extremely rare but always indicates a build /
  load-order bug.
* If `tmpfs`, `devtmpfs`, `cgroup2`, `proc`, `sysfs` aren't in
  /proc/filesystems the GPU userspace stack will fail in
  surprising ways (NVIDIA's `nvidia-persistenced` needs
  cgroup2 for instance).
* /proc/consoles silently degrading to no enabled console
  means dmesg can't be read at the serial port — a real
  rescue-mode footgun.

Verdicts (priority order) :
  out_of_tree_tainting_module_loaded  ≥1 module has the (O) or
                                        (OE) taint flag.
  duplicate_or_orphan_major           same major number listed
                                        twice in /proc/devices.
  missing_required_fs_for_gpu_stack   ≥1 of {tmpfs, devtmpfs,
                                        proc, sysfs, cgroup2}
                                        absent from
                                        /proc/filesystems.
  stale_console_misroute              /proc/consoles has no
                                        enabled console
                                        (column-3 flag
                                        starts with '-').
  ok                                  all clean.
  unknown                             one or more of the five
                                        files unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Dict, List, Optional


NAME = "proc_static_kernel_registry_audit"


_PROC_MODULES = "/proc/modules"
_PROC_DEVICES = "/proc/devices"
_PROC_MISC = "/proc/misc"
_PROC_FILESYSTEMS = "/proc/filesystems"
_PROC_CONSOLES = "/proc/consoles"

_REQUIRED_FS = {"tmpfs", "devtmpfs", "proc", "sysfs", "cgroup2"}

_TAINT_FLAG_RE = re.compile(r"\(([A-Z]+)\)")


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def parse_modules(text: Optional[str]) -> List[dict]:
    """Each /proc/modules line :
      name  size  refcnt  used_by  state  base_addr  [(flags)]
    """
    if not text:
        return []
    out: List[dict] = []
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) < 4:
            continue
        flags = ""
        m = _TAINT_FLAG_RE.search(ln)
        if m:
            flags = m.group(1)
        out.append({"name": parts[0],
                       "size": int(parts[1]) if parts[1].isdigit()
                                                       else None,
                       "refcnt": int(parts[2])
                                       if parts[2].isdigit()
                                       else None,
                       "state": (parts[4] if len(parts) > 4
                                                  else ""),
                       "flags": flags})
    return out


def parse_devices(text: Optional[str]) -> Dict[str, List[dict]]:
    """Returns {'character': [{major, name}, …],
                'block': [{major, name}, …]}"""
    out: Dict[str, List[dict]] = {"character": [], "block": []}
    if not text:
        return out
    section = None
    for ln in text.splitlines():
        ln_strip = ln.strip()
        if ln_strip.startswith("Character devices"):
            section = "character"
            continue
        if ln_strip.startswith("Block devices"):
            section = "block"
            continue
        if not section or not ln_strip:
            continue
        parts = ln_strip.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            major = int(parts[0])
        except ValueError:
            continue
        out[section].append({"major": major,
                                 "name": parts[1].strip()})
    return out


def parse_filesystems(text: Optional[str]) -> List[str]:
    if not text:
        return []
    out: List[str] = []
    for ln in text.splitlines():
        parts = ln.split()
        if not parts:
            continue
        # "nodev<TAB>tmpfs" → "tmpfs" ; "ext4" → "ext4"
        if len(parts) == 1:
            out.append(parts[0])
        else:
            out.append(parts[-1])
    return out


def parse_consoles(text: Optional[str]) -> List[dict]:
    """Each line :
      <name>   <flags> (driver)    <major>:<minor>
    flags : the second column ; first char 'E' = enabled,
    '-' = disabled."""
    if not text:
        return []
    out: List[dict] = []
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        flags_str = parts[1]
        enabled = ("E" in flags_str)
        out.append({"name": name,
                       "flags": flags_str,
                       "enabled": enabled})
    return out


def parse_misc(text: Optional[str]) -> List[dict]:
    if not text:
        return []
    out: List[dict] = []
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


def classify(modules: List[dict],
              devices: Dict[str, List[dict]],
              filesystems: List[str],
              consoles: List[dict],
              files_readable: Dict[str, bool]) -> dict:

    if not all(files_readable.values()):
        # At least one core file is unreadable. unknown.
        return {"verdict": "unknown",
                "reason": (f"Some /proc files unreadable : "
                          f"{', '.join(k for k, v in files_readable.items() if not v)}."),
                "recommendation": ""}

    # 1) out_of_tree_tainting_module_loaded
    tainting = [m for m in modules
                  if "O" in (m.get("flags") or "")]
    if tainting:
        sample = ", ".join(
            f"{m['name']}({m['flags']})" for m in tainting[:3])
        return {"verdict": "out_of_tree_tainting_module_loaded",
                "reason": (f"{len(tainting)} module(s) tainting "
                          f"the kernel with (O)/(OE) : "
                          f"{sample}."),
                "recommendation": _recipe_taint_oot()}

    # 2) duplicate_or_orphan_major
    for section in ("character", "block"):
        counts = Counter(d["major"] for d in devices[section])
        dups = [m for m, n in counts.items() if n > 1]
        if dups:
            return {"verdict": "duplicate_or_orphan_major",
                    "reason": (f"/proc/devices {section} section "
                              f"has duplicate major(s) : "
                              f"{dups[:3]}."),
                    "recommendation": _recipe_dup_major()}

    # 3) missing_required_fs_for_gpu_stack
    missing = _REQUIRED_FS - set(filesystems)
    if missing:
        return {"verdict": "missing_required_fs_for_gpu_stack",
                "reason": (f"/proc/filesystems is missing : "
                          f"{sorted(missing)}. GPU userspace "
                          f"stack may fail (cgroup2 / "
                          f"persistenced)."),
                "recommendation": _recipe_missing_fs()}

    # 4) stale_console_misroute
    if consoles and not any(c["enabled"] for c in consoles):
        return {"verdict": "stale_console_misroute",
                "reason": ("/proc/consoles has no enabled "
                          "console — kernel messages have no "
                          "destination."),
                "recommendation": _recipe_console()}

    return {"verdict": "ok",
            "reason": (f"modules={len(modules)} ; "
                      f"chr_majors="
                      f"{len(devices['character'])} ; "
                      f"blk_majors={len(devices['block'])} ; "
                      f"fs_types={len(filesystems)} ; "
                      f"consoles={len(consoles)}."),
            "recommendation": ""}


def status(config=None,
            proc_modules: str = _PROC_MODULES,
            proc_devices: str = _PROC_DEVICES,
            proc_misc: str = _PROC_MISC,
            proc_filesystems: str = _PROC_FILESYSTEMS,
            proc_consoles: str = _PROC_CONSOLES) -> dict:
    mtxt = _read(proc_modules)
    dtxt = _read(proc_devices)
    mitxt = _read(proc_misc)
    ftxt = _read(proc_filesystems)
    ctxt = _read(proc_consoles)
    files_readable = {
        "modules": mtxt is not None,
        "devices": dtxt is not None,
        "misc": mitxt is not None,
        "filesystems": ftxt is not None,
        "consoles": ctxt is not None,
    }
    modules = parse_modules(mtxt)
    devices = parse_devices(dtxt)
    misc = parse_misc(mitxt)
    filesystems = parse_filesystems(ftxt)
    consoles = parse_consoles(ctxt)
    verdict = classify(modules, devices, filesystems, consoles,
                          files_readable)

    return {"ok": all(files_readable.values()),
              "module_count": len(modules),
              "tainting_module_count": sum(
                  1 for m in modules if "O" in (m.get("flags") or "")),
              "character_major_count": len(devices["character"]),
              "block_major_count": len(devices["block"]),
              "misc_count": len(misc),
              "filesystems": filesystems,
              "console_count": len(consoles),
              "enabled_console_count": sum(
                  1 for c in consoles if c.get("enabled")),
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_taint_oot() -> str:
    return ("# Out-of-tree modules taint the kernel. Common\n"
            "# culprits : nvidia, virtualbox, zfs.\n"
            "# Identify all OOT modules currently loaded :\n"
            "grep '(O' /proc/modules\n"
            "# Decode the kernel taint mask :\n"
            "cat /proc/sys/kernel/tainted\n"
            "# Reference : Documentation/admin-guide/tainted-kernels.rst\n")


def _recipe_dup_major() -> str:
    return ("# Duplicate major numbers in /proc/devices.\n"
            "awk '/^Character devices/,/^Block devices/{print}' \\\n"
            "    /proc/devices | awk 'NF==2 {print $1}' | \\\n"
            "    sort | uniq -d\n"
            "# Identify which drivers — only one can own a major.\n")


def _recipe_missing_fs() -> str:
    return ("# Required filesystem type missing from\n"
            "# /proc/filesystems. Modprobe it :\n"
            "sudo modprobe cgroup2  # or tmpfs, etc.\n"
            "# Or rebuild kernel with the matching CONFIG.\n")


def _recipe_console() -> str:
    return ("# No enabled console — dmesg has no output sink.\n"
            "# Add 'console=ttyS0,115200 console=tty0' to kernel\n"
            "# cmdline and regenerate the bootloader config.\n")
