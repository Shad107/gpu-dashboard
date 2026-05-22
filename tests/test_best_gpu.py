"""R&D #13.7 — workload power-balancer tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.api import best_gpu


def _snap(idx=0, temp=50, util=20, mem_used=8192, mem_total=24576, alive=True,
          name="NVIDIA GeForce RTX 3090"):
    return {
        "alive": alive, "index": idx, "name": name,
        "temp": temp, "util_gpu": util,
        "mem_used_mib": mem_used, "mem_total_mib": mem_total,
        "power": 80, "power_limit": 350,
    }


# ── _score ────────────────────────────────────────────────────────────────


def test_score_cooler_gpu_lower():
    s_hot = _snap(temp=80, util=50)
    s_cool = _snap(temp=40, util=10)
    assert best_gpu._score(s_cool, 1.0, 0.5, 0.3) < best_gpu._score(s_hot, 1.0, 0.5, 0.3)


def test_score_offline_gpu_infinity():
    s = _snap(alive=False)
    assert best_gpu._score(s, 1.0, 0.5, 0.3) == float("inf")


def test_score_vram_pressure_increases_score():
    s_empty = _snap(mem_used=1024)
    s_full = _snap(mem_used=23000)
    assert best_gpu._score(s_full, 1.0, 0.5, 0.3) > best_gpu._score(s_empty, 1.0, 0.5, 0.3)


# ── handle_best_gpu ───────────────────────────────────────────────────────


def test_best_gpu_no_devices_returns_503():
    with patch.object(best_gpu, "_gpus_available", return_value=[]):
        code, body = api.handle_best_gpu({})
    assert code == 503
    assert body["available"] is False


def test_best_gpu_single_device():
    with patch.object(best_gpu, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(best_gpu, "_gpu_card_snapshot", return_value=_snap(idx=0, temp=50)):
        code, body = api.handle_best_gpu({})
    assert code == 200
    assert body["best_index"] == 0
    assert "CUDA_VISIBLE_DEVICES=0" in body["shell_export"]


def test_best_gpu_picks_coolest_among_two():
    def fake_snap(gpu_index=0, **kw):
        return _snap(idx=gpu_index, temp=70 if gpu_index == 0 else 45)
    with patch.object(best_gpu, "_gpus_available", return_value=[{"index": 0}, {"index": 1}]), \
         patch.object(best_gpu, "_gpu_card_snapshot", side_effect=fake_snap):
        code, body = api.handle_best_gpu({})
    assert body["best_index"] == 1
    assert body["shell_export"] == "CUDA_VISIBLE_DEVICES=1"


def test_best_gpu_offline_card_excluded():
    """An offline GPU has inf score → never chosen."""
    def fake_snap(gpu_index=0, **kw):
        if gpu_index == 0:
            return _snap(idx=0, alive=False)
        return _snap(idx=1, temp=80)
    with patch.object(best_gpu, "_gpus_available", return_value=[{"index": 0}, {"index": 1}]), \
         patch.object(best_gpu, "_gpu_card_snapshot", side_effect=fake_snap):
        code, body = api.handle_best_gpu({})
    assert body["best_index"] == 1  # online card wins despite higher temp


def test_best_gpu_weights_applied_correctly():
    """w_util=0 → util doesn't matter ; the busy-but-cool card wins."""
    def fake_snap(gpu_index=0, **kw):
        return _snap(idx=gpu_index, temp=80 if gpu_index == 0 else 40, util=10 if gpu_index == 0 else 90)
    with patch.object(best_gpu, "_gpus_available", return_value=[{"index": 0}, {"index": 1}]), \
         patch.object(best_gpu, "_gpu_card_snapshot", side_effect=fake_snap):
        code, body = api.handle_best_gpu({}, {"w_util": "0"})
    # With w_util=0, only temp matters → GPU 1 (40°C) wins regardless of 90% util
    assert body["best_index"] == 1


def test_best_gpu_invalid_weights_falls_back_to_defaults():
    with patch.object(best_gpu, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(best_gpu, "_gpu_card_snapshot", return_value=_snap()):
        code, body = api.handle_best_gpu({}, {"w_temp": "not-a-number"})
    assert code == 200
    assert body["weights"]["temp"] == 1.0  # default


def test_best_gpu_ranked_list_sorted_by_score():
    def fake_snap(gpu_index=0, **kw):
        return _snap(idx=gpu_index, temp={0: 70, 1: 50, 2: 60}.get(gpu_index, 80))
    with patch.object(best_gpu, "_gpus_available",
                      return_value=[{"index": 0}, {"index": 1}, {"index": 2}]), \
         patch.object(best_gpu, "_gpu_card_snapshot", side_effect=fake_snap):
        code, body = api.handle_best_gpu({})
    # Ranked order : 1 (50°C) → 2 (60°C) → 0 (70°C)
    assert [r["index"] for r in body["ranked"]] == [1, 2, 0]


# ── handle_best_gpu_env ───────────────────────────────────────────────────


def test_best_gpu_env_returns_plain_text():
    with patch.object(best_gpu, "_gpus_available", return_value=[{"index": 2}]), \
         patch.object(best_gpu, "_gpu_card_snapshot", return_value=_snap(idx=2)):
        code, text = api.handle_best_gpu_env({})
    assert code == 200
    assert text.strip() == "CUDA_VISIBLE_DEVICES=2"


def test_best_gpu_env_no_devices_emits_comment():
    with patch.object(best_gpu, "_gpus_available", return_value=[]):
        code, text = api.handle_best_gpu_env({})
    assert code == 503
    assert text.startswith("#")
