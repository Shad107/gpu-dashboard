"""Module nvlink_health — NVLink CRC / replay error tracker (R&D #28.4).

Dual-3090 / 4090 / A100 homelab LLM rigs rely on NVLink for tensor
parallelism. CRC + replay errors are by design transparent (the
link self-heals) — but a steady drip silently drops effective
bandwidth, knocking 40 % off tensor-parallel inference throughput.
None of the shipped modules track NVLink errors, and no existing
open-source tool surfaces them.

nvidia-smi exposes :
  nvidia-smi nvlink --status                  → link state per link
  nvidia-smi nvlink --errorcounters           → per-link CRC + replay

Output format example (one block per GPU) :
  GPU 0: NVIDIA GeForce RTX 3090 (UUID: GPU-xxx)
           Link 0: Replay Errors: 0
           Link 0: Recovery Errors: 0
           Link 0: CRC Errors: 0
           ...

This module parses that output, baselines counters per (uuid, link)
on first observation, computes deltas. Verdicts :

  - no_nvlink         (single-GPU rig or NVLink not supported)
  - clean             (no error growth)
  - replay_growth     (>10 replay errors since baseline)
  - crc_growth        (>10 CRC errors)
  - link_down         (at least one link state != Up)

stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Optional


NAME = "nvlink_health"


_BASELINE_PATH = "~/.config/gpu-dashboard/nvlink_baseline.json"


def baseline_path() -> str:
    return os.path.expanduser(_BASELINE_PATH)


def load_baseline() -> dict:
    p = baseline_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_baseline(data: dict) -> None:
    p = baseline_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2)


_GPU_HEADER_RE = re.compile(r"^GPU\s+(\d+):.*UUID:\s*(GPU-[^)]+)\)")
_LINK_RE = re.compile(r"^\s*Link\s+(\d+):\s*(.+?):\s*(.+)$")


def parse_error_counters(text: str) -> dict:
    """Parse `nvidia-smi nvlink --errorcounters` into
    {uuid: {link_n: {Replay: N, Recovery: N, CRC: N, ...}}}.
    """
    result: dict = {}
    current_uuid: Optional[str] = None
    for line in text.splitlines():
        m = _GPU_HEADER_RE.match(line)
        if m:
            current_uuid = m.group(2).strip()
            result.setdefault(current_uuid, {})
            continue
        if current_uuid is None:
            continue
        lm = _LINK_RE.match(line)
        if not lm:
            continue
        link_n = int(lm.group(1))
        field = lm.group(2).strip()
        raw_val = lm.group(3).strip()
        try:
            value = int(raw_val)
        except ValueError:
            continue
        per_link = result[current_uuid].setdefault(link_n, {})
        # Normalize field names like 'Replay Errors' -> 'Replay'
        key = (field.replace(" Errors", "")
                     .replace(" Error", "")
                     .strip())
        per_link[key] = value
    return result


def parse_link_status(text: str) -> dict:
    """Parse `nvidia-smi nvlink --status` →
    {uuid: {link_n: 'Up'|'Down'|...}}."""
    result: dict = {}
    current_uuid: Optional[str] = None
    for line in text.splitlines():
        m = _GPU_HEADER_RE.match(line)
        if m:
            current_uuid = m.group(2).strip()
            result.setdefault(current_uuid, {})
            continue
        if current_uuid is None:
            continue
        # 'Link N: <X> GB/s' OR 'Link N: <state>'
        lm = re.match(r"^\s*Link\s+(\d+):\s*(.+)$", line)
        if not lm:
            continue
        link_n = int(lm.group(1))
        # Treat any "GB/s" line as up
        body = lm.group(2).strip()
        state = "up" if "GB/s" in body or body.lower() == "up" \
            else body.lower()
        result[current_uuid][link_n] = state
    return result


def run_nvidia_smi(args: list[str], timeout: float = 3.0) -> Optional[str]:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def query_errors() -> Optional[dict]:
    txt = run_nvidia_smi(["nvlink", "--errorcounters"])
    if txt is None:
        return None
    return parse_error_counters(txt)


def query_status() -> Optional[dict]:
    txt = run_nvidia_smi(["nvlink", "--status"])
    if txt is None:
        return None
    return parse_link_status(txt)


def compute_delta(prev: dict, curr: dict) -> dict:
    """For each (uuid, link, field) compute curr - prev. Skip negative."""
    out: dict = {}
    for uuid, links in curr.items():
        for link_n, fields in links.items():
            for k, v in fields.items():
                p = prev.get(uuid, {}).get(str(link_n), {}).get(k,
                       prev.get(uuid, {}).get(link_n, {}).get(k, 0))
                d = v - p
                if d > 0:
                    out.setdefault(uuid, {}).setdefault(link_n, {})[k] = d
    return out


def classify(deltas: dict, statuses: dict) -> dict:
    """Pick the worst signal across all (uuid, link)."""
    replay_total = 0
    crc_total = 0
    link_down_count = 0
    for uuid, links in deltas.items():
        for link_n, fields in links.items():
            for k, v in fields.items():
                kl = k.lower()
                if "replay" in kl:
                    replay_total += v
                if "crc" in kl:
                    crc_total += v
    for uuid, links in statuses.items():
        for link_n, state in links.items():
            if state != "up":
                link_down_count += 1
    if link_down_count > 0:
        return {"verdict": "link_down",
                "reason": (f"{link_down_count} NVLink(s) are not 'Up'. "
                           "Tensor-parallel throughput collapses to PCIe."),
                "replay_delta": replay_total,
                "crc_delta": crc_total,
                "link_down_count": link_down_count,
                "recommendation": "Reseat the NVLink bridge. Verify it's the right SKU."}
    if crc_total > 10:
        return {"verdict": "crc_growth",
                "reason": (f"+{crc_total} CRC errors since baseline. "
                           "Bandwidth is silently dropping."),
                "replay_delta": replay_total,
                "crc_delta": crc_total,
                "link_down_count": 0,
                "recommendation": "Reseat NVLink bridge ; check seating + cable."}
    if replay_total > 10:
        return {"verdict": "replay_growth",
                "reason": (f"+{replay_total} replay errors since baseline. "
                           "Marginal link integrity."),
                "replay_delta": replay_total,
                "crc_delta": crc_total,
                "link_down_count": 0,
                "recommendation": "Watch for further growth. Consider reseating."}
    return {"verdict": "clean",
            "reason": "No NVLink error growth since baseline.",
            "replay_delta": replay_total,
            "crc_delta": crc_total,
            "link_down_count": 0,
            "recommendation": ""}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    errors = query_errors()
    statuses = query_status()
    if errors is None and statuses is None:
        return {"ok": False,
                "reason": "nvidia-smi unreachable.",
                "supported": False,
                "verdict": {"verdict": "no_nvlink",
                             "reason": "NVLink probe failed."}}
    if not errors:
        return {"ok": True,
                "supported": False,
                "verdict": {"verdict": "no_nvlink",
                             "reason": ("No NVLink detected (single-GPU rig "
                                         "or NVLink unsupported on this "
                                         "model).")},
                "per_link": {},
                "statuses": statuses or {}}
    baseline = load_baseline()
    deltas = compute_delta(baseline, errors)
    verdict = classify(deltas, statuses or {})
    # Seed any missing entries
    new_baseline = dict(baseline)
    for uuid, links in errors.items():
        for link_n, fields in links.items():
            slot = new_baseline.setdefault(uuid, {}).setdefault(
                str(link_n), {})
            for k, v in fields.items():
                if k not in slot:
                    slot[k] = v
    if new_baseline != baseline:
        save_baseline(new_baseline)
    return {
        "ok": True,
        "supported": True,
        "per_link": errors,
        "statuses": statuses or {},
        "deltas": deltas,
        "verdict": verdict,
    }
