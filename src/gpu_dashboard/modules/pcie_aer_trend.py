"""Module pcie_aer_trend — PCIe AER counter trend tracker (R&D #38.1).

Shipped #28.5 pcie_aer reads /sys/bus/pci/devices/<gpu>/aer_dev_*
once and reports the snapshot. But a "TOTAL_ERR_COR=42" reading
without context tells you nothing — 42 over 60 days uptime is
noise, 42 over 60 minutes is a dying cable. This module persists
a baseline and surfaces the delta on subsequent reads.

Reads three AER counter files per NVIDIA VGA device:
  aer_dev_correctable   correctable errors (RxErr, BadTLP, ...)
  aer_dev_fatal         fatal errors (TLP, DLP, MalfTLP, ...)
  aer_dev_nonfatal      non-fatal errors

Verdicts (worst-pick across all GPUs):
  any_fatal          ≥1 fatal counter > 0 — critical, recipe
                     points to reseat/riser/slot swap
  any_nonfatal       ≥1 nonfatal counter > 0 — warn, link
                     degraded
  high_correctable   TOTAL_ERR_COR ≥ 100 — bad cable / connector
  low_correctable    0 < TOTAL_ERR_COR < 100 — informational
  clean              all zero
  no_gpus            no NVIDIA VGA devices
  unknown            files unreadable

Baseline lives at ~/.config/gpu-dashboard/aer_baseline.json,
updated each call. Drift block reports "no_drift" / "drift_detected"
with per-counter deltas.

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional


NAME = "pcie_aer_trend"


_PCI_ROOT = "/sys/bus/pci/devices"
_BASELINE_PATH = "~/.config/gpu-dashboard/aer_baseline.json"


def baseline_path() -> str:
    return os.path.expanduser(_BASELINE_PATH)


_LINE_RE = re.compile(r"^(\w+)\s+(\d+)\s*$")


def parse_aer_file(text: str) -> dict:
    if not text:
        return {}
    out: dict = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            out[m.group(1)] = int(m.group(2))
    return out


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def find_nvidia_bdfs(pci_root: str = _PCI_ROOT) -> list:
    out: list = []
    try:
        for n in sorted(os.listdir(pci_root)):
            vp = os.path.join(pci_root, n, "vendor")
            cp = os.path.join(pci_root, n, "class")
            try:
                with open(vp) as f:
                    if f.read().strip().lower() != "0x10de":
                        continue
                with open(cp) as f:
                    klass = f.read().strip().lower()
                if klass.startswith("0x03"):
                    out.append(n)
            except OSError:
                continue
    except OSError:
        return []
    return out


def read_aer(pci_root: str, bdf: str) -> dict:
    base = os.path.join(pci_root, bdf)
    return {
        "correctable": parse_aer_file(_read(os.path.join(base,
                                                            "aer_dev_correctable")) or ""),
        "fatal": parse_aer_file(_read(os.path.join(base,
                                                      "aer_dev_fatal")) or ""),
        "nonfatal": parse_aer_file(_read(os.path.join(base,
                                                        "aer_dev_nonfatal")) or ""),
    }


_HIGH_CORRECTABLE_THRESHOLD = 100


_RECIPE_FATAL = (
    "# Fatal AER counter is non-zero on the GPU's PCIe link.\n"
    "# Common physical-layer causes (try in order):\n"
    "#  1. Reseat the GPU in its slot (corrosion, vibration).\n"
    "#  2. Replace any PCIe riser / extender cable.\n"
    "#  3. Try a different PCIe slot (different root complex).\n"
    "#  4. Inspect dmesg for AER reports:\n"
    "dmesg | grep -E 'AER|PCIe Bus Error' | tail -30\n"
    "# Companion: #28.5 pcie_aer (snapshot), this card (trend)."
)

_RECIPE_NONFATAL = (
    "# Non-fatal AER counter is set. Link recovered but degraded.\n"
    "# Check link width / speed for downgrades:\n"
    "cat /sys/bus/pci/devices/<bdf>/current_link_speed\n"
    "cat /sys/bus/pci/devices/<bdf>/current_link_width\n"
    "# And the same physical-layer triage as fatal."
)


_RANK = {
    "clean": 0, "no_gpus": 0, "unknown": 1,
    "low_correctable": 2, "high_correctable": 3,
    "any_nonfatal": 4, "any_fatal": 5,
}


def classify(cards: list) -> dict:
    if not cards:
        return {"verdict": "no_gpus",
                "reason": "No NVIDIA VGA devices found.",
                "recommendation": ""}
    worst = "clean"
    worst_card = None
    for c in cards:
        v = _per_card_verdict(c)
        if _RANK.get(v, 0) > _RANK.get(worst, 0):
            worst = v
            worst_card = c
    if worst == "clean":
        return {"verdict": "clean",
                "reason": (f"All {len(cards)} GPU(s) report zero AER "
                           f"counters."),
                "recommendation": ""}
    if worst == "any_fatal":
        non_zero = [k for k, v in worst_card["fatal"].items() if v > 0]
        return {"verdict": "any_fatal",
                "reason": (f"GPU {worst_card['gpu_bdf']} reports fatal "
                           f"AER counter(s) non-zero: "
                           f"{', '.join(non_zero)}."),
                "recommendation": _RECIPE_FATAL}
    if worst == "any_nonfatal":
        non_zero = [k for k, v in worst_card["nonfatal"].items() if v > 0]
        return {"verdict": "any_nonfatal",
                "reason": (f"GPU {worst_card['gpu_bdf']} reports non-"
                           f"fatal AER counter(s) non-zero: "
                           f"{', '.join(non_zero)}."),
                "recommendation": _RECIPE_NONFATAL}
    if worst == "high_correctable":
        tot = worst_card["correctable"].get("TOTAL_ERR_COR", 0)
        return {"verdict": "high_correctable",
                "reason": (f"GPU {worst_card['gpu_bdf']} TOTAL_ERR_COR="
                           f"{tot} — sustained correctable errors "
                           f"suggest a degraded physical link."),
                "recommendation": _RECIPE_NONFATAL}
    tot = worst_card["correctable"].get("TOTAL_ERR_COR", 0) if worst_card else 0
    return {"verdict": "low_correctable",
            "reason": (f"Low-volume correctable AER errors "
                       f"(TOTAL_ERR_COR={tot}). Informational ; watch "
                       f"the trend over time."),
            "recommendation": ""}


def _per_card_verdict(c: dict) -> str:
    fatal_any = any(v > 0 for v in c.get("fatal", {}).values())
    if fatal_any:
        return "any_fatal"
    nonfatal_any = any(v > 0 for v in c.get("nonfatal", {}).values())
    if nonfatal_any:
        return "any_nonfatal"
    tot = c.get("correctable", {}).get("TOTAL_ERR_COR", 0)
    if tot >= _HIGH_CORRECTABLE_THRESHOLD:
        return "high_correctable"
    if tot > 0:
        return "low_correctable"
    return "clean"


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


def _compute_drift(cards: list, baseline: dict) -> dict:
    if not baseline:
        return {"status": "baseline_recorded"}
    deltas: dict = {}
    any_change = False
    for c in cards:
        bdf = c["gpu_bdf"]
        prev = baseline.get(bdf, {})
        per: dict = {}
        for kind in ("correctable", "fatal", "nonfatal"):
            cur_map = c.get(kind, {})
            prev_map = prev.get(kind, {}) if isinstance(prev.get(kind), dict) else {}
            for k, v in cur_map.items():
                d = v - prev_map.get(k, 0)
                if d != 0:
                    per[k] = d
                    any_change = True
        if per:
            deltas[bdf] = per
    if any_change:
        return {"status": "drift_detected", "deltas": deltas}
    return {"status": "no_drift"}


def status(cfg=None) -> dict:
    bdfs = find_nvidia_bdfs(_PCI_ROOT)
    cards: list = []
    for bdf in bdfs:
        c = read_aer(_PCI_ROOT, bdf)
        cards.append({"gpu_bdf": bdf, **c})
    verdict = classify(cards)
    baseline = _load_baseline()
    drift = _compute_drift(cards, baseline)
    # Persist on first record OR when drift is detected (so the
    # next call sees the new state as the baseline)
    if drift["status"] in ("baseline_recorded", "drift_detected"):
        snap = {c["gpu_bdf"]: {"correctable": c["correctable"],
                                "fatal": c["fatal"],
                                "nonfatal": c["nonfatal"]} for c in cards}
        _save_baseline(snap)
    return {
        "ok": True,
        "gpu_count": len(cards),
        "cards": cards,
        "verdict": verdict,
        "drift": drift,
    }
