"""Tests for modules/vm_tuning_deep.py — R&D #40.3."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import vm_tuning_deep


def _mk_sysvm(root: Path, **fields):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in fields.items():
        (root / k).write_text(str(v) + "\n")


# --- read_knobs ----------------------------------------------------

def test_read_knobs_missing(tmp_path):
    assert vm_tuning_deep.read_knobs(str(tmp_path / "nope")) == {}


def test_read_knobs_all_present(tmp_path):
    root = tmp_path / "vm"
    _mk_sysvm(root, **{
        "page-cluster": 3,
        "watermark_scale_factor": 10,
        "vfs_cache_pressure": 100,
        "min_free_kbytes": 67584,
        "zone_reclaim_mode": 0,
    })
    k = vm_tuning_deep.read_knobs(str(root))
    assert k["page-cluster"] == 3
    assert k["watermark_scale_factor"] == 10
    assert k["min_free_kbytes"] == 67584


def test_read_knobs_partial_ok(tmp_path):
    root = tmp_path / "vm"
    _mk_sysvm(root, **{"page-cluster": 0})
    k = vm_tuning_deep.read_knobs(str(root))
    assert k == {"page-cluster": 0}


def test_read_knobs_bad_value_skipped(tmp_path):
    root = tmp_path / "vm"
    _mk_sysvm(root, **{"page-cluster": "notnum"})
    k = vm_tuning_deep.read_knobs(str(root))
    assert "page-cluster" not in k


# --- read_swap_active ----------------------------------------------

def test_read_swap_active_empty(tmp_path):
    p = tmp_path / "swaps"
    p.write_text("Filename\tType\tSize\tUsed\tPriority\n")
    assert vm_tuning_deep.read_swap_active(str(p)) is False


def test_read_swap_active_present(tmp_path):
    p = tmp_path / "swaps"
    p.write_text("Filename\tType\tSize\tUsed\tPriority\n"
                  "/swap.img\tfile\t8388604\t1000\t-2\n")
    assert vm_tuning_deep.read_swap_active(str(p)) is True


def test_read_swap_active_missing(tmp_path):
    # /proc/swaps unreadable → no swap detected
    assert vm_tuning_deep.read_swap_active(str(tmp_path / "nope")) is False


# --- read_meminfo --------------------------------------------------

def test_read_meminfo_basic(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal:       32115212 kB\n"
                  "MemFree:         5000000 kB\n"
                  "MemAvailable:   10000000 kB\n"
                  "Buffers:         1000000 kB\n")
    m = vm_tuning_deep.read_meminfo(str(p))
    assert m["MemTotal"] == 32115212
    assert m["MemAvailable"] == 10000000


def test_read_meminfo_missing(tmp_path):
    assert vm_tuning_deep.read_meminfo(str(tmp_path / "nope")) == {}


# --- mem_pressure --------------------------------------------------

def test_mem_pressure_ratio():
    m = {"MemTotal": 1000, "MemAvailable": 200}
    # used = 800, ratio = 0.8
    assert vm_tuning_deep.mem_pressure(m) == 0.8


def test_mem_pressure_none_when_missing():
    assert vm_tuning_deep.mem_pressure({}) is None
    assert vm_tuning_deep.mem_pressure({"MemTotal": 0}) is None


# --- classify ------------------------------------------------------

def _meminfo(total_gb=32, available_gb=15):
    return {"MemTotal": total_gb * 1024 * 1024,
            "MemAvailable": available_gb * 1024 * 1024}


def _defaults():
    return {
        "page-cluster": 3,
        "watermark_scale_factor": 10,
        "vfs_cache_pressure": 100,
        "zone_reclaim_mode": 0,
    }


def test_classify_unknown_when_no_knobs():
    v = vm_tuning_deep.classify({}, False, {})
    assert v["verdict"] == "unknown"


def test_classify_zone_reclaim_conflict_wins():
    k = _defaults()
    k["zone_reclaim_mode"] = 1
    v = vm_tuning_deep.classify(k, True, _meminfo())
    assert v["verdict"] == "zone_reclaim_conflict"
    assert "zone_reclaim_mode" in v["recommendation"]


def test_classify_nvme_swap_readahead_waste():
    k = _defaults()  # page-cluster=3 default
    v = vm_tuning_deep.classify(k, swap_active=True,
                                  meminfo=_meminfo())
    assert v["verdict"] == "nvme_swap_readahead_waste"
    assert "page-cluster" in v["recommendation"]


def test_classify_no_readahead_waste_when_swap_inactive():
    k = _defaults()
    v = vm_tuning_deep.classify(k, swap_active=False,
                                  meminfo=_meminfo())
    assert v["verdict"] == "defaults_on_tight_box"


def test_classify_late_kswapd_wake_when_pressure_high():
    k = _defaults()
    k["page-cluster"] = 0  # already tuned
    # 32 GB total, 5 GB available → ~84 % pressure
    v = vm_tuning_deep.classify(k, swap_active=False,
                                  meminfo=_meminfo(32, 5))
    assert v["verdict"] == "late_kswapd_wake"
    assert "watermark_scale_factor" in v["recommendation"]


def test_classify_no_late_kswapd_when_already_bumped():
    k = _defaults()
    k["page-cluster"] = 0
    k["watermark_scale_factor"] = 200
    v = vm_tuning_deep.classify(k, swap_active=False,
                                  meminfo=_meminfo(32, 5))
    # All knobs tuned + ≥1 deviates → ok
    assert v["verdict"] == "ok"


def test_classify_defaults_on_tight_box():
    k = _defaults()
    v = vm_tuning_deep.classify(k, swap_active=False,
                                  meminfo=_meminfo(32, 15))
    assert v["verdict"] == "defaults_on_tight_box"
    assert "sysctl.d/99-llm-vm-tuning.conf" in v["recommendation"]


def test_classify_ok_when_big_box():
    # 128-GB rig at defaults → not "tight"
    k = _defaults()
    v = vm_tuning_deep.classify(k, swap_active=False,
                                  meminfo=_meminfo(128, 100))
    assert v["verdict"] == "ok"


def test_classify_ok_when_tuned():
    k = _defaults()
    k["page-cluster"] = 0   # tuned
    v = vm_tuning_deep.classify(k, swap_active=False,
                                  meminfo=_meminfo())
    assert v["verdict"] == "ok"


# --- status integration -------------------------------------------

def test_status_with_isolated_roots(monkeypatch, tmp_path):
    sysvm = tmp_path / "vm"
    _mk_sysvm(sysvm, **{
        "page-cluster": 3, "watermark_scale_factor": 10,
        "vfs_cache_pressure": 100, "min_free_kbytes": 67584,
        "zone_reclaim_mode": 1,
    })
    swaps = tmp_path / "swaps"
    swaps.write_text("Filename\tType\tSize\tUsed\tPriority\n"
                      "/swap.img\tfile\t8388604\t1000\t-2\n")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       32115212 kB\n"
                        "MemAvailable:   10000000 kB\n")
    monkeypatch.setattr(vm_tuning_deep, "_PROC_SYS_VM", str(sysvm))
    monkeypatch.setattr(vm_tuning_deep, "_PROC_SWAPS", str(swaps))
    monkeypatch.setattr(vm_tuning_deep, "_MEMINFO", str(meminfo))
    out = vm_tuning_deep.status()
    assert out["ok"] is True
    assert out["swap_active"] is True
    # zone_reclaim_mode=1 wins
    assert out["verdict"]["verdict"] == "zone_reclaim_conflict"


def test_status_unknown_when_proc_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(vm_tuning_deep, "_PROC_SYS_VM",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(vm_tuning_deep, "_PROC_SWAPS",
                        str(tmp_path / "noswap"))
    monkeypatch.setattr(vm_tuning_deep, "_MEMINFO",
                        str(tmp_path / "nomem"))
    out = vm_tuning_deep.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
