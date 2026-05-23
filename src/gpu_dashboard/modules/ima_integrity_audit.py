"""Module ima_integrity_audit — IMA / EVM / SecureBoot (R&D #53.3).

Reads :
  /sys/kernel/security/ima/                runtime_measurements_count,
                                            violations, policy (req. root)
  /sys/kernel/security/evm                 EVM hex flags (req. root)
  /sys/kernel/security/integrity/          presence
  /sys/firmware/efi/efivars/SecureBoot-*   SecureBoot variable

The chain we care about :

  EFI Secure Boot → only signed kernel + signed shim/grub.
                    Stops *boot-time* tampering only.
  IMA (appraise + measure) → only signed binaries/libs run from
                    userland on this host. Extends the chain
                    *past* boot.
  EVM             → metadata integrity (xattrs, signatures) for
                    IMA's measurements.

A common foot-gun : user enables Secure Boot for the kernel/shim
chain, then never deploys an IMA policy or never arms EVM, so any
unsigned binary (incl. nvidia.ko sometimes) still runs.

Verdicts (priority-ordered) :
  evm_disabled_secureboot_on   Secure Boot is on AND
                               /sys/kernel/security/evm reports 0 /
                               unreadable → chain stops at boot.
  ima_violations_nonzero       violations > 0 (kernel saw a file
                               whose measurement disagreed with the
                               loaded IMA policy).
  ima_no_policy_loaded         IMA available + policy file empty
                               or no measurement entries.
  measurement_log_stagnant     measurement count = 0 — kernel
                               built with IMA but no policy ever
                               loaded → effectively off.
  ok                           IMA armed, EVM armed if Secure Boot
                               on, no violations.
  requires_root                runtime_measurements_count + policy
                               not readable as the daemon's user.
  unknown                      /sys/kernel/security absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "ima_integrity_audit"


_SYS_SEC = "/sys/kernel/security"
_EFI_EFIVARS = "/sys/firmware/efi/efivars"


def _read_str(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except PermissionError:
        return "__EACCES__"
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read_str(p)
    if t is None or t == "__EACCES__":
        return None
    try:
        return int(t)
    except ValueError:
        return None


def read_ima(sys_sec: str = _SYS_SEC) -> dict:
    ima_dir = os.path.join(sys_sec, "ima")
    out: dict = {"available": os.path.isdir(ima_dir)}
    if not out["available"]:
        return out
    out["runtime_measurements_count"] = _read_int(
        os.path.join(ima_dir, "runtime_measurements_count"))
    out["violations"] = _read_int(
        os.path.join(ima_dir, "violations"))
    policy_value = _read_str(os.path.join(ima_dir, "policy"))
    out["policy_readable"] = policy_value not in (None, "__EACCES__")
    if out["policy_readable"]:
        out["policy_lines"] = (policy_value or "").count("\n") + (
            1 if policy_value else 0)
    else:
        out["policy_lines"] = None
    out["permission_denied"] = (policy_value == "__EACCES__") or (
        out["runtime_measurements_count"] is None and
        os.path.exists(os.path.join(
            ima_dir, "runtime_measurements_count")))
    return out


def read_evm(sys_sec: str = _SYS_SEC) -> dict:
    p = os.path.join(sys_sec, "evm")
    out: dict = {"available": os.path.isfile(p)}
    if not out["available"]:
        return out
    raw = _read_str(p)
    out["raw"] = raw
    if raw is None or raw == "__EACCES__":
        out["armed"] = None
        out["permission_denied"] = raw == "__EACCES__"
        return out
    try:
        # /sys/kernel/security/evm reports a small integer (0 = off,
        # non-zero bitmask = armed).
        out["armed"] = int(raw) != 0
    except ValueError:
        out["armed"] = None
    out["permission_denied"] = False
    return out


def read_secureboot(efivars: str = _EFI_EFIVARS) -> dict:
    out: dict = {"present": False, "enabled": None}
    if not os.path.isdir(efivars):
        return out
    target: Optional[str] = None
    for name in os.listdir(efivars):
        if name.startswith("SecureBoot-"):
            target = name
            break
    if target is None:
        return out
    out["present"] = True
    try:
        with open(os.path.join(efivars, target), "rb") as f:
            data = f.read()
    except PermissionError:
        out["permission_denied"] = True
        return out
    except OSError:
        return out
    # EFI variable layout : 4-byte attribute prefix + value bytes.
    if len(data) >= 5:
        out["enabled"] = data[4] == 1
    return out


def classify(ima: dict, evm: dict, sb: dict) -> dict:
    integrity_present = ima.get("available") or evm.get("available")
    if not integrity_present and not sb.get("present"):
        return {"verdict": "unknown",
                "reason": ("Neither /sys/kernel/security nor EFI "
                          "SecureBoot vars are accessible."),
                "recommendation": ""}

    sb_on = sb.get("enabled") is True

    # 1) evm_disabled_secureboot_on
    if sb_on and evm.get("available") and evm.get("armed") is False:
        return {"verdict": "evm_disabled_secureboot_on",
                "reason": ("Secure Boot is enabled but EVM is off "
                          "(/sys/kernel/security/evm = 0). The "
                          "boot chain stops at the kernel — "
                          "userland binaries run unmeasured."),
                "recommendation": _recipe_arm_evm()}

    # 2) ima_violations_nonzero
    if ima.get("available") and ima.get("violations") and \
            ima["violations"] > 0:
        return {"verdict": "ima_violations_nonzero",
                "reason": (f"IMA reports {ima['violations']} "
                          f"violation(s) — a measured file "
                          f"changed unexpectedly."),
                "recommendation": _recipe_ima_violations()}

    # 3) ima_no_policy_loaded
    if ima.get("available") and ima.get("policy_readable") and \
            (ima.get("policy_lines") or 0) == 0:
        return {"verdict": "ima_no_policy_loaded",
                "reason": ("IMA kernel feature compiled in but no "
                          "policy is loaded — measurement + "
                          "appraisal effectively off."),
                "recommendation": _recipe_load_ima_policy()}

    # 4) measurement_log_stagnant
    if ima.get("available") and \
            ima.get("runtime_measurements_count") == 0:
        return {"verdict": "measurement_log_stagnant",
                "reason": ("IMA available but measurement log is "
                          "empty — no policy ever triggered."),
                "recommendation": _recipe_load_ima_policy()}

    # 5) requires_root — IMA available but counters/policy unreadable
    if ima.get("available") and ima.get("permission_denied"):
        return {"verdict": "requires_root",
                "reason": ("IMA sysfs present but counters / policy "
                          "require root to read."),
                "recommendation": _recipe_root_check()}

    return {"verdict": "ok",
            "reason": ("Integrity stack consistent : "
                      + ("SecureBoot on, "
                          if sb_on else "SecureBoot off, ")
                      + ("IMA armed"
                          if ima.get("available") else "IMA n/a")
                      + "."),
            "recommendation": ""}


def status(config=None,
            sys_sec: str = _SYS_SEC,
            efivars: str = _EFI_EFIVARS) -> dict:
    ima = read_ima(sys_sec)
    evm = read_evm(sys_sec)
    sb = read_secureboot(efivars)
    ok = bool(ima.get("available") or evm.get("available")
                  or sb.get("present"))
    verdict = classify(ima, evm, sb)
    return {"ok": ok, "ima": ima, "evm": evm,
              "secureboot": sb, "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_arm_evm() -> str:
    return ("# EVM arming usually happens via initramfs hook that\n"
            "# echoes a magic value into /sys/kernel/security/evm.\n"
            "# Quick test (root) :\n"
            "echo 1 | sudo tee /sys/kernel/security/evm\n"
            "# For persistence on Debian/Ubuntu : install ima-evm-utils,\n"
            "# generate keys, sign xattrs, then add evm=fix to the\n"
            "# kernel cmdline for a first boot to populate xattrs.\n")


def _recipe_ima_violations() -> str:
    return ("# Inspect the measurement log (requires root) :\n"
            "sudo cat /sys/kernel/security/ima/ascii_runtime_measurements | tail -50\n"
            "# A violation often means an unsigned / modified file\n"
            "# was executed. Re-sign or reinstall the offending\n"
            "# package, then reboot to reset the count.\n")


def _recipe_load_ima_policy() -> str:
    return ("# Load a baseline IMA measurement policy :\n"
            "echo 'measure func=BPRM_CHECK' | sudo tee /sys/kernel/security/ima/policy\n"
            "# For persistence : add ima_policy=tcb to the kernel\n"
            "# cmdline (GRUB_CMDLINE_LINUX). Reboot, then re-check :\n"
            "sudo wc -l /sys/kernel/security/ima/ascii_runtime_measurements\n")


def _recipe_root_check() -> str:
    return ("# The dashboard daemon doesn't have read access to\n"
            "# IMA counters / policy. As a quick interactive check :\n"
            "sudo cat /sys/kernel/security/ima/runtime_measurements_count\n"
            "sudo cat /sys/kernel/security/ima/policy\n"
            "# Either run the daemon as root, grant CAP_SYS_ADMIN,\n"
            "# or accept that IMA verdict will stay 'requires_root'.\n")
