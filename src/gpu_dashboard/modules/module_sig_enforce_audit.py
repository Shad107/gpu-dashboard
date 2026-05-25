"""Module module_sig_enforce_audit — kernel module signature
enforcement vs Secure Boot posture (R&D #102.3).

A common Secure-Boot homelab footgun : SB enabled in firmware
but the kernel doesn't actually enforce module signatures
because module.sig_enforce=N. Modules can be loaded unsigned
despite SB → tooling assumes hardened, reality isn't.

The existing module_integrity_audit covers kernel.tainted,
modules_disabled, and nvidia version drift. It does NOT
read sig_enforce or cross-reference SB. efi_boot_order_audit
and ima_integrity_audit don't touch sig_enforce either.

Reads :

  /sys/module/module/parameters/sig_enforce  Y/N
  /sys/kernel/security/lockdown              [mode] ...
  /sys/firmware/efi/efivars/SecureBoot-*     EFI bool var
  /proc/cmdline                              boot args

Secure Boot EFI variable layout: first 4 bytes are attribute
flags, byte 5 is the boolean value (0=off, 1=on).

Verdicts (worst-first) :

  sb_on_sig_enforce_off    err     SB enabled in firmware
                                   but sig_enforce=N — policy
                                   mismatch ; modules can
                                   load unsigned.
  sig_enforce_off_lockdown_none warn  Neither layer enforces.
  ok                               consistent (sig_enforce=Y,
                                   OR sig_enforce=N + SB off
                                   + lockdown integrity+).
  requires_root                    sig_enforce unreadable.
  unknown                          /sys/module/module/parameters
                                   absent (CONFIG_MODULE_SIG=n).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "module_sig_enforce_audit"

DEFAULT_SIG_ENFORCE = (
    "/sys/module/module/parameters/sig_enforce")
DEFAULT_LOCKDOWN = "/sys/kernel/security/lockdown"
DEFAULT_EFIVARS = "/sys/firmware/efi/efivars"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_str(path: str) -> Optional[str]:
    t = _read_text(path)
    return t.strip() if t is not None else None


def parse_lockdown_active(text: Optional[str]
                            ) -> Optional[str]:
    """Format: 'none [integrity] confidentiality' or
    '[none] integrity confidentiality' — find the
    bracketed item."""
    if not text:
        return None
    for tok in text.split():
        if tok.startswith("[") and tok.endswith("]"):
            return tok[1:-1]
    return None


def read_secure_boot(efivars_dir: str = DEFAULT_EFIVARS
                       ) -> Optional[bool]:
    """Read SecureBoot-* var, byte 5 is the boolean."""
    if not os.path.isdir(efivars_dir):
        return None
    try:
        entries = os.listdir(efivars_dir)
    except OSError:
        return None
    target = None
    for ent in entries:
        if ent.startswith("SecureBoot-"):
            target = os.path.join(efivars_dir, ent)
            break
    if target is None:
        return None
    try:
        with open(target, "rb") as fh:
            data = fh.read()
    except (OSError, PermissionError):
        return None
    if len(data) < 5:
        return None
    return data[4] == 1


def classify(sig_enforce: Optional[str],
             lockdown: Optional[str],
             secure_boot: Optional[bool],
             sig_enforce_present: bool) -> dict:
    if not sig_enforce_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/module/module/parameters/"
                    "sig_enforce absent — "
                    "CONFIG_MODULE_SIG=n.")}
    if sig_enforce is None:
        return {"verdict": "requires_root",
                "reason": (
                    "sig_enforce unreadable — re-run "
                    "as root.")}

    enforced = sig_enforce.upper() == "Y"

    # err — SB on but no sig enforcement
    if secure_boot is True and not enforced:
        return {
            "verdict": "sb_on_sig_enforce_off",
            "reason": (
                "Secure Boot is enabled in firmware but "
                "module.sig_enforce=N — kernel won't "
                "actually reject unsigned modules. SB is "
                "cosmetic.")}

    # warn — sig_enforce=N and lockdown=none (no defense)
    if (not enforced
            and (lockdown is None or lockdown == "none")):
        return {
            "verdict": "sig_enforce_off_lockdown_none",
            "reason": (
                "sig_enforce=N AND lockdown=none — no "
                "kernel-side defence against rogue "
                "modules. OK on a dev box, worth fixing "
                "on a shared homelab.")}

    return {"verdict": "ok",
            "reason": (
                f"sig_enforce={sig_enforce} ; "
                f"lockdown={lockdown} ; "
                f"SecureBoot={secure_boot}. Consistent.")}


def status(config: Optional[dict] = None,
           sig_enforce_path: str = DEFAULT_SIG_ENFORCE,
           lockdown_path: str = DEFAULT_LOCKDOWN,
           efivars: str = DEFAULT_EFIVARS) -> dict:
    sig_enforce_present = os.path.isfile(sig_enforce_path)
    sig_enforce = (_read_str(sig_enforce_path)
                   if sig_enforce_present else None)
    lockdown = parse_lockdown_active(
        _read_text(lockdown_path))
    secure_boot = read_secure_boot(efivars)
    verdict = classify(sig_enforce, lockdown, secure_boot,
                       sig_enforce_present)
    return {
        "ok": verdict["verdict"] == "ok",
        "sig_enforce": sig_enforce,
        "lockdown": lockdown,
        "secure_boot": secure_boot,
        "verdict": verdict,
    }
