"""Module efi_boot_order_audit — EFI boot vars + varstore (R&D #55.4).

Distinct from existing IMA / SecureBoot check (#53.3) which only
reads `SecureBoot-*`. This module covers the rest of the EFI runtime
view that's relevant to homelab LLM hosts :

* BootCurrent / BootOrder drift after a kernel install — firmware
  sometimes pins BootNext to a fallback entry, you reboot into
  a 6-month-old kernel without noticing.
* Varstore filling up — Lenovo / MSI firmwares brick when the
  efivarfs blob exceeds ~50 % of NVRAM (the kernel logs
  EFI_NVRAM_FULL but a lot of users miss it).
* dbx (SecureBoot revocation list) absent on a SecureBoot-on host
  → vulnerable signed shim / grub can still boot.

Reads :
  /sys/firmware/efi/efivars/BootCurrent-*
  /sys/firmware/efi/efivars/BootOrder-*
  /sys/firmware/efi/efivars/BootNext-*                 (when set)
  /sys/firmware/efi/efivars/Boot0*-*                   (entries)
  /sys/firmware/efi/efivars/SecureBoot-*               (re-read,
                                                          shape only)
  /sys/firmware/efi/efivars/dbx*                       (presence)
  /sys/firmware/efi/efivars/                           total size
  /sys/firmware/efi/fw_platform_size                   (32/64 bit)

Verdicts (priority-ordered) :
  bootnext_pinned                 BootNext-* exists — the next
                                  boot will skip BootOrder.
  varstore_near_full              total efivars size > 32 KiB
                                  (50 % of typical 64 KiB NVRAM).
  dbx_absent_with_secureboot      SecureBoot = 1 AND no dbx*
                                  variable — revocation list
                                  missing.
  bootorder_drift                 BootCurrent != BootOrder[0].
  ok                              BootOrder and varstore healthy.
  unknown                         /sys/firmware/efi not present
                                  (legacy BIOS / non-EFI host).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "efi_boot_order_audit"


_EFI_DIR = "/sys/firmware/efi"
_EFI_EFIVARS = "/sys/firmware/efi/efivars"

_GLOBAL_GUID = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
_BOOT_ENTRY_RE = re.compile(r"^Boot([0-9A-Fa-f]{4})-")

# Heuristic NVRAM threshold (most firmwares ship with 64 KiB or
# 128 KiB total ; 32 KiB used is half of 64 KiB).
_VARSTORE_WARN_BYTES = 32 * 1024


def _read_bytes(p: str) -> Optional[bytes]:
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


def _strip_attr_prefix(data: Optional[bytes]) -> Optional[bytes]:
    """EFI variable layout : 4-byte attribute prefix + payload."""
    if data is None or len(data) < 4:
        return None
    return data[4:]


def _read_uint16(p: str) -> Optional[int]:
    payload = _strip_attr_prefix(_read_bytes(p))
    if payload is None or len(payload) < 2:
        return None
    return payload[0] | (payload[1] << 8)


def _read_uint16_array(p: str) -> List[int]:
    payload = _strip_attr_prefix(_read_bytes(p))
    if payload is None:
        return []
    out: List[int] = []
    for i in range(0, len(payload) - 1, 2):
        out.append(payload[i] | (payload[i + 1] << 8))
    return out


def _find_var(efivars: str, prefix: str) -> Optional[str]:
    if not os.path.isdir(efivars):
        return None
    for name in os.listdir(efivars):
        if name.startswith(prefix):
            return os.path.join(efivars, name)
    return None


def list_boot_entries(efivars: str = _EFI_EFIVARS) -> List[int]:
    """Returns the hex IDs of all Boot#### entries."""
    if not os.path.isdir(efivars):
        return []
    out: List[int] = []
    for name in os.listdir(efivars):
        m = _BOOT_ENTRY_RE.match(name)
        if not m:
            continue
        try:
            out.append(int(m.group(1), 16))
        except ValueError:
            continue
    return sorted(out)


def read_state(efivars: str = _EFI_EFIVARS) -> dict:
    out: dict = {"present": os.path.isdir(efivars)}
    if not out["present"]:
        return out
    bc = _find_var(efivars, "BootCurrent-")
    bo = _find_var(efivars, "BootOrder-")
    bn = _find_var(efivars, "BootNext-")
    sb = _find_var(efivars, "SecureBoot-")
    out["BootCurrent"] = _read_uint16(bc) if bc else None
    out["BootOrder"] = _read_uint16_array(bo) if bo else []
    out["BootNext"] = _read_uint16(bn) if bn else None
    out["BootEntries"] = list_boot_entries(efivars)
    # SecureBoot is 1 byte
    sb_payload = _strip_attr_prefix(_read_bytes(sb)) if sb else None
    out["SecureBoot"] = (sb_payload[0] == 1
                            if sb_payload and len(sb_payload) >= 1
                            else None)
    # dbx presence (any variable name starting with dbx)
    out["dbx_present"] = any(
        name.startswith("dbx") for name in os.listdir(efivars))
    # Total varstore size (best-effort sum of file sizes)
    total = 0
    for name in os.listdir(efivars):
        p = os.path.join(efivars, name)
        try:
            total += os.path.getsize(p)
        except OSError:
            continue
    out["varstore_total_bytes"] = total
    return out


def classify(state: dict) -> dict:
    if not state.get("present"):
        return {"verdict": "unknown",
                "reason": ("/sys/firmware/efi/efivars not present "
                          "— host booted in legacy BIOS mode."),
                "recommendation": ""}

    # 1) bootnext_pinned
    if state.get("BootNext") is not None:
        return {"verdict": "bootnext_pinned",
                "reason": (f"BootNext = 0x{state['BootNext']:04X} "
                          f"is set — next reboot will skip "
                          f"BootOrder. Often a leftover from a "
                          f"failed kernel install."),
                "recommendation": _recipe_clear_bootnext()}

    # 2) varstore_near_full
    total = state.get("varstore_total_bytes") or 0
    if total > _VARSTORE_WARN_BYTES:
        kib = total / 1024
        return {"verdict": "varstore_near_full",
                "reason": (f"efivarfs uses {kib:.1f} KiB across "
                          f"variables — over the 32 KiB heuristic. "
                          f"Some firmwares brick at 50 % NVRAM full."),
                "recommendation": _recipe_varstore_full()}

    # 3) dbx_absent_with_secureboot
    if state.get("SecureBoot") is True and \
            not state.get("dbx_present"):
        return {"verdict": "dbx_absent_with_secureboot",
                "reason": ("Secure Boot is enabled but no dbx "
                          "(revocation list) variable is present "
                          "— vulnerable signed shims/grubs can "
                          "still boot."),
                "recommendation": _recipe_install_dbx()}

    # 4) bootorder_drift
    bc = state.get("BootCurrent")
    bo = state.get("BootOrder") or []
    if bo and bc is not None and bo[0] != bc:
        return {"verdict": "bootorder_drift",
                "reason": (f"BootCurrent = 0x{bc:04X} but "
                          f"BootOrder[0] = 0x{bo[0]:04X}. Booted "
                          f"a fallback entry rather than the "
                          f"preferred one."),
                "recommendation": _recipe_bootorder_drift()}

    return {"verdict": "ok",
            "reason": (f"BootOrder healthy, varstore "
                      f"{(total/1024):.1f} KiB used."),
            "recommendation": ""}


def status(config=None, efivars: str = _EFI_EFIVARS) -> dict:
    state = read_state(efivars)
    ok = state.get("present", False)
    verdict = classify(state)
    return {"ok": ok, **state, "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_clear_bootnext() -> str:
    return ("# Clear BootNext so the next boot follows BootOrder :\n"
            "sudo efibootmgr --quiet --delete-bootnext\n"
            "# Or, if efibootmgr isn't installed :\n"
            "sudo chattr -i /sys/firmware/efi/efivars/BootNext-*\n"
            "sudo rm /sys/firmware/efi/efivars/BootNext-*\n")


def _recipe_varstore_full() -> str:
    return ("# Prune stale EFI variables. First inventory :\n"
            "ls -laSr /sys/firmware/efi/efivars/ | head -20\n"
            "# Common culprits : MokListXRT / MokListTrustedRT /\n"
            "# expired Boot#### entries / vendor crash dumps.\n"
            "# Backup *before* removing anything :\n"
            "sudo tar -czf /root/efivars-backup.tar.gz /sys/firmware/efi/efivars/\n")


def _recipe_install_dbx() -> str:
    return ("# Install / refresh the dbx (revocation list) :\n"
            "sudo apt install fwupd  # Debian / Ubuntu\n"
            "sudo fwupdmgr refresh\n"
            "sudo fwupdmgr update\n"
            "# fwupd ships the LVFS-signed dbx update automatically.\n")


def _recipe_bootorder_drift() -> str:
    return ("# Inspect current boot order :\n"
            "sudo efibootmgr -v\n"
            "# Re-prioritize so BootCurrent comes first :\n"
            "sudo efibootmgr --bootorder <hex,hex,…>\n")
