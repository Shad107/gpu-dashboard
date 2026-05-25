"""Tests for modules/vmstat_reclaim_pressure_audit.py
R&D #91.4."""
from __future__ import annotations

import json
import os

import pytest

from gpu_dashboard.modules import (
    vmstat_reclaim_pressure_audit as mod)


_VMSTAT_BASE = (
    "pgsteal_kswapd 1000\n"
    "pgsteal_direct 10\n"
    "pgscan_kswapd 1000\n"
    "pgscan_direct 10\n"
    "oom_kill 0\n"
    "compact_stall 50\n"
    "compact_fail 5\n"
    "compact_success 45\n"
    "thp_fault_fallback 0\n"
)


def _mk_vmstat(tmp_path, text=_VMSTAT_BASE, name="vmstat"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def _mk_proc_sys_vm(tmp_path, *, watermark_scale_factor=10):
    d = tmp_path / "vm"
    d.mkdir(exist_ok=True)
    (d / "watermark_scale_factor").write_text(
        f"{watermark_scale_factor}\n")
    return str(d)


def _mk_meminfo(tmp_path, mem_kib=64 * 2**20):
    p = tmp_path / "meminfo"
    p.write_text(f"MemTotal: {mem_kib} kB\n")
    return str(p)


def _mk_state(tmp_path, counters):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"counters": counters}))
    return str(p)


# --- parse_vmstat ----------------------------------------------

def test_parse_vmstat_empty():
    assert mod.parse_vmstat("") == {}


def test_parse_vmstat_typical():
    out = mod.parse_vmstat(_VMSTAT_BASE)
    assert out["oom_kill"] == 0
    assert out["pgsteal_kswapd"] == 1000


def test_parse_vmstat_garbage_skipped():
    text = "garbage line\nkey 42\nbad foo\n"
    assert mod.parse_vmstat(text) == {"key": 42}


# --- load_prev_state -------------------------------------------

def test_load_prev_missing(tmp_path):
    assert mod.load_prev_state(
        str(tmp_path / "nope")) is None


def test_load_prev_corrupt(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json")
    assert mod.load_prev_state(str(p)) is None


def test_load_prev_wrong_shape(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('"just a string"')
    assert mod.load_prev_state(str(p)) is None


def test_load_prev_valid(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('{"counters": {"oom_kill": 7}}')
    out = mod.load_prev_state(str(p))
    assert out["counters"]["oom_kill"] == 7


# --- compute_deltas --------------------------------------------

def test_compute_deltas_no_prev():
    current = {"oom_kill": 5}
    out = mod.compute_deltas(current, {})
    assert out["oom_kill"] == 5


def test_compute_deltas_with_prev():
    current = {"oom_kill": 5, "pgsteal_direct": 100}
    prev = {"counters": {"oom_kill": 2,
                            "pgsteal_direct": 50}}
    out = mod.compute_deltas(current, prev)
    assert out["oom_kill"] == 3
    assert out["pgsteal_direct"] == 50


# --- classify --------------------------------------------------

def _zero_deltas():
    return {k: 0 for k in mod._COUNTERS}


def test_classify_unknown_no_prev():
    v = mod.classify(_zero_deltas(), False, 10, 64 * 2**30)
    assert v["verdict"] == "unknown"


def test_classify_ok_all_zero():
    v = mod.classify(_zero_deltas(), True, 10, 8 * 2**30)
    assert v["verdict"] == "ok"


def test_classify_oom_kill_err():
    d = _zero_deltas()
    d["oom_kill"] = 3
    v = mod.classify(d, True, 10, 8 * 2**30)
    assert v["verdict"] == "oom_or_direct_reclaim_heavy"


def test_classify_direct_reclaim_ratio_err():
    d = _zero_deltas()
    # direct 80% of total under high activity
    d["pgsteal_direct"] = 8000
    d["pgsteal_kswapd"] = 2000
    v = mod.classify(d, True, 10, 8 * 2**30)
    assert v["verdict"] == "oom_or_direct_reclaim_heavy"


def test_classify_direct_low_activity_is_ok():
    # Direct ratio 80% but total activity below threshold
    d = _zero_deltas()
    d["pgsteal_direct"] = 80
    d["pgsteal_kswapd"] = 20
    v = mod.classify(d, True, 10, 8 * 2**30)
    assert v["verdict"] == "ok"


def test_classify_compaction_failing():
    d = _zero_deltas()
    d["compact_fail"] = 100
    d["compact_success"] = 10
    v = mod.classify(d, True, 10, 8 * 2**30)
    assert v["verdict"] == "compaction_failing"


def test_classify_watermarks_loose_big_box():
    d = _zero_deltas()
    v = mod.classify(d, True, 10, 64 * 2**30)
    assert v["verdict"] == "watermarks_loose_big_box"


def test_classify_watermarks_ok_when_tuned():
    d = _zero_deltas()
    v = mod.classify(d, True, 200, 64 * 2**30)
    assert v["verdict"] == "ok"


# Priority : oom > direct_ratio > compaction > watermark
def test_priority_oom_over_compaction():
    d = _zero_deltas()
    d["oom_kill"] = 1
    d["compact_fail"] = 1000
    d["compact_success"] = 0
    v = mod.classify(d, True, 10, 64 * 2**30)
    assert v["verdict"] == "oom_or_direct_reclaim_heavy"


def test_priority_compaction_over_watermark():
    d = _zero_deltas()
    d["compact_fail"] = 100
    d["compact_success"] = 0
    v = mod.classify(d, True, 10, 64 * 2**30)
    assert v["verdict"] == "compaction_failing"


# --- status integration ----------------------------------------

def test_status_first_run_unknown(tmp_path):
    v = _mk_vmstat(tmp_path)
    s = _mk_proc_sys_vm(tmp_path)
    m = _mk_meminfo(tmp_path)
    sp = str(tmp_path / "state.json")
    out = mod.status(None, v, s, m, sp)
    assert out["verdict"]["verdict"] == "unknown"
    # State file should now exist for next run
    assert os.path.isfile(sp)


def test_status_second_run_ok_no_change(tmp_path):
    v = _mk_vmstat(tmp_path)
    s = _mk_proc_sys_vm(tmp_path)
    m = _mk_meminfo(tmp_path, mem_kib=8 * 2**20)
    sp = str(tmp_path / "state.json")
    mod.status(None, v, s, m, sp)
    out = mod.status(None, v, s, m, sp)
    assert out["verdict"]["verdict"] == "ok"


def test_status_oom_delta_synthetic(tmp_path):
    s = _mk_proc_sys_vm(tmp_path)
    m = _mk_meminfo(tmp_path, mem_kib=8 * 2**20)
    sp = str(tmp_path / "state.json")
    # First run: baseline
    v1 = _mk_vmstat(tmp_path, _VMSTAT_BASE)
    mod.status(None, v1, s, m, sp)
    # Second run: oom_kill incremented
    bumped = _VMSTAT_BASE.replace(
        "oom_kill 0", "oom_kill 5")
    v2 = _mk_vmstat(tmp_path, bumped, name="vmstat2")
    out = mod.status(None, v2, s, m, sp)
    assert (out["verdict"]["verdict"]
            == "oom_or_direct_reclaim_heavy")
