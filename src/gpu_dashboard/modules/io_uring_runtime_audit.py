"""Module io_uring_runtime_audit — io_uring runtime gates (R&D #54.4).

io_uring is the fastest async-IO submission path on Linux ≥ 5.1.
vLLM, llama.cpp, and most modern model-loaders use it for parallel
GGUF / safetensors loads — sometimes silently. The gating knobs
that quietly break or expose this path :

* /proc/sys/kernel/io_uring_disabled (introduced 6.4) :
    0 = unrestricted (the user-facing default until Ubuntu 24.04+)
    1 = restricted to a CAP_SYS_ADMIN or io_uring_group
    2 = fully disabled — vLLM async-IO falls back to syscall ; many
        users never debug the regression.
* /proc/sys/kernel/io_uring_group (introduced 6.4) :
    GID of the group that may submit when *_disabled = 1, or -1
    when no group gate is set.
* Older kernels are below the 2023 privesc patchset
  (CVE-2023-2598 class) ; allowing io_uring for all users on a
  5.10-pre-181 / 5.15-pre-115 / 6.1-pre-30 kernel is a known
  local-privesc footgun.
* /sys/kernel/debug/io_uring (the per-uring debug surface) is
  root-only ; we surface that gracefully rather than 500-ing.

Verdicts (priority-ordered) :
  disabled_systemwide          io_uring_disabled = 2.
  unrestricted_to_all_users    io_uring_disabled = 0 AND
                               io_uring_group = -1 → anyone can
                               submit.
  kernel_pre_cve_fix           kernel major.minor < 5.10 OR
                               between 5.10 and 5.15 (where 5.15
                               isn't reached) — broad heuristic
                               for "pre-2023 CVE patchset".
  debugfs_locked_requires_root /sys/kernel/debug/io_uring not
                               readable as the daemon user.
  ok                           gates are deliberately set, kernel
                               new enough.
  unknown                      sysctl path missing (kernel
                               built without io_uring or pre-6.4
                               without the gating knobs).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional, Tuple


NAME = "io_uring_runtime_audit"


_PROC_DISABLED = "/proc/sys/kernel/io_uring_disabled"
_PROC_GROUP = "/proc/sys/kernel/io_uring_group"
_DEBUGFS = "/sys/kernel/debug/io_uring"


# Roughly : anything below 5.10 is pre-CVE-2023 even with all LTS
# backports.
_PRE_CVE_MAX = (5, 10)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except PermissionError:
        return "__EACCES__"
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None or t == "__EACCES__":
        return None
    try:
        return int(t)
    except ValueError:
        return None


def parse_kernel_release(rel: str) -> Optional[Tuple[int, int]]:
    """Parse 'X.Y.Z…' to (X, Y), tolerant of suffixes like
    '6.17.0-29-generic'."""
    if not rel:
        return None
    try:
        head = rel.split("-")[0]
        parts = head.split(".")
        if len(parts) < 2:
            return None
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def read_state(proc_disabled: str = _PROC_DISABLED,
                 proc_group: str = _PROC_GROUP,
                 debugfs: str = _DEBUGFS) -> dict:
    out: dict = {
        "io_uring_disabled": _read_int(proc_disabled),
        "io_uring_group": _read_int(proc_group),
        "sysctl_present": os.path.exists(proc_disabled),
        "debugfs_present": os.path.isdir(debugfs),
        "debugfs_readable": False,
    }
    if out["debugfs_present"]:
        try:
            os.listdir(debugfs)
            out["debugfs_readable"] = True
        except PermissionError:
            out["debugfs_readable"] = False
        except OSError:
            out["debugfs_readable"] = False
    return out


def classify(state: dict,
              kernel_release: Optional[str]) -> dict:
    disabled = state.get("io_uring_disabled")
    group = state.get("io_uring_group")
    sysctl_present = state.get("sysctl_present")

    # 1) disabled_systemwide
    if disabled == 2:
        return {"verdict": "disabled_systemwide",
                "reason": ("io_uring_disabled = 2 — io_uring "
                          "submission is blocked for everyone. "
                          "vLLM / llama.cpp async-IO falls back to "
                          "blocking syscalls."),
                "recommendation": _recipe_loosen()}

    # 2) kernel_pre_cve_fix — must check *before* the unrestricted
    #    verdict, because age + unrestricted is worse than recent +
    #    unrestricted.
    if disabled == 0 and group == -1:
        kver = parse_kernel_release(kernel_release or "")
        if kver and kver < _PRE_CVE_MAX:
            return {"verdict": "kernel_pre_cve_fix",
                    "reason": (f"Kernel {kernel_release} is below "
                              f"5.10 and io_uring is open to all "
                              f"users — known local-privesc surface."),
                    "recommendation": _recipe_restrict()}

    # 3) unrestricted_to_all_users
    if disabled == 0 and group == -1:
        return {"verdict": "unrestricted_to_all_users",
                "reason": ("io_uring_disabled = 0 and "
                          "io_uring_group = -1 — any UID can "
                          "submit. On a multi-tenant host this is "
                          "a worth-checking attack surface."),
                "recommendation": _recipe_restrict()}

    # 4) debugfs_locked_requires_root
    if state.get("debugfs_present") and \
            not state.get("debugfs_readable"):
        return {"verdict": "debugfs_locked_requires_root",
                "reason": ("/sys/kernel/debug/io_uring exists but "
                          "is not readable as the daemon user — "
                          "per-uring debug surface unavailable."),
                "recommendation": _recipe_root_debugfs()}

    if not sysctl_present:
        return {"verdict": "unknown",
                "reason": ("/proc/sys/kernel/io_uring_disabled is "
                          "absent — kernel built without io_uring "
                          "or below 6.4 (no gating knobs)."),
                "recommendation": ""}

    return {"verdict": "ok",
            "reason": (f"io_uring gated as intended "
                      f"(disabled={disabled}, group={group})."),
            "recommendation": ""}


def status(config=None,
            proc_disabled: str = _PROC_DISABLED,
            proc_group: str = _PROC_GROUP,
            debugfs: str = _DEBUGFS,
            kernel_release: Optional[str] = None) -> dict:
    if kernel_release is None:
        kernel_release = os.uname().release
    state = read_state(proc_disabled, proc_group, debugfs)
    ok = bool(state.get("sysctl_present") or
                state.get("debugfs_present"))
    verdict = classify(state, kernel_release)
    return {"ok": ok,
              "kernel_release": kernel_release,
              **state,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_loosen() -> str:
    return ("# Restore io_uring for trusted users :\n"
            "echo 1 | sudo tee /proc/sys/kernel/io_uring_disabled\n"
            "sudo groupadd io_uring 2>/dev/null || true\n"
            "echo $(getent group io_uring | cut -d: -f3) | \\\n"
            "  sudo tee /proc/sys/kernel/io_uring_group\n"
            "# Add your inference user to the io_uring group.\n")


def _recipe_restrict() -> str:
    return ("# Gate io_uring to a dedicated group :\n"
            "sudo groupadd io_uring\n"
            "sudo usermod -aG io_uring $USER\n"
            "echo 1 | sudo tee /proc/sys/kernel/io_uring_disabled\n"
            "echo $(getent group io_uring | cut -d: -f3) | \\\n"
            "  sudo tee /proc/sys/kernel/io_uring_group\n"
            "# Persist via /etc/sysctl.d/99-io-uring.conf :\n"
            "#   kernel.io_uring_disabled = 1\n"
            "#   kernel.io_uring_group    = <gid>\n")


def _recipe_root_debugfs() -> str:
    return ("# Inspect /sys/kernel/debug/io_uring (root) :\n"
            "sudo ls /sys/kernel/debug/io_uring\n"
            "# Either run the daemon as root or accept that the\n"
            "# per-uring debug surface stays opaque to the dashboard.\n")
