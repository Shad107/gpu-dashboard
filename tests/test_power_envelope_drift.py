"""R&D #27.4 — Power-envelope drift detector tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import power_envelope_drift as pe


def _with_baseline(td):
    return patch.object(pe, "baseline_path",
                        lambda: os.path.join(td, "power_envelope_baseline.json"))


# ── _to_float ──────────────────────────────────────────────────────────


def test_to_float_normal():
    assert pe._to_float("350.0") == 350.0


def test_to_float_na():
    assert pe._to_float("N/A") is None
    assert pe._to_float("[N/A]") is None
    assert pe._to_float("") is None


def test_to_float_garbage():
    assert pe._to_float("abc") is None


# ── classify_drift ─────────────────────────────────────────────────────


def test_classify_first_seen():
    v = pe.classify_drift(None, 350.0, 270.0)
    assert v["verdict"] == "first_seen"


def test_classify_unknown_current():
    v = pe.classify_drift(350.0, None, 270.0)
    assert v["verdict"] == "unknown"


def test_classify_clean():
    v = pe.classify_drift(350.0, 351.0, 270.0)
    assert v["verdict"] == "clean"


def test_classify_reset_to_default():
    """User had 350, driver upgrade dropped to 270 → reset_to_default."""
    v = pe.classify_drift(350.0, 270.0, 270.0)
    assert v["verdict"] == "reset_to_default"
    assert v["severity"] == "warn"
    assert "driver upgrade" in v["reason"]
    assert v["delta_w"] == -80.0


def test_classify_drifted_lowered():
    """Limit lowered but not to factory default → drifted with warn severity."""
    v = pe.classify_drift(350.0, 320.0, 270.0)
    assert v["verdict"] == "drifted"
    assert v["severity"] == "warn"  # downward drift is concerning


def test_classify_drifted_raised():
    """User raised limit → drifted with info severity."""
    v = pe.classify_drift(270.0, 350.0, 270.0)
    assert v["verdict"] == "drifted"
    assert v["severity"] == "info"


def test_classify_threshold_edge():
    """Δ exactly at threshold → still clean (threshold is exclusive)."""
    v = pe.classify_drift(350.0, 355.0, 270.0)
    # delta=5.0, threshold=5.0 → not > threshold → drifted ? Wait:
    # check: |delta|=5, threshold=5, so |delta| > 5 is False → clean
    # But code uses > threshold, so 5 doesn't trigger
    assert v["verdict"] == "clean"


def test_classify_no_default_falls_through():
    """If default_w missing, can't detect reset → falls into drifted/clean."""
    v = pe.classify_drift(350.0, 270.0, None)
    assert v["verdict"] == "drifted"  # not reset since no default to compare


# ── recovery_command ───────────────────────────────────────────────────


def test_recovery_cmd_basic():
    cmd = pe.recovery_command("GPU-abc", 350.0)
    assert "nvidia-smi -i GPU-abc -pl 350" in cmd
    assert cmd.startswith("sudo")


def test_recovery_cmd_no_target():
    assert pe.recovery_command("GPU-abc", None) == ""


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_smi(tmp_path):
    with _with_baseline(str(tmp_path)):
        with patch.object(pe, "query_envelope", return_value=None):
            s = pe.status()
    assert s["ok"] is False


def test_status_seeds_baseline(tmp_path):
    env = [{"uuid": "GPU-x", "name": "RTX 3090",
            "current_w": 350.0, "default_w": 270.0,
            "min_w": 100.0, "max_w": 350.0}]
    with _with_baseline(str(tmp_path)):
        with patch.object(pe, "query_envelope", return_value=env):
            s = pe.status()
            base = pe.load_baseline()
    assert s["gpus"][0]["verdict"]["verdict"] == "first_seen"
    assert "GPU-x" in base


def test_status_detects_reset_after_baseline(tmp_path):
    """Seed at 350 W ; second call shows 270 W (default) → reset."""
    with _with_baseline(str(tmp_path)):
        # First call : seed at 350
        env_v1 = [{"uuid": "GPU-x", "name": "RTX 3090",
                    "current_w": 350.0, "default_w": 270.0,
                    "min_w": 100.0, "max_w": 350.0}]
        with patch.object(pe, "query_envelope", return_value=env_v1):
            pe.status()
        # Second call : driver upgrade reset to default
        env_v2 = [{"uuid": "GPU-x", "name": "RTX 3090",
                    "current_w": 270.0, "default_w": 270.0,
                    "min_w": 100.0, "max_w": 350.0}]
        with patch.object(pe, "query_envelope", return_value=env_v2):
            s2 = pe.status()
    assert s2["gpus"][0]["verdict"]["verdict"] == "reset_to_default"
    assert "nvidia-smi -i GPU-x -pl 350" in s2["gpus"][0]["recovery_cmd"]


def test_status_clean_when_stable(tmp_path):
    with _with_baseline(str(tmp_path)):
        env = [{"uuid": "GPU-x", "name": "RTX 3090",
                "current_w": 350.0, "default_w": 270.0,
                "min_w": 100.0, "max_w": 350.0}]
        with patch.object(pe, "query_envelope", return_value=env):
            pe.status()  # seed
            s2 = pe.status()  # same value
    assert s2["gpus"][0]["verdict"]["verdict"] == "clean"
