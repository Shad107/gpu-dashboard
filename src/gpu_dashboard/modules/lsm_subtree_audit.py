"""Module lsm_subtree_audit — Linux Security Module subtree
audit (R&D #75.1).

The existing security_posture module reads /sys/kernel/security/
{lsm, lockdown} as world-readable presence flags. This audit
goes one level deeper : it walks each per-LSM subdirectory
under /sys/kernel/security/ to confirm the stacked LSMs
actually surface their interfaces, and surfaces policy-load
state where readable.

Reads :
  /sys/kernel/security/lsm             active LSM stack
  /sys/kernel/security/lockdown        kernel lockdown mode
  /sys/kernel/security/{apparmor,
                          ima,
                          safesetid,
                          integrity,
                          evm,
                          landlock,
                          yama}/        per-LSM subtrees

Verdicts (priority order) :
  lsm_disabled              /sys/kernel/security/lsm reads
                              "capability" alone (or unreadable).
  policy_unloaded           apparmor subtree present AND
                              /sys/kernel/security/apparmor/
                              profiles is readable and empty.
  requires_root             ≥1 expected per-LSM subdir present
                              but core files unreadable
                              (typical 0640 root).
  stacked_partial           ≥1 LSM in the lsm string that
                              normally has a sysfs subtree
                              (apparmor, ima, safesetid,
                              integrity) lacks its directory.
  ok                        stack healthy, subtrees consistent.
  unknown                   /sys/kernel/security absent
                              (kernel without
                              CONFIG_SECURITYFS).

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional, Set


NAME = "lsm_subtree_audit"


_SYS_SECURITY = "/sys/kernel/security"

# LSMs that normally expose a sysfs subdir under
# /sys/kernel/security/<lsm>/.
_EXPECTED_SUBDIR = {"apparmor", "ima", "safesetid",
                       "integrity"}


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _can_read(path: str) -> bool:
    try:
        with open(path) as f:
            f.read(1)
        return True
    except OSError:
        return False


def list_lsm_stack(sec_root: str = _SYS_SECURITY) -> List[str]:
    """Parses the comma-separated /sys/kernel/security/lsm."""
    text = _read(os.path.join(sec_root, "lsm"))
    if not text:
        return []
    return [s for s in text.split(",") if s]


def list_subdirs(sec_root: str = _SYS_SECURITY) -> List[str]:
    if not os.path.isdir(sec_root):
        return []
    try:
        return sorted(n for n in os.listdir(sec_root)
                          if os.path.isdir(
                              os.path.join(sec_root, n)))
    except OSError:
        return []


def read_lockdown(sec_root: str = _SYS_SECURITY) -> Optional[str]:
    text = _read(os.path.join(sec_root, "lockdown"))
    if not text:
        return None
    # Format : "[none] integrity confidentiality"
    for tok in text.split():
        if tok.startswith("[") and tok.endswith("]"):
            return tok[1:-1]
    return None


def apparmor_profile_count(sec_root: str = _SYS_SECURITY
                                ) -> Optional[int]:
    p = os.path.join(sec_root, "apparmor", "profiles")
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return sum(1 for ln in f if ln.strip())
    except OSError:
        return None


def classify(security_present: bool,
              lsm_stack: List[str],
              subdirs: List[str],
              lockdown: Optional[str],
              apparmor_profiles: Optional[int],
              core_readable: bool,
              core_files_present: bool) -> dict:
    if not security_present:
        return {"verdict": "unknown",
                "reason": ("/sys/kernel/security absent — "
                          "kernel built without CONFIG_SECURITYFS."),
                "recommendation": ""}

    # 1) lsm_disabled
    interesting = [l for l in lsm_stack if l != "capability"]
    if not interesting:
        return {"verdict": "lsm_disabled",
                "reason": (f"LSM stack lists only 'capability' "
                          f"({lsm_stack}). No MAC layer active."),
                "recommendation": _recipe_lsm_disabled()}

    # 2) policy_unloaded
    if (apparmor_profiles is not None
            and apparmor_profiles == 0):
        return {"verdict": "policy_unloaded",
                "reason": ("AppArmor LSM present and "
                          "profiles file readable but contains "
                          "zero loaded profiles."),
                "recommendation": _recipe_policy_unloaded()}

    # 3) requires_root — core files unreadable
    if core_files_present and not core_readable:
        return {"verdict": "requires_root",
                "reason": ("Per-LSM detail files (apparmor "
                          "profiles, ima policy, etc.) exist "
                          "but are 0640 root — running "
                          "unprivileged."),
                "recommendation": _recipe_requires_root()}

    # 4) stacked_partial — listed LSM without expected subdir
    subdirs_set = set(subdirs)
    missing = [l for l in lsm_stack
                  if l in _EXPECTED_SUBDIR
                    and l not in subdirs_set]
    if missing:
        return {"verdict": "stacked_partial",
                "reason": (f"LSM(s) {missing} listed in stack "
                          f"but their /sys/kernel/security/"
                          f"<lsm>/ subdirs are absent."),
                "recommendation": _recipe_stacked_partial()}

    return {"verdict": "ok",
            "reason": (f"LSM stack : {lsm_stack} ; "
                      f"lockdown={lockdown} ; "
                      f"{len(subdirs)} subdirs ; "
                      f"apparmor profiles="
                      f"{apparmor_profiles}."),
            "recommendation": ""}


def status(config=None,
            sec_root: str = _SYS_SECURITY) -> dict:
    security_present = os.path.isdir(sec_root)
    lsm_stack = list_lsm_stack(sec_root) if security_present \
        else []
    subdirs = list_subdirs(sec_root) if security_present else []
    lockdown = read_lockdown(sec_root) if security_present \
        else None
    apparmor_profiles = (apparmor_profile_count(sec_root)
                                if security_present else None)

    # Core file readability probe — pick well-known files.
    probe_paths = [
        os.path.join(sec_root, "apparmor", "profiles"),
        os.path.join(sec_root, "ima", "policy"),
    ]
    core_files_present = any(os.path.exists(p)
                                    for p in probe_paths)
    core_readable = any(_can_read(p) for p in probe_paths)

    verdict = classify(security_present, lsm_stack, subdirs,
                          lockdown, apparmor_profiles,
                          core_readable, core_files_present)
    return {"ok": security_present,
              "security_present": security_present,
              "lsm_stack": lsm_stack,
              "subdirs": subdirs,
              "lockdown": lockdown,
              "apparmor_profile_count": apparmor_profiles,
              "core_files_readable": core_readable,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_lsm_disabled() -> str:
    return ("# No major LSM active. Pick one and enable via\n"
            "# kernel cmdline 'lsm=' :\n"
            "cat /proc/cmdline | tr ' ' '\\n' | grep lsm\n"
            "# Example : add 'lsm=lockdown,yama,apparmor' to\n"
            "# GRUB_CMDLINE_LINUX_DEFAULT.\n"
            "sudo update-grub && sudo reboot\n")


def _recipe_policy_unloaded() -> str:
    return ("# AppArmor enabled but zero profiles loaded.\n"
            "# Reload via apparmor_parser :\n"
            "sudo systemctl restart apparmor.service\n"
            "# Confirm :\n"
            "sudo aa-status\n")


def _recipe_requires_root() -> str:
    return ("# Per-LSM detail files (profiles, policy, log)\n"
            "# require root. Inspect :\n"
            "sudo cat /sys/kernel/security/apparmor/profiles | wc -l\n"
            "sudo cat /sys/kernel/security/ima/policy | head\n")


def _recipe_stacked_partial() -> str:
    return ("# An LSM is listed but its sysfs subdir is missing.\n"
            "# Verify LSM is built-in :\n"
            "cat /sys/kernel/security/lsm\n"
            "ls /sys/kernel/security/\n"
            "# Reload the matching kernel module if applicable.\n")
