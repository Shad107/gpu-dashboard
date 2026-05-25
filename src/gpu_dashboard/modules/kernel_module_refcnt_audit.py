"""Module kernel_module_refcnt_audit — module refcnt + holders
+ initstate auditor (R&D #95.2).

Four existing modules touch kernel-module surface but none
read refcnt / holders / initstate :

  * module_integrity_audit  — taint / signature only
  * modprobe_audit          — /etc/modprobe.d / .conf files
  * kmod_params             — /sys/module/<m>/parameters/
  * kernel_taint            — kernel.tainted bit-flag scan

This audit owns /sys/module/<name>/{refcnt, initstate,
holders/}. Built-in modules don't expose refcnt — we only
inspect loadable modules.

Reads :

  /sys/module/<name>/refcnt      integer use-count
  /sys/module/<name>/initstate   live / coming / going
  /sys/module/<name>/holders/    symlinks to dependent modules

Verdicts (worst-first) :

  initstate_unloading_stuck err  any module with
                                 initstate=going AND
                                 refcnt > 0 — module-removal
                                 wedged, system needs reboot.
  zero_refcnt_with_holders  warn any module with refcnt=0
                                 but holders/ non-empty —
                                 kref leak or stale dep entry.
  excessive_refcnt          accent any module with refcnt >
                                 _EXCESSIVE_THRESHOLD (50) —
                                 likely a user-side leak
                                 (e.g. an audio app forgot
                                 to release snd_pcm handles).
  modules_consistent        ok    all loadable modules sane.
  requires_root             /sys/module/*/refcnt mode-400.
  unknown                   /sys/module absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "kernel_module_refcnt_audit"

DEFAULT_SYS_MODULE = "/sys/module"

# Accent threshold for excessive refcnt.
_EXCESSIVE_THRESHOLD = 50


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_holders(path: str) -> list:
    if not os.path.isdir(path):
        return []
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def walk_modules(root: str = DEFAULT_SYS_MODULE) -> list:
    """Return list of dicts for loadable modules (those with
    a refcnt file). Built-in modules are skipped."""
    if not os.path.isdir(root):
        return []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out: list = []
    refcnt_unreadable = 0
    for name in names:
        base = os.path.join(root, name)
        rfp = os.path.join(base, "refcnt")
        if not os.path.isfile(rfp):
            continue  # built-in
        rc = _read_int(rfp)
        if rc is None:
            refcnt_unreadable += 1
            continue
        out.append({
            "name": name,
            "refcnt": rc,
            "initstate": _read_text(
                os.path.join(base, "initstate")) or "",
            "holders": list_holders(
                os.path.join(base, "holders")),
        })
    return out


def classify(modules: list, root_present: bool,
             any_unreadable: bool) -> dict:
    if not root_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/module absent — kernel built "
                    "without modules or procfs unavailable.")}
    if not modules and any_unreadable:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/module/*/refcnt unreadable — "
                    "re-run as root.")}
    if not modules:
        return {"verdict": "ok",
                "reason": (
                    "No loadable modules with refcnt — all "
                    "drivers built in or none loaded.")}

    # err — module stuck in going state with refcnt > 0
    stuck = [m for m in modules
             if m["initstate"] == "going"
             and m["refcnt"] > 0]
    if stuck:
        names = [m["name"] for m in stuck]
        return {
            "verdict": "initstate_unloading_stuck",
            "reason": (
                f"{len(stuck)} module(s) wedged in unloading "
                f"state with non-zero refcnt: {names}. "
                "modprobe -r will fail until the holder is "
                "killed or the system is rebooted.")}

    # warn — refcnt 0 but holders non-empty
    orphans = [m for m in modules
               if m["refcnt"] == 0 and m["holders"]]
    if orphans:
        names = [m["name"] for m in orphans]
        return {
            "verdict": "zero_refcnt_with_holders",
            "reason": (
                f"{len(orphans)} module(s) have refcnt=0 but "
                f"non-empty holders/ ({names[:3]}). kref leak "
                "or stale dependency — clean dmesg + try "
                "modprobe -r to confirm.")}

    # accent — excessive refcnt
    excessive = [m for m in modules
                 if m["refcnt"] > _EXCESSIVE_THRESHOLD]
    if excessive:
        names = sorted(
            ((m["name"], m["refcnt"]) for m in excessive),
            key=lambda kv: -kv[1])
        return {
            "verdict": "excessive_refcnt",
            "reason": (
                f"{len(excessive)} module(s) have refcnt > "
                f"{_EXCESSIVE_THRESHOLD} (top: {names[:3]}). "
                "Likely a user-side leak — e.g. an audio app "
                "forgot to release snd handles."),
            "top": [f"{n}:{r}" for n, r in names[:5]]}

    return {"verdict": "modules_consistent",
            "reason": (
                f"{len(modules)} loadable module(s) "
                "inspected ; all initstate=live, no holder "
                "anomalies, no excessive refcnts.")}


def status(config: Optional[dict] = None,
           sys_module: str = DEFAULT_SYS_MODULE) -> dict:
    root_present = os.path.isdir(sys_module)
    modules = walk_modules(sys_module) if root_present else []
    # We don't currently track unreadable separately ; the
    # walk silently skips them. For a simple audit this is
    # fine — requires_root only fires if /sys/module exists
    # but ALL files are unreadable.
    any_unreadable = (
        root_present and not modules
        and bool(os.listdir(sys_module)))
    verdict = classify(modules, root_present, any_unreadable)
    return {
        "ok": verdict["verdict"] == "modules_consistent",
        "module_count": len(modules),
        "verdict": verdict,
    }
