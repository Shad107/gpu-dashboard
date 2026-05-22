"""HTTP handler for /api/best-gpu — recommend the least-loaded GPU (R&D #13.7).

Multi-GPU rigs benefit from steering a new CUDA process to the coolest /
least-loaded card. This endpoint computes a score per GPU and returns
the index + a ready-to-use CUDA_VISIBLE_DEVICES suggestion :

  curl -s http://localhost:9999/api/best-gpu/env
  → CUDA_VISIBLE_DEVICES=2

  $(curl -s .../api/best-gpu/env) python my_training.py

Score (lower = better) :
  score = w_temp * temp_c + w_util * util_pct + w_vram * vram_used_pct

Defaults : w_temp=1.0, w_util=0.5, w_vram=0.3. Tunable via query params.

Returns the per-GPU snapshot for transparency.
"""
from __future__ import annotations

from typing import Optional, Tuple

from . import _core as _m


Response = Tuple[int, dict]


def _gpu_card_snapshot(*args, **kw):
    return _m._gpu_card_snapshot(*args, **kw)


def _gpus_available(*args, **kw):
    return _m._gpus_available(*args, **kw)


def _score(snap: dict, w_temp: float, w_util: float, w_vram: float) -> float:
    """Lower score = better candidate (cooler, less loaded)."""
    if not snap or not snap.get("alive"):
        return float("inf")  # offline GPUs cannot be best
    t = float(snap.get("temp") or 0)
    u = float(snap.get("util_gpu") or 0)
    used = float(snap.get("mem_used_mib") or 0)
    total = float(snap.get("mem_total_mib") or 1)
    vram_used_pct = (used / total) * 100 if total > 0 else 0
    return w_temp * t + w_util * u + w_vram * vram_used_pct


def _gather_snapshots() -> list:
    """Walk available GPUs + collect their live snapshot."""
    gpus = []
    try:
        gpus = _gpus_available() or []
    except Exception:
        gpus = []
    out: list = []
    for g in gpus:
        try:
            idx = int(g.get("index", g.get("idx", 0)))
        except (ValueError, TypeError):
            continue
        snap = _gpu_card_snapshot(gpu_index=idx)
        if snap:
            snap.setdefault("index", idx)
            out.append(snap)
    return out


def handle_best_gpu(ctx: dict, params: Optional[dict] = None) -> Tuple[int, dict]:
    """JSON response : {best_index, score, reasoning, ranked: [...]}.

    Query params :
      w_temp = weight for temperature (default 1.0)
      w_util = weight for util % (default 0.5)
      w_vram = weight for vram used % (default 0.3)
    """
    params = params or {}
    try:
        w_temp = float(params.get("w_temp", "1.0"))
        w_util = float(params.get("w_util", "0.5"))
        w_vram = float(params.get("w_vram", "0.3"))
    except (ValueError, TypeError):
        w_temp, w_util, w_vram = 1.0, 0.5, 0.3

    snaps = _gather_snapshots()
    if not snaps:
        return 503, {
            "ok": False,
            "available": False,
            "reason": "no GPUs detected",
        }

    scored = []
    for s in snaps:
        sc = _score(s, w_temp, w_util, w_vram)
        scored.append({
            "index": s.get("index"),
            "name": s.get("name", "?"),
            "temp_c": s.get("temp"),
            "util_pct": s.get("util_gpu"),
            "vram_used_mib": s.get("mem_used_mib"),
            "vram_total_mib": s.get("mem_total_mib"),
            "score": round(sc, 2),
        })
    scored.sort(key=lambda r: r["score"])
    best = scored[0]
    return 200, {
        "ok": True,
        "available": True,
        "best_index": best["index"],
        "best_score": best["score"],
        "weights": {"temp": w_temp, "util": w_util, "vram": w_vram},
        "ranked": scored,
        "shell_export": f"CUDA_VISIBLE_DEVICES={best['index']}",
        "reasoning": (
            f"GPU {best['index']} ({best.get('name', '?')}) — "
            f"score {best['score']} (temp={best.get('temp_c')}°C, "
            f"util={best.get('util_pct')}%, "
            f"vram={best.get('vram_used_mib')}/{best.get('vram_total_mib')} MiB)"
        ),
    }


def handle_best_gpu_env(ctx: dict, params: Optional[dict] = None) -> Tuple[int, str]:
    """Plain-text variant : just the CUDA_VISIBLE_DEVICES=N line, ready
    to drop into a shell command."""
    code, body = handle_best_gpu(ctx, params)
    if code != 200 or not body.get("available"):
        return code, "# no GPU available\n"
    return 200, body["shell_export"] + "\n"
