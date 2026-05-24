"""Tests for modules/cpu_dma_latency_qos_audit.py — R&D #82.2."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import cpu_dma_latency_qos_audit as mod


def _mk_cpu(root, idx, *, latency=0, with_cpuidle=True,
              state_count=3):
    d = root / f"cpu{idx}"
    d.mkdir(parents=True, exist_ok=True)
    power = d / "power"
    power.mkdir(exist_ok=True)
    (power / "pm_qos_resume_latency_us").write_text(
        f"{latency}\n")
    if with_cpuidle:
        cpuidle = d / "cpuidle"
        cpuidle.mkdir(exist_ok=True)
        for i in range(state_count):
            (cpuidle / f"state{i}").mkdir(exist_ok=True)


def _mk_holder(tmp_path, pid, *, comm="evilproc",
                fds=None):
    """fds: list of (fd_num, target_path)"""
    d = tmp_path / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n")
    fd_dir = d / "fd"
    fd_dir.mkdir(exist_ok=True)
    for fd, target in (fds or []):
        os.symlink(target, str(fd_dir / str(fd)))


# --- list_cpu_dirs ---------------------------------------------

def test_list_cpu_dirs_missing(tmp_path):
    assert mod.list_cpu_dirs(str(tmp_path / "nope")) == []


def test_list_cpu_dirs_basic(tmp_path):
    _mk_cpu(tmp_path, 0)
    _mk_cpu(tmp_path, 1)
    (tmp_path / "cpuidle").mkdir()  # not cpu<N>
    out = mod.list_cpu_dirs(str(tmp_path))
    assert out == ["cpu0", "cpu1"]


# --- read_pm_qos -----------------------------------------------

def test_read_pm_qos_basic(tmp_path):
    _mk_cpu(tmp_path, 0, latency=0)
    _mk_cpu(tmp_path, 1, latency=100)
    out = mod.read_pm_qos(str(tmp_path))
    assert len(out) == 2
    assert out[0]["pm_qos_resume_latency_us"] == 0
    assert out[1]["pm_qos_resume_latency_us"] == 100
    assert out[0]["cpuidle_state_count"] == 3


def test_read_pm_qos_no_cpuidle(tmp_path):
    _mk_cpu(tmp_path, 0, with_cpuidle=False)
    out = mod.read_pm_qos(str(tmp_path))
    assert out[0]["cpuidle_state_count"] == 0


# --- find_dma_latency_holders ----------------------------------

def test_find_holders_missing(tmp_path):
    h, scanned, inacc = mod.find_dma_latency_holders(
        str(tmp_path / "nope"))
    assert h == []
    assert scanned == 0


def test_find_holders_one(tmp_path):
    dev = tmp_path / "fake_dev"
    dev.write_text("")
    _mk_holder(tmp_path, 100,
                  comm="pulseaudio",
                  fds=[(0, "/dev/null"),
                         (3, str(dev))])
    h, scanned, _ = mod.find_dma_latency_holders(
        str(tmp_path), str(dev))
    assert len(h) == 1
    assert h[0]["pid"] == 100
    assert h[0]["comm"] == "pulseaudio"


def test_find_holders_skips_non_matching(tmp_path):
    _mk_holder(tmp_path, 100,
                  comm="other",
                  fds=[(0, "/dev/null")])
    h, _, _ = mod.find_dma_latency_holders(
        str(tmp_path), "/dev/cpu_dma_latency")
    assert h == []


# --- classify --------------------------------------------------

def _cpu(name, latency=0, state_count=3):
    return {"cpu": name,
            "pm_qos_resume_latency_us": latency,
            "cpuidle_state_count": state_count}


def test_classify_unknown_no_cpus():
    v = mod.classify([], [], 0, 0)
    assert v["verdict"] == "unknown"


def test_classify_unknown_no_pm_qos():
    cpus = [{"cpu": "cpu0",
                "pm_qos_resume_latency_us": None,
                "cpuidle_state_count": 0}]
    v = mod.classify(cpus, [], 10, 0)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    cpus = [_cpu(f"cpu{i}") for i in range(4)]
    v = mod.classify(cpus, [], 100, 5)
    assert v["verdict"] == "ok"


def test_classify_no_cpuidle():
    cpus = [_cpu(f"cpu{i}", state_count=0) for i in range(4)]
    v = mod.classify(cpus, [], 100, 5)
    assert v["verdict"] == "no_cpuidle"


def test_classify_clamped_majority():
    cpus = [_cpu(f"cpu{i}", latency=100)
              for i in range(4)]
    v = mod.classify(cpus, [], 100, 5)
    assert v["verdict"] == "pm_qos_latency_clamped_majority"


def test_classify_clamped_50pct_not_majority():
    # exactly 50% clamped — accent mixed
    cpus = [
        _cpu("cpu0", latency=100),
        _cpu("cpu1", latency=100),
        _cpu("cpu2", latency=0),
        _cpu("cpu3", latency=0),
    ]
    v = mod.classify(cpus, [], 100, 5)
    assert v["verdict"] == "pm_qos_mixed"


def test_classify_external_holder():
    cpus = [_cpu(f"cpu{i}") for i in range(4)]
    v = mod.classify(
        cpus,
        [{"pid": 1234, "comm": "pulseaudio"}],
        100, 5)
    assert v["verdict"] == "cpu_dma_latency_held_external"
    assert v["pid"] == 1234


def test_classify_benign_holder_ok():
    cpus = [_cpu(f"cpu{i}") for i in range(4)]
    v = mod.classify(
        cpus,
        [{"pid": 1, "comm": "systemd"}],
        100, 5)
    assert v["verdict"] == "ok"


def test_classify_requires_root():
    cpus = [_cpu(f"cpu{i}") for i in range(4)]
    v = mod.classify(cpus, [], 0, 366)
    assert v["verdict"] == "requires_root"


# Priority : clamped_majority > external_holder > mixed > no_cpuidle
def test_priority_clamped_over_holder():
    cpus = [_cpu(f"cpu{i}", latency=100)
              for i in range(4)]
    v = mod.classify(
        cpus, [{"pid": 1, "comm": "pulseaudio"}],
        100, 0)
    assert v["verdict"] == "pm_qos_latency_clamped_majority"


def test_priority_holder_over_mixed():
    cpus = [
        _cpu("cpu0", latency=100),
        _cpu("cpu1", latency=0),
    ]
    v = mod.classify(
        cpus, [{"pid": 1, "comm": "pulseaudio"}],
        100, 0)
    assert v["verdict"] == "cpu_dma_latency_held_external"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_cpu"),
                       str(tmp_path / "no_proc"),
                       "/dev/cpu_dma_latency")
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    cpu_root = tmp_path / "sys"
    cpu_root.mkdir()
    for i in range(4):
        _mk_cpu(cpu_root, i, latency=0)
    proc = tmp_path / "proc"
    proc.mkdir()
    out = mod.status(None, str(cpu_root), str(proc),
                       "/dev/no_such")
    assert out["ok"] is True
    assert out["cpu_count"] == 4
    assert out["clamped_count"] == 0
    assert out["verdict"]["verdict"] == "ok"


def test_status_clamped_synthetic(tmp_path):
    cpu_root = tmp_path / "sys"
    cpu_root.mkdir()
    for i in range(4):
        _mk_cpu(cpu_root, i, latency=200)
    proc = tmp_path / "proc"
    proc.mkdir()
    out = mod.status(None, str(cpu_root), str(proc),
                       "/dev/no_such")
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "pm_qos_latency_clamped_majority")
