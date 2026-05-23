"""Module security_posture — LSM + lockdown + paranoia auditor (R&D #46.2).

Single-pane summary of the kernel's userspace-isolation posture.
The actionable signals on a LAN-served inference rig with non-root
daemons :

  /sys/kernel/security/lsm                comma list of active
                                          LSMs (capability, lockdown,
                                          landlock, yama, apparmor,
                                          selinux, ima, evm, ...).
  /sys/kernel/security/lockdown           "[none] integrity
                                          confidentiality" — current
                                          mode flagged in brackets.
  /proc/sys/kernel/perf_event_paranoid    -1 = unrestricted,
                                          0 = no kernel events,
                                          1 = no CPU events,
                                          2 = no per-process events,
                                          ≥ 3 = paranoid (most
                                          modern distros default).
  /proc/sys/kernel/yama/ptrace_scope      0 = anyone,
                                          1 = parent only,
                                          2 = admin only,
                                          3 = nobody.
  /proc/sys/kernel/unprivileged_bpf_disabled
                                          0 = open, 1 = privileged
                                          only, 2 = denied entirely.
  /proc/sys/kernel/kptr_restrict          0 = leak,
                                          1 = redact for non-root,
                                          2 = redact always.
  /proc/sys/kernel/dmesg_restrict         0 = anyone read dmesg,
                                          1 = CAP_SYSLOG only.
  /proc/sys/kernel/modules_disabled       0 = loadable,
                                          1 = no more modules
                                          can be loaded (sealed).

Verdicts (priority-ordered) :
  paranoid_too_loose      ≥1 of {ptrace_scope=0, kptr_restrict=0,
                          dmesg_restrict=0, perf_event_paranoid<2}
                          → unprivileged userspace can read more
                          kernel state than necessary. Surface for
                          a homelab rig that does NOT have
                          development needs.
  lockdown_confined       /sys/kernel/security/lockdown active mode
                          is "integrity" or "confidentiality" →
                          some module loading / sysfs writes will
                          fail ; surface for awareness.
  ok                      reasonable defaults : ptrace_scope≥1,
                          kptr_restrict≥1, dmesg_restrict=1,
                          perf_event_paranoid≥2.
  unknown                 /proc/sys/kernel + /sys/kernel/security
                          both unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "security_posture"


_SYS_SECURITY = "/sys/kernel/security"
_PROC_SYS_KERNEL = "/proc/sys/kernel"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_lockdown(text: Optional[str]) -> tuple:
    """[none] integrity confidentiality → ('none', [...])"""
    if not text:
        return (None, [])
    t = text.strip()
    available = re.findall(r"\[?(\w[\w-]*)\]?", t)
    active_match = re.search(r"\[(\w[\w-]*)\]", t)
    active = active_match.group(1) if active_match else None
    return (active, available)


_FIELDS = (
    ("perf_event_paranoid", "perf_event_paranoid"),
    ("ptrace_scope", "yama/ptrace_scope"),
    ("unprivileged_bpf_disabled", "unprivileged_bpf_disabled"),
    ("kptr_restrict", "kptr_restrict"),
    ("dmesg_restrict", "dmesg_restrict"),
    ("modules_disabled", "modules_disabled"),
)


def read_sysctls(sys_k: str = _PROC_SYS_KERNEL) -> dict:
    out: dict = {}
    for key, path in _FIELDS:
        v = _read_int(os.path.join(sys_k, path))
        if v is not None:
            out[key] = v
    return out


def read_security(sys_sec: str = _SYS_SECURITY) -> dict:
    out: dict = {}
    lsm_text = _read(os.path.join(sys_sec, "lsm"))
    if lsm_text:
        out["lsm"] = lsm_text.strip().split(",")
    lockdown_text = _read(os.path.join(sys_sec, "lockdown"))
    if lockdown_text is not None:
        active, available = parse_lockdown(lockdown_text)
        out["lockdown"] = active
        out["lockdown_available"] = available
    return out


_RECIPE_TIGHTEN = (
    "# Tighten the most-common kernel-visibility knobs for a\n"
    "# LAN-served homelab rig. None of these break inference\n"
    "# workloads — they only block unprivileged userspace from\n"
    "# reading kernel addresses, peer-process state, dmesg, etc :\n"
    "sudo tee /etc/sysctl.d/99-security-posture.conf <<'EOF'\n"
    "kernel.kptr_restrict = 2\n"
    "kernel.dmesg_restrict = 1\n"
    "kernel.yama.ptrace_scope = 1\n"
    "kernel.perf_event_paranoid = 3\n"
    "EOF\n"
    "sudo sysctl --system"
)

_RECIPE_LOCKDOWN_INFO = (
    "# Kernel lockdown is active in '{mode}' mode. Some operations\n"
    "# (loading unsigned modules, /dev/mem, kdb, sysfs writes that\n"
    "# alter kernel state) will be refused. To check if a failure\n"
    "# is lockdown-related :\n"
    "dmesg | grep -i lockdown\n"
    "# To disable (UEFI Secure Boot reboot required) :\n"
    "# Mokutil --disable-validation  → reboot into mokmanager."
)


def _too_loose(sysctls: dict) -> list:
    issues: list = []
    if sysctls.get("ptrace_scope", 1) == 0:
        issues.append(
            "kernel.yama.ptrace_scope=0 (any process can ptrace any)")
    if sysctls.get("kptr_restrict", 1) == 0:
        issues.append(
            "kernel.kptr_restrict=0 (kernel addresses leak via /proc)")
    if sysctls.get("dmesg_restrict", 1) == 0:
        issues.append(
            "kernel.dmesg_restrict=0 (any user can read dmesg)")
    pep = sysctls.get("perf_event_paranoid")
    if isinstance(pep, int) and pep < 2:
        issues.append(
            f"kernel.perf_event_paranoid={pep} (< 2 = liberal)")
    return issues


def classify(sysctls: dict, security: dict) -> dict:
    if not sysctls and not security:
        return {"verdict": "unknown",
                "reason": ("/proc/sys/kernel + "
                           "/sys/kernel/security both unreadable."),
                "recommendation": ""}
    loose = _too_loose(sysctls)
    if loose:
        return {"verdict": "paranoid_too_loose",
                "reason": (f"{len(loose)} loose kernel-visibility "
                           f"knob(s) : " + " ; ".join(loose)),
                "recommendation": _RECIPE_TIGHTEN}
    lockdown = security.get("lockdown")
    if lockdown and lockdown != "none":
        return {"verdict": "lockdown_confined",
                "reason": (f"Kernel lockdown active in '{lockdown}' "
                           f"mode — some module loading / sysfs "
                           f"writes will be refused."),
                "recommendation": _RECIPE_LOCKDOWN_INFO.replace(
                    "{mode}", lockdown)}
    return {"verdict": "ok",
            "reason": (f"LSMs active : "
                       f"{', '.join(security.get('lsm') or ['?'])}. "
                       f"ptrace_scope={sysctls.get('ptrace_scope')}, "
                       f"perf_event_paranoid="
                       f"{sysctls.get('perf_event_paranoid')}, "
                       f"kptr_restrict="
                       f"{sysctls.get('kptr_restrict')}."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    sysctls = read_sysctls(_PROC_SYS_KERNEL)
    security = read_security(_SYS_SECURITY)
    verdict = classify(sysctls, security)
    return {
        "ok": bool(sysctls) or bool(security),
        "sysctls": sysctls,
        "security": security,
        "verdict": verdict,
    }
