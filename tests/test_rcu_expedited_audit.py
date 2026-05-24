"""Tests for modules/rcu_expedited_audit.py — R&D #82.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import rcu_expedited_audit as mod


def _mk_sys_rcu(tmp_path, expedited=None, normal=None):
    d = tmp_path / "kernel"
    d.mkdir(parents=True, exist_ok=True)
    if expedited is not None:
        (d / "rcu_expedited").write_text(f"{expedited}\n")
    if normal is not None:
        (d / "rcu_normal").write_text(f"{normal}\n")
    return str(d)


def _mk_proc_kernel(tmp_path, expedited=None,
                     normal=None, stall=None):
    d = tmp_path / "proc_kernel"
    d.mkdir(parents=True, exist_ok=True)
    if expedited is not None:
        (d / "rcu_expedited").write_text(f"{expedited}\n")
    if normal is not None:
        (d / "rcu_normal").write_text(f"{normal}\n")
    if stall is not None:
        (d / "rcu_cpu_stall_timeout").write_text(f"{stall}\n")
    return str(d)


def _mk_cmdline(tmp_path, args=""):
    p = tmp_path / "cmdline"
    p.write_text(args + "\n")
    return str(p)


def _mk_isolated(tmp_path, cpus=""):
    p = tmp_path / "isolated"
    p.write_text(cpus + "\n")
    return str(p)


# --- _parse_cpu_list -------------------------------------------

def test_parse_cpu_list_empty():
    assert mod._parse_cpu_list(None) == []
    assert mod._parse_cpu_list("") == []


def test_parse_cpu_list_single():
    assert mod._parse_cpu_list("5") == [5]


def test_parse_cpu_list_range():
    assert mod._parse_cpu_list("1-3") == [1, 2, 3]


def test_parse_cpu_list_mixed():
    assert mod._parse_cpu_list("1-3,5,7-9") == [
        1, 2, 3, 5, 7, 8, 9]


def test_parse_cpu_list_garbage_skipped():
    assert mod._parse_cpu_list("1,bad,3") == [1, 3]


# --- read_state ------------------------------------------------

def test_read_state_empty(tmp_path):
    s = mod.read_state(
        str(tmp_path / "no_sys"),
        str(tmp_path / "no_proc"),
        str(tmp_path / "no_iso"),
        str(tmp_path / "no_cmdline"))
    assert s["rcu_expedited"] is None
    assert s["isolated_cpus"] == []


def test_read_state_populated(tmp_path):
    sys_r = _mk_sys_rcu(tmp_path, expedited=0, normal=0)
    proc_r = _mk_proc_kernel(tmp_path, stall=60)
    iso = _mk_isolated(tmp_path, "1-3")
    cmd = _mk_cmdline(tmp_path,
                         "BOOT_IMAGE=/vmlinuz ro "
                         "rcu_nocbs=1-3 isolcpus=1-3")
    s = mod.read_state(sys_r, proc_r, iso, cmd)
    assert s["rcu_expedited"] == 0
    assert s["rcu_cpu_stall_timeout"] == 60
    assert s["isolated_cpus"] == [1, 2, 3]
    assert s["isolcpus_cmd"] == "1-3"
    assert s["rcu_nocbs_cmd"] == "1-3"


def test_read_state_fallback_proc_when_sys_missing(tmp_path):
    proc_r = _mk_proc_kernel(tmp_path, expedited=1)
    s = mod.read_state(
        str(tmp_path / "no_sys"),
        proc_r,
        str(tmp_path / "no_iso"),
        str(tmp_path / "no_cmd"))
    assert s["rcu_expedited"] == 1


# --- classify --------------------------------------------------

def test_classify_unknown():
    s = {"rcu_expedited": None, "rcu_normal": None,
          "rcu_cpu_stall_timeout": None,
          "isolated_cpus": [], "isolcpus_cmd": None,
          "nohz_full_cmd": None, "rcu_nocbs_cmd": None}
    v = mod.classify(s)
    assert v["verdict"] == "unknown"


def _ok_state():
    return {"rcu_expedited": 0, "rcu_normal": 0,
              "rcu_cpu_stall_timeout": 60,
              "isolated_cpus": [], "isolcpus_cmd": None,
              "nohz_full_cmd": None, "rcu_nocbs_cmd": None}


def test_classify_ok():
    v = mod.classify(_ok_state())
    assert v["verdict"] == "ok"


def test_classify_expedited_with_isolation():
    s = _ok_state()
    s["rcu_expedited"] = 1
    s["isolated_cpus"] = [1, 2, 3]
    v = mod.classify(s)
    assert v["verdict"] == "rcu_expedited_with_isolation"


def test_classify_expedited_no_isolation_ok():
    # expedited=1 alone (no isolation) — not a defeating
    # combination
    s = _ok_state()
    s["rcu_expedited"] = 1
    v = mod.classify(s)
    assert v["verdict"] == "ok"


def test_classify_stall_timeout_short():
    s = _ok_state()
    s["rcu_cpu_stall_timeout"] = 10
    v = mod.classify(s)
    assert v["verdict"] == "rcu_stall_timeout_short"


def test_classify_stall_at_floor_ok():
    s = _ok_state()
    s["rcu_cpu_stall_timeout"] = 21
    v = mod.classify(s)
    assert v["verdict"] == "ok"


def test_classify_nocbs_no_isolation():
    s = _ok_state()
    s["rcu_nocbs_cmd"] = "1-3"
    v = mod.classify(s)
    assert v["verdict"] == "rcu_nocbs_no_isolation"


def test_classify_nocbs_with_isolcpus_ok():
    s = _ok_state()
    s["rcu_nocbs_cmd"] = "1-3"
    s["isolcpus_cmd"] = "1-3"
    v = mod.classify(s)
    assert v["verdict"] == "ok"


def test_classify_nocbs_with_nohz_full_ok():
    s = _ok_state()
    s["rcu_nocbs_cmd"] = "1-3"
    s["nohz_full_cmd"] = "1-3"
    v = mod.classify(s)
    assert v["verdict"] == "ok"


# Priority : expedited+iso > stall_short > nocbs_no_iso
def test_priority_expedited_over_stall():
    s = _ok_state()
    s["rcu_expedited"] = 1
    s["isolated_cpus"] = [1]
    s["rcu_cpu_stall_timeout"] = 10
    v = mod.classify(s)
    assert v["verdict"] == "rcu_expedited_with_isolation"


def test_priority_stall_over_nocbs():
    s = _ok_state()
    s["rcu_cpu_stall_timeout"] = 10
    s["rcu_nocbs_cmd"] = "1-3"
    v = mod.classify(s)
    assert v["verdict"] == "rcu_stall_timeout_short"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_sys"),
                       str(tmp_path / "no_proc"),
                       str(tmp_path / "no_iso"),
                       str(tmp_path / "no_cmd"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    sys_r = _mk_sys_rcu(tmp_path, expedited=0)
    proc_r = _mk_proc_kernel(tmp_path, stall=60)
    iso = _mk_isolated(tmp_path, "")
    cmd = _mk_cmdline(tmp_path, "BOOT_IMAGE=/v ro")
    out = mod.status(None, sys_r, proc_r, iso, cmd)
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"


def test_status_expedited_isolation(tmp_path):
    sys_r = _mk_sys_rcu(tmp_path, expedited=1)
    proc_r = _mk_proc_kernel(tmp_path)
    iso = _mk_isolated(tmp_path, "1-3")
    cmd = _mk_cmdline(tmp_path,
                         "BOOT_IMAGE=/v ro isolcpus=1-3")
    out = mod.status(None, sys_r, proc_r, iso, cmd)
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "rcu_expedited_with_isolation")
