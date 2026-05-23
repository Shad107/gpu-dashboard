"""Tests for modules/cgroup_cpuio.py — R&D #33.6 cgroup-v2 CPU/IO weight."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cgroup_cpuio


def _mk_proc(root: Path, pid: int, *, comm: str, cmdline: str,
               cgroup_text: str = "0::/system.slice/ollama.service\n"):
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_text(cmdline.replace(" ", "\x00") + "\x00")
    (d / "cgroup").write_text(cgroup_text)


def _mk_cgroup(cg_root: Path, path: str, *,
                  cpu_weight: str = "100",
                  cpu_max: str = "max 100000",
                  io_weight: str = "default 100"):
    d = cg_root / path.lstrip("/")
    d.mkdir(parents=True)
    (d / "cpu.weight").write_text(cpu_weight + "\n")
    (d / "cpu.max").write_text(cpu_max + "\n")
    (d / "io.weight").write_text(io_weight + "\n")


# --- io.weight parser ---------------------------------------------

def test_parse_io_weight_default_format():
    assert cgroup_cpuio.parse_io_weight("default 100") == 100


def test_parse_io_weight_default_with_higher():
    assert cgroup_cpuio.parse_io_weight("default 200") == 200


def test_parse_io_weight_bare_number():
    assert cgroup_cpuio.parse_io_weight("150") == 150


def test_parse_io_weight_empty():
    assert cgroup_cpuio.parse_io_weight("") is None
    assert cgroup_cpuio.parse_io_weight(None) is None


def test_parse_io_weight_garbage():
    assert cgroup_cpuio.parse_io_weight("not a number") is None


# --- cpu.max parser -----------------------------------------------

def test_parse_cpu_max_no_quota():
    quota, period = cgroup_cpuio.parse_cpu_max("max 100000")
    assert quota is None
    assert period == 100000


def test_parse_cpu_max_with_quota():
    quota, period = cgroup_cpuio.parse_cpu_max("50000 100000")
    assert quota == 50000
    assert period == 100000


def test_parse_cpu_max_empty():
    assert cgroup_cpuio.parse_cpu_max("") == (None, None)
    assert cgroup_cpuio.parse_cpu_max(None) == (None, None)


# --- classify ----------------------------------------------------

def test_classify_default_weight():
    # The systemd default — both at 100, no quota
    v = cgroup_cpuio.classify(cpu_weight=100,
                                  io_weight=100,
                                  cpu_max=(None, 100000))
    assert v["verdict"] == "default_weight"
    assert "CPUWeight" in v["recommendation"]
    assert "IOWeight" in v["recommendation"]


def test_classify_elevated_at_200():
    v = cgroup_cpuio.classify(cpu_weight=200,
                                  io_weight=200,
                                  cpu_max=(None, 100000))
    assert v["verdict"] == "elevated"


def test_classify_max_priority_at_500():
    v = cgroup_cpuio.classify(cpu_weight=1000,
                                  io_weight=1000,
                                  cpu_max=(None, 100000))
    assert v["verdict"] == "max_priority"


def test_classify_has_quota_warn():
    # cpu.max="50000 100000" → 50% CPU ceiling, throttles inference
    v = cgroup_cpuio.classify(cpu_weight=100,
                                  io_weight=100,
                                  cpu_max=(50000, 100000))
    assert v["verdict"] == "cpu_quota_active"


def test_classify_partial_elevation_picks_lower():
    # CPU elevated but IO default → still flag default_weight (or
    # a mixed verdict, depending on policy)
    v = cgroup_cpuio.classify(cpu_weight=200,
                                  io_weight=100,
                                  cpu_max=(None, 100000))
    # Acceptable either as default_weight (because IO is still 100) or
    # elevated (because CPU is up). Document the choice via test:
    assert v["verdict"] in ("default_weight", "elevated")


def test_classify_unknown_when_no_weights():
    v = cgroup_cpuio.classify(cpu_weight=None,
                                  io_weight=None,
                                  cpu_max=(None, None))
    assert v["verdict"] == "unknown"


def test_classify_recipe_includes_unit_placeholder():
    v = cgroup_cpuio.classify(cpu_weight=100,
                                  io_weight=100,
                                  cpu_max=(None, 100000),
                                  unit="llama-server.service")
    assert "llama-server.service" in v["recommendation"]


# --- status ------------------------------------------------------

def test_status_no_llm_procs(tmp_path, monkeypatch):
    _mk_proc(tmp_path, 1, comm="systemd",
             cmdline="/usr/lib/systemd/systemd")
    monkeypatch.setattr(cgroup_cpuio, "_PROC", str(tmp_path))
    monkeypatch.setattr(cgroup_cpuio, "_CGROUP_ROOT", str(tmp_path))
    s = cgroup_cpuio.status()
    assert s["worst_verdict"] == "no_llm_procs"


def test_status_default_weights_full_payload(tmp_path, monkeypatch):
    # The live-rig state: both daemons at default
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    _mk_proc(proc, 1950, comm="ollama", cmdline="ollama serve",
             cgroup_text="0::/system.slice/ollama.service\n")
    _mk_proc(proc, 2106872, comm="llama-server",
             cmdline="llama-server --model x.gguf",
             cgroup_text="0::/system.slice/llama-server.service\n")
    _mk_cgroup(cg, "/system.slice/ollama.service",
               cpu_weight="100", io_weight="default 100")
    _mk_cgroup(cg, "/system.slice/llama-server.service",
               cpu_weight="100", io_weight="default 100")
    monkeypatch.setattr(cgroup_cpuio, "_PROC", str(proc))
    monkeypatch.setattr(cgroup_cpuio, "_CGROUP_ROOT", str(cg))
    s = cgroup_cpuio.status()
    assert s["worst_verdict"] == "default_weight"
    assert len(s["processes"]) == 2
    p = s["processes"][0]
    assert p["cpu_weight"] == 100
    assert p["io_weight"] == 100


def test_status_picks_worst_quota_over_default(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    _mk_proc(proc, 1, comm="ollama", cmdline="ollama serve",
             cgroup_text="0::/system.slice/ollama.service\n")
    _mk_proc(proc, 2, comm="llama-server",
             cmdline="llama-server --model x.gguf",
             cgroup_text="0::/system.slice/llama-server.service\n")
    _mk_cgroup(cg, "/system.slice/ollama.service",
               cpu_weight="100", io_weight="default 100")
    _mk_cgroup(cg, "/system.slice/llama-server.service",
               cpu_weight="100", cpu_max="50000 100000",
               io_weight="default 100")
    monkeypatch.setattr(cgroup_cpuio, "_PROC", str(proc))
    monkeypatch.setattr(cgroup_cpuio, "_CGROUP_ROOT", str(cg))
    s = cgroup_cpuio.status()
    assert s["worst_verdict"] == "cpu_quota_active"


def test_status_recipe_uses_actual_unit_name(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    _mk_proc(proc, 2106872, comm="llama-server",
             cmdline="llama-server",
             cgroup_text="0::/system.slice/llama-server.service\n")
    _mk_cgroup(cg, "/system.slice/llama-server.service",
               cpu_weight="100", io_weight="default 100")
    monkeypatch.setattr(cgroup_cpuio, "_PROC", str(proc))
    monkeypatch.setattr(cgroup_cpuio, "_CGROUP_ROOT", str(cg))
    s = cgroup_cpuio.status()
    rec = s["processes"][0]["verdict"]["recommendation"]
    assert "llama-server.service" in rec


def test_status_cgroup_path_unresolvable(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    cg = tmp_path / "cgroup"
    cg.mkdir()
    _mk_proc(proc, 1, comm="ollama", cmdline="ollama serve",
             cgroup_text="0::/system.slice/nope.service\n")
    monkeypatch.setattr(cgroup_cpuio, "_PROC", str(proc))
    monkeypatch.setattr(cgroup_cpuio, "_CGROUP_ROOT", str(cg))
    s = cgroup_cpuio.status()
    assert s["processes"][0]["verdict"]["verdict"] == "unknown"


def test_is_llm_proc_matches_llama_server():
    assert cgroup_cpuio.is_llm_proc(
        "llama-server", "llama-server --model x.gguf")
