"""Tests for modules/cpufreq_setspeed_drift_audit.py R&D #106.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cpufreq_setspeed_drift_audit as mod


def _mk_cpu(root, cpu_id, *, governor="schedutil",
              setspeed="<unsupported>",
              max_freq=4500000):
    d = root / f"cpu{cpu_id}" / "cpufreq"
    d.mkdir(parents=True, exist_ok=True)
    (d / "scaling_governor").write_text(governor + "\n")
    (d / "scaling_setspeed").write_text(setspeed + "\n")
    (d / "cpuinfo_max_freq").write_text(f"{max_freq}\n")


def _c(*, cpu_id=0, governor="schedutil",
       setspeed=None, max_freq=4500000):
    return {"cpu_id": cpu_id, "governor": governor,
            "setspeed": setspeed, "max_freq": max_freq}


def test_classify_unknown_no_cpu():
    v = mod.classify(False, False, [])
    assert v["verdict"] == "unknown"


def test_classify_unknown_no_cpufreq():
    v = mod.classify(True, False, [])
    assert v["verdict"] == "unknown"


def test_classify_ok_schedutil():
    v = mod.classify(True, True,
                          [_c(governor="schedutil")])
    assert v["verdict"] == "ok"


def test_classify_pinned_low_warn():
    v = mod.classify(
        True, True,
        [_c(governor="userspace",
            setspeed=800000, max_freq=4500000)])
    assert v["verdict"] == "setspeed_pinned_low"


def test_classify_pinned_high_is_ok():
    # >= 50 % → not flagged
    v = mod.classify(
        True, True,
        [_c(governor="userspace",
            setspeed=3000000, max_freq=4500000)])
    assert v["verdict"] == "ok"


def test_classify_unused_accent():
    v = mod.classify(
        True, True,
        [_c(governor="schedutil",
            setspeed=800000, max_freq=4500000)])
    assert v["verdict"] == "setspeed_unused"


# Priority : pinned_low > unused
def test_priority_pinned_over_unused():
    v = mod.classify(
        True, True,
        [_c(governor="userspace",
            setspeed=800000, max_freq=4500000),
         _c(governor="schedutil",
            setspeed=800000, max_freq=4500000)])
    assert v["verdict"] == "setspeed_pinned_low"


def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    _mk_cpu(tmp_path, 0, governor="schedutil",
                 setspeed="<unsupported>")
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "ok"


def test_status_pinned_low(tmp_path):
    _mk_cpu(tmp_path, 0, governor="userspace",
                 setspeed="800000",
                 max_freq=4500000)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "setspeed_pinned_low"
