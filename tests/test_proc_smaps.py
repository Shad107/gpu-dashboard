"""Tests for modules/proc_smaps.py — R&D #31.2 smaps_rollup residence."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import proc_smaps


_LIVE_ROLLUP = """\
64eed5228000-7ffeaa764000 ---p 00000000 00:00 0                          [rollup]
Rss:            21196356 kB
Pss:            21193171 kB
Pss_Dirty:      11856248 kB
Pss_Anon:       11856248 kB
Pss_File:        9336923 kB
Pss_Shmem:             0 kB
Shared_Clean:       3328 kB
Shared_Dirty:          0 kB
Private_Clean:   9336780 kB
Private_Dirty:  11856248 kB
Referenced:     16865748 kB
Anonymous:      11856248 kB
KSM:                   0 kB
LazyFree:              0 kB
AnonHugePages:         0 kB
ShmemPmdMapped:        0 kB
FilePmdMapped:         0 kB
Shared_Hugetlb:        0 kB
Private_Hugetlb:       0 kB
Swap:                  0 kB
SwapPss:               0 kB
Locked:                0 kB
"""


def _mk_proc(root: Path, pid: int, *, comm: str, cmdline: str,
               rollup: str | None = _LIVE_ROLLUP):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00") + "\x00")
    if rollup is not None:
        (d / "smaps_rollup").write_text(rollup)


# --- parsing -------------------------------------------------------

def test_parse_rollup_extracts_rss_pss():
    r = proc_smaps.parse_rollup(_LIVE_ROLLUP)
    assert r["rss_kb"] == 21196356
    assert r["pss_kb"] == 21193171


def test_parse_rollup_extracts_breakdown():
    r = proc_smaps.parse_rollup(_LIVE_ROLLUP)
    assert r["pss_anon_kb"] == 11856248
    assert r["pss_file_kb"] == 9336923
    assert r["pss_shmem_kb"] == 0
    assert r["swap_kb"] == 0
    assert r["anonymous_kb"] == 11856248


def test_parse_rollup_handles_empty():
    assert proc_smaps.parse_rollup("") == {}


def test_parse_rollup_ignores_unknown_keys():
    r = proc_smaps.parse_rollup("WeirdKey:    1234 kB\nRss:    100 kB\n")
    assert r["rss_kb"] == 100
    assert "weirdkey_kb" not in r


def test_parse_rollup_swap_present():
    txt = "Rss:  100 kB\nSwap:  2048 kB\nSwapPss:  1024 kB\n"
    r = proc_smaps.parse_rollup(txt)
    assert r["swap_kb"] == 2048
    assert r["swap_pss_kb"] == 1024


# --- classify ------------------------------------------------------

def test_classify_healthy_no_swap():
    v = proc_smaps.classify({
        "rss_kb": 21196356,
        "pss_kb": 21193171,
        "pss_anon_kb": 11856248,
        "pss_file_kb": 9336923,
        "swap_kb": 0,
    })
    assert v["verdict"] == "ok"
    assert v["recommendation"] == ""


def test_classify_swap_pressure():
    v = proc_smaps.classify({
        "rss_kb": 10_000_000,
        "pss_kb": 9_000_000,
        "pss_anon_kb": 5_000_000,
        "pss_file_kb": 4_000_000,
        "swap_kb": 2_000_000,  # 2 GiB swap usage
    })
    assert v["verdict"] == "swapping"
    assert "swap" in v["reason"].lower()
    assert "mlock" in v["recommendation"].lower() or "swap" in v["recommendation"].lower()


def test_classify_unmapped_when_file_share_low():
    # Anon-heavy with tiny file share → model wasn't mmap'd, or it
    # was kicked out of page cache entirely
    v = proc_smaps.classify({
        "rss_kb": 20_000_000,
        "pss_kb": 19_500_000,
        "pss_anon_kb": 19_000_000,  # almost all anon
        "pss_file_kb": 100_000,     # tiny file share (~100 MiB)
        "swap_kb": 0,
    })
    assert v["verdict"] == "mmap_evicted"
    assert "page cache" in v["reason"].lower() or "mmap" in v["reason"].lower()


def test_classify_huge_anon_dominant_normal():
    # Big anon share with significant file share too → normal LLM
    # inference (KV cache anon + GGUF file)
    v = proc_smaps.classify({
        "rss_kb": 21_000_000,
        "pss_kb": 21_000_000,
        "pss_anon_kb": 12_000_000,
        "pss_file_kb": 9_000_000,  # 43 % file → normal
        "swap_kb": 0,
    })
    assert v["verdict"] == "ok"


def test_classify_unknown_when_no_rollup():
    v = proc_smaps.classify({})
    assert v["verdict"] == "unreadable"
    assert "rollup" in v["reason"].lower() or "permission" in v["reason"].lower()


def test_classify_unreadable_when_only_rss():
    v = proc_smaps.classify({"rss_kb": 1000})
    # Insufficient fields → unreadable (can't reason about breakdown)
    assert v["verdict"] in ("unreadable", "ok")


# --- status -------------------------------------------------------

def test_status_no_llm_procs(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="systemd",
             cmdline="/usr/lib/systemd/systemd")
    monkeypatch.setattr(proc_smaps, "_PROC", str(tmp_path))
    s = proc_smaps.status()
    assert s["ok"] is True
    assert s["worst_verdict"] == "no_llm_procs"
    assert s["processes"] == []


def test_status_full_live_breakdown(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 2106872, comm="llama-server",
             cmdline="/home/olivier/llama.cpp/build/bin/llama-server")
    monkeypatch.setattr(proc_smaps, "_PROC", str(tmp_path))
    s = proc_smaps.status()
    assert s["process_count"] == 1
    p = s["processes"][0]
    assert p["pid"] == 2106872
    assert p["rss_bytes"] == 21196356 * 1024
    assert p["pss_file_bytes"] == 9336923 * 1024
    assert p["pss_anon_bytes"] == 11856248 * 1024
    assert p["verdict"]["verdict"] == "ok"


def test_status_unreadable_rollup_present_but_empty(tmp_path, monkeypatch):
    # Root-owned daemon: smaps_rollup readable but unparseable / empty
    # for non-root callers
    _mk_proc(tmp_path, 1950, comm="ollama",
             cmdline="/usr/local/bin/ollama serve",
             rollup="")
    monkeypatch.setattr(proc_smaps, "_PROC", str(tmp_path))
    s = proc_smaps.status()
    p = s["processes"][0]
    assert p["verdict"]["verdict"] == "unreadable"


def test_status_rollup_file_missing(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1950, comm="ollama",
             cmdline="/usr/local/bin/ollama serve",
             rollup=None)  # No smaps_rollup file at all
    monkeypatch.setattr(proc_smaps, "_PROC", str(tmp_path))
    s = proc_smaps.status()
    p = s["processes"][0]
    assert p["verdict"]["verdict"] == "unreadable"


def test_status_picks_worst_across_procs(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="ollama", cmdline="ollama serve")
    _mk_proc(tmp_path, 2, comm="llama-server",
             cmdline="llama-server --model x.gguf",
             rollup="Rss: 10000000 kB\nPss: 9000000 kB\n"
                    "Pss_Anon: 5000000 kB\nPss_File: 4000000 kB\n"
                    "Swap: 3000000 kB\n")
    monkeypatch.setattr(proc_smaps, "_PROC", str(tmp_path))
    s = proc_smaps.status()
    assert s["worst_verdict"] == "swapping"


def test_status_aggregates_totals(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server",
             cmdline="llama-server --model a.gguf")
    _mk_proc(tmp_path, 2, comm="ollama",
             cmdline="ollama serve",
             rollup="Rss:   1000000 kB\nPss:   1000000 kB\n"
                    "Pss_Anon: 500000 kB\nPss_File: 500000 kB\n"
                    "Swap: 0 kB\n")
    monkeypatch.setattr(proc_smaps, "_PROC", str(tmp_path))
    s = proc_smaps.status()
    # Total RSS = 21.2 GiB + 1 GiB = 22.2 GiB
    assert s["total_rss_bytes"] == (21196356 + 1_000_000) * 1024


def test_is_llm_proc_matches_ollama():
    assert proc_smaps.is_llm_proc("ollama", "/usr/local/bin/ollama serve")


def test_is_llm_proc_rejects_systemd():
    assert not proc_smaps.is_llm_proc("systemd", "/usr/lib/systemd/systemd")
