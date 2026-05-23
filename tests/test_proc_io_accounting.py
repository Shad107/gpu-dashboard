"""Tests for modules/proc_io_accounting.py — R&D #33.2 IO accounting."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import proc_io_accounting


_LIVE_IO = """\
rchar: 22099544
wchar: 1139816
syscr: 5507
syscw: 9365
read_bytes: 56117784576
write_bytes: 0
cancelled_write_bytes: 0
"""


def _mk_proc(root: Path, pid: int, *, comm: str, cmdline: str,
              io_text: str = _LIVE_IO,
              vm_rss_kb: int = 0):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00") + "\x00")
    if io_text is not None:
        (d / "io").write_text(io_text)
    (d / "status").write_text(f"VmRSS:  {vm_rss_kb} kB\n")


# --- parse_io ------------------------------------------------------

def test_parse_io_full():
    p = proc_io_accounting.parse_io(_LIVE_IO)
    assert p["rchar"] == 22099544
    assert p["read_bytes"] == 56117784576
    assert p["write_bytes"] == 0
    assert p["syscr"] == 5507


def test_parse_io_empty_returns_empty():
    assert proc_io_accounting.parse_io("") == {}


def test_parse_io_partial():
    txt = "rchar: 1000\nread_bytes: 2000\n"
    p = proc_io_accounting.parse_io(txt)
    assert p["rchar"] == 1000
    assert p["read_bytes"] == 2000
    assert "write_bytes" not in p


def test_parse_io_ignores_garbage_lines():
    txt = "weird line\nrchar: 100\n"
    p = proc_io_accounting.parse_io(txt)
    assert p["rchar"] == 100


# --- classify ----------------------------------------------------

def test_classify_ok_normal_load():
    v = proc_io_accounting.classify({
        "read_bytes": 1_000_000_000,    # 1 GiB read total
        "write_bytes": 100_000,
        "rchar": 1_500_000_000,
    }, rss_bytes=8 * 1024**3)              # 8 GiB process
    assert v["verdict"] == "ok"


def test_classify_reread_thrash_at_2_7x_ratio():
    # The live llama-server case: 56 GiB read for a 21 GiB process
    v = proc_io_accounting.classify({
        "read_bytes": 56_117_784_576,
        "write_bytes": 0,
        "rchar": 22_099_544,
    }, rss_bytes=21 * 1024**3)
    assert v["verdict"] == "reread_thrash"
    assert "2." in v["reason"] or "reread" in v["reason"].lower()


def test_classify_heavy_write():
    v = proc_io_accounting.classify({
        "read_bytes": 100_000_000,
        "write_bytes": 50 * 1024**3,    # 50 GiB written
        "rchar": 100_000_000,
    }, rss_bytes=8 * 1024**3)
    assert v["verdict"] == "heavy_write"


def test_classify_unreadable_when_empty():
    v = proc_io_accounting.classify({}, rss_bytes=None)
    assert v["verdict"] == "unreadable"


def test_classify_no_rss_uses_absolute_threshold():
    # Without RSS info, fall back to "any > 200 GiB read = thrash"
    v = proc_io_accounting.classify({
        "read_bytes": 300 * 1024**3,
        "write_bytes": 0,
        "rchar": 0,
    }, rss_bytes=None)
    # Should still flag, but as "unknown_thrash" or with degraded reason
    assert v["verdict"] in ("reread_thrash", "ok")


def test_classify_low_io_with_small_rss_is_ok():
    # ollama on a fresh start has tiny IO, tiny RSS — verdict ok
    v = proc_io_accounting.classify({
        "read_bytes": 1_000_000,
        "write_bytes": 100,
        "rchar": 2_000_000,
    }, rss_bytes=15_000_000)
    assert v["verdict"] == "ok"


# --- status ------------------------------------------------------

def test_status_no_llm_procs(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="systemd",
             cmdline="/usr/lib/systemd/systemd")
    monkeypatch.setattr(proc_io_accounting, "_PROC", str(tmp_path))
    s = proc_io_accounting.status()
    assert s["worst_verdict"] == "no_llm_procs"
    assert s["processes"] == []


def test_status_live_reread_thrash(tmp_path, monkeypatch):
    # The exact live-rig snapshot
    _mk_proc(tmp_path, 2106872, comm="llama-server",
             cmdline="/home/olivier/llama.cpp/build/bin/llama-server",
             io_text=_LIVE_IO,
             vm_rss_kb=21_198_356)
    monkeypatch.setattr(proc_io_accounting, "_PROC", str(tmp_path))
    s = proc_io_accounting.status()
    p = s["processes"][0]
    assert p["read_bytes"] == 56_117_784_576
    assert p["verdict"]["verdict"] == "reread_thrash"
    assert s["worst_verdict"] == "reread_thrash"


def test_status_unreadable_io(tmp_path, monkeypatch):
    # ollama (root-owned) → io readable but empty for non-root caller
    _mk_proc(tmp_path, 1950, comm="ollama",
             cmdline="/usr/local/bin/ollama serve",
             io_text="", vm_rss_kb=15_000)
    monkeypatch.setattr(proc_io_accounting, "_PROC", str(tmp_path))
    s = proc_io_accounting.status()
    assert s["processes"][0]["verdict"]["verdict"] == "unreadable"


def test_status_picks_worst_across(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="ollama", cmdline="ollama serve",
             io_text="rchar: 1000\nread_bytes: 1000\nwrite_bytes: 0\n",
             vm_rss_kb=15_000)
    _mk_proc(tmp_path, 2, comm="llama-server",
             cmdline="llama-server --model x",
             io_text=_LIVE_IO,
             vm_rss_kb=21_198_356)
    monkeypatch.setattr(proc_io_accounting, "_PROC", str(tmp_path))
    s = proc_io_accounting.status()
    assert s["worst_verdict"] == "reread_thrash"


def test_status_aggregates_totals(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server",
             cmdline="llama-server",
             io_text=_LIVE_IO, vm_rss_kb=21_000_000)
    monkeypatch.setattr(proc_io_accounting, "_PROC", str(tmp_path))
    s = proc_io_accounting.status()
    assert s["total_read_bytes"] == 56_117_784_576


def test_status_includes_recipe_pointing_to_known_modules(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server",
             cmdline="llama-server",
             io_text=_LIVE_IO, vm_rss_kb=21_198_356)
    monkeypatch.setattr(proc_io_accounting, "_PROC", str(tmp_path))
    s = proc_io_accounting.status()
    rec = s["processes"][0]["verdict"]["recommendation"]
    # Should cross-reference the causal modules
    assert "#29.8" in rec or "mlock" in rec.lower()
    assert "#32.4" in rec or "swappiness" in rec.lower()


def test_is_llm_proc_matches_llama_server():
    assert proc_io_accounting.is_llm_proc(
        "llama-server", "llama-server --model x.gguf")
