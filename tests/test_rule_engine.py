"""R&D #12.4 — declarative rule engine tests."""
import json
import os
import tempfile
import time
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import rule_engine as re


def _with_tmp_paths(td):
    return patch.multiple(
        re,
        rules_path=lambda: os.path.join(td, "rules.json"),
        lastfire_path=lambda: os.path.join(td, "lastfire.json"),
    )


def _hot_rule(rid="hot-gpu", window_s=0, cooldown_s=0):
    return {
        "id": rid,
        "name": "Hot GPU",
        "enabled": True,
        "when": [{"metric": "temp", "op": ">=", "value": 80, "window_s": window_s}],
        "then": [{"kind": "log", "message": f"fired-{rid}"}],
        "cooldown_s": cooldown_s,
    }


def _sample(temp=50, util=20, power=80, ts=None):
    return {
        "ts": ts if ts is not None else int(time.time()),
        "temp": temp, "util_gpu": util, "power": power,
        "mem_used_mib": 8192, "mem_total_mib": 24576, "fan0_rpm": 800,
    }


# ── validate_rule ─────────────────────────────────────────────────────────


def test_valid_rule_passes():
    assert re.validate_rule(_hot_rule()) is None


def test_rule_without_id_fails():
    r = _hot_rule()
    del r["id"]
    assert "id" in re.validate_rule(r)


def test_rule_with_unknown_metric_fails():
    r = _hot_rule()
    r["when"][0]["metric"] = "lol_no"
    assert "lol_no" in re.validate_rule(r)


def test_rule_with_unknown_op_fails():
    r = _hot_rule()
    r["when"][0]["op"] = "~="
    assert "~=" in re.validate_rule(r)


def test_rule_with_non_numeric_value_fails():
    r = _hot_rule()
    r["when"][0]["value"] = "eighty"
    assert "numeric" in re.validate_rule(r)


def test_notif_action_without_channel_fails():
    r = _hot_rule()
    r["then"] = [{"kind": "notif", "level": "warn"}]
    assert "channel" in re.validate_rule(r)


# ── _cmp / condition_holds ────────────────────────────────────────────────


def test_cmp_ops():
    assert re._cmp(80, ">=", 80) is True
    assert re._cmp(79.9, ">=", 80) is False
    assert re._cmp(50, "<", 80) is True
    assert re._cmp(80, "==", 80) is True
    assert re._cmp(81, "!=", 80) is True


def test_sample_value_alias_util():
    """metric=util maps to util_gpu field."""
    s = _sample(util=42)
    assert re._sample_value(s, "util") == 42


def test_sample_value_mem_free_gb():
    """mem_free_gb computed from total - used."""
    s = _sample()
    free = re._sample_value(s, "mem_free_gb")
    # 24576 - 8192 = 16384 MiB → 16.0 GiB
    assert free == 16.0


def test_condition_holds_single_sample_match():
    w = {"metric": "temp", "op": ">=", "value": 80, "window_s": 0}
    assert re.condition_holds(w, [_sample(temp=85)]) is True


def test_condition_holds_single_sample_no_match():
    w = {"metric": "temp", "op": ">=", "value": 80, "window_s": 0}
    assert re.condition_holds(w, [_sample(temp=70)]) is False


def test_condition_holds_window_all_above():
    """Window 60s with 3 samples spaced 20s all above 80 → fire."""
    now = time.time()
    samples = [_sample(temp=82, ts=now - 40), _sample(temp=85, ts=now - 20), _sample(temp=88, ts=now)]
    w = {"metric": "temp", "op": ">=", "value": 80, "window_s": 60}
    assert re.condition_holds(w, samples) is True


def test_condition_holds_window_one_dip_no_fire():
    """Same window but one sample drops below → don't fire."""
    now = time.time()
    samples = [_sample(temp=82, ts=now - 40), _sample(temp=79, ts=now - 20), _sample(temp=88, ts=now)]
    w = {"metric": "temp", "op": ">=", "value": 80, "window_s": 60}
    assert re.condition_holds(w, samples) is False


# ── cooldown ──────────────────────────────────────────────────────────────


def test_in_cooldown_zero_means_no_cooldown():
    assert re.in_cooldown({"id": "x", "cooldown_s": 0}, time.time(), {}) is False


def test_in_cooldown_recent_fire():
    now = time.time()
    assert re.in_cooldown({"id": "x", "cooldown_s": 600}, now, {"x": now - 100}) is True


def test_in_cooldown_old_fire_expired():
    now = time.time()
    assert re.in_cooldown({"id": "x", "cooldown_s": 600}, now, {"x": now - 700}) is False


# ── evaluate_all ──────────────────────────────────────────────────────────


def test_evaluate_no_rules():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        assert re.evaluate_all([_sample(temp=90)]) == []


def test_evaluate_fires_when_match():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        re.save_rules([_hot_rule()])
        fires = re.evaluate_all([_sample(temp=85)], dry_run=True)
    assert len(fires) == 1
    assert fires[0]["fired"] is True
    assert fires[0]["actions"][0]["kind"] == "log"


def test_evaluate_no_fire_when_below():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        re.save_rules([_hot_rule()])
        fires = re.evaluate_all([_sample(temp=50)], dry_run=True)
    assert fires == []


def test_evaluate_respects_cooldown():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        re.save_rules([_hot_rule(cooldown_s=300)])
        re.save_lastfire({"hot-gpu": time.time() - 100})
        fires = re.evaluate_all([_sample(temp=85)], dry_run=False)
    assert len(fires) == 1
    assert fires[0]["fired"] is False
    assert fires[0]["in_cooldown"] is True


def test_evaluate_skips_disabled_rule():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        r = _hot_rule()
        r["enabled"] = False
        re.save_rules([r])
        fires = re.evaluate_all([_sample(temp=85)])
    assert fires == []


def test_evaluate_dry_run_does_not_update_lastfire():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        re.save_rules([_hot_rule()])
        re.evaluate_all([_sample(temp=85)], dry_run=True)
        assert re.load_lastfire() == {}


def test_evaluate_live_updates_lastfire():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        re.save_rules([_hot_rule()])
        re.evaluate_all([_sample(temp=85)], dry_run=False)
        lf = re.load_lastfire()
        assert "hot-gpu" in lf


def test_evaluate_multiple_rules_independent_cooldowns():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        r1 = _hot_rule(rid="a")
        r2 = _hot_rule(rid="b")
        re.save_rules([r1, r2])
        fires = re.evaluate_all([_sample(temp=85)], dry_run=True)
    ids = {f["rule_id"] for f in fires}
    assert ids == {"a", "b"}
