"""Tests for modules/proc_ns_mountinfo_audit.py — R&D #64.3."""
from __future__ import annotations

import os
import pytest

from gpu_dashboard.modules import proc_ns_mountinfo_audit as mod


def _mk_proc(root, pid, *, comm="llama-server",
                ns_inodes=None, mountinfo=""):
    """Set up a synthetic /proc/<pid> dir."""
    pid_dir = root / str(pid)
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / "comm").write_text(comm + "\n")
    (pid_dir / "mountinfo").write_text(mountinfo)
    ns = pid_dir / "ns"
    ns.mkdir(parents=True, exist_ok=True)
    defaults = {"mnt": "4026531832", "pid": "4026531836",
                  "net": "4026531833", "user": "4026531837",
                  "uts": "4026531838", "ipc": "4026531839",
                  "cgroup": "4026531835", "time": "4026531834"}
    inodes = {**defaults, **(ns_inodes or {})}
    for k, ino in inodes.items():
        link = ns / k
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(f"{k}:[{ino}]", str(link))


# --- read_ns ----------------------------------------------------

def test_read_ns(tmp_path):
    _mk_proc(tmp_path, 100)
    ns = mod.read_ns(str(tmp_path), "100")
    assert ns["mnt"] == "4026531832"
    assert ns["net"] == "4026531833"


def test_read_ns_missing(tmp_path):
    ns = mod.read_ns(str(tmp_path), "999")
    assert all(v is None for v in ns.values())


# --- has_nvidia_in_mountinfo ------------------------------------

def test_has_nvidia_yes(tmp_path):
    _mk_proc(tmp_path, 100,
              mountinfo="42 33 - char /dev/nvidia0 ro\n")
    assert mod.has_nvidia_in_mountinfo(str(tmp_path), "100") is True


def test_has_nvidia_no(tmp_path):
    _mk_proc(tmp_path, 100,
              mountinfo="42 33 - tmpfs /tmp\n")
    assert mod.has_nvidia_in_mountinfo(str(tmp_path), "100") is False


# --- find_llm_processes -----------------------------------------

def test_find_llm_processes(tmp_path):
    _mk_proc(tmp_path, 100, comm="llama-server")
    _mk_proc(tmp_path, 200, comm="bash")
    _mk_proc(tmp_path, 300, comm="ollama")
    out = mod.find_llm_processes(str(tmp_path))
    pids = sorted(c["pid"] for c in out)
    assert pids == [100, 300]


# --- classify ---------------------------------------------------

_SAME_NS = {"mnt": "4026531832", "pid": "4026531836",
              "net": "4026531833", "user": "4026531837",
              "uts": "4026531838", "ipc": "4026531839",
              "cgroup": "4026531835", "time": "4026531834"}


def _c(pid=100, comm="llama-server", ns=None, has_nvidia=True):
    return {"pid": pid, "comm": comm,
              "ns": ns or dict(_SAME_NS),
              "has_nvidia": has_nvidia}


def test_classify_unknown():
    v = mod.classify({}, [], host_has_nv=True)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_SAME_NS, [_c()], host_has_nv=True)
    assert v["verdict"] == "ok"


def test_classify_different_mnt():
    diff = dict(_SAME_NS)
    diff["mnt"] = "9999999999"
    v = mod.classify(_SAME_NS, [_c(ns=diff)],
                       host_has_nv=True)
    assert v["verdict"] == "cuda_pid_in_different_mnt_ns"


def test_classify_different_net():
    diff = dict(_SAME_NS)
    diff["net"] = "8888888888"
    v = mod.classify(_SAME_NS, [_c(ns=diff)],
                       host_has_nv=True)
    assert v["verdict"] == "netns_split_for_nccl"


def test_classify_nvidia_hidden():
    v = mod.classify(_SAME_NS, [_c(has_nvidia=False)],
                       host_has_nv=True)
    assert v["verdict"] == "nvidia_uvm_hidden_by_bind"


def test_classify_priority_mnt_wins():
    diff = dict(_SAME_NS)
    diff["mnt"] = "9999999999"
    diff["net"] = "8888888888"
    v = mod.classify(_SAME_NS, [_c(ns=diff, has_nvidia=False)],
                       host_has_nv=True)
    assert v["verdict"] == "cuda_pid_in_different_mnt_ns"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "noproc"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    # Set up /proc/self + /proc/100 in the same namespace
    _mk_proc(tmp_path, "self",
                comm="dashboard",
                mountinfo="42 33 - char /dev/nvidia0 ro\n")
    _mk_proc(tmp_path, "100", comm="llama-server",
                mountinfo="42 33 - char /dev/nvidia0 ro\n")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["candidate_count"] == 1
    assert out["host_has_nvidia"] is True
    assert out["verdict"]["verdict"] == "ok"
