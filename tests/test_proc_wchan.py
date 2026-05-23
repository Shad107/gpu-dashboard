"""Tests for modules/proc_wchan.py — R&D #32.3 wchan inference-stuck debugger."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import proc_wchan


def _mk_proc(root: Path, pid: int, *, comm: str, cmdline: str,
               wchan: str = "0",
               state: str = "S",
               stack_lines: list | None = None):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00") + "\x00")
    (d / "wchan").write_text(wchan)  # no trailing newline (matches kernel)
    (d / "status").write_text(
        f"Name:   {comm}\n"
        f"State:  {state} ({proc_wchan._STATE_NAMES.get(state, 'unknown')})\n"
    )
    if stack_lines is not None:
        (d / "stack").write_text("\n".join(stack_lines) + "\n")


# --- field readers --------------------------------------------------

def test_read_wchan_returns_symbol(tmp_path):
    _mk_proc(tmp_path, 100, comm="ollama", cmdline="ollama serve",
             wchan="io_schedule")
    assert proc_wchan.read_wchan(100, str(tmp_path)) == "io_schedule"


def test_read_wchan_zero_returns_none(tmp_path):
    # Kernel writes literal "0" when no wait channel
    _mk_proc(tmp_path, 100, comm="ollama", cmdline="ollama serve",
             wchan="0")
    assert proc_wchan.read_wchan(100, str(tmp_path)) is None


def test_read_wchan_missing_returns_none(tmp_path):
    (tmp_path / "100").mkdir()
    assert proc_wchan.read_wchan(100, str(tmp_path)) is None


def test_read_state_returns_single_letter(tmp_path):
    _mk_proc(tmp_path, 100, comm="ollama", cmdline="ollama serve", state="D")
    assert proc_wchan.read_state(100, str(tmp_path)) == "D"


def test_read_state_missing_returns_none(tmp_path):
    assert proc_wchan.read_state(100, str(tmp_path)) is None


def test_read_stack_returns_lines(tmp_path):
    _mk_proc(tmp_path, 100, comm="ollama", cmdline="ollama serve",
             stack_lines=["[<0>] do_syscall_64", "[<0>] folio_wait_bit_common"])
    s = proc_wchan.read_stack(100, str(tmp_path))
    assert s is not None
    assert len(s) == 2


def test_read_stack_missing_returns_none(tmp_path):
    (tmp_path / "100").mkdir()
    assert proc_wchan.read_stack(100, str(tmp_path)) is None


# --- classify -------------------------------------------------------

def test_classify_running_is_ok():
    v = proc_wchan.classify(state="R", wchan=None)
    assert v["verdict"] == "running"


def test_classify_idle_sleep_is_ok():
    v = proc_wchan.classify(state="S", wchan=None)
    assert v["verdict"] == "idle"


def test_classify_normal_wait_futex():
    v = proc_wchan.classify(state="S", wchan="futex_wait_queue")
    assert v["verdict"] == "normal_wait"


def test_classify_normal_wait_poll():
    v = proc_wchan.classify(state="S", wchan="do_select")
    assert v["verdict"] == "normal_wait"


def test_classify_io_bound_io_schedule():
    v = proc_wchan.classify(state="D", wchan="io_schedule")
    assert v["verdict"] == "io_bound"
    assert "io" in v["reason"].lower()
    assert "nvme" in v["recommendation"].lower() or "iosched" in v["recommendation"].lower()


def test_classify_page_cache_wait_folio():
    v = proc_wchan.classify(state="D", wchan="folio_wait_bit_common")
    assert v["verdict"] == "page_cache_wait"
    assert "page cache" in v["reason"].lower() or "mmap" in v["reason"].lower()


def test_classify_mem_pressure_reclaim():
    v = proc_wchan.classify(state="D", wchan="memory_reclaim")
    assert v["verdict"] == "mem_pressure"
    assert "swap" in v["recommendation"].lower() or "swappiness" in v["recommendation"]


def test_classify_mem_pressure_shrink():
    v = proc_wchan.classify(state="D", wchan="shrink_node_memcgs")
    assert v["verdict"] == "mem_pressure"


def test_classify_d_state_unknown_wchan_is_blocked():
    v = proc_wchan.classify(state="D", wchan="some_obscure_kernel_func")
    assert v["verdict"] == "blocked"


def test_classify_zombie():
    v = proc_wchan.classify(state="Z", wchan=None)
    assert v["verdict"] == "zombie"


def test_classify_unknown_no_state():
    v = proc_wchan.classify(state=None, wchan=None)
    assert v["verdict"] == "unknown"


# --- is_llm_proc reused pattern -----------------------------------

def test_is_llm_proc_matches_llama_server():
    assert proc_wchan.is_llm_proc("llama-server", "llama-server --model x.gguf")


def test_is_llm_proc_rejects_random():
    assert not proc_wchan.is_llm_proc("bash", "/bin/bash")


# --- status -------------------------------------------------------

def test_status_no_llm_procs(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="systemd",
             cmdline="/usr/lib/systemd/systemd")
    monkeypatch.setattr(proc_wchan, "_PROC", str(tmp_path))
    s = proc_wchan.status()
    assert s["ok"] is True
    assert s["worst_verdict"] == "no_llm_procs"
    assert s["processes"] == []


def test_status_running_daemon_is_ok(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1950, comm="ollama",
             cmdline="/usr/local/bin/ollama serve",
             wchan="0", state="R")
    monkeypatch.setattr(proc_wchan, "_PROC", str(tmp_path))
    s = proc_wchan.status()
    assert s["worst_verdict"] == "running"
    p = s["processes"][0]
    assert p["state"] == "R"
    assert p["wchan"] is None


def test_status_io_bound_daemon(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1950, comm="llama-server",
             cmdline="llama-server --model x.gguf",
             wchan="io_schedule", state="D")
    monkeypatch.setattr(proc_wchan, "_PROC", str(tmp_path))
    s = proc_wchan.status()
    assert s["worst_verdict"] == "io_bound"


def test_status_picks_worst_across(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="ollama",
             cmdline="ollama serve",
             wchan="0", state="R")
    _mk_proc(tmp_path, 2, comm="llama-server",
             cmdline="llama-server --model x.gguf",
             wchan="folio_wait_bit_common", state="D")
    monkeypatch.setattr(proc_wchan, "_PROC", str(tmp_path))
    s = proc_wchan.status()
    assert s["worst_verdict"] == "page_cache_wait"


def test_status_includes_stack_when_readable(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="llama-server",
             cmdline="llama-server --model x.gguf",
             wchan="io_schedule", state="D",
             stack_lines=["[<0>] do_syscall_64",
                           "[<0>] folio_wait_bit_common"])
    monkeypatch.setattr(proc_wchan, "_PROC", str(tmp_path))
    s = proc_wchan.status()
    p = s["processes"][0]
    assert p["stack"] is not None
    assert len(p["stack"]) == 2


def test_status_missing_stack_is_handled(tmp_path, monkeypatch):
    # Permission-denied stack (typical for non-root) → field is None
    _mk_proc(tmp_path, 1, comm="ollama",
             cmdline="ollama serve",
             wchan="0", state="R", stack_lines=None)
    monkeypatch.setattr(proc_wchan, "_PROC", str(tmp_path))
    s = proc_wchan.status()
    assert s["processes"][0]["stack"] is None
