"""R&D #20.2 — VBIOS / ROM drift tracker tests."""
import os
import json
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import vbios_drift as vd


def _with_baseline(td):
    return patch.object(vd, "baseline_path",
                        lambda: os.path.join(td, "vbios_baseline.json"))


# ── load / save baseline ───────────────────────────────────────────────


def test_load_baseline_empty_when_missing(tmp_path):
    with _with_baseline(str(tmp_path)):
        assert vd.load_baseline() == {}


def test_save_and_reload_baseline(tmp_path):
    with _with_baseline(str(tmp_path)):
        vd.save_baseline({"GPU-abc": {"vbios_version": "94.02"}})
        loaded = vd.load_baseline()
    assert "GPU-abc" in loaded


def test_load_baseline_handles_malformed_json(tmp_path):
    with _with_baseline(str(tmp_path)):
        with open(vd.baseline_path(), "w") as f:
            f.write("{not json")
        assert vd.load_baseline() == {}


# ── hash_rom ───────────────────────────────────────────────────────────


def test_hash_rom_missing(tmp_path):
    # No real device path in tmp
    assert vd.hash_rom("0000:99:00.0") is None


# ── detect_drift ───────────────────────────────────────────────────────


def test_detect_drift_first_seen():
    cur = [{"uuid": "GPU-1", "vbios_version": "94.02",
             "name": "RTX 3090", "bdf": "0000:01:00.0"}]
    out = vd.detect_drift(baseline={}, current_gpus=cur)
    assert len(out) == 1
    assert out[0]["drift"] is False
    assert "first_seen" in out[0]["reasons"]


def test_detect_drift_no_change():
    baseline = {"GPU-1": {"vbios_version": "94.02", "rom_sha256": None}}
    cur = [{"uuid": "GPU-1", "vbios_version": "94.02",
             "name": "RTX 3090", "bdf": "0000:01:00.0"}]
    with patch.object(vd, "hash_rom", return_value=None):
        out = vd.detect_drift(baseline, cur)
    assert out[0]["drift"] is False
    assert out[0]["reasons"] == ["ok"]


def test_detect_drift_vbios_changed():
    baseline = {"GPU-1": {"vbios_version": "94.02", "rom_sha256": None}}
    cur = [{"uuid": "GPU-1", "vbios_version": "94.10",
             "name": "RTX 3090", "bdf": "0000:01:00.0"}]
    with patch.object(vd, "hash_rom", return_value=None):
        out = vd.detect_drift(baseline, cur)
    assert out[0]["drift"] is True
    assert any("vbios changed" in r for r in out[0]["reasons"])


def test_detect_drift_rom_changed():
    baseline = {"GPU-1": {"vbios_version": "94.02",
                           "rom_sha256": "aaaa"}}
    cur = [{"uuid": "GPU-1", "vbios_version": "94.02",
             "name": "RTX 3090", "bdf": "0000:01:00.0"}]
    with patch.object(vd, "hash_rom", return_value="bbbb"):
        out = vd.detect_drift(baseline, cur)
    assert out[0]["drift"] is True
    assert any("rom sha256 changed" in r for r in out[0]["reasons"])


def test_detect_drift_multiple_changes():
    baseline = {"GPU-1": {"vbios_version": "94.02",
                           "rom_sha256": "aaaa"}}
    cur = [{"uuid": "GPU-1", "vbios_version": "94.10",
             "name": "RTX 3090", "bdf": "0000:01:00.0"}]
    with patch.object(vd, "hash_rom", return_value="bbbb"):
        out = vd.detect_drift(baseline, cur)
    assert out[0]["drift"] is True
    assert len(out[0]["reasons"]) == 2


def test_detect_drift_handles_unreadable_current_rom():
    """If we can't read the ROM now but had a baseline, skip the
    sha256 comparison."""
    baseline = {"GPU-1": {"vbios_version": "94.02",
                           "rom_sha256": "aaaa"}}
    cur = [{"uuid": "GPU-1", "vbios_version": "94.02",
             "name": "RTX 3090", "bdf": "0000:01:00.0"}]
    with patch.object(vd, "hash_rom", return_value=None):
        out = vd.detect_drift(baseline, cur)
    assert out[0]["drift"] is False


# ── rebaseline ─────────────────────────────────────────────────────────


def test_rebaseline_writes_current(tmp_path):
    with _with_baseline(str(tmp_path)):
        cur = [{"uuid": "GPU-1", "vbios_version": "94.10",
                 "name": "RTX 3090", "bdf": "0000:01:00.0"}]
        with patch.object(vd, "hash_rom", return_value="cccc"):
            out = vd.rebaseline(current_gpus=cur)
        loaded = vd.load_baseline()
    assert "GPU-1" in out
    assert loaded["GPU-1"]["vbios_version"] == "94.10"
    assert loaded["GPU-1"]["rom_sha256"] == "cccc"


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_nvidia_smi(tmp_path):
    with _with_baseline(str(tmp_path)):
        with patch.object(vd, "current_vbios", return_value=[]):
            s = vd.status()
    assert s["ok"] is False
    assert "unreachable" in s["reason"]


def test_status_first_seen_seeds_baseline(tmp_path):
    """A new GPU on first observation should be auto-baselined."""
    cur = [{"uuid": "GPU-1", "vbios_version": "94.02",
             "name": "RTX 3090", "bdf": "0000:01:00.0"}]
    with _with_baseline(str(tmp_path)):
        with patch.object(vd, "current_vbios", return_value=cur):
            with patch.object(vd, "hash_rom", return_value=None):
                s = vd.status()
        loaded = vd.load_baseline()
    assert s["first_seen_count"] == 1
    assert "GPU-1" in loaded


def test_status_drift_detected_on_subsequent_call(tmp_path):
    """First call seeds baseline ; second call with different vbios
    should detect drift."""
    with _with_baseline(str(tmp_path)):
        cur_v1 = [{"uuid": "GPU-1", "vbios_version": "94.02",
                    "name": "RTX 3090", "bdf": "0000:01:00.0"}]
        with patch.object(vd, "current_vbios", return_value=cur_v1):
            with patch.object(vd, "hash_rom", return_value=None):
                vd.status()  # seeds baseline
        cur_v2 = [{"uuid": "GPU-1", "vbios_version": "94.10",
                    "name": "RTX 3090", "bdf": "0000:01:00.0"}]
        with patch.object(vd, "current_vbios", return_value=cur_v2):
            with patch.object(vd, "hash_rom", return_value=None):
                s2 = vd.status()
    assert s2["drift_count"] == 1
