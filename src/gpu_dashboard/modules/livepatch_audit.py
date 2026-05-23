"""Module livepatch_audit — /sys/kernel/livepatch (R&D #57.1).

Reads /sys/kernel/livepatch/*/{enabled, transition} and per-object
sub-dirs. Live kernel patches (kpatch / kgraft / Ubuntu Canonical
livepatch) reload kernel functions without a reboot — the
mechanism is invaluable on LLM hosts you can't easily reboot, but
brings its own foot-guns :

* A live-patch hung in transition (`transition = 1`) pins task
  stacks indefinitely. Suspend / CUDA-init / module reload can
  stall without Xid.
* An unsigned out-of-tree patch on a Secure Boot kernel is the
  obvious bypass.
* A disabled patch is just bookkeeping noise — flag it so the
  user remembers it's there.

Reads :
  /sys/kernel/livepatch/<patch>/enabled
  /sys/kernel/livepatch/<patch>/transition
  /sys/kernel/livepatch/<patch>/signature        (presence test)
  /sys/kernel/livepatch/<patch>/                 directory list
  /proc/sys/kernel/livepatch_replace             (kernel ≥ 5.1)

Verdicts (priority-ordered) :
  stuck_transition       ≥1 patch with transition != 0.
  unsigned_patch         ≥1 patch with no signature file.
  disabled_patch         ≥1 patch with enabled = 0.
  ok                     all patches enabled, transitions clear,
                         or no patches loaded.
  unknown                /sys/kernel/livepatch absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "livepatch_audit"


_SYS_LIVEPATCH = "/sys/kernel/livepatch"
_PROC_REPLACE = "/proc/sys/kernel/livepatch_replace"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_patches(sys_livepatch: str = _SYS_LIVEPATCH) -> List[dict]:
    if not os.path.isdir(sys_livepatch):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_livepatch)):
        d = os.path.join(sys_livepatch, name)
        if not os.path.isdir(d):
            continue
        out.append({
            "name": name,
            "enabled": _read_int(os.path.join(d, "enabled")),
            "transition": _read_int(
                os.path.join(d, "transition")),
            "has_signature": os.path.exists(
                os.path.join(d, "signature")),
        })
    return out


def classify(patches: List[dict], livepatch_present: bool,
              replace_val: Optional[int]) -> dict:
    if not livepatch_present:
        return {"verdict": "unknown",
                "reason": ("/sys/kernel/livepatch is absent — "
                          "kernel built without CONFIG_LIVEPATCH "
                          "or no live patch service installed."),
                "recommendation": ""}

    if not patches:
        return {"verdict": "ok",
                "reason": ("Live-patch subsystem present, no "
                          "patches currently loaded."),
                "recommendation": ""}

    # 1) stuck_transition
    stuck = [p for p in patches
                if p.get("transition") not in (0, None)]
    if stuck:
        sample = ", ".join(p["name"] for p in stuck[:3])
        return {"verdict": "stuck_transition",
                "reason": (f"{len(stuck)} live-patch(es) hung in "
                          f"transition : {sample}. Task stacks "
                          f"pinned ; suspend / CUDA init may stall."),
                "recommendation": _recipe_unstick()}

    # 2) unsigned_patch
    unsigned = [p for p in patches if not p.get("has_signature")]
    if unsigned:
        sample = ", ".join(p["name"] for p in unsigned[:3])
        return {"verdict": "unsigned_patch",
                "reason": (f"{len(unsigned)} live-patch(es) lack a "
                          f"signature file : {sample}. On a Secure "
                          f"Boot kernel this is the obvious bypass."),
                "recommendation": _recipe_sign()}

    # 3) disabled_patch
    disabled = [p for p in patches
                   if p.get("enabled") == 0]
    if disabled:
        sample = ", ".join(p["name"] for p in disabled[:3])
        return {"verdict": "disabled_patch",
                "reason": (f"{len(disabled)} live-patch(es) loaded "
                          f"but disabled : {sample}. Either "
                          f"finalize-disable to free the slot or "
                          f"re-enable."),
                "recommendation": _recipe_remove_disabled()}

    return {"verdict": "ok",
            "reason": (f"{len(patches)} live-patch(es) loaded, "
                      f"all enabled with clear transition."),
            "recommendation": ""}


def status(config=None,
            sys_livepatch: str = _SYS_LIVEPATCH,
            proc_replace: str = _PROC_REPLACE) -> dict:
    livepatch_present = os.path.isdir(sys_livepatch)
    patches = list_patches(sys_livepatch)
    replace_val = _read_int(proc_replace)
    ok = livepatch_present
    verdict = classify(patches, livepatch_present, replace_val)
    return {"ok": ok,
              "livepatch_present": livepatch_present,
              "patch_count": len(patches),
              "patches": patches,
              "livepatch_replace": replace_val,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_unstick() -> str:
    return ("# Inspect the stuck patch(es) :\n"
            "for p in /sys/kernel/livepatch/*; do\n"
            "  [ -d $p ] || continue\n"
            "  echo \"$(basename $p) : enabled=$(cat $p/enabled) "
            "transition=$(cat $p/transition)\"\n"
            "done\n"
            "# Force-finish the transition — only when you're sure\n"
            "# no task is mid-call inside a patched function :\n"
            "echo 0 | sudo tee /sys/kernel/livepatch/<patch>/enabled\n"
            "# If that won't budge, a reboot is the safe escape.\n")


def _recipe_sign() -> str:
    return ("# Inspect the patch source / install :\n"
            "lsmod | grep -i klp\n"
            "kpatch list  # if kpatch-runtime installed\n"
            "# On Secure Boot, only signed (.ko) live patches load.\n"
            "# Re-install from a signed vendor channel.\n")


def _recipe_remove_disabled() -> str:
    return ("# Either re-enable :\n"
            "echo 1 | sudo tee /sys/kernel/livepatch/<patch>/enabled\n"
            "# … or rmmod it to free the slot. kpatch users :\n"
            "sudo kpatch unload <patch>\n")
