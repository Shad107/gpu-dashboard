"""Tests for modules/sysvipc_limits_audit.py — R&D #89.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import sysvipc_limits_audit as mod


_GIB = 2**30


def _mk_proc_sys(tmp_path, *, shmmax=18 * 2**60,
                   shmall=18 * 2**60 // 4096,
                   shmmni=4096, msgmni=32000):
    d = tmp_path / "sys" / "kernel"
    d.mkdir(parents=True, exist_ok=True)
    (d / "shmmax").write_text(f"{shmmax}\n")
    (d / "shmall").write_text(f"{shmall}\n")
    (d / "shmmni").write_text(f"{shmmni}\n")
    (d / "msgmni").write_text(f"{msgmni}\n")
    return str(tmp_path / "sys" / "kernel")


def _mk_meminfo(tmp_path, mem_total_kib=32 * 2**20):
    p = tmp_path / "meminfo"
    p.write_text(
        f"MemTotal:       {mem_total_kib} kB\n"
        "MemFree:         1000000 kB\n")
    return str(p)


# --- parse_meminfo_total_bytes ---------------------------------

def test_parse_meminfo_present():
    text = "MemTotal:       32000000 kB\nMemFree: 100 kB\n"
    assert mod.parse_meminfo_total_bytes(text) == 32000000 * 1024


def test_parse_meminfo_missing():
    assert mod.parse_meminfo_total_bytes("") is None


def test_parse_meminfo_garbage_field():
    assert mod.parse_meminfo_total_bytes(
        "MemTotal:       abc kB\n") is None


# --- read_limits -----------------------------------------------

def test_read_limits_missing(tmp_path):
    out = mod.read_limits(str(tmp_path / "nope"))
    assert all(v is None for v in out.values())


def test_read_limits_populated(tmp_path):
    r = _mk_proc_sys(tmp_path)
    out = mod.read_limits(r)
    assert out["shmmni"] == 4096
    assert out["msgmni"] == 32000


# --- classify --------------------------------------------------

def test_classify_unknown_empty():
    v = mod.classify({"shmmax": None, "shmall": None,
                          "shmmni": None, "msgmni": None},
                          None, 4096)
    assert v["verdict"] == "unknown"


def test_classify_shmmax_zero():
    v = mod.classify({"shmmax": 0, "shmall": 1000,
                          "shmmni": 4096, "msgmni": 32000},
                          32 * _GIB, 4096)
    assert v["verdict"] == "shmmax_zero"


def test_classify_shmall_under_ram():
    # shmall=1000 pages × 4096 = 4 MiB << 32 GiB
    v = mod.classify({"shmmax": 8 * _GIB, "shmall": 1000,
                          "shmmni": 4096, "msgmni": 32000},
                          32 * _GIB, 4096)
    assert v["verdict"] == "shmall_under_ram"


def test_classify_shmmax_capped_low_on_big_box():
    v = mod.classify({"shmmax": 1 * _GIB,
                          "shmall": 32 * _GIB // 4096,
                          "shmmni": 4096, "msgmni": 32000},
                          32 * _GIB, 4096)
    assert v["verdict"] == "shmmax_capped_low"


def test_classify_shmmax_capped_low_not_on_small_box():
    # 1 GiB shmmax on a 8 GiB box → not a complaint
    v = mod.classify({"shmmax": 1 * _GIB,
                          "shmall": 8 * _GIB // 4096,
                          "shmmni": 4096, "msgmni": 32000},
                          8 * _GIB, 4096)
    assert v["verdict"] == "ok"


def test_classify_shmmni_low_accent():
    v = mod.classify({"shmmax": 8 * _GIB,
                          "shmall": 32 * _GIB // 4096,
                          "shmmni": 128, "msgmni": 32000},
                          32 * _GIB, 4096)
    assert v["verdict"] == "shmmni_low"


def test_classify_ok():
    v = mod.classify({"shmmax": 18 * 2**60,
                          "shmall": 18 * 2**60 // 4096,
                          "shmmni": 4096, "msgmni": 32000},
                          32 * _GIB, 4096)
    assert v["verdict"] == "ok"


# Priority : shmmax_zero > shmall_under_ram > shmmax_capped > shmmni_low
def test_priority_zero_over_under_ram():
    v = mod.classify({"shmmax": 0, "shmall": 1000,
                          "shmmni": 4096, "msgmni": 32000},
                          32 * _GIB, 4096)
    assert v["verdict"] == "shmmax_zero"


def test_priority_under_ram_over_capped_low():
    v = mod.classify({"shmmax": 1 * _GIB, "shmall": 1000,
                          "shmmni": 4096, "msgmni": 32000},
                          32 * _GIB, 4096)
    assert v["verdict"] == "shmall_under_ram"


def test_priority_capped_low_over_shmmni_low():
    v = mod.classify({"shmmax": 1 * _GIB,
                          "shmall": 32 * _GIB // 4096,
                          "shmmni": 128, "msgmni": 32000},
                          32 * _GIB, 4096)
    assert v["verdict"] == "shmmax_capped_low"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_sys"),
                       str(tmp_path / "nope_mem"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    r = _mk_proc_sys(tmp_path)
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, m)
    assert out["verdict"]["verdict"] == "ok"
    assert out["ok"] is True


def test_status_shmall_under_ram_synthetic(tmp_path):
    r = _mk_proc_sys(tmp_path,
                          shmall=1000)  # 4 MiB
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, m)
    assert out["verdict"]["verdict"] == "shmall_under_ram"
    assert out["ok"] is False
