"""Tests for modules/proc_task_affinity_audit.py — R&D #62.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import proc_task_affinity_audit as mod


# --- parse_cpu_list ---------------------------------------------

def test_parse_cpu_list_range():
    assert mod.parse_cpu_list("0-3") == {0, 1, 2, 3}


def test_parse_cpu_list_complex():
    assert mod.parse_cpu_list("0-3,8-11") == {
        0, 1, 2, 3, 8, 9, 10, 11}


def test_parse_cpu_list_single():
    assert mod.parse_cpu_list("0") == {0}


def test_parse_cpu_list_empty():
    assert mod.parse_cpu_list("") == set()
    assert mod.parse_cpu_list(None) == set()


# --- parse_status -----------------------------------------------

def test_parse_status():
    text = ("Name:	llama-server\n"
              "Cpus_allowed_list:	0-11\n"
              "Mems_allowed_list:	0\n"
              "voluntary_ctxt_switches:	1200\n"
              "nonvoluntary_ctxt_switches:	100\n")
    out = mod.parse_status(text)
    assert out["Cpus_allowed_list"] == "0-11"
    assert out["Mems_allowed_list"] == "0"
    assert out["voluntary_ctxt_switches"] == 1200
    assert out["nonvoluntary_ctxt_switches"] == 100


def test_parse_status_empty():
    out = mod.parse_status("")
    assert all(v is None for v in out.values())


# --- find_nvidia_gpus -------------------------------------------

def _mk_pci(root, bdf, vendor, klass, local_cpulist="0-11",
              numa_node=-1):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "class").write_text(klass + "\n")
    (d / "local_cpulist").write_text(local_cpulist + "\n")
    (d / "numa_node").write_text(f"{numa_node}\n")


def test_find_nvidia_gpus(tmp_path):
    _mk_pci(tmp_path, "0000:01:00.0", "0x10de", "0x030000")
    out = mod.find_nvidia_gpus(str(tmp_path))
    assert len(out) == 1
    assert out[0]["local_cpus"] == set(range(12))


def test_find_nvidia_gpus_skips_audio(tmp_path):
    _mk_pci(tmp_path, "0000:01:00.1", "0x10de", "0x040300")
    assert mod.find_nvidia_gpus(str(tmp_path)) == []


# --- find_llm_processes -----------------------------------------

def test_find_llm_processes(tmp_path):
    self_d = tmp_path / "self"
    self_d.mkdir()
    (self_d / "status").write_text(
        "Cpus_allowed_list:\t0-11\nMems_allowed_list:\t0\n"
        "voluntary_ctxt_switches:\t0\n"
        "nonvoluntary_ctxt_switches:\t0\n")

    for pid, comm, cpus in [
        ("100", "llama-server", "0-11"),
        ("200", "ollama", "0-11"),
        ("300", "bash", "0-11"),
    ]:
        d = tmp_path / pid
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "status").write_text(
            f"Cpus_allowed_list:\t{cpus}\n"
            "Mems_allowed_list:\t0\n"
            "voluntary_ctxt_switches:\t100\n"
            "nonvoluntary_ctxt_switches:\t10\n")

    out = mod.find_llm_processes(str(tmp_path))
    pids = [c["pid"] for c in out]
    assert 100 in pids
    assert 200 in pids
    assert 300 not in pids


# --- classify ---------------------------------------------------

def _cand(pid=100, comm="llama-server", cpus="0-11", mems="0",
           vcs=100, nvcs=10):
    return {"pid": pid, "comm": comm,
              "status": {"Cpus_allowed_list": cpus,
                            "Mems_allowed_list": mems,
                            "voluntary_ctxt_switches": vcs,
                            "nonvoluntary_ctxt_switches": nvcs}}


def _gpu(local_cpulist="0-11", numa_node=-1):
    return {"bdf": "0000:01:00.0",
              "local_cpulist": local_cpulist,
              "numa_node": numa_node,
              "local_cpus": mod.parse_cpu_list(local_cpulist)}


def test_classify_unknown():
    v = mod.classify([], [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_cand()], [_gpu()])
    assert v["verdict"] == "ok"


def test_classify_affinity_excludes():
    v = mod.classify(
        [_cand(cpus="20-23")],
        [_gpu(local_cpulist="0-15")])
    assert v["verdict"] == "affinity_excludes_local_numa"


def test_classify_mems_remote_only():
    v = mod.classify(
        [_cand(cpus="0-11", mems="1")],
        [_gpu(local_cpulist="0-11", numa_node=0)])
    assert v["verdict"] == "mems_allowed_remote_only"


def test_classify_narrow_nvcs():
    v = mod.classify(
        [_cand(cpus="0-1", nvcs=50_000)],
        [_gpu()])
    assert v["verdict"] == "narrow_cpuset_high_nvcs"


def test_classify_priority_affinity_wins():
    v = mod.classify(
        [_cand(cpus="20-23", mems="1", nvcs=50_000)],
        [_gpu(local_cpulist="0-15", numa_node=0)])
    assert v["verdict"] == "affinity_excludes_local_numa"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "noproc"),
                       str(tmp_path / "nopci"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
