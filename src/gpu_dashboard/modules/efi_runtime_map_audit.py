"""Module efi_runtime_map_audit — UEFI runtime map (R&D #65.3).

Reads /sys/firmware/efi/runtime-map/<n>/{phys_addr, virt_addr,
num_pages, attribute, type}.

Distinct from R&D #55.4 efi_boot_order_audit (which reads
/sys/firmware/efi/efivars/Boot* + SecureBoot). This is the
*runtime-map* — the kernel-stashed list of UEFI memory regions
kept mapped for SetVirtualAddressMap (firmware runtime services
like efi.set_variable / efi.get_time).

Why this matters :

* `/sys/firmware/efi/runtime-map` empty on an EFI host = kexec'd
  kernel booted without `efi=runtime`. Runtime services are
  inaccessible — efibootmgr, fwupd, secureboot tooling silently
  fail.
* Unusually large EFI_MEMORY_RUNTIME pinned regions (>16 MiB)
  indicate firmware bugs that pin RAM (Lenovo/MSI quirk).

Verdicts (priority-ordered) :
  runtime_map_absent           /sys/firmware/efi/runtime-map
                               doesn't exist on an EFI host.
  kexec_no_efi_rt              /sys/firmware/efi exists but
                               /sys/firmware/efi/runtime-map
                               is empty (kexec without
                               efi=runtime).
  runtime_pinned_large         Sum of pinned EFI-RT pages > 16
                               MiB (root needed to compute ; we
                               degrade to informational if
                               unreadable).
  ok                           Map present and reasonable size.
  requires_root                Entries enumerated but contents
                               unreadable (mode 0400).
  unknown                      /sys/firmware/efi itself absent
                               (legacy BIOS / non-EFI).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "efi_runtime_map_audit"


_SYS_EFI = "/sys/firmware/efi"
_RUNTIME_MAP = "/sys/firmware/efi/runtime-map"


_ENTRY_DIR_RE = re.compile(r"^\d+$")


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
        return int(t, 0)
    except ValueError:
        return None


def list_entries(runtime_map: str = _RUNTIME_MAP) -> List[dict]:
    if not os.path.isdir(runtime_map):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(runtime_map),
                          key=lambda x: int(x) if x.isdigit()
                                            else -1):
        if not _ENTRY_DIR_RE.match(name):
            continue
        d = os.path.join(runtime_map, name)
        out.append({
            "id": name,
            "type": _read_int(os.path.join(d, "type")),
            "num_pages": _read_int(
                os.path.join(d, "num_pages")),
            "attribute": _read_int(
                os.path.join(d, "attribute")),
            "phys_addr": _read(os.path.join(d, "phys_addr")),
            "virt_addr": _read(os.path.join(d, "virt_addr")),
        })
    return out


def classify(entries: List[dict],
              efi_present: bool, runtime_map_present: bool,
              perm_denied: bool) -> dict:
    if not efi_present:
        return {"verdict": "unknown",
                "reason": ("/sys/firmware/efi absent — legacy "
                          "BIOS / non-EFI host."),
                "recommendation": ""}

    if not runtime_map_present:
        return {"verdict": "runtime_map_absent",
                "reason": ("/sys/firmware/efi/runtime-map absent "
                          "on an EFI host. UEFI runtime services "
                          "unavailable — efibootmgr / fwupd will "
                          "fail."),
                "recommendation": _recipe_runtime_map_absent()}

    if not entries:
        return {"verdict": "kexec_no_efi_rt",
                "reason": ("runtime-map directory present but "
                          "empty. Probably kexec'd without "
                          "efi=runtime cmdline."),
                "recommendation": _recipe_kexec_no_efi()}

    # If we have entries but couldn't read any num_pages → root-only
    readable = [e for e in entries
                   if e.get("num_pages") is not None]
    if perm_denied and not readable:
        return {"verdict": "requires_root",
                "reason": (f"{len(entries)} runtime-map entries "
                          f"enumerated but mode 0400 root-only — "
                          f"can't compute pinned-page totals."),
                "recommendation": _recipe_root()}

    if readable:
        total_pages = sum(e.get("num_pages") or 0 for e in readable)
        # 4 KiB per page → > 16 MiB = > 4096 pages
        if total_pages > 4096:
            mib = total_pages * 4 / 1024
            return {"verdict": "runtime_pinned_large",
                    "reason": (f"EFI runtime regions pin "
                              f"{total_pages} pages "
                              f"(~{mib:.1f} MiB). Firmware bug "
                              f"keeps RAM stranded."),
                    "recommendation": _recipe_pinned_large()}

    return {"verdict": "ok",
            "reason": (f"{len(entries)} runtime-map entries "
                      f"present, sizes reasonable."),
            "recommendation": ""}


def status(config=None,
            sys_efi: str = _SYS_EFI,
            runtime_map: str = _RUNTIME_MAP) -> dict:
    efi_present = os.path.isdir(sys_efi)
    runtime_map_present = os.path.isdir(runtime_map)
    entries = list_entries(runtime_map)
    # Detect "all permission denied" : look at any entry's num_pages
    # — if dir exists, entries enumerated, but every num_pages is
    # None → mode 0400 on the regular daemon user.
    perm_denied = bool(entries) and all(
        e.get("num_pages") is None for e in entries)
    ok = efi_present
    verdict = classify(entries, efi_present, runtime_map_present,
                          perm_denied)
    return {"ok": ok,
              "efi_present": efi_present,
              "runtime_map_present": runtime_map_present,
              "entry_count": len(entries),
              "entries_sample": entries[:6],
              "permission_denied": perm_denied,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_runtime_map_absent() -> str:
    return ("# Boot into an EFI-runtime-aware kernel. Verify EFI\n"
            "# boot mode :\n"
            "ls /sys/firmware/efi 2>/dev/null && echo 'EFI booted'\n"
            "# Re-install via efibootmgr if needed.\n")


def _recipe_kexec_no_efi() -> str:
    return ("# A kexec'd kernel needs 'efi=runtime' in its cmdline\n"
            "# to keep UEFI runtime services. Add it next kexec :\n"
            "kexec -l <vmlinuz> --append=\"$(cat /proc/cmdline) efi=runtime\"\n")


def _recipe_pinned_large() -> str:
    return ("# Inspect the pinned regions (root) :\n"
            "sudo grep . /sys/firmware/efi/runtime-map/*/num_pages\n"
            "sudo grep . /sys/firmware/efi/runtime-map/*/type\n"
            "# Vendor BIOS fix is the real solution ; some boards\n"
            "# expose a 'reduce ESP runtime size' BIOS option.\n")


def _recipe_root() -> str:
    return ("# runtime-map entries are mode 0400 root-only. Either\n"
            "# run the dashboard as root for full details, or :\n"
            "sudo cat /sys/firmware/efi/runtime-map/0/num_pages\n"
            "sudo cat /sys/firmware/efi/runtime-map/0/type\n")
