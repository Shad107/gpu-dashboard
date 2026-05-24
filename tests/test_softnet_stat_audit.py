"""Tests for modules/softnet_stat_audit.py — R&D #79.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import softnet_stat_audit as mod


def _row(processed=0, dropped=0, time_squeeze=0,
          cpu_collision=0):
    """Generate one /proc/net/softnet_stat row (15 hex cols)."""
    cols = ["0"] * 15
    cols[0] = f"{processed:08x}"
    cols[1] = f"{dropped:08x}"
    cols[2] = f"{time_squeeze:08x}"
    cols[8] = f"{cpu_collision:08x}"
    return " ".join(cols)


# --- parse -----------------------------------------------------

def test_parse_empty():
    assert mod.parse("") == []


def test_parse_one_row():
    rows = mod.parse(_row(processed=100, dropped=2,
                            time_squeeze=5) + "\n")
    assert len(rows) == 1
    assert rows[0]["cpu"] == 0
    assert rows[0]["processed"] == 100
    assert rows[0]["dropped"] == 2
    assert rows[0]["time_squeeze"] == 5


def test_parse_multi():
    text = "\n".join([
        _row(processed=100),
        _row(processed=200),
        _row(processed=300),
    ])
    rows = mod.parse(text)
    assert [r["cpu"] for r in rows] == [0, 1, 2]
    assert rows[2]["processed"] == 300


def test_parse_skips_bad_lines():
    text = "\n".join([
        _row(processed=100),
        "not_hex_garbage",
        _row(processed=300),
    ])
    rows = mod.parse(text)
    # cpu index follows the row position — line 1 was bad
    assert len(rows) == 2


# --- read_softnet_stat -----------------------------------------

def test_read_missing(tmp_path):
    assert mod.read_softnet_stat(
        str(tmp_path / "nope")) is None


def test_read_present(tmp_path):
    p = tmp_path / "ssn"
    p.write_text(_row(processed=16) + "\n")
    out = mod.read_softnet_stat(str(p))
    assert len(out) == 1
    # _row formats `16` as `00000010` hex → parses to 16
    assert out[0]["processed"] == 16


# --- classify --------------------------------------------------

def test_classify_unknown_none():
    v = mod.classify(None)
    assert v["verdict"] == "unknown"


def test_classify_unknown_empty():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def _ok_row(cpu=0):
    return {"cpu": cpu, "processed": 10000, "dropped": 0,
            "time_squeeze": 5, "cpu_collision": 0}


def test_classify_ok():
    v = mod.classify([_ok_row(0), _ok_row(1)])
    assert v["verdict"] == "ok"


def test_classify_err_high_ratio():
    r = _ok_row(0)
    r["processed"] = 1000
    r["dropped"] = 100  # 10 % drop
    v = mod.classify([r])
    assert v["verdict"] == "err"


def test_classify_err_multi_cpu_drops():
    r1 = _ok_row(0); r1["processed"] = 100000; r1["dropped"] = 1
    r2 = _ok_row(1); r2["processed"] = 100000; r2["dropped"] = 1
    v = mod.classify([r1, r2])
    assert v["verdict"] == "err"


def test_classify_warn_single_cpu_drop():
    r1 = _ok_row(0); r1["processed"] = 1000000
    r1["dropped"] = 5  # below ratio threshold
    v = mod.classify([r1, _ok_row(1)])
    assert v["verdict"] == "warn"


def test_classify_warn_time_squeeze():
    r = _ok_row(0); r["time_squeeze"] = 5000
    v = mod.classify([r, _ok_row(1)])
    assert v["verdict"] == "warn"


def test_classify_accent_cpu_collision():
    r = _ok_row(0); r["cpu_collision"] = 3
    v = mod.classify([r, _ok_row(1)])
    assert v["verdict"] == "accent"


# Priority : ratio_err > multi_drop > single_drop > ts > coll
def test_priority_ratio_err_over_multi():
    r1 = _ok_row(0); r1["processed"] = 1000; r1["dropped"] = 100
    r2 = _ok_row(1); r2["dropped"] = 1
    v = mod.classify([r1, r2])
    assert v["verdict"] == "err"


def test_priority_drop_over_ts():
    r1 = _ok_row(0); r1["dropped"] = 1; r1["processed"] = 100000
    r2 = _ok_row(1); r2["time_squeeze"] = 5000
    v = mod.classify([r1, r2])
    assert v["verdict"] == "warn"
    # message specifies CPU0 drop, not CPU1 squeeze
    assert "CPU0" in v["reason"]


def test_priority_ts_over_collision():
    r1 = _ok_row(0); r1["time_squeeze"] = 5000
    r2 = _ok_row(1); r2["cpu_collision"] = 1
    v = mod.classify([r1, r2])
    assert v["verdict"] == "warn"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok(tmp_path):
    p = tmp_path / "ssn"
    p.write_text(_row(processed=10000) + "\n"
                  + _row(processed=20000) + "\n")
    out = mod.status(None, str(p))
    assert out["ok"] is True
    assert out["cpu_count"] == 2
    assert out["totals"]["processed"] == 30000
    assert out["verdict"]["verdict"] == "ok"


def test_status_err(tmp_path):
    p = tmp_path / "ssn"
    p.write_text(_row(processed=1000, dropped=100) + "\n")
    out = mod.status(None, str(p))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "err"
