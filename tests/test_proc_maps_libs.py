"""Tests for modules/proc_maps_libs.py — R&D #38.3 maps drift."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import proc_maps_libs


_CLEAN_MAPS = """\
7bebf5a00000-7bec0c3a6000 r-xp 00000000 08:02 3288393                    /usr/lib/x86_64-linux-gnu/libcublasLt.so.12.4.5.8
7bec13a00000-7bec14bf3000 r--p 00000000 08:02 3278937                    /usr/lib/x86_64-linux-gnu/libcuda.so.590.48.01
7bec19200000-7bec19300000 r--p 00000000 08:02 1234567                    /usr/lib/x86_64-linux-gnu/libc.so.6
55a000000000-55a000001000 r--p 00000000 08:02 7654321                    /llama-server
"""


_DELETED_MAPS = """\
7bebf5a00000-7bec0c3a6000 r-xp 00000000 08:02 3288393                    /usr/lib/x86_64-linux-gnu/libcuda.so.580.00.00 (deleted)
7bec0c3a6000-7bec0c5a6000 ---p 169a6000 08:02 3288393                    /usr/lib/x86_64-linux-gnu/libcuda.so.580.00.00 (deleted)
7bec13a00000-7bec14bf3000 r--p 00000000 08:02 3278937                    /usr/lib/x86_64-linux-gnu/libcudart.so.11.7
"""


def _mk_proc(root: Path, pid: int, *, comm: str, cmdline: str,
                maps: str = _CLEAN_MAPS):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00") + "\x00")
    if maps:
        (d / "maps").write_text(maps)


# --- parse_maps_line -----------------------------------------

def test_parse_maps_line_basic():
    line = "7bebf5a00000-7bec0c3a6000 r-xp 00000000 08:02 3288393                    /usr/lib/x86_64-linux-gnu/libcuda.so.590.48.01"
    rec = proc_maps_libs.parse_maps_line(line)
    assert rec["path"] == "/usr/lib/x86_64-linux-gnu/libcuda.so.590.48.01"
    assert rec["deleted"] is False


def test_parse_maps_line_deleted():
    line = "7bebf5a00000-7bec0c3a6000 r-xp 00000000 08:02 3288393                    /usr/lib/x86_64-linux-gnu/libcuda.so.580.00.00 (deleted)"
    rec = proc_maps_libs.parse_maps_line(line)
    assert rec["path"] == "/usr/lib/x86_64-linux-gnu/libcuda.so.580.00.00"
    assert rec["deleted"] is True


def test_parse_maps_line_anonymous_returns_none():
    line = "55a000000000-55a000001000 rw-p 00000000 00:00 0"
    assert proc_maps_libs.parse_maps_line(line) is None


def test_parse_maps_line_empty():
    assert proc_maps_libs.parse_maps_line("") is None
    assert proc_maps_libs.parse_maps_line("garbage") is None


def test_parse_maps_line_special_mappings_skipped():
    assert proc_maps_libs.parse_maps_line(
        "7ffff7ffd000-7ffff7fff000 r-xp 00000000 00:00 0 [vdso]") is None


# --- extract_libs --------------------------------------------

def test_extract_libs_groups_by_basename():
    libs = proc_maps_libs.extract_libs(_CLEAN_MAPS)
    names = {lib["basename"] for lib in libs}
    assert "libcuda.so.590.48.01" in names
    assert "libcublasLt.so.12.4.5.8" in names
    assert "libc.so.6" in names
    # The non-.so executable should NOT be included
    assert not any(lib["basename"] == "llama-server" for lib in libs)


def test_extract_libs_marks_deleted():
    libs = proc_maps_libs.extract_libs(_DELETED_MAPS)
    cuda = next(lib for lib in libs
                 if lib["basename"] == "libcuda.so.580.00.00")
    assert cuda["deleted"] is True


def test_extract_libs_empty():
    assert proc_maps_libs.extract_libs("") == []


# --- nvidia detection --------------------------------------

def test_is_nvidia_lib_libcuda():
    assert proc_maps_libs.is_nvidia_lib("libcuda.so.590.48.01")


def test_is_nvidia_lib_libcudart():
    assert proc_maps_libs.is_nvidia_lib("libcudart.so.11.7")


def test_is_nvidia_lib_libcublas():
    assert proc_maps_libs.is_nvidia_lib("libcublasLt.so.12.4.5.8")
    assert proc_maps_libs.is_nvidia_lib("libcublas.so.12.4")


def test_is_nvidia_lib_libcudnn():
    assert proc_maps_libs.is_nvidia_lib("libcudnn.so.8")


def test_is_nvidia_lib_libnvidia_anything():
    assert proc_maps_libs.is_nvidia_lib("libnvidia-ml.so.1")
    assert proc_maps_libs.is_nvidia_lib("libnvidia-glcore.so.560.35.03")


def test_is_nvidia_lib_negative():
    assert not proc_maps_libs.is_nvidia_lib("libc.so.6")
    assert not proc_maps_libs.is_nvidia_lib("libstdc++.so.6")


# --- classify ----------------------------------------------

def test_classify_clean():
    cards = [{"pid": 100, "comm": "llama-server",
                "libs": [{"basename": "libcuda.so.590.48.01",
                            "deleted": False, "path": "/x"}],
                "deleted_libs": [],
                "readable": True}]
    v = proc_maps_libs.classify(cards)
    assert v["verdict"] == "clean"


def test_classify_deleted_libs():
    cards = [{"pid": 100, "comm": "llama-server",
                "libs": [{"basename": "libcuda.so.580.00.00",
                            "deleted": True, "path": "/x"}],
                "deleted_libs": ["libcuda.so.580.00.00"],
                "readable": True}]
    v = proc_maps_libs.classify(cards)
    assert v["verdict"] == "deleted_libs"
    assert "libcuda" in v["reason"]
    assert "restart" in v["recommendation"].lower()


def test_classify_unreadable():
    cards = [{"pid": 100, "comm": "ollama",
                "libs": [], "deleted_libs": [],
                "readable": False}]
    v = proc_maps_libs.classify(cards)
    assert v["verdict"] == "unreadable"


def test_classify_no_procs():
    v = proc_maps_libs.classify(cards=[])
    assert v["verdict"] == "no_llm_procs"


def test_classify_picks_deleted_over_clean():
    cards = [
        {"pid": 1, "comm": "ollama", "libs": [],
         "deleted_libs": [], "readable": True},
        {"pid": 2, "comm": "llama-server",
         "libs": [], "deleted_libs": ["libcuda.so.580"],
         "readable": True},
    ]
    v = proc_maps_libs.classify(cards)
    assert v["verdict"] == "deleted_libs"


# --- status -----------------------------------------------

def test_status_no_llm_procs(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="systemd",
             cmdline="/usr/lib/systemd/systemd", maps="")
    monkeypatch.setattr(proc_maps_libs, "_PROC", str(tmp_path))
    s = proc_maps_libs.status()
    assert s["worst_verdict"] == "no_llm_procs"


def test_status_clean_live(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 2106872, comm="llama-server",
             cmdline="/llama-server --model x.gguf",
             maps=_CLEAN_MAPS)
    monkeypatch.setattr(proc_maps_libs, "_PROC", str(tmp_path))
    s = proc_maps_libs.status()
    assert s["process_count"] == 1
    p = s["processes"][0]
    assert p["readable"] is True
    assert len(p["libs"]) >= 2  # libcuda + libcublasLt at least
    assert s["worst_verdict"] == "clean"


def test_status_deleted_warns(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server",
             cmdline="/llama-server",
             maps=_DELETED_MAPS)
    monkeypatch.setattr(proc_maps_libs, "_PROC", str(tmp_path))
    s = proc_maps_libs.status()
    assert s["worst_verdict"] == "deleted_libs"


def test_status_unreadable_maps(tmp_path, monkeypatch):
    # maps file absent (root-owned daemon)
    d = tmp_path / "1950"
    d.mkdir()
    (d / "comm").write_text("ollama\n")
    (d / "cmdline").write_text("ollama serve")
    # no maps file
    monkeypatch.setattr(proc_maps_libs, "_PROC", str(tmp_path))
    s = proc_maps_libs.status()
    p = s["processes"][0]
    assert p["readable"] is False


def test_status_lists_nvidia_libs_specifically(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server",
             cmdline="/llama-server",
             maps=_CLEAN_MAPS)
    monkeypatch.setattr(proc_maps_libs, "_PROC", str(tmp_path))
    s = proc_maps_libs.status()
    p = s["processes"][0]
    nvidia_names = {lib["basename"] for lib in p["libs"]
                       if lib["is_nvidia"]}
    assert "libcuda.so.590.48.01" in nvidia_names
    assert "libcublasLt.so.12.4.5.8" in nvidia_names
