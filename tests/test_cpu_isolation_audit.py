"""Tests for modules/cpu_isolation_audit.py — R&D #74.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import cpu_isolation_audit as mod


# --- parse_cpu_list --------------------------------------------

def test_parse_empty():
    assert mod.parse_cpu_list("") == set()
    assert mod.parse_cpu_list(None) == set()
    assert mod.parse_cpu_list("(null)") == set()


def test_parse_simple_range():
    assert mod.parse_cpu_list("0-3") == {0, 1, 2, 3}


def test_parse_mixed():
    assert mod.parse_cpu_list("0,3-5,8") == {0, 3, 4, 5, 8}


def test_parse_full():
    assert mod.parse_cpu_list("0-11") == set(range(12))


# --- parse_cmdline ---------------------------------------------

def test_parse_cmdline_empty():
    out = mod.parse_cmdline(None)
    assert out == {"isolcpus": set(), "nohz_full": set(),
                      "had_cmdline": False}


def test_parse_cmdline_no_iso():
    out = mod.parse_cmdline("BOOT_IMAGE=/vmlinuz root=UUID=x ro")
    assert out["isolcpus"] == set()
    assert out["nohz_full"] == set()


def test_parse_cmdline_isolcpus_simple():
    out = mod.parse_cmdline("ro isolcpus=1-3 quiet")
    assert out["isolcpus"] == {1, 2, 3}


def test_parse_cmdline_isolcpus_with_flag():
    # Newer kernels accept "isolcpus=managed_irq,1-3"
    out = mod.parse_cmdline("ro isolcpus=managed_irq,1-3 quiet")
    assert out["isolcpus"] == {1, 2, 3}


def test_parse_cmdline_nohz_full():
    out = mod.parse_cmdline("ro nohz_full=2-7")
    assert out["nohz_full"] == {2, 3, 4, 5, 6, 7}


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, set(), set(), set(), set(),
                          set(), set())
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(True, set(), set(), set(),
                          set(range(12)), set(), set())
    assert v["verdict"] == "ok"


def test_classify_misaligned_cmdline():
    # cmdline says 1-3 but sysfs reports {2,3,4}
    v = mod.classify(True, {2, 3, 4}, set(), set(),
                          set(range(12)), {1, 2, 3}, set())
    assert v["verdict"] == "isolation_misaligned_cmdline"


def test_classify_misaligned_cmdline_empty_isolated():
    # cmdline asks for isolation, sysfs has none = drift
    v = mod.classify(True, set(), set(), set(),
                          set(range(12)), {1, 2, 3}, set())
    assert v["verdict"] == "isolation_misaligned_cmdline"


def test_classify_nohz_full_without_isolcpus():
    v = mod.classify(True, set(), {2, 3, 4}, set(),
                          set(range(12)), set(), {2, 3, 4})
    assert v["verdict"] == "nohz_full_without_isolcpus"


def test_classify_heavy_isolation():
    # 8/12 isolated = 66 % > 50 %
    v = mod.classify(True, set(range(8)), set(), set(),
                          set(range(12)),
                          set(range(8)), set())
    assert v["verdict"] == "heavy_isolation_on_desktop"


def test_classify_partial_offline():
    v = mod.classify(True, set(), set(), {4, 5},
                          set(range(12)), set(), set())
    assert v["verdict"] == "partial_offline_unexpected"


# Priority : misaligned > nohz_without_iso > heavy > offline
def test_priority_misaligned_over_nohz():
    v = mod.classify(True, set(), {2, 3}, set(),
                          set(range(12)), {1, 2, 3},
                          {2, 3})
    assert v["verdict"] == "isolation_misaligned_cmdline"


def test_priority_nohz_over_heavy():
    # nohz_full set, no isolcpus → nohz_without_iso wins
    v = mod.classify(True, set(), {2, 3, 4}, set(),
                          set(range(12)),
                          set(), {2, 3, 4})
    assert v["verdict"] == "nohz_full_without_isolcpus"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "nope"),
                          str(tmp_path / "nope_cmd"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    sys_cpu = tmp_path / "cpu"; sys_cpu.mkdir()
    (sys_cpu / "isolated").write_text("\n")
    (sys_cpu / "nohz_full").write_text("(null)\n")
    (sys_cpu / "offline").write_text("\n")
    (sys_cpu / "possible").write_text("0-11\n")
    (sys_cpu / "present").write_text("0-11\n")
    (sys_cpu / "kernel_max").write_text("8191\n")
    cmd = tmp_path / "cmdline"
    cmd.write_text("BOOT_IMAGE=/vmlinuz ro quiet\n")
    out = mod.status(None, str(sys_cpu), str(cmd))
    assert out["ok"] is True
    assert out["possible_count"] == 12
    assert out["isolated"] == []
    assert out["verdict"]["verdict"] == "ok"


def test_status_heavy_iso_synthetic(tmp_path):
    sys_cpu = tmp_path / "cpu"; sys_cpu.mkdir()
    (sys_cpu / "isolated").write_text("0-7\n")
    (sys_cpu / "nohz_full").write_text("(null)\n")
    (sys_cpu / "offline").write_text("\n")
    (sys_cpu / "possible").write_text("0-11\n")
    (sys_cpu / "present").write_text("0-11\n")
    (sys_cpu / "kernel_max").write_text("8191\n")
    cmd = tmp_path / "cmdline"
    cmd.write_text("ro isolcpus=0-7\n")
    out = mod.status(None, str(sys_cpu), str(cmd))
    assert out["verdict"]["verdict"] == \
        "heavy_isolation_on_desktop"
