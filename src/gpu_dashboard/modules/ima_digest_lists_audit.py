"""Module ima_digest_lists_audit — IMA-appraisal digest-list
provisioning posture (R&D #105.1).

IMA-appraisal enforcement (the strict 'reject unsigned binaries'
mode used by Secure-Boot + IMA homelabs) requires a set of
digest lists pre-loaded into the kernel keyring. If appraisal=
enforce but zero digest lists are loaded, every exec gets denied.

The surface :

  /sys/kernel/security/integrity/digest_lists_loaded   integer
  /sys/kernel/security/integrity/digest_lists/         dir
  /sys/kernel/security/ima/policy                      appraisal?

This is Fedora/SUSE-style downstream patchset territory ; Ubuntu
generic kernels typically don't ship the digest_lists files at
all — verdict on those is `unknown`. ima_integrity_audit and
ima_measurement_freshness_audit cover the measurement log /
counters ; neither reads digest_lists.

Verdicts (worst-first) :

  appraisal_no_digest_lists  err     ima policy contains
                                     'appraise func=BPRM_CHECK'
                                     and digest_lists_loaded=0
                                     — every exec will be
                                     denied.
  digest_lists_world_readable_drift accent some files mode 0644,
                                     some 0600 — permission
                                     drift.
  digest_lists_absent_evm_off accent  digest_lists_loaded=0 +
                                     EVM not enforced —
                                     informational, host just
                                     doesn't use IMA-appraisal.
  ok                                 lists loaded coherently.
  requires_root                      securityfs unreadable.
  unknown                            digest_lists surface absent
                                     (mainline / Ubuntu kernel).

stdlib only.
"""
from __future__ import annotations

import os
import stat
from typing import Optional

NAME = "ima_digest_lists_audit"

DEFAULT_INTEGRITY = "/sys/kernel/security/integrity"
DEFAULT_IMA_POLICY = "/sys/kernel/security/ima/policy"
DEFAULT_EVM = "/sys/kernel/security/evm"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def policy_enforces_appraise(text: Optional[str]) -> bool:
    if not text:
        return False
    for line in text.splitlines():
        if line.startswith("appraise "):
            return True
    return False


def scan_digest_lists_perms(d: str) -> dict:
    """Return {count, mode_644, mode_600, other}."""
    out = {"count": 0, "mode_644": 0, "mode_600": 0,
           "other": 0}
    if not os.path.isdir(d):
        return out
    try:
        entries = os.listdir(d)
    except OSError:
        return out
    for ent in entries:
        path = os.path.join(d, ent)
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            continue
        out["count"] += 1
        if mode == 0o644:
            out["mode_644"] += 1
        elif mode == 0o600:
            out["mode_600"] += 1
        else:
            out["other"] += 1
    return out


def classify(integrity_present: bool,
             loaded_count: Optional[int],
             appraises: bool,
             evm_enforced: bool,
             perms: dict) -> dict:
    if not integrity_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/security/integrity absent "
                    "— kernel without IMA / integrity "
                    "subsystem.")}
    if loaded_count is None:
        return {"verdict": "unknown",
                "reason": (
                    "digest_lists_loaded surface absent "
                    "— mainline / Ubuntu kernel doesn't "
                    "ship the IMA-appraisal digest-lists "
                    "patchset.")}

    # err — appraisal enforce + no lists
    if appraises and loaded_count == 0:
        return {
            "verdict": "appraisal_no_digest_lists",
            "reason": (
                "IMA policy contains 'appraise' rules but "
                "digest_lists_loaded=0. Every exec / mmap "
                "will be denied at next boot in enforcing "
                "mode.")}

    # accent — permission drift across digest list files
    if perms["mode_644"] > 0 and perms["mode_600"] > 0:
        return {
            "verdict": "digest_lists_world_readable_drift",
            "reason": (
                f"{perms['mode_644']} digest list(s) at "
                f"0644 + {perms['mode_600']} at 0600 — "
                "permission drift, fix to a consistent "
                "mode.")}

    # accent — no lists + EVM not enforced (info)
    if loaded_count == 0 and not evm_enforced:
        return {
            "verdict": "digest_lists_absent_evm_off",
            "reason": (
                "digest_lists_loaded=0 and EVM not "
                "enforcing — host simply doesn't use "
                "IMA-appraisal. Informational ; normal on "
                "a non-hardened desktop.")}

    return {"verdict": "ok",
            "reason": (
                f"digest_lists_loaded={loaded_count} ; "
                f"file_count={perms['count']} ; "
                f"appraise={appraises}. Coherent.")}


def status(config: Optional[dict] = None,
           integrity: str = DEFAULT_INTEGRITY,
           ima_policy_path: str = DEFAULT_IMA_POLICY,
           evm_path: str = DEFAULT_EVM) -> dict:
    integrity_present = os.path.isdir(integrity)
    loaded_count = _read_int(
        os.path.join(integrity, "digest_lists_loaded"))
    perms = scan_digest_lists_perms(
        os.path.join(integrity, "digest_lists"))
    appraises = policy_enforces_appraise(
        _read_text(ima_policy_path))
    evm_val = _read_int(evm_path) or 0
    evm_enforced = evm_val > 0
    verdict = classify(integrity_present, loaded_count,
                       appraises, evm_enforced, perms)
    return {
        "ok": verdict["verdict"] == "ok",
        "digest_lists_loaded": loaded_count,
        "digest_list_file_count": perms["count"],
        "ima_appraise_active": appraises,
        "evm_enforced": evm_enforced,
        "verdict": verdict,
    }
