"""Tests for modules/workqueue_cpumask_audit.py — R&D #86.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import workqueue_cpumask_audit as mod


def _mk_wq_root(tmp_path, *, global_mask="fff",
                 isolated_mask="000", requested="fff"):
    d = tmp_path / "wq"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cpumask").write_text(global_mask + "\n")
    (d / "cpumask_isolated").write_text(
        isolated_mask + "\n")
    (d / "cpumask_requested").write_text(
        requested + "\n")
    return str(d)


def _mk_wq(root, name, *, cpumask="fff", max_active=1024,
            nice=0, per_cpu=1):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "cpumask").write_text(cpumask + "\n")
    (d / "max_active").write_text(f"{max_active}\n")
    (d / "nice").write_text(f"{nice}\n")
    (d / "per_cpu").write_text(f"{per_cpu}\n")


# --- _parse_cpumask_hex ----------------------------------------

def test_parse_hex_simple():
    assert mod._parse_cpumask_hex("fff") == 0xfff


def test_parse_hex_multi():
    # "ffff,ffffffff" = 64 bits all set
    out = mod._parse_cpumask_hex("ffff,ffffffff")
    assert out == 0xffffffffffff


def test_parse_hex_empty():
    assert mod._parse_cpumask_hex("") == 0
    assert mod._parse_cpumask_hex(None) == 0


# --- _parse_cpu_list -------------------------------------------

def test_parse_cpu_list_empty():
    assert mod._parse_cpu_list("") == 0


def test_parse_cpu_list_range():
    # 4-7 → bits 4,5,6,7
    assert mod._parse_cpu_list("4-7") == (1<<4)|(1<<5)|(1<<6)|(1<<7)


def test_parse_cpu_list_mixed():
    assert mod._parse_cpu_list("1,3-4") == (1<<1)|(1<<3)|(1<<4)


# --- _cpu_count ------------------------------------------------

def test_cpu_count():
    text = "processor\t: 0\nprocessor\t: 1\nfoo\nprocessor\t: 2\n"
    assert mod._cpu_count(text) == 3


def test_cpu_count_empty():
    assert mod._cpu_count("") == 0


# --- list_workqueues -------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_workqueues(
        str(tmp_path / "nope")) == []


def test_list_basic(tmp_path):
    root = _mk_wq_root(tmp_path)
    _mk_wq(tmp_path / "wq", "writeback")
    _mk_wq(tmp_path / "wq", "raid5wq", per_cpu=0)
    out = mod.list_workqueues(root)
    assert "writeback" in out
    assert "raid5wq" in out


# --- classify --------------------------------------------------

def test_classify_na():
    v = mod.classify(0, 0, [], wq_present=False,
                          cpu_count=12)
    assert v["verdict"] == "n/a"


def test_classify_unknown_empty():
    v = mod.classify(0, 0, [], wq_present=True,
                          cpu_count=12)
    assert v["verdict"] == "unknown"


def _wq(name="wq1", mask="fff", nice=0, per_cpu=1):
    return {"name": name, "cpumask": mask,
              "max_active": 1024, "nice": nice,
              "per_cpu": per_cpu}


def test_classify_ok():
    v = mod.classify(
        0xfff, 0, [_wq("writeback")], True, 12)
    assert v["verdict"] == "ok"


def test_classify_global_overlap_isolated():
    # isolcpus=4-11 = 0xff0 ; global = fff overlaps
    v = mod.classify(
        0xfff, 0xff0, [_wq()], True, 12)
    assert v["verdict"] == "wq_on_isolated_cpu"


def test_classify_per_wq_overlap_isolated():
    # global mask non-overlapping but per-WQ overlaps
    v = mod.classify(
        0x00f, 0xff0,
        [_wq("writeback", mask="010")],
        True, 12)
    assert v["verdict"] == "wq_on_isolated_cpu"


def test_classify_no_isolation_ok():
    v = mod.classify(
        0xfff, 0, [_wq()], True, 12)
    assert v["verdict"] == "ok"


def test_classify_unbound_default():
    # 3+ unbound WQs all on CPU 0 only with 12 CPUs total
    wqs = [_wq(f"u{i}", mask="1", per_cpu=0)
            for i in range(3)]
    v = mod.classify(0xfff, 0, wqs, True, 12)
    assert v["verdict"] == "unbound_wq_default_only"


def test_classify_unbound_default_low_cpu_ok():
    # 2-CPU box → default behavior is fine, don't flag
    wqs = [_wq(f"u{i}", mask="1", per_cpu=0)
            for i in range(3)]
    v = mod.classify(0x3, 0, wqs, True, 2)
    assert v["verdict"] == "ok"


def test_classify_nice_drift():
    v = mod.classify(
        0xfff, 0,
        [_wq("u1", nice=-5)],
        True, 12)
    assert v["verdict"] == "nice_drift"


# Priority : isolated > unbound_default > nice
def test_priority_isolated_over_unbound():
    wqs = [_wq(f"u{i}", mask="1", per_cpu=0)
            for i in range(5)]
    v = mod.classify(0xfff, 0xff0, wqs, True, 12)
    assert v["verdict"] == "wq_on_isolated_cpu"


def test_priority_unbound_over_nice():
    wqs = [_wq(f"u{i}", mask="1", per_cpu=0, nice=-5)
            for i in range(3)]
    v = mod.classify(0xfff, 0, wqs, True, 12)
    assert v["verdict"] == "unbound_wq_default_only"


# --- status integration ----------------------------------------

def test_status_na(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_wq"),
                       str(tmp_path / "nope_iso"),
                       str(tmp_path / "nope_cpu"))
    assert out["verdict"]["verdict"] == "n/a"


def test_status_ok_synthetic(tmp_path):
    root = _mk_wq_root(tmp_path, global_mask="fff")
    _mk_wq(tmp_path / "wq", "writeback", cpumask="fff")
    isolated = tmp_path / "isolated"
    isolated.write_text("")  # nothing isolated
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("\n".join(
        f"processor\t: {i}" for i in range(12)) + "\n")
    out = mod.status(None, root, str(isolated),
                       str(cpuinfo))
    assert out["wq_count"] == 1
    assert out["cpu_count"] == 12
    assert out["verdict"]["verdict"] == "ok"


def test_status_isolation_overlap_synthetic(tmp_path):
    root = _mk_wq_root(tmp_path, global_mask="fff")
    _mk_wq(tmp_path / "wq", "writeback", cpumask="fff")
    isolated = tmp_path / "isolated"
    isolated.write_text("4-11\n")
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("\n".join(
        f"processor\t: {i}" for i in range(12)) + "\n")
    out = mod.status(None, root, str(isolated),
                       str(cpuinfo))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "wq_on_isolated_cpu"
