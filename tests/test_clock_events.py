"""R&D #4.2 — clocks_event_reasons decoder tests."""
import subprocess
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.config import Config


def _ctx():
    return {"config": Config(defaults={})}


class FakeProc:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_no_nvidia_smi_returns_available_false():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        code, body = api.handle_clock_events(_ctx())
    assert code == 200
    assert body["ok"] is True
    assert body["available"] is False
    assert body["reasons"] == []


def test_all_inactive_returns_empty_reasons():
    out = "Not Active, Not Active, Not Active, Not Active, Not Active, Not Active, Not Active, Not Active, Not Active"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_clock_events(_ctx())
    assert code == 200
    assert body["available"] is True
    assert body["reasons"] == []
    assert body["raw"]["gpu_idle"] is False


def test_idle_only_returns_gpu_idle_reason():
    # gpu_idle is the FIRST field per _CLOCK_EVENT_REASONS order
    out = "Active, Not Active, Not Active, Not Active, Not Active, Not Active, Not Active, Not Active, Not Active"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_clock_events(_ctx())
    assert code == 200
    assert len(body["reasons"]) == 1
    assert body["reasons"][0]["key"] == "gpu_idle"
    assert body["reasons"][0]["label"] == "Idle"
    assert "idle" in body["reasons"][0]["hint"].lower()


def test_power_cap_throttle():
    # sw_power_cap is the 3rd field
    out = "Not Active, Not Active, Active, Not Active, Not Active, Not Active, Not Active, Not Active, Not Active"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_clock_events(_ctx())
    assert code == 200
    keys = [r["key"] for r in body["reasons"]]
    assert "sw_power_cap" in keys
    assert body["raw"]["sw_power_cap"] is True


def test_multi_throttle_returns_all():
    # SW thermal (6th) + HW thermal (7th) both active
    out = "Not Active, Not Active, Not Active, Not Active, Not Active, Active, Active, Not Active, Not Active"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_clock_events(_ctx())
    assert code == 200
    keys = {r["key"] for r in body["reasons"]}
    assert keys == {"sw_thermal", "hw_thermal"}


def test_nvidia_smi_non_zero_returns_unavailable():
    with patch.object(subprocess, "run", return_value=FakeProc(stdout="", returncode=1)):
        code, body = api.handle_clock_events(_ctx())
    assert code == 200
    assert body["available"] is False
