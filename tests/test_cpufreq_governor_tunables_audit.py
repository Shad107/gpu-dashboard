"""Tests for modules/cpufreq_governor_tunables_audit.py
R&D #90.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    cpufreq_governor_tunables_audit as mod)


def _mk_policy(tmp_path, name, *, governor="schedutil",
                rate_limit_us=None,
                rate_limit_in_subdir=True):
    d = tmp_path / "cpufreq" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "scaling_governor").write_text(governor + "\n")
    if rate_limit_us is not None:
        if rate_limit_in_subdir:
            sub = d / governor
            sub.mkdir(exist_ok=True)
            (sub / "rate_limit_us").write_text(
                f"{rate_limit_us}\n")
        else:
            (d / "rate_limit_us").write_text(
                f"{rate_limit_us}\n")
    return str(tmp_path / "cpufreq")


# --- list_policies ---------------------------------------------

def test_list_policies_missing(tmp_path):
    assert mod.list_policies(str(tmp_path / "nope")) == []


def test_list_policies_ignores_non_policy_dirs(tmp_path):
    root = tmp_path / "cpufreq"
    root.mkdir()
    (root / "policy0").mkdir()
    (root / "policy1").mkdir()
    (root / "boost").mkdir()
    assert mod.list_policies(str(root)) == [
        "policy0", "policy1"]


# --- read_policy -----------------------------------------------

def test_read_policy_missing(tmp_path):
    out = mod.read_policy(
        str(tmp_path / "cpufreq"), "policy0")
    assert out["governor"] == ""


def test_read_policy_full(tmp_path):
    r = _mk_policy(tmp_path, "policy0",
                       governor="schedutil",
                       rate_limit_us=500)
    out = mod.read_policy(r, "policy0")
    assert out["governor"] == "schedutil"
    assert out["rate_limit_us"] == 500


def test_read_policy_rate_limit_at_policy_level(tmp_path):
    r = _mk_policy(tmp_path, "policy0",
                       governor="schedutil",
                       rate_limit_us=2000,
                       rate_limit_in_subdir=False)
    out = mod.read_policy(r, "policy0")
    assert out["rate_limit_us"] == 2000


# --- classify --------------------------------------------------

def _pol(*, governor="schedutil", rate_limit_us=None,
         name="policy0"):
    return {"name": name, "governor": governor,
            "rate_limit_us": rate_limit_us}


def test_classify_unknown_no_policies():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_requires_root_blank_govs():
    v = mod.classify([_pol(governor=""), _pol(governor="")])
    assert v["verdict"] == "requires_root"


def test_classify_ok_uniform_schedutil():
    v = mod.classify([
        _pol(governor="schedutil", rate_limit_us=500),
        _pol(governor="schedutil", rate_limit_us=500,
             name="policy1"),
    ])
    assert v["verdict"] == "ok"


def test_classify_rate_limit_too_high():
    v = mod.classify([
        _pol(governor="schedutil", rate_limit_us=20000),
    ])
    assert v["verdict"] == "rate_limit_too_high"
    assert v["rate_limit_us"] == 20000


def test_classify_ondemand_legacy():
    v = mod.classify([
        _pol(governor="ondemand"),
        _pol(governor="ondemand", name="policy1"),
    ])
    assert v["verdict"] == "ondemand_legacy_active"


def test_classify_conservative_legacy():
    v = mod.classify([
        _pol(governor="conservative"),
    ])
    assert v["verdict"] == "ondemand_legacy_active"


def test_classify_governor_drift():
    v = mod.classify([
        _pol(governor="schedutil"),
        _pol(governor="performance", name="policy1"),
    ])
    assert v["verdict"] == "governor_drift_across_policies"


def test_classify_ok_uniform_performance():
    v = mod.classify([
        _pol(governor="performance"),
        _pol(governor="performance", name="policy1"),
    ])
    assert v["verdict"] == "ok"


# Priority : rate_limit > legacy > drift > ok
def test_priority_rate_over_legacy():
    v = mod.classify([
        _pol(governor="schedutil", rate_limit_us=20000),
        _pol(governor="ondemand", name="policy1"),
    ])
    # rate_limit fires on policy0, which is also drift, but
    # priority says rate_limit_too_high wins
    assert v["verdict"] == "rate_limit_too_high"


def test_priority_legacy_over_drift():
    v = mod.classify([
        _pol(governor="ondemand"),
        _pol(governor="schedutil", name="policy1"),
    ])
    # legacy AND drift both true → legacy wins
    assert v["verdict"] == "ondemand_legacy_active"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"
    assert out["policy_count"] == 0


def test_status_ok_synthetic(tmp_path):
    _mk_policy(tmp_path, "policy0",
                  governor="schedutil", rate_limit_us=500)
    _mk_policy(tmp_path, "policy1",
                  governor="schedutil", rate_limit_us=500)
    out = mod.status(None, str(tmp_path / "cpufreq"))
    assert out["verdict"]["verdict"] == "ok"
    assert out["policy_count"] == 2


def test_status_rate_limit_synthetic(tmp_path):
    _mk_policy(tmp_path, "policy0",
                  governor="schedutil",
                  rate_limit_us=50000)
    out = mod.status(None, str(tmp_path / "cpufreq"))
    assert out["verdict"]["verdict"] == "rate_limit_too_high"
    assert out["ok"] is False
