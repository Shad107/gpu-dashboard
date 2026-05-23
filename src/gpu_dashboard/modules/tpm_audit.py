"""Module tpm_audit — TPM 1.2/2.0 inventory + measured boot (R&D #49.2).

Reads /sys/class/tpm/tpm*/{tpm_version_major, active_locality,
device/firmware_node/path, ppi/*} and
/sys/kernel/security/tpm0/binary_bios_measurements (root-only
when present).

Verdicts (priority-ordered) :
  tpm1_legacy           tpm_version_major < 2 → TPM 1.2 ; many
                        modern features (full-disk encryption
                        unlock, attestation) require TPM 2.0.
  measured_boot_missing /sys/class/tpm/tpm0 exists but
                        /sys/kernel/security/tpm0/binary_bios_
                        measurements absent → Linux IMA can't
                        seal to PCR values (encrypted-volume
                        key sealing won't work).
  ok                    TPM 2.0 + measured boot log present
                        (or root-only but probed).
  no_tpm                /sys/class/tpm empty (typical VM
                        without virtio-tpm).
  unknown               /sys/class/tpm unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "tpm_audit"


_SYS_CLASS_TPM = "/sys/class/tpm"
_SECURITY_TPM = "/sys/kernel/security/tpm0"


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


def list_tpms(sys_tpm: str = _SYS_CLASS_TPM) -> list:
    if not os.path.isdir(sys_tpm):
        return []
    out: list = []
    try:
        for name in sorted(os.listdir(sys_tpm)):
            if not name.startswith("tpm"):
                continue
            d = os.path.join(sys_tpm, name)
            out.append({
                "name": name,
                "tpm_version_major": _read_int(
                    os.path.join(d, "tpm_version_major")),
                "active_locality": _read_int(
                    os.path.join(d, "active_locality")),
                "firmware_path": (
                    _read(os.path.join(d, "device", "firmware_node",
                                          "path")) or "").strip()
                or None,
                "vendor_id_str": (
                    _read(os.path.join(d, "device",
                                          "description")) or "").strip()
                or None,
            })
    except OSError:
        return []
    return out


def measured_boot_present(security_tpm: str = _SECURITY_TPM) -> dict:
    """Returns {available, permission_error, size_bytes}."""
    path = os.path.join(security_tpm, "binary_bios_measurements")
    if not os.path.isfile(path):
        return {"available": False, "permission_error": False,
                  "size_bytes": 0}
    try:
        size = os.path.getsize(path)
        # Read 0 bytes to test permission ; size_bytes already
        # available from stat which doesn't need read.
        try:
            with open(path, "rb") as f:
                f.read(1)
            return {"available": True, "permission_error": False,
                      "size_bytes": size}
        except PermissionError:
            return {"available": True, "permission_error": True,
                      "size_bytes": size}
    except OSError:
        return {"available": False, "permission_error": False,
                  "size_bytes": 0}


_RECIPE_LEGACY = (
    "# TPM 1.2 detected — many modern features (full-disk\n"
    "# encryption auto-unlock, attestation) require TPM 2.0.\n"
    "# If your motherboard has a TPM 2.0 header, install a\n"
    "# discrete TPM 2.0 module. Otherwise, check BIOS for\n"
    "# 'PTT' (Intel Platform Trust Technology) — firmware TPM\n"
    "# 2.0 is often disabled by default."
)

_RECIPE_NO_MEASURED_BOOT = (
    "# TPM present but no measured-boot log exposed. Likely\n"
    "# kernel built without CONFIG_TCG_TPM_LOG_FILE, or\n"
    "# secfs not mounted. Check :\n"
    "ls /sys/kernel/security/tpm0/\n"
    "# Mount securityfs if missing :\n"
    "sudo mount -t securityfs none /sys/kernel/security"
)


def classify(tpms: list, mb: dict) -> dict:
    if not tpms:
        return {"verdict": "no_tpm",
                "reason": ("/sys/class/tpm empty — no TPM device "
                           "exposed (typical for VMs without "
                           "virtio-tpm)."),
                "recommendation": ""}
    head = tpms[0]
    ver = head.get("tpm_version_major")
    if isinstance(ver, int) and ver < 2:
        return {"verdict": "tpm1_legacy",
                "reason": (f"{head['name']} reports "
                           f"tpm_version_major={ver} — TPM 1.2 "
                           f"legacy. Many modern features (LUKS "
                           f"auto-unlock, attestation) need TPM "
                           f"2.0."),
                "recommendation": _RECIPE_LEGACY}
    if not mb.get("available"):
        return {"verdict": "measured_boot_missing",
                "reason": (f"{head['name']} present but "
                           f"/sys/kernel/security/tpm0/"
                           f"binary_bios_measurements absent. "
                           f"IMA / kernel attestation features "
                           f"won't seal to PCR values."),
                "recommendation": _RECIPE_NO_MEASURED_BOOT}
    return {"verdict": "ok",
            "reason": (f"{head['name']} (TPM v{ver}) + measured-"
                       f"boot log {mb.get('size_bytes', 0)} bytes."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_CLASS_TPM):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/sys/class/tpm unreadable.",
                         "recommendation": ""},
            "tpms": [], "measured_boot": {"available": False},
        }
    tpms = list_tpms(_SYS_CLASS_TPM)
    mb = measured_boot_present(_SECURITY_TPM)
    verdict = classify(tpms, mb)
    return {
        "ok": True,
        "tpm_count": len(tpms),
        "tpms": tpms,
        "measured_boot": mb,
        "verdict": verdict,
    }
