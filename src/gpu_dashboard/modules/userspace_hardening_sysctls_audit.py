"""Module userspace_hardening_sysctls_audit — ASLR + fs/* +
suid_dumpable hardening posture (R&D #88.1).

security_posture (R&D #?.?) already owns the
dmesg_restrict / kptr_restrict / perf_event_paranoid /
ptrace_scope axis. This audit owns the orthogonal set of
classic userspace-hardening sysctls that no existing module
inspects :

  /proc/sys/kernel/randomize_va_space
      0 = no ASLR  | 1 = stack/heap | 2 = full
  /proc/sys/fs/protected_hardlinks
      0 = anyone can hardlink anything  | 1 = restricted
  /proc/sys/fs/protected_symlinks
      0 = follow-anyone  | 1 = restricted to owner
  /proc/sys/fs/protected_fifos
      0 = off  | 1 = world-writable  | 2 = sticky+world (safer)
  /proc/sys/fs/protected_regular
      same scale as protected_fifos — protects /tmp regulars
  /proc/sys/fs/suid_dumpable
      0 = never  | 1 = always  | 2 = readable-by-root-only
      (2 leaks SUID-process memory into /var/lib/systemd/coredump
      under default systemd-coredump setups).

Verdicts (worst-first) :

  aslr_disabled                err  randomize_va_space = 0
  suid_dumpable_world          err  suid_dumpable = 1 — SUID
                                    coredumps world-readable
  protected_symlinks_off       warn protected_symlinks = 0
  protected_hardlinks_off      warn protected_hardlinks = 0
  protected_fifos_regular_off  accent either flag < 2
  hardened                     ok   all knobs at safe values
  unknown                      /proc/sys/kernel absent

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "userspace_hardening_sysctls_audit"

DEFAULT_PROC_SYS = "/proc/sys"

_KNOBS = (
    ("randomize_va_space", "kernel/randomize_va_space"),
    ("protected_hardlinks", "fs/protected_hardlinks"),
    ("protected_symlinks", "fs/protected_symlinks"),
    ("protected_fifos", "fs/protected_fifos"),
    ("protected_regular", "fs/protected_regular"),
    ("suid_dumpable", "fs/suid_dumpable"),
)


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def read_sysctls(root: str = DEFAULT_PROC_SYS) -> dict:
    out: dict = {}
    for key, rel in _KNOBS:
        v = _read_int(os.path.join(root, rel))
        if v is not None:
            out[key] = v
    return out


def classify(s: dict) -> dict:
    if not s:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/{kernel,fs} sysctls unreadable "
                    "— procfs unavailable.")}

    # err 1 — ASLR explicitly off
    if s.get("randomize_va_space", 2) == 0:
        return {"verdict": "aslr_disabled",
                "reason": (
                    "kernel.randomize_va_space = 0 — ASLR is "
                    "completely disabled, every process maps "
                    "at predictable addresses.")}

    # err 2 — SUID core dumps enabled
    if s.get("suid_dumpable", 0) == 1:
        return {"verdict": "suid_dumpable_world",
                "reason": (
                    "fs.suid_dumpable = 1 — SUID processes "
                    "can produce core dumps that may leak "
                    "credentials/secrets from privileged "
                    "binaries.")}

    # warn 1 — symlink protection off
    if s.get("protected_symlinks", 1) == 0:
        return {"verdict": "protected_symlinks_off",
                "reason": (
                    "fs.protected_symlinks = 0 — symlinks in "
                    "world-writable sticky dirs (/tmp) can "
                    "be followed across UIDs, enabling "
                    "classic /tmp race attacks.")}

    # warn 2 — hardlink protection off
    if s.get("protected_hardlinks", 1) == 0:
        return {"verdict": "protected_hardlinks_off",
                "reason": (
                    "fs.protected_hardlinks = 0 — unprivileged "
                    "users can hardlink to files they don't "
                    "own, defeating quota/audit on /tmp.")}

    # accent — fifos/regular not at 2
    fifos = s.get("protected_fifos", 0)
    regular = s.get("protected_regular", 0)
    if fifos < 2 or regular < 2:
        return {
            "verdict": "protected_fifos_regular_off",
            "reason": (
                f"fs.protected_fifos = {fifos}, "
                f"fs.protected_regular = {regular} — "
                "open(O_CREAT) into world-writable sticky "
                "dirs is not fully sandboxed (recommend 2)."),
            "protected_fifos": fifos,
            "protected_regular": regular,
        }

    return {"verdict": "hardened",
            "reason": (
                f"All {len(s)} userspace-hardening sysctl(s) "
                "at safe defaults.")}


def status(config: Optional[dict] = None,
           proc_sys: str = DEFAULT_PROC_SYS) -> dict:
    sysctls = read_sysctls(proc_sys)
    verdict = classify(sysctls)
    return {
        "ok": verdict["verdict"] in ("hardened",),
        "sysctls": sysctls,
        "verdict": verdict,
    }
