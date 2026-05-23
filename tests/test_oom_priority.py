"""Tests for modules/oom_priority.py — R&D #31.4 OOM-priority auditor."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from gpu_dashboard.modules import oom_priority


def _mk_proc(proc_root: Path, pid: int, *, comm: str = "test",
              cmdline: str = "test", oom_score: str = "0",
              oom_score_adj: str = "0", rss_kb: int = 0):
    d = proc_root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00") + "\x00")
    (d / "oom_score").write_text(oom_score + "\n")
    (d / "oom_score_adj").write_text(oom_score_adj + "\n")
    if rss_kb:
        (d / "status").write_text(f"VmRSS:  {rss_kb} kB\n")


# --- field readers --------------------------------------------------

def test_read_oom_score_returns_int(tmp_path):
    _mk_proc(tmp_path, 100, oom_score="666")
    assert oom_priority.read_oom_score(100, str(tmp_path)) == 666


def test_read_oom_score_adj_returns_int(tmp_path):
    _mk_proc(tmp_path, 100, oom_score_adj="-500")
    assert oom_priority.read_oom_score_adj(100, str(tmp_path)) == -500


def test_read_oom_score_missing_returns_none(tmp_path):
    assert oom_priority.read_oom_score(999, str(tmp_path)) is None


def test_read_oom_score_adj_zero(tmp_path):
    _mk_proc(tmp_path, 100, oom_score_adj="0")
    assert oom_priority.read_oom_score_adj(100, str(tmp_path)) == 0


def test_read_oom_score_adj_garbage_returns_none(tmp_path):
    d = tmp_path / "100"
    d.mkdir()
    (d / "oom_score_adj").write_text("garbage\n")
    assert oom_priority.read_oom_score_adj(100, str(tmp_path)) is None


# --- LLM proc detection (reused pattern from rlimit_audit) ---------

def test_is_llm_proc_matches_ollama():
    assert oom_priority.is_llm_proc("ollama", "/usr/local/bin/ollama serve")


def test_is_llm_proc_matches_llama_server():
    assert oom_priority.is_llm_proc("llama-server", "/llama.cpp/build/bin/llama-server --model x.gguf")


def test_is_llm_proc_matches_python_vllm():
    assert oom_priority.is_llm_proc("python3", "python -m vllm.entrypoints.api_server")


def test_is_llm_proc_rejects_systemd():
    assert not oom_priority.is_llm_proc("systemd", "/usr/lib/systemd/systemd")


def test_is_llm_proc_rejects_random_python():
    assert not oom_priority.is_llm_proc("python3", "python3 unrelated_script.py")


# --- classify -------------------------------------------------------

def test_classify_default_is_the_headline_catch():
    """oom_score_adj=0 with high oom_score → first to die. The exact
    state we see on this rig for ollama + llama-server."""
    v = oom_priority.classify(oom_score=1050, oom_score_adj=0,
                                comm="llama-server")
    assert v["verdict"] == "default"
    assert "first to die" in v["reason"].lower()
    assert "OOMScoreAdjust=-500" in v["recommendation"]
    assert "systemctl" in v["recommendation"].lower()


def test_classify_protected_for_strong_negative():
    v = oom_priority.classify(oom_score=666, oom_score_adj=-500,
                                comm="ollama")
    assert v["verdict"] == "protected"
    assert v["recommendation"] == ""


def test_classify_protected_for_minus_1000():
    v = oom_priority.classify(oom_score=0, oom_score_adj=-1000,
                                comm="ollama")
    assert v["verdict"] == "protected"


def test_classify_hardened_for_weak_negative():
    v = oom_priority.classify(oom_score=400, oom_score_adj=-100,
                                comm="ollama")
    assert v["verdict"] == "hardened"
    assert "stronger" in v["reason"].lower() or "-500" in v["recommendation"]


def test_classify_sacrificial_for_positive_adj():
    v = oom_priority.classify(oom_score=2000, oom_score_adj=500,
                                comm="ollama")
    assert v["verdict"] == "sacrificial"
    assert "voluntarily" in v["reason"].lower() or "increased" in v["reason"].lower()


def test_classify_unknown_when_score_missing():
    v = oom_priority.classify(oom_score=None, oom_score_adj=None,
                                comm="llama-server")
    assert v["verdict"] == "unknown"


def test_classify_unknown_when_only_adj_missing():
    v = oom_priority.classify(oom_score=666, oom_score_adj=None,
                                comm="llama-server")
    assert v["verdict"] == "unknown"


# --- status ---------------------------------------------------------

def test_status_no_llm_procs(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="systemd",
             cmdline="/usr/lib/systemd/systemd")
    monkeypatch.setattr(oom_priority, "_PROC", str(tmp_path))
    s = oom_priority.status()
    assert s["ok"] is True
    assert s["worst_verdict"] == "no_llm_procs"
    assert s["processes"] == []


def test_status_finds_default_state_daemons(tmp_path, monkeypatch):
    # The live-rig case
    _mk_proc(tmp_path, 1950, comm="ollama",
             cmdline="/usr/local/bin/ollama serve",
             oom_score="666", oom_score_adj="0")
    _mk_proc(tmp_path, 2106872, comm="llama-server",
             cmdline="/home/olivier/llama.cpp/build/bin/llama-server",
             oom_score="1050", oom_score_adj="0")
    monkeypatch.setattr(oom_priority, "_PROC", str(tmp_path))
    s = oom_priority.status()
    assert s["ok"] is True
    assert s["worst_verdict"] == "default"
    assert len(s["processes"]) == 2
    # Sort order shouldn't matter; just check both made it in
    pids = {p["pid"] for p in s["processes"]}
    assert pids == {1950, 2106872}


def test_status_protected_daemon(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1950, comm="ollama",
             cmdline="ollama serve",
             oom_score="100", oom_score_adj="-500")
    monkeypatch.setattr(oom_priority, "_PROC", str(tmp_path))
    s = oom_priority.status()
    assert s["worst_verdict"] == "protected"


def test_status_includes_rss_when_available(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1950, comm="ollama",
             cmdline="ollama serve",
             oom_score="666", oom_score_adj="0",
             rss_kb=8_388_608)  # 8 GiB
    monkeypatch.setattr(oom_priority, "_PROC", str(tmp_path))
    s = oom_priority.status()
    assert s["processes"][0]["vm_rss_bytes"] == 8_388_608 * 1024


def test_status_picks_worst_across_mixed(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="ollama",
             cmdline="ollama serve",
             oom_score="100", oom_score_adj="-500")
    _mk_proc(tmp_path, 2, comm="llama-server",
             cmdline="llama-server --model x.gguf",
             oom_score="1050", oom_score_adj="0")
    monkeypatch.setattr(oom_priority, "_PROC", str(tmp_path))
    s = oom_priority.status()
    assert s["worst_verdict"] == "default"


def test_status_recommendation_names_systemd_unit(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="ollama",
             cmdline="ollama serve",
             oom_score="666", oom_score_adj="0")
    monkeypatch.setattr(oom_priority, "_PROC", str(tmp_path))
    s = oom_priority.status()
    rec = s["processes"][0]["recipe"]
    assert "ollama" in rec.lower()
    assert "OOMScoreAdjust=-500" in rec
    assert "systemctl" in rec.lower()


def test_status_handles_partial_proc_data(tmp_path, monkeypatch):
    # cmdline exists but oom_score missing → verdict=unknown
    d = tmp_path / "100"
    d.mkdir()
    (d / "comm").write_text("ollama\n")
    (d / "cmdline").write_text("ollama serve")
    monkeypatch.setattr(oom_priority, "_PROC", str(tmp_path))
    s = oom_priority.status()
    assert len(s["processes"]) == 1
    assert s["processes"][0]["verdict"]["verdict"] == "unknown"
