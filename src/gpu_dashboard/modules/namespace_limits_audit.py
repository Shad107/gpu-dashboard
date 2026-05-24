"""Module namespace_limits_audit — /proc/sys/user/max_*_namespaces
caps (R&D #89.2).

No existing module reads the per-NS-type kernel limits :

  * userspace_hardening_sysctls_audit (R&D #88.1) — ASLR /
    protected_* / suid_dumpable
  * security_posture — LSM + lockdown + ptrace_scope
  * proc_ns_mountinfo_audit — per-PID NS mountinfo,
    not the limits
  * container_audit — runtime detection only

This audit owns /proc/sys/user/max_{user,pid,net,mnt,ipc,uts,
cgroup,time}_namespaces — the ceilings that determine whether
podman / bwrap / docker rootless can create namespaces at all.

Reads :

  /proc/sys/user/max_user_namespaces      = 0 breaks rootless
                                            containers, bwrap,
                                            firejail.
  /proc/sys/user/max_pid_namespaces
  /proc/sys/user/max_net_namespaces
  /proc/sys/user/max_mnt_namespaces
  /proc/sys/user/max_ipc_namespaces
  /proc/sys/user/max_uts_namespaces
  /proc/sys/user/max_cgroup_namespaces
  /proc/sys/user/max_time_namespaces

Verdicts (worst-first) :

  user_ns_disabled       warn  max_user_namespaces = 0 —
                               podman / bwrap silently fail.
  ns_caps_aggressive     accent ≥3 NS types capped to 0 —
                               likely intentional hardening
                               but worth surfacing.
  ok                     all limits at sane non-zero values.
  unknown                /proc/sys/user absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "namespace_limits_audit"

DEFAULT_PROC_SYS_USER = "/proc/sys/user"

_NS_TYPES = (
    "user", "pid", "net", "mnt",
    "ipc", "uts", "cgroup", "time",
)


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def read_limits(root: str = DEFAULT_PROC_SYS_USER) -> dict:
    out: dict = {}
    for ns in _NS_TYPES:
        v = _read_int(
            os.path.join(root, f"max_{ns}_namespaces"))
        if v is not None:
            out[ns] = v
    return out


def classify(limits: dict) -> dict:
    if not limits:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/user/max_*_namespaces unreadable "
                    "— kernel built without CONFIG_USER_NS or "
                    "procfs unavailable.")}

    if limits.get("user", -1) == 0:
        return {
            "verdict": "user_ns_disabled",
            "reason": (
                "max_user_namespaces = 0 — rootless containers "
                "(podman / docker rootless / bwrap / firejail) "
                "will refuse to start. Distro likely enabled "
                "this as a hardening default."),
            "limit": 0,
        }

    zeroed = [ns for ns, v in limits.items() if v == 0]
    if len(zeroed) >= 3:
        return {
            "verdict": "ns_caps_aggressive",
            "reason": (
                f"{len(zeroed)} namespace type(s) capped to 0: "
                f"{sorted(zeroed)} — intentional hardening "
                "policy, but breaks containers that need those "
                "namespaces."),
            "zeroed_types": sorted(zeroed),
        }

    return {"verdict": "ok",
            "reason": (
                f"{len(limits)} namespace cap(s) at safe "
                "non-zero defaults ; container runtimes "
                "should work normally.")}


def status(config: Optional[dict] = None,
           proc_sys_user: str = DEFAULT_PROC_SYS_USER) -> dict:
    limits = read_limits(proc_sys_user)
    verdict = classify(limits)
    return {
        "ok": verdict["verdict"] == "ok",
        "limits": limits,
        "verdict": verdict,
    }
