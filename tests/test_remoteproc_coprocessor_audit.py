"""Tests for modules/remoteproc_coprocessor_audit.py — R&D #70.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import remoteproc_coprocessor_audit as mod


def _mk_rproc(root, name, *, state="running", proc_name="m4",
                  firmware="cm4.elf", recovery="enabled",
                  crash_count=0):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "state").write_text(state + "\n")
    (d / "name").write_text(proc_name + "\n")
    (d / "firmware").write_text(firmware + "\n")
    (d / "recovery").write_text(recovery + "\n")
    (d / "crash_count").write_text(f"{crash_count}\n")


# --- list_remoteprocs ------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_remoteprocs(str(tmp_path / "nope")) == []


def test_list_one(tmp_path):
    _mk_rproc(tmp_path, "remoteproc0")
    out = mod.list_remoteprocs(str(tmp_path))
    assert len(out) == 1
    assert out[0]["id"] == "remoteproc0"
    assert out[0]["state"] == "running"
    assert out[0]["crash_count"] == 0


def test_list_two(tmp_path):
    _mk_rproc(tmp_path, "remoteproc0", state="running")
    _mk_rproc(tmp_path, "remoteproc1", state="crashed",
                 crash_count=3)
    out = mod.list_remoteprocs(str(tmp_path))
    assert len(out) == 2
    by_id = {r["id"]: r for r in out}
    assert by_id["remoteproc1"]["state"] == "crashed"


# --- classify ---------------------------------------------------

def test_classify_unknown_missing():
    v = mod.classify([], False)
    assert v["verdict"] == "unknown"


def test_classify_unknown_empty_dir():
    v = mod.classify([], True)
    assert v["verdict"] == "unknown"


def test_classify_crashed_state():
    v = mod.classify(
        [{"id": "remoteproc0", "state": "crashed",
            "name": "m4", "firmware": "cm4.elf",
            "recovery": "enabled", "crash_count": 0}],
        True)
    assert v["verdict"] == "remoteproc_crashed"


def test_classify_crashed_count():
    v = mod.classify(
        [{"id": "remoteproc0", "state": "running",
            "name": "m4", "firmware": "cm4.elf",
            "recovery": "enabled", "crash_count": 2}],
        True)
    assert v["verdict"] == "remoteproc_crashed"


def test_classify_recovery_disabled():
    v = mod.classify(
        [{"id": "remoteproc0", "state": "running",
            "name": "m4", "firmware": "cm4.elf",
            "recovery": "disabled", "crash_count": 0}],
        True)
    assert v["verdict"] == "recovery_disabled"


def test_classify_firmware_missing():
    v = mod.classify(
        [{"id": "remoteproc0", "state": "offline",
            "name": "m4", "firmware": "cm4.elf",
            "recovery": "enabled", "crash_count": 0}],
        True)
    assert v["verdict"] == "firmware_missing"


def test_classify_state_offline_no_firmware():
    v = mod.classify(
        [{"id": "remoteproc0", "state": "offline",
            "name": "m4", "firmware": None,
            "recovery": "enabled", "crash_count": 0}],
        True)
    assert v["verdict"] == "state_offline"


def test_classify_ok():
    v = mod.classify(
        [{"id": "remoteproc0", "state": "running",
            "name": "m4", "firmware": "cm4.elf",
            "recovery": "enabled", "crash_count": 0}],
        True)
    assert v["verdict"] == "ok"


# Priority : crashed > recovery_disabled > firmware_missing
def test_priority_crashed_over_recovery_disabled():
    v = mod.classify(
        [{"id": "remoteproc0", "state": "crashed",
            "name": "m4", "firmware": "cm4.elf",
            "recovery": "disabled", "crash_count": 5}],
        True)
    assert v["verdict"] == "remoteproc_crashed"


def test_priority_recovery_disabled_over_firmware_missing():
    v = mod.classify(
        [{"id": "remoteproc0", "state": "offline",
            "name": "m4", "firmware": "cm4.elf",
            "recovery": "disabled", "crash_count": 0}],
        True)
    assert v["verdict"] == "recovery_disabled"


def test_priority_firmware_missing_over_state_offline():
    v = mod.classify(
        [{"id": "remoteproc0", "state": "offline",
            "name": "m4", "firmware": "cm4.elf",
            "recovery": "enabled", "crash_count": 0},
          {"id": "remoteproc1", "state": "offline",
            "name": "m4", "firmware": None,
            "recovery": "enabled", "crash_count": 0}],
        True)
    assert v["verdict"] == "firmware_missing"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_synthetic_ok(tmp_path):
    _mk_rproc(tmp_path, "remoteproc0", state="running")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["remoteproc_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_crashed_synthetic(tmp_path):
    _mk_rproc(tmp_path, "remoteproc0", state="crashed",
                 crash_count=4)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "remoteproc_crashed"
