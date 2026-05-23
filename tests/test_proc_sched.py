"""Tests for modules/proc_sched.py — R&D #34.4 per-daemon scheduler stats."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import proc_sched


_LIVE_SCHED = """\
llama-server (2106872, #threads: 26)
-------------------------------------------------------------------
se.exec_start                                :     194318339.633956
se.vruntime                                  :       3521556.871677
se.sum_exec_runtime                          :       3878426.987682
se.nr_migrations                             :                36119
nr_switches                                  :               768518
nr_voluntary_switches                        :               224309
nr_involuntary_switches                      :               544209
se.load.weight                               :              1048576
"""

_LIVE_STATUS = """\
Name:   llama-server
State:  R (running)
Tgid:   2106872
voluntary_ctxt_switches:        224309
nonvoluntary_ctxt_switches:     544209
"""


def _mk_proc(root: Path, pid: int, *, comm: str, cmdline: str,
              sched_text: str = _LIVE_SCHED,
              status_text: str = _LIVE_STATUS):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00") + "\x00")
    if sched_text is not None:
        (d / "sched").write_text(sched_text)
    if status_text is not None:
        (d / "status").write_text(status_text)


# --- parse_sched -------------------------------------------------

def test_parse_sched_extracts_switches():
    p = proc_sched.parse_sched(_LIVE_SCHED)
    assert p["voluntary_switches"] == 224309
    assert p["involuntary_switches"] == 544209
    assert p["nr_migrations"] == 36119
    assert p["nr_switches"] == 768518


def test_parse_sched_sum_exec_runtime_ms():
    p = proc_sched.parse_sched(_LIVE_SCHED)
    # sum_exec_runtime is in ms in /proc/<pid>/sched
    assert p["sum_exec_runtime_ms"] == pytest.approx(3878426.987682,
                                                       rel=1e-6)


def test_parse_sched_threads():
    p = proc_sched.parse_sched(_LIVE_SCHED)
    assert p["threads"] == 26


def test_parse_sched_empty():
    assert proc_sched.parse_sched("") == {}


# --- parse_status_switches ---------------------------------------

def test_parse_status_switches_returns_pair():
    p = proc_sched.parse_status_switches(_LIVE_STATUS)
    assert p == (224309, 544209)


def test_parse_status_switches_missing_returns_none_pair():
    assert proc_sched.parse_status_switches("Name: x\n") == (None, None)


# --- classify ----------------------------------------------------

def test_classify_ok_low_involuntary():
    v = proc_sched.classify(voluntary=100, involuntary=10,
                                nr_migrations=5, sum_exec_ms=10000)
    assert v["verdict"] == "ok"


def test_classify_contended_30pct():
    # 30 % involuntary ratio → contended
    v = proc_sched.classify(voluntary=70, involuntary=30,
                                nr_migrations=10, sum_exec_ms=10000)
    assert v["verdict"] == "contended"


def test_classify_severely_contended_70pct_live_case():
    # The live llama-server snapshot
    v = proc_sched.classify(voluntary=224309, involuntary=544209,
                                nr_migrations=36119,
                                sum_exec_ms=3878426)
    assert v["verdict"] == "severely_contended"
    assert "70" in v["reason"] or "involuntary" in v["reason"].lower()


def test_classify_ratio_below_threshold_is_ok():
    # 10 % involuntary → ok
    v = proc_sched.classify(voluntary=284, involuntary=33,
                                nr_migrations=20, sum_exec_ms=52)
    assert v["verdict"] == "ok"


def test_classify_unknown_when_zero_total():
    v = proc_sched.classify(voluntary=0, involuntary=0,
                                nr_migrations=0, sum_exec_ms=0)
    assert v["verdict"] == "unknown"


def test_classify_unknown_when_none():
    v = proc_sched.classify(voluntary=None, involuntary=None,
                                nr_migrations=None, sum_exec_ms=None)
    assert v["verdict"] == "unknown"


def test_classify_recipe_cross_refs_modules():
    v = proc_sched.classify(voluntary=100, involuntary=400,
                                nr_migrations=200, sum_exec_ms=100000)
    rec = v["recommendation"]
    assert "#32.1" in rec or "PSI" in rec
    assert "#33.6" in rec or "CPUWeight" in rec or "cgroup_cpuio" in rec


# --- status ------------------------------------------------------

def test_status_no_llm_procs(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="systemd",
             cmdline="/usr/lib/systemd/systemd")
    monkeypatch.setattr(proc_sched, "_PROC", str(tmp_path))
    s = proc_sched.status()
    assert s["worst_verdict"] == "no_llm_procs"


def test_status_full_live_payload(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 2106872, comm="llama-server",
             cmdline="/llama.cpp/llama-server --model x.gguf",
             sched_text=_LIVE_SCHED, status_text=_LIVE_STATUS)
    monkeypatch.setattr(proc_sched, "_PROC", str(tmp_path))
    s = proc_sched.status()
    assert s["process_count"] == 1
    p = s["processes"][0]
    assert p["voluntary_switches"] == 224309
    assert p["involuntary_switches"] == 544209
    assert p["nr_migrations"] == 36119
    assert p["verdict"]["verdict"] == "severely_contended"


def test_status_picks_worst_across(tmp_path, monkeypatch):
    # ollama healthy, llama-server contended
    _mk_proc(tmp_path, 1, comm="ollama",
             cmdline="ollama serve",
             sched_text="ollama (1, #threads: 13)\n"
                        "se.sum_exec_runtime                          :  52.0\n"
                        "se.nr_migrations                             :   20\n"
                        "nr_switches                                  :  317\n"
                        "nr_voluntary_switches                        :  284\n"
                        "nr_involuntary_switches                      :   33\n",
             status_text="voluntary_ctxt_switches: 284\n"
                          "nonvoluntary_ctxt_switches: 33\n")
    _mk_proc(tmp_path, 2, comm="llama-server",
             cmdline="llama-server --model x.gguf",
             sched_text=_LIVE_SCHED, status_text=_LIVE_STATUS)
    monkeypatch.setattr(proc_sched, "_PROC", str(tmp_path))
    s = proc_sched.status()
    assert s["worst_verdict"] == "severely_contended"


def test_status_falls_back_to_status_when_sched_missing(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server",
             cmdline="llama-server",
             sched_text=None, status_text=_LIVE_STATUS)
    monkeypatch.setattr(proc_sched, "_PROC", str(tmp_path))
    s = proc_sched.status()
    # No /sched but /status has the switches → still classifiable
    p = s["processes"][0]
    assert p["voluntary_switches"] == 224309
    assert p["involuntary_switches"] == 544209


def test_status_includes_involuntary_ratio(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server",
             cmdline="llama-server",
             sched_text=_LIVE_SCHED, status_text=_LIVE_STATUS)
    monkeypatch.setattr(proc_sched, "_PROC", str(tmp_path))
    s = proc_sched.status()
    p = s["processes"][0]
    # 544209 / (224309 + 544209) ≈ 0.708
    assert p["involuntary_ratio"] == pytest.approx(0.708, abs=0.01)


def test_is_llm_proc_matches_llama_server():
    assert proc_sched.is_llm_proc("llama-server",
                                      "llama-server --model x.gguf")
