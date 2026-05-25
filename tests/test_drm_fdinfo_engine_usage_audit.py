"""Tests for modules/drm_fdinfo_engine_usage_audit.py
R&D #92.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    drm_fdinfo_engine_usage_audit as mod)


def _mk_proc_fdinfo(tmp_path, pid, fd, content):
    d = tmp_path / "proc" / str(pid) / "fdinfo"
    d.mkdir(parents=True, exist_ok=True)
    (d / str(fd)).write_text(content)
    return str(tmp_path / "proc")


# --- _parse_size -----------------------------------------------

def test_parse_size_bare_int():
    assert mod._parse_size("12345") == 12345


def test_parse_size_kib():
    assert mod._parse_size("1024 KiB") == 1024 * 1024


def test_parse_size_mib():
    assert mod._parse_size("8 MiB") == 8 * 1024 * 1024


def test_parse_size_gib():
    assert mod._parse_size("4 GiB") == 4 * 1024 ** 3


def test_parse_size_empty():
    assert mod._parse_size("") is None
    assert mod._parse_size("zz") is None


# --- parse_fdinfo_drm ------------------------------------------

def test_parse_fdinfo_drm_not_drm():
    text = "pos: 0\nflags: 0100000\nmnt_id: 29\n"
    assert mod.parse_fdinfo_drm(text) is None


def test_parse_fdinfo_drm_with_pdev():
    text = (
        "pos: 0\n"
        "flags: 0100000\n"
        "drm-pdev: 0000:01:00.0\n"
        "drm-client-id: 42\n"
        "drm-memory-vram: 4 GiB\n"
        "drm-memory-gtt: 128 MiB\n")
    out = mod.parse_fdinfo_drm(text)
    assert out is not None
    assert out["pdev"] == "0000:01:00.0"
    assert out["client_id"] == 42
    assert out["vram"] == 4 * 1024 ** 3
    assert out["gtt"] == 128 * 1024 ** 2


def test_parse_fdinfo_drm_missing_memory_keys():
    text = (
        "drm-pdev: 0000:01:00.0\n"
        "drm-client-id: 7\n")
    out = mod.parse_fdinfo_drm(text)
    assert out["vram"] == 0
    assert out["gtt"] == 0


# --- walk_fdinfo -----------------------------------------------

def test_walk_fdinfo_no_proc(tmp_path):
    out = mod.walk_fdinfo(str(tmp_path / "nope"))
    assert out["total_clients"] == 0
    assert out["readable_files"] == 0


def test_walk_fdinfo_no_drm_clients(tmp_path):
    p = _mk_proc_fdinfo(tmp_path, 100, 0,
                              "pos: 0\nflags: 0100000\n")
    out = mod.walk_fdinfo(p)
    assert out["total_clients"] == 0
    assert out["readable_files"] == 1


def test_walk_fdinfo_aggregates_per_pid(tmp_path):
    drm = ("drm-pdev: 0000:01:00.0\n"
           "drm-client-id: {}\n"
           "drm-memory-vram: {} MiB\n")
    _mk_proc_fdinfo(tmp_path, 100, 5,
                          drm.format(1, 100))
    _mk_proc_fdinfo(tmp_path, 100, 6,
                          drm.format(2, 50))
    _mk_proc_fdinfo(tmp_path, 200, 7,
                          drm.format(3, 25))
    out = mod.walk_fdinfo(str(tmp_path / "proc"))
    assert out["total_clients"] == 3
    assert out["pid_vram"][100] == 150 * 1024 ** 2
    assert out["pid_vram"][200] == 25 * 1024 ** 2


# --- classify --------------------------------------------------

def _summary(*, pid_vram=None, total_clients=0,
              readable=100, unreadable=0):
    pv = pid_vram or {}
    tv = sum(pv.values())
    pc = {pid: 1 for pid in pv}
    return {
        "pid_vram": pv,
        "pid_clients": pc,
        "total_vram": tv,
        "total_clients": total_clients or len(pv),
        "readable_files": readable,
        "unreadable_files": unreadable,
    }


def test_classify_unknown_empty_no_unreadable():
    v = mod.classify(_summary(readable=200))
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(_summary(
        readable=10, unreadable=500))
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(_summary(pid_vram={
        100: 1 * 1024**3,
        200: 1 * 1024**3,
        300: 1 * 1024**3,
        400: 1 * 1024**3,
        500: 1 * 1024**3,
    }))
    assert v["verdict"] == "ok"


def test_classify_vram_overcommit():
    v = mod.classify(_summary(pid_vram={
        100: 23 * 1024**3,
        200: 1 * 1024**3,
    }))
    assert v["verdict"] == "vram_overcommit_per_client"
    assert v["pid"] == 100


def test_classify_vram_top3_concentrated():
    # 3 clients at 3 GiB each = 9 GiB on 10 GiB total
    v = mod.classify(_summary(pid_vram={
        100: 3 * 1024**3,
        200: 3 * 1024**3,
        300: 3 * 1024**3,
        400: 1 * 1024**3,  # makes a 4th
        500: 100 * 1024**2,  # tiny
    }))
    assert v["verdict"] == "vram_top3_concentrated"


def test_classify_many_drm_clients():
    pid_vram = {i: 100 for i in range(40)}
    v = mod.classify(_summary(pid_vram=pid_vram))
    assert v["verdict"] == "many_drm_clients"


# Priority : overcommit > top3 > many_clients > ok
def test_priority_overcommit_over_top3():
    pv = {100: 100 * 1024**3}
    for i in range(40):
        pv[i] = 1
    v = mod.classify(_summary(pid_vram=pv))
    assert v["verdict"] == "vram_overcommit_per_client"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    p = _mk_proc_fdinfo(tmp_path, 100, 0,
                              "pos: 0\nflags: 0100000\n")
    out = mod.status(None, p)
    assert out["verdict"]["verdict"] == "unknown"


def test_status_overcommit_synthetic(tmp_path):
    drm = ("drm-pdev: 0000:01:00.0\n"
           "drm-client-id: 1\n"
           "drm-memory-vram: {} MiB\n")
    _mk_proc_fdinfo(tmp_path, 100, 5,
                          drm.format(20000))
    _mk_proc_fdinfo(tmp_path, 200, 5,
                          drm.format(100))
    out = mod.status(None, str(tmp_path / "proc"))
    assert (out["verdict"]["verdict"]
            == "vram_overcommit_per_client")
    assert out["drm_client_count"] == 2
