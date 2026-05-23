"""Tests for modules/cgroup_memcap.py — R&D #32.5 cgroup-v2 memcap scanner."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cgroup_memcap


def _mk_proc(root: Path, pid: int, *, comm: str, cmdline: str,
               cgroup_text: str = "0::/system.slice/ollama.service\n"):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00") + "\x00")
    (d / "cgroup").write_text(cgroup_text)


def _mk_cgroup(cg_root: Path, path: str, *,
                  memory_max: str = "max", memory_high: str = "max",
                  memory_low: str = "0", memory_current: str = "0",
                  memory_swap_max: str = "max",
                  memory_swap_current: str = "0",
                  oom_kill: int = 0):
    d = cg_root / path.lstrip("/")
    d.mkdir(parents=True)
    (d / "memory.max").write_text(memory_max + "\n")
    (d / "memory.high").write_text(memory_high + "\n")
    (d / "memory.low").write_text(memory_low + "\n")
    (d / "memory.current").write_text(memory_current + "\n")
    (d / "memory.swap.max").write_text(memory_swap_max + "\n")
    (d / "memory.swap.current").write_text(memory_swap_current + "\n")
    (d / "memory.events").write_text(
        f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom_kill}\n"
    )


# --- helpers --------------------------------------------------------

def test_parse_cgroup_path_v2():
    txt = "0::/system.slice/ollama.service\n"
    assert cgroup_memcap.parse_cgroup_path(txt) == "/system.slice/ollama.service"


def test_parse_cgroup_path_v2_root():
    assert cgroup_memcap.parse_cgroup_path("0::/\n") == "/"


def test_parse_cgroup_path_empty():
    assert cgroup_memcap.parse_cgroup_path("") is None


def test_parse_cgroup_path_v1_fallback():
    # Hybrid cgroup mode → multiple lines, find the v2 line first
    txt = ("12:freezer:/\n"
           "0::/system.slice/llama-server.service\n")
    assert cgroup_memcap.parse_cgroup_path(txt) == "/system.slice/llama-server.service"


def test_read_memory_max_returns_int_for_value(tmp_path):
    _mk_cgroup(tmp_path, "/test", memory_max="1073741824")
    v = cgroup_memcap.read_memory_field(str(tmp_path), "/test", "memory.max")
    assert v == 1073741824


def test_read_memory_max_returns_sentinel_for_max(tmp_path):
    _mk_cgroup(tmp_path, "/test", memory_max="max")
    v = cgroup_memcap.read_memory_field(str(tmp_path), "/test", "memory.max")
    assert v == cgroup_memcap.MAX_SENTINEL


def test_read_memory_field_missing_returns_none(tmp_path):
    assert cgroup_memcap.read_memory_field(str(tmp_path), "/absent",
                                              "memory.max") is None


def test_read_events_parses_oom_kill(tmp_path):
    _mk_cgroup(tmp_path, "/test", oom_kill=3)
    events = cgroup_memcap.read_memory_events(str(tmp_path), "/test")
    assert events["oom_kill"] == 3
    assert events["max"] == 0


def test_read_events_missing(tmp_path):
    assert cgroup_memcap.read_memory_events(str(tmp_path), "/absent") == {}


# --- classify ------------------------------------------------------

def test_classify_uncapped_is_ok():
    v = cgroup_memcap.classify(memory_max=cgroup_memcap.MAX_SENTINEL,
                                  memory_high=cgroup_memcap.MAX_SENTINEL,
                                  memory_current=21_000_000_000,
                                  memory_swap_current=0,
                                  events={"oom_kill": 0, "max": 0})
    assert v["verdict"] == "uncapped"
    assert v["recommendation"] == ""


def test_classify_capped_below_model():
    # Default systemd MemoryMax can land at small values; user loads 16G
    v = cgroup_memcap.classify(memory_max=4 * 1024**3,   # 4 GiB cap
                                  memory_high=cgroup_memcap.MAX_SENTINEL,
                                  memory_current=3 * 1024**3,  # 3 GiB used
                                  memory_swap_current=0,
                                  events={"oom_kill": 0, "max": 0})
    assert v["verdict"] == "capped_below_model"
    assert "MemoryMax" in v["recommendation"]


def test_classify_capped_tight_high_pressure():
    # Cap is uncomfortably close (within 20%) to current usage
    v = cgroup_memcap.classify(memory_max=16 * 1024**3,
                                  memory_high=cgroup_memcap.MAX_SENTINEL,
                                  memory_current=14 * 1024**3,
                                  memory_swap_current=0,
                                  events={"oom_kill": 0, "max": 0})
    assert v["verdict"] == "capped_tight"


def test_classify_oom_killed():
    # The smoking gun: max > 0 in memory.events means we hit the cap
    v = cgroup_memcap.classify(memory_max=8 * 1024**3,
                                  memory_high=cgroup_memcap.MAX_SENTINEL,
                                  memory_current=7 * 1024**3,
                                  memory_swap_current=0,
                                  events={"oom_kill": 0, "max": 5})
    assert v["verdict"] == "oom_capped"


def test_classify_oom_kill_event():
    # OOM-kill happened inside cgroup
    v = cgroup_memcap.classify(memory_max=cgroup_memcap.MAX_SENTINEL,
                                  memory_high=cgroup_memcap.MAX_SENTINEL,
                                  memory_current=4 * 1024**3,
                                  memory_swap_current=0,
                                  events={"oom_kill": 2, "max": 0})
    assert v["verdict"] == "oom_killed"


def test_classify_swap_in_cgroup_warns():
    # The live-rig case: uncapped, but swap.current > 0 — the kernel
    # swap (host-wide) is hitting this daemon
    v = cgroup_memcap.classify(memory_max=cgroup_memcap.MAX_SENTINEL,
                                  memory_high=cgroup_memcap.MAX_SENTINEL,
                                  memory_current=21 * 1024**3,
                                  memory_swap_current=1_893_871_616,
                                  events={"oom_kill": 0, "max": 0})
    assert v["verdict"] == "swap_active"
    assert "swappiness" in v["recommendation"].lower() or "swap" in v["recommendation"].lower()


def test_classify_memory_high_throttle():
    # memory.high is a soft cap that triggers reclaim ; if current is
    # above it, the kernel is actively reclaiming this daemon
    v = cgroup_memcap.classify(memory_max=cgroup_memcap.MAX_SENTINEL,
                                  memory_high=8 * 1024**3,
                                  memory_current=10 * 1024**3,
                                  memory_swap_current=0,
                                  events={"oom_kill": 0, "max": 0})
    assert v["verdict"] == "memory_high_throttle"


# --- status -------------------------------------------------------

def test_status_no_llm_procs(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="systemd",
             cmdline="/usr/lib/systemd/systemd")
    monkeypatch.setattr(cgroup_memcap, "_PROC", str(tmp_path))
    monkeypatch.setattr(cgroup_memcap, "_CGROUP_ROOT", str(tmp_path))
    s = cgroup_memcap.status()
    assert s["worst_verdict"] == "no_llm_procs"


def test_status_uncapped_llm_daemon(tmp_path, monkeypatch):
    # llama-server with no cgroup memory cap (the live-rig state)
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    _mk_proc(proc, 2106872, comm="llama-server",
             cmdline="/llama.cpp/llama-server",
             cgroup_text="0::/system.slice/llama-server.service\n")
    _mk_cgroup(cg, "/system.slice/llama-server.service",
               memory_max="max", memory_current="21941313536",
               memory_swap_current="0")
    monkeypatch.setattr(cgroup_memcap, "_PROC", str(proc))
    monkeypatch.setattr(cgroup_memcap, "_CGROUP_ROOT", str(cg))
    s = cgroup_memcap.status()
    assert s["worst_verdict"] == "uncapped"
    p = s["processes"][0]
    assert p["memory_max"] == cgroup_memcap.MAX_SENTINEL
    assert p["memory_current"] == 21941313536


def test_status_swap_active_picks_warn(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    _mk_proc(proc, 2106872, comm="llama-server",
             cmdline="/llama.cpp/llama-server",
             cgroup_text="0::/system.slice/llama-server.service\n")
    _mk_cgroup(cg, "/system.slice/llama-server.service",
               memory_max="max", memory_current="21941313536",
               memory_swap_current="1893871616")
    monkeypatch.setattr(cgroup_memcap, "_PROC", str(proc))
    monkeypatch.setattr(cgroup_memcap, "_CGROUP_ROOT", str(cg))
    s = cgroup_memcap.status()
    assert s["worst_verdict"] == "swap_active"


def test_status_oom_capped_is_worst(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    _mk_proc(proc, 1, comm="ollama", cmdline="ollama serve",
             cgroup_text="0::/system.slice/ollama.service\n")
    _mk_proc(proc, 2, comm="llama-server",
             cmdline="llama-server --model x",
             cgroup_text="0::/system.slice/llama-server.service\n")
    _mk_cgroup(cg, "/system.slice/ollama.service",
               memory_max="max", memory_current="1000000",
               memory_swap_current="0")
    _mk_cgroup(cg, "/system.slice/llama-server.service",
               memory_max="8589934592",   # 8 GiB
               memory_current="8000000000",
               memory_swap_current="0",
               oom_kill=2)
    monkeypatch.setattr(cgroup_memcap, "_PROC", str(proc))
    monkeypatch.setattr(cgroup_memcap, "_CGROUP_ROOT", str(cg))
    s = cgroup_memcap.status()
    assert s["worst_verdict"] == "oom_killed"


def test_status_cgroup_path_unresolvable(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    cg.mkdir()
    _mk_proc(proc, 1, comm="ollama", cmdline="ollama serve",
             cgroup_text="0::/system.slice/nonexistent.service\n")
    monkeypatch.setattr(cgroup_memcap, "_PROC", str(proc))
    monkeypatch.setattr(cgroup_memcap, "_CGROUP_ROOT", str(cg))
    s = cgroup_memcap.status()
    p = s["processes"][0]
    assert p["verdict"]["verdict"] == "unknown"


def test_recipe_contains_systemd_drop_in(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    _mk_proc(proc, 1, comm="llama-server",
             cmdline="llama-server --model x",
             cgroup_text="0::/system.slice/llama-server.service\n")
    _mk_cgroup(cg, "/system.slice/llama-server.service",
               memory_max="4294967296",     # 4 GiB
               memory_current="3000000000",
               memory_swap_current="0")
    monkeypatch.setattr(cgroup_memcap, "_PROC", str(proc))
    monkeypatch.setattr(cgroup_memcap, "_CGROUP_ROOT", str(cg))
    s = cgroup_memcap.status()
    rec = s["processes"][0]["verdict"]["recommendation"]
    assert "MemoryMax" in rec
    assert "/etc/systemd/system/llama-server.service.d" in rec or "Drop-In" in rec


def test_is_llm_proc_matches_ollama():
    assert cgroup_memcap.is_llm_proc("ollama", "/usr/local/bin/ollama serve")
