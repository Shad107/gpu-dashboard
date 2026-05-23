"""Tests for modules/coredump_ready.py — R&D #39.3 coredump audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import coredump_ready


def _mk_proc(root: Path, pid: int, *, comm: str, cmdline: str,
                filter_hex: str = "00000033"):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00") + "\x00")
    (d / "coredump_filter").write_text(filter_hex + "\n")


def _mk_sys(root: Path, *, core_pattern: str = "core",
              core_uses_pid: str = "1"):
    d = root / "sys" / "kernel"
    d.mkdir(parents=True, exist_ok=True)
    (d / "core_pattern").write_text(core_pattern + "\n")
    (d / "core_uses_pid").write_text(core_uses_pid + "\n")


# --- parse_coredump_filter -------------------------------------

def test_parse_coredump_filter_default():
    bits = coredump_ready.parse_coredump_filter("00000033")
    assert bits == 0x33


def test_parse_coredump_filter_full():
    bits = coredump_ready.parse_coredump_filter("000000ff")
    assert bits == 0xff


def test_parse_coredump_filter_with_0x():
    assert coredump_ready.parse_coredump_filter("0x33") == 0x33


def test_parse_coredump_filter_empty():
    assert coredump_ready.parse_coredump_filter("") is None
    assert coredump_ready.parse_coredump_filter(None) is None


def test_parse_coredump_filter_garbage():
    assert coredump_ready.parse_coredump_filter("not_hex") is None


# --- describe_filter -----------------------------------------

def test_describe_filter_default_bits():
    items = coredump_ready.describe_filter(0x33)
    keys = [it["key"] for it in items]
    assert "anon_private" in keys
    assert "anon_shared" in keys
    assert "elf_headers" in keys
    assert "huge_private" in keys


def test_describe_filter_only_anon_private():
    items = coredump_ready.describe_filter(0x01)
    assert [it["key"] for it in items] == ["anon_private"]


# --- analyze_core_pattern -----------------------------------

def test_analyze_core_pattern_pipe_handler():
    info = coredump_ready.analyze_core_pattern("|/lib/systemd/systemd-coredump %P %u %g")
    assert info["kind"] == "pipe_handler"
    assert "systemd-coredump" in info["target"]


def test_analyze_core_pattern_disabled():
    info = coredump_ready.analyze_core_pattern("|/bin/false")
    assert info["kind"] == "disabled"


def test_analyze_core_pattern_absolute_path():
    info = coredump_ready.analyze_core_pattern("/var/crash/core.%e.%p")
    assert info["kind"] == "file_based"
    assert info["has_pid"] is True
    assert info["has_exe"] is True


def test_analyze_core_pattern_relative_default():
    info = coredump_ready.analyze_core_pattern("core")
    assert info["kind"] == "relative_only"
    assert info["has_pid"] is False


def test_analyze_core_pattern_with_pid_only():
    info = coredump_ready.analyze_core_pattern("core.%p")
    assert info["kind"] == "relative_only"
    assert info["has_pid"] is True


# --- classify -----------------------------------------------

def test_classify_disabled_pattern():
    v = coredump_ready.classify(pattern_info={"kind": "disabled"},
                                    procs=[])
    assert v["verdict"] == "core_disabled"


def test_classify_systemd_coredump_ok():
    v = coredump_ready.classify(
        pattern_info={"kind": "pipe_handler",
                       "target": "/lib/systemd/systemd-coredump"},
        procs=[{"pid": 1950, "comm": "ollama", "filter": 0x33}])
    assert v["verdict"] == "ok_pipe_handler"


def test_classify_file_based_with_pid_ok():
    v = coredump_ready.classify(
        pattern_info={"kind": "file_based", "has_pid": True,
                       "has_exe": True},
        procs=[{"pid": 1, "comm": "x", "filter": 0x33}])
    assert v["verdict"] == "ok_file_based"


def test_classify_relative_warns():
    v = coredump_ready.classify(
        pattern_info={"kind": "relative_only", "has_pid": False},
        procs=[{"pid": 1, "comm": "x", "filter": 0x33}])
    assert v["verdict"] == "relative_pattern"
    assert "absolute path" in v["recommendation"].lower() or "core_pattern" in v["recommendation"]


def test_classify_filter_too_low_warns():
    # filter=0x01 — only anon_private, no ELF headers
    v = coredump_ready.classify(
        pattern_info={"kind": "pipe_handler", "target": "/x"},
        procs=[{"pid": 1, "comm": "llama-server", "filter": 0x01}])
    assert v["verdict"] == "filter_too_low"


def test_classify_no_procs_falls_back_to_pattern():
    v = coredump_ready.classify(
        pattern_info={"kind": "disabled"}, procs=[])
    assert v["verdict"] == "core_disabled"


# --- status -------------------------------------------------

def test_status_no_llm_procs(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="systemd",
             cmdline="/usr/lib/systemd/systemd")
    _mk_sys(tmp_path, core_pattern="|/lib/systemd/systemd-coredump")
    monkeypatch.setattr(coredump_ready, "_PROC", str(tmp_path))
    s = coredump_ready.status()
    assert s["ok"] is True
    # No LLM procs but pattern is OK — verdict still based on pattern
    assert s["process_count"] == 0
    assert s["verdict"]["verdict"] == "ok_pipe_handler"


def test_status_live_relative_pattern(tmp_path, monkeypatch):
    # The live-rig case
    _mk_proc(tmp_path, 1950, comm="ollama",
             cmdline="/usr/local/bin/ollama serve",
             filter_hex="00000033")
    _mk_proc(tmp_path, 2106872, comm="llama-server",
             cmdline="/llama-server",
             filter_hex="00000033")
    _mk_sys(tmp_path, core_pattern="core")
    monkeypatch.setattr(coredump_ready, "_PROC", str(tmp_path))
    s = coredump_ready.status()
    assert s["process_count"] == 2
    assert s["core_pattern"] == "core"
    assert s["pattern_info"]["kind"] == "relative_only"
    assert s["verdict"]["verdict"] == "relative_pattern"


def test_status_systemd_coredump(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server", cmdline="/llama-server")
    _mk_sys(tmp_path,
             core_pattern="|/lib/systemd/systemd-coredump %P %u %g")
    monkeypatch.setattr(coredump_ready, "_PROC", str(tmp_path))
    s = coredump_ready.status()
    assert s["verdict"]["verdict"] == "ok_pipe_handler"


def test_status_disabled(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server", cmdline="/llama-server")
    _mk_sys(tmp_path, core_pattern="|/bin/false")
    monkeypatch.setattr(coredump_ready, "_PROC", str(tmp_path))
    s = coredump_ready.status()
    assert s["verdict"]["verdict"] == "core_disabled"


def test_status_filter_describes_bits(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server", cmdline="/llama-server",
             filter_hex="33")
    _mk_sys(tmp_path,
             core_pattern="|/lib/systemd/systemd-coredump %P")
    monkeypatch.setattr(coredump_ready, "_PROC", str(tmp_path))
    s = coredump_ready.status()
    proc = s["processes"][0]
    assert proc["filter_value"] == 0x33
    bit_keys = [b["key"] for b in proc["filter_bits"]]
    assert "anon_private" in bit_keys
