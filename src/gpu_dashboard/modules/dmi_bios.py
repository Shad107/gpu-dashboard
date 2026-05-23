"""Module dmi_bios — DMI/BIOS revision tracker (R&D #30.5).

Shipped #27.1 ReBAR auditor and #18.x AER counter both flag bad configs,
but their *fix* is always "update your BIOS". Without surfacing the
current BIOS revision and date the user can't tell if they're already
on the fix or how far behind.

This module reads `/sys/devices/virtual/dmi/id/{bios_version,bios_date,
board_name,sys_vendor,...}` (no sudo, no extra deps), cross-references
the board against a small catalog of "known-good BIOS for ReBAR/AER",
and emits one of:

  - up_to_date     current BIOS >= min_rebar and min_aer
  - outdated       current BIOS < min_rebar or min_aer (specific advice)
  - unknown_board  board not in catalog (most boards — bench until we add)
  - unknown        DMI missing board_name (VMs, exotic firmwares)

Drift detection: persist the (bios_version, bios_date) tuple in
~/.config/gpu-dashboard/dmi_baseline.json — if it changes between
calls, surface "your BIOS was just flashed".

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional


NAME = "dmi_bios"


_DMI_ROOT = "/sys/devices/virtual/dmi/id"

_BASELINE_PATH = "~/.config/gpu-dashboard/dmi_baseline.json"

_DMI_FIELDS = (
    "bios_version",
    "bios_date",
    "bios_release",
    "bios_vendor",
    "board_name",
    "board_vendor",
    "sys_vendor",
    "product_name",
    "product_family",
)


# Tiny seed catalog. Real-world fixes for ReBAR/AER unlock per board.
# Expandable as users open issues. Keys are exact board_name strings as
# they appear in /sys/devices/virtual/dmi/id/board_name.
_CATALOG: dict = {
    "X570 AORUS ELITE": {
        "min_rebar": "F33",
        "min_aer": "F31",
        "vendor_url": "https://www.gigabyte.com/Motherboard/X570-AORUS-ELITE-rev-10/support#support-dl-bios",
    },
    "X570 AORUS MASTER": {
        "min_rebar": "F33",
        "min_aer": "F31",
        "vendor_url": "https://www.gigabyte.com/Motherboard/X570-AORUS-MASTER-rev-10/support#support-dl-bios",
    },
    "B550 AORUS ELITE": {
        "min_rebar": "F15",
        "min_aer": "F14",
        "vendor_url": "https://www.gigabyte.com/Motherboard/B550-AORUS-ELITE-rev-10/support#support-dl-bios",
    },
    "ROG STRIX X570-E GAMING": {
        "min_rebar": "4408",
        "min_aer": "4408",
        "vendor_url": "https://rog.asus.com/motherboards/rog-strix/rog-strix-x570-e-gaming-model/helpdesk_bios/",
    },
    "ROG STRIX B550-F GAMING": {
        "min_rebar": "2604",
        "min_aer": "2604",
        "vendor_url": "https://rog.asus.com/motherboards/rog-strix/rog-strix-b550-f-gaming-model/helpdesk_bios/",
    },
    "PRIME X570-PRO": {
        "min_rebar": "4408",
        "min_aer": "4408",
        "vendor_url": "https://www.asus.com/motherboards-components/motherboards/prime/prime-x570-pro/helpdesk_bios/",
    },
    "MAG X570 TOMAHAWK WIFI": {
        "min_rebar": "7C84vAE",
        "min_aer": "7C84vAE",
        "vendor_url": "https://www.msi.com/Motherboard/MAG-X570-TOMAHAWK-WIFI/support",
    },
    "Z690 AORUS ELITE": {
        "min_rebar": "F22",
        "min_aer": "F20",
        "vendor_url": "https://www.gigabyte.com/Motherboard/Z690-AORUS-ELITE-rev-10/support#support-dl-bios",
    },
    "ROG STRIX Z690-A GAMING WIFI": {
        "min_rebar": "2103",
        "min_aer": "2103",
        "vendor_url": "https://rog.asus.com/motherboards/rog-strix/rog-strix-z690-a-gaming-wifi-model/helpdesk_bios/",
    },
}


def baseline_path() -> str:
    return os.path.expanduser(_BASELINE_PATH)


def read_field(root: str, name: str) -> Optional[str]:
    p = os.path.join(root, name)
    try:
        with open(p) as f:
            return f.read().strip()
    except (OSError, IOError):
        return None


def read_dmi(root: str = _DMI_ROOT) -> dict:
    return {f: read_field(root, f) for f in _DMI_FIELDS}


def parse_bios_date(s: Optional[str]) -> Optional[str]:
    """DMI typically stores BIOS dates as MM/DD/YYYY. Return ISO."""
    if not s:
        return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s.strip())
    if not m:
        return None
    mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
    return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"


_TOKEN_RE = re.compile(r"(\d+|[A-Za-z]+)")


def _tokenize(v: str) -> list:
    """Split version into mixed alpha/numeric tokens for ordering."""
    out: list = []
    for tok in _TOKEN_RE.findall(v):
        if tok.isdigit():
            out.append((0, int(tok)))
        else:
            out.append((1, tok.lower()))
    return out


def version_ge(a: Optional[str], b: Optional[str]) -> bool:
    """Compare two version strings token-wise. Returns True iff a >= b.
    Designed for BIOS vendor schemes (F11, F16, 1.2, 4408, 7C84vAE)."""
    if a is None or b is None:
        return False
    ta, tb = _tokenize(a), _tokenize(b)
    for x, y in zip(ta, tb):
        # Numeric tokens beat alpha for ordering by kind=0 first
        if x[0] != y[0]:
            return x[0] < y[0]  # 0 (digit) sorts before 1 (alpha)
        if x[1] != y[1]:
            return x[1] > y[1]
    return len(ta) >= len(tb)


def classify(dmi: dict, catalog: dict) -> dict:
    board = dmi.get("board_name")
    bios = dmi.get("bios_version")
    if not board:
        return {
            "verdict": "unknown",
            "reason": ("DMI board_name unreadable — likely a VM or exotic "
                       "firmware. BIOS-revision advice unavailable."),
            "recommendation": "",
        }
    entry = catalog.get(board)
    if not entry:
        return {
            "verdict": "unknown_board",
            "reason": (f"Board '{board}' not yet in our catalog of ReBAR/AER "
                       f"BIOS minimums. Open an issue with this board name "
                       f"and your current BIOS (`{bios}`) to add it."),
            "recommendation": "",
        }
    min_rebar = entry.get("min_rebar")
    min_aer = entry.get("min_aer")
    url = entry.get("vendor_url", "")
    if min_rebar and not version_ge(bios, min_rebar):
        return {
            "verdict": "outdated",
            "reason": (f"You're on BIOS {bios}; {min_rebar} is the first "
                       f"revision that enables ReBAR on your {board}."),
            "recommendation": (f"Flash BIOS >= {min_rebar} from the vendor "
                                f"page: {url}"),
        }
    if min_aer and not version_ge(bios, min_aer):
        return {
            "verdict": "outdated",
            "reason": (f"You're on BIOS {bios}; {min_aer} is the first "
                       f"revision that fixes AER-counter spam on your "
                       f"{board}."),
            "recommendation": (f"Flash BIOS >= {min_aer} from the vendor "
                                f"page: {url}"),
        }
    return {
        "verdict": "up_to_date",
        "reason": (f"BIOS {bios} on {board} meets known minimums for "
                   f"ReBAR ({min_rebar}) and AER ({min_aer})."),
        "recommendation": "",
    }


def _load_baseline() -> dict:
    p = baseline_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_baseline(data: dict) -> None:
    p = baseline_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def _drift(dmi: dict, baseline: dict) -> dict:
    if not baseline:
        return {"status": "baseline_recorded"}
    keys = ("bios_version", "bios_date")
    changed = {k: baseline.get(k) for k in keys if baseline.get(k) != dmi.get(k)}
    if not changed:
        return {"status": "no_drift"}
    return {
        "status": "drift_detected",
        "from": {k: baseline.get(k) for k in keys},
        "to": {k: dmi.get(k) for k in keys},
        "reason": ("Your BIOS revision or date changed since last seen — "
                   "it was flashed or DMI strings were modified."),
    }


def status(cfg=None) -> dict:
    if not os.path.isdir(_DMI_ROOT):
        return {"ok": False, "error": "dmi_unavailable",
                "reason": f"{_DMI_ROOT} not present on this system."}
    dmi = read_dmi(_DMI_ROOT)
    baseline = _load_baseline()
    drift = _drift(dmi, baseline)
    verdict = classify(dmi, _CATALOG)
    # Persist current as new baseline only the first time or on drift
    if drift["status"] in ("baseline_recorded", "drift_detected"):
        _save_baseline({k: dmi.get(k) for k in ("bios_version", "bios_date")})
    return {
        "ok": True,
        "dmi": dmi,
        "bios_date_iso": parse_bios_date(dmi.get("bios_date")),
        "verdict": verdict,
        "drift": drift,
        "catalog_size": len(_CATALOG),
    }
