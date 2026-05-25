"""Module modprobe_blacklist_drift_audit — modprobe.d
blacklist / install drift detector (R&D #102.2).

Classic single-GPU homelab footgun: user adds
`blacklist nouveau` to /etc/modprobe.d/blacklist-nvidia.conf
but never runs `update-initramfs -u`, so initrd reloads
nouveau at boot and it co-exists with nvidia.ko — reset
storms ensue.

Similar trap: `install <mod> /bin/true` (force-no-op) entry
gets shadowed by a later .conf file or simply isn't picked
up by the running kernel.

No existing module parses modprobe.d directives. modprobe_audit
only reads `options <nvidia*>` parameter overrides ;
kernel_module_refcnt_audit and module_integrity_audit ignore
modprobe.d entirely.

Reads :

  /etc/modprobe.d/*.conf
  /run/modprobe.d/*.conf
  /usr/lib/modprobe.d/*.conf
  /lib/modprobe.d/*.conf

  /proc/modules                # what's actually loaded

Verdicts (worst-first) :

  blacklist_drift          err     a `blacklist`-listed
                                   module appears in
                                   /proc/modules — initrd
                                   stale or load via
                                   different path.
  install_noop_drift       warn    a module with `install
                                   <mod> /bin/true` is
                                   currently loaded.
  no_blacklist_files       accent  no .conf files found —
                                   distro lost its defaults.
  ok                               all blacklists honored.
  requires_root                    /run/modprobe.d unreadable.
  unknown                          no modprobe.d dir found.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "modprobe_blacklist_drift_audit"

DEFAULT_DIRS = (
    "/etc/modprobe.d",
    "/run/modprobe.d",
    "/usr/lib/modprobe.d",
    "/lib/modprobe.d",
)
DEFAULT_PROC_MODULES = "/proc/modules"

_BL_RE = re.compile(r"^\s*blacklist\s+(\S+)")
_INSTALL_NOOP_RE = re.compile(
    r"^\s*install\s+(\S+)\s+/bin/(true|false)")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError, UnicodeDecodeError):
        return None


def parse_conf(text: Optional[str]) -> dict:
    """Return {'blacklist': set(...), 'install_noop': set(...)}."""
    out: dict = {"blacklist": set(), "install_noop": set()}
    if not text:
        return out
    for line in text.splitlines():
        # strip comments
        stripped = line.split("#", 1)[0]
        m = _BL_RE.match(stripped)
        if m:
            out["blacklist"].add(m.group(1))
            continue
        m2 = _INSTALL_NOOP_RE.match(stripped)
        if m2:
            out["install_noop"].add(m2.group(1))
    return out


def walk_dirs(dirs: tuple = DEFAULT_DIRS) -> dict:
    """Walk all modprobe.d dirs ; return combined sets."""
    out: dict = {
        "blacklist": set(),
        "install_noop": set(),
        "file_count": 0,
    }
    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for ent in entries:
            if not ent.endswith(".conf"):
                continue
            path = os.path.join(d, ent)
            data = parse_conf(_read_text(path))
            out["blacklist"] |= data["blacklist"]
            out["install_noop"] |= data["install_noop"]
            out["file_count"] += 1
    return out


def parse_proc_modules(text: Optional[str]) -> set:
    """Return set of loaded module names."""
    out: set = set()
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if parts:
            out.add(parts[0])
    # /proc/modules uses underscores; modprobe accepts hyphen
    # or underscore. Add the hyphenated form too for matching.
    out |= {name.replace("_", "-") for name in list(out)}
    return out


def classify(any_dir_present: bool,
             file_count: int,
             blacklist: set,
             install_noop: set,
             loaded: set,
             any_readable: bool) -> dict:
    if not any_dir_present:
        return {"verdict": "unknown",
                "reason": (
                    "No modprobe.d directories found.")}
    if not any_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "modprobe.d dirs present but no "
                    "files readable — re-run as root.")}

    # err — blacklist drift
    bl_drift = sorted(blacklist & loaded)
    if bl_drift:
        return {
            "verdict": "blacklist_drift",
            "reason": (
                f"{len(bl_drift)} module(s) listed as "
                f"'blacklist' are currently loaded: "
                f"{bl_drift[:5]}. Initrd likely stale ; "
                "run update-initramfs / dracut -f and "
                "reboot.")}

    # warn — install /bin/true drift
    inst_drift = sorted(install_noop & loaded)
    if inst_drift:
        return {
            "verdict": "install_noop_drift",
            "reason": (
                f"{len(inst_drift)} module(s) with "
                f"'install <mod> /bin/true' directive are "
                f"loaded anyway: {inst_drift[:5]}. Loaded "
                "via different path (insmod, dependency).")}

    # accent — no blacklist files
    if file_count == 0:
        return {
            "verdict": "no_blacklist_files",
            "reason": (
                "No .conf files in any modprobe.d "
                "directory — distro defaults lost.")}

    return {"verdict": "ok",
            "reason": (
                f"{file_count} modprobe.d file(s) parsed ; "
                f"{len(blacklist)} blacklist(s) honored.")}


def status(config: Optional[dict] = None,
           dirs: tuple = DEFAULT_DIRS,
           proc_modules: str = DEFAULT_PROC_MODULES) -> dict:
    any_dir_present = any(os.path.isdir(d) for d in dirs)
    data = walk_dirs(dirs)
    # If no files but dir exists, fall back to requires_root
    any_readable = data["file_count"] > 0 or not any_dir_present
    loaded = parse_proc_modules(_read_text(proc_modules))
    verdict = classify(
        any_dir_present, data["file_count"],
        data["blacklist"], data["install_noop"],
        loaded, any_readable)
    return {
        "ok": verdict["verdict"] == "ok",
        "conf_file_count": data["file_count"],
        "blacklist_count": len(data["blacklist"]),
        "install_noop_count": len(data["install_noop"]),
        "verdict": verdict,
    }
