"""R&D #15.8 — boot-time profile applicator tests."""
import json
import os
import subprocess
import tempfile
import time
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import boot_profile as bp


def _with_tmp(td):
    return patch.multiple(
        bp,
        profile_path=lambda: os.path.join(td, "boot_profile.json"),
        history_path=lambda: os.path.join(td, "history.json"),
    )


def _profile(name="silent", pl=250, gpu_off=-50, fan=None):
    return {
        "name": name,
        "power_limit_w": pl,
        "gpu_clock_offset_mhz": gpu_off,
        "mem_clock_offset_mhz": 500,
        "fan_curve": fan or [[40, 30], [70, 80], [85, 100]],
        "persistence_mode": True,
    }


# ── load / save / clear ────────────────────────────────────────────────


def test_load_profile_missing_returns_none():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        assert bp.load_profile() is None


def test_save_then_load_roundtrip():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        bp.save_profile(_profile("test-1"))
        p = bp.load_profile()
    assert p is not None
    assert p["name"] == "test-1"
    assert p["power_limit_w"] == 250


def test_clear_profile_removes_file():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        bp.save_profile(_profile())
        assert bp.clear_profile() is True
        assert bp.load_profile() is None


def test_clear_profile_no_file_returns_false():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        assert bp.clear_profile() is False


# ── wait_for_driver ────────────────────────────────────────────────────


def test_wait_for_driver_success_first_try():
    """nvidia-smi -L returns immediately with 'GPU 0: ...' → ready=True."""
    class FakeProc:
        stdout = "GPU 0: NVIDIA GeForce RTX 3090 (UUID: ...)\n"
        returncode = 0
        stderr = ""
    with patch.object(subprocess, "run", return_value=FakeProc()):
        r = bp.wait_for_driver(timeout_s=5, poll_s=0.01)
    assert r["ready"] is True
    assert r["attempts"] == 1


def test_wait_for_driver_timeout():
    """nvidia-smi never succeeds → ready=False after timeout."""
    class FakeProc:
        stdout = ""
        returncode = 1
        stderr = "driver not ready"
    with patch.object(subprocess, "run", return_value=FakeProc()):
        r = bp.wait_for_driver(timeout_s=0.1, poll_s=0.03)
    assert r["ready"] is False
    assert r["attempts"] >= 1


def test_wait_for_driver_missing_smi():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        r = bp.wait_for_driver(timeout_s=0.1, poll_s=0.03)
    assert r["ready"] is False
    assert "error" in r


# ── apply_profile ──────────────────────────────────────────────────────


def test_apply_profile_driver_not_ready_logs_failure():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        ready_fail = {"ready": False, "attempts": 30, "elapsed_s": 30, "error": "timeout"}
        outcome = bp.apply_profile(_profile(), ready=ready_fail)
        hist = bp.load_history()
    assert outcome["ok"] is False
    assert "driver did not initialise" in outcome["reason"]
    assert len(hist) == 1


def test_apply_profile_runs_nvidia_smi_pl():
    """power_limit_w → `nvidia-smi -pl N` is invoked."""
    cmds: list = []
    def fake_run(cmd):
        cmds.append(cmd)
        return True, "ok"
    with tempfile.TemporaryDirectory() as td, _with_tmp(td), \
         patch.object(bp, "_run", side_effect=fake_run):
        outcome = bp.apply_profile(_profile(pl=300),
                                    ready={"ready": True, "attempts": 1, "elapsed_s": 0.5})
    pl_cmds = [c for c in cmds if "-pl" in c]
    assert len(pl_cmds) == 1
    assert "300" in pl_cmds[0]
    assert outcome["ok"] is True
    assert outcome["applied"]["power_limit_w"]["value"] == 300


def test_apply_profile_persistence_mode_invokes_pm():
    cmds: list = []
    def fake_run(cmd):
        cmds.append(cmd)
        return True, "ok"
    with tempfile.TemporaryDirectory() as td, _with_tmp(td), \
         patch.object(bp, "_run", side_effect=fake_run):
        prof = _profile()
        prof["persistence_mode"] = True
        bp.apply_profile(prof, ready={"ready": True, "attempts": 1, "elapsed_s": 0.5})
    pm_cmds = [c for c in cmds if "-pm" in c]
    assert len(pm_cmds) == 1
    assert "1" in pm_cmds[0]


def test_apply_profile_failure_in_pl_records_error():
    def fake_run(cmd):
        if "-pl" in cmd:
            return False, "permission denied"
        return True, "ok"
    with tempfile.TemporaryDirectory() as td, _with_tmp(td), \
         patch.object(bp, "_run", side_effect=fake_run):
        outcome = bp.apply_profile(_profile(),
                                    ready={"ready": True, "attempts": 1, "elapsed_s": 0.5})
    assert outcome["ok"] is False
    assert any("-pl" in e for e in outcome["errors"])


def test_apply_profile_clocks_are_deferred():
    """gpu_clock_offset is left to the existing clock_offsets module."""
    with tempfile.TemporaryDirectory() as td, _with_tmp(td), \
         patch.object(bp, "_run", return_value=(True, "ok")):
        outcome = bp.apply_profile(_profile(),
                                    ready={"ready": True, "attempts": 1, "elapsed_s": 0.5})
    assert outcome["applied"]["gpu_clock_offset_mhz"]["deferred"] is True
    assert outcome["applied"]["mem_clock_offset_mhz"]["value"] == 500


# ── history ────────────────────────────────────────────────────────────


def test_append_history_bounded():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        for i in range(bp._HISTORY_MAX + 10):
            bp.append_history({"i": i, "ts": i})
        hist = bp.load_history()
    assert len(hist) == bp._HISTORY_MAX
    # Oldest evicted ; newest preserved
    assert hist[-1]["i"] == bp._HISTORY_MAX + 9


# ── status ────────────────────────────────────────────────────────────


def test_status_no_profile():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        s = bp.status()
    assert s["configured"] is False
    assert s["profile"] is None
    assert s["last_outcome"] is None


def test_status_with_profile_and_history():
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        bp.save_profile(_profile("test"))
        bp.append_history({"ok": True, "profile_name": "test", "ts": 100})
        s = bp.status()
    assert s["configured"] is True
    assert s["profile"]["name"] == "test"
    assert s["last_outcome"]["ok"] is True
    assert s["history_count"] == 1


# ── CLI main ───────────────────────────────────────────────────────────


def test_main_no_args_exits_with_usage_error():
    assert bp.main([]) == 2


def test_main_no_profile_returns_zero():
    """No profile = no-op (don't fail the systemd unit on every boot)."""
    with tempfile.TemporaryDirectory() as td, _with_tmp(td):
        rc = bp.main(["apply"])
    assert rc == 0
