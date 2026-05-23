"""Module modprobe_audit — modprobe.d ↔ runtime drift (R&D #38.2).

Shipped #29.1 kmod_params reads /sys/module/nvidia/parameters/* and
flags foot-guns at the runtime level. But the user's actual intent
lives in /etc/modprobe.d/*.conf — and when those files disagree
with runtime, it usually means the user edited modprobe.d but never
ran `sudo update-initramfs -u && reboot`, so the kernel boot picked
up the OLD initramfs without their fix.

This module parses every /etc/modprobe.d/*.conf, extracts `options
<module> <KEY=VALUE>...` lines for the nvidia driver family
(nvidia, nvidia_drm, nvidia_modeset, nvidia_uvm), reads the
matching /sys/module/<module>/parameters/<key>, and emits:

  synced              every on-disk option matches runtime — healthy
  drift               at least one on-disk option ≠ runtime value
                      — recipe surfaces update-initramfs + reboot
  driver_not_loaded   on-disk options exist but /sys/module/<mod>
                      is empty (driver not loaded — typical VM)
  no_options          no nvidia options in any modprobe.d file
  unknown             can't read either side

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "modprobe_audit"


_MODPROBE_ROOT = "/etc/modprobe.d"
_SYS_MODULE_ROOT = "/sys/module"


# Modules whose options we audit
_NVIDIA_MODULES = ("nvidia", "nvidia_drm", "nvidia_modeset", "nvidia_uvm")


_OPTIONS_RE = re.compile(r"^\s*options\s+(\S+)\s+(.+)$")
_KV_RE = re.compile(r"(\S+?)=(\S+)")


def parse_options_line(line: str) -> Optional[dict]:
    if not line:
        return None
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    m = _OPTIONS_RE.match(s)
    if not m:
        return None
    module = m.group(1)
    options: dict = {}
    for km in _KV_RE.finditer(m.group(2)):
        options[km.group(1)] = km.group(2)
    return {"module": module, "options": options}


def collect_options_from_dir(root: str = _MODPROBE_ROOT,
                                only_modules: Optional[tuple] = None) -> dict:
    """Return {module: {"options": {k: v}, "files": [...]}} merged from
    every <root>/*.conf. When `only_modules` is set, return only those.
    """
    out: dict = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return {}
    for n in names:
        if not n.endswith(".conf"):
            continue
        path = os.path.join(root, n)
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            continue
        for line in text.splitlines():
            rec = parse_options_line(line)
            if not rec:
                continue
            mod = rec["module"]
            if only_modules and mod not in only_modules:
                continue
            entry = out.setdefault(mod, {"options": {}, "files": []})
            entry["options"].update(rec["options"])
            if path not in entry["files"]:
                entry["files"].append(path)
    return out


def read_runtime_param(sys_module_root: str, module: str,
                        param: str) -> Optional[str]:
    p = os.path.join(sys_module_root, module, "parameters", param)
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


_RECIPE_DRIFT = (
    "# modprobe.d disagrees with runtime — kernel booted with stale\n"
    "# initramfs. Rebuild + reboot to pick up your changes:\n"
    "sudo update-initramfs -u   # Debian / Ubuntu\n"
    "# OR  sudo dracut --force   # Fedora / RHEL\n"
    "sudo reboot\n"
    "# After reboot, re-check #29.1 kmod_params to confirm."
)


def classify(on_disk: dict, runtime: dict) -> dict:
    if not on_disk:
        return {"verdict": "no_options",
                "reason": ("No `options nvidia*` lines in any "
                           "/etc/modprobe.d/*.conf file."),
                "recommendation": ""}
    # Aggregate drift across all known modules
    drift_rows: list = []
    matched = 0
    runtime_has_any = False
    for mod, conf in on_disk.items():
        opts = conf.get("options") or {}
        mod_runtime = runtime.get(mod) or {}
        if mod_runtime:
            runtime_has_any = True
        for key, want in opts.items():
            got = mod_runtime.get(key)
            if got is None:
                continue   # driver_not_loaded path handles it
            if got != want:
                drift_rows.append({"module": mod, "param": key,
                                    "on_disk": want, "runtime": got})
            else:
                matched += 1
    if not runtime_has_any:
        return {"verdict": "driver_not_loaded",
                "reason": ("on-disk modprobe.d has nvidia options but "
                           "no /sys/module/<mod>/parameters/ files are "
                           "present — driver isn't loaded (typical for "
                           "VMs where the GPU isn't passed through to "
                           "this host, or after a fresh install before "
                           "first reboot)."),
                "recommendation": ""}
    if drift_rows:
        examples = ", ".join(
            f"{r['module']}/{r['param']}: on-disk={r['on_disk']} "
            f"runtime={r['runtime']}" for r in drift_rows[:3])
        return {"verdict": "drift",
                "reason": (f"{len(drift_rows)} option(s) differ between "
                           f"/etc/modprobe.d/*.conf and "
                           f"/sys/module/*/parameters/. Example(s): "
                           f"{examples}."),
                "recommendation": _RECIPE_DRIFT,
                "drift_rows": drift_rows}
    return {"verdict": "synced",
            "reason": (f"All {matched} on-disk option(s) match runtime."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_MODPROBE_ROOT):
        return {"ok": False, "error": "modprobe_unavailable",
                "reason": f"{_MODPROBE_ROOT} not present."}
    on_disk = collect_options_from_dir(_MODPROBE_ROOT,
                                          only_modules=_NVIDIA_MODULES)
    runtime: dict = {}
    for mod in _NVIDIA_MODULES:
        per: dict = {}
        # Read every param mentioned on-disk for this module
        opts = on_disk.get(mod, {}).get("options", {})
        for key in opts:
            v = read_runtime_param(_SYS_MODULE_ROOT, mod, key)
            if v is not None:
                per[key] = v
        runtime[mod] = per
    verdict = classify(on_disk, runtime)
    return {
        "ok": True,
        "on_disk": on_disk,
        "runtime": runtime,
        "verdict": verdict,
        "drift_rows": verdict.get("drift_rows", []),
    }
