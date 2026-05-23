"""R&D #21.2 — nvidia-persistenced check tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import persistence_mode as pm


# ── daemon_socket_present / pid ────────────────────────────────────────


def test_socket_present(tmp_path):
    p = tmp_path / "socket"
    p.touch()
    assert pm.daemon_socket_present(str(p)) is True


def test_socket_missing(tmp_path):
    assert pm.daemon_socket_present(str(tmp_path / "nope")) is False


def test_pid_parses_valid(tmp_path):
    p = tmp_path / "p.pid"
    p.write_text("4321\n")
    assert pm.daemon_pid(str(p)) == 4321


def test_pid_missing(tmp_path):
    assert pm.daemon_pid(str(tmp_path / "x.pid")) is None


def test_pid_garbage(tmp_path):
    p = tmp_path / "p.pid"
    p.write_text("notapid\n")
    assert pm.daemon_pid(str(p)) is None


# ── classify ───────────────────────────────────────────────────────────


def test_classify_no_gpus():
    v = pm.classify(daemon_up=True, gpus=[])
    assert v["verdict"] == "unknown"


def test_classify_ok_all_on():
    gpus = [{"index": 0, "name": "RTX 3090", "enabled": True, "raw": "Enabled"}]
    v = pm.classify(daemon_up=True, gpus=gpus)
    assert v["verdict"] == "ok"


def test_classify_partial_daemon_up_gpu_off():
    gpus = [
        {"index": 0, "name": "RTX 3090", "enabled": True, "raw": "Enabled"},
        {"index": 1, "name": "RTX 4090", "enabled": False, "raw": "Disabled"},
    ]
    v = pm.classify(daemon_up=True, gpus=gpus)
    assert v["verdict"] == "partial"
    assert "nvidia-smi -pm 1" in v["advisory"]


def test_classify_off_when_all_off():
    gpus = [{"index": 0, "name": "RTX 3090", "enabled": False, "raw": "Disabled"}]
    v = pm.classify(daemon_up=False, gpus=gpus)
    assert v["verdict"] == "off"
    assert "cold-start" in v["reason"].lower()
    assert "systemctl enable" in v["advisory"]


def test_classify_off_with_manual():
    """User did `nvidia-smi -pm 1` but daemon is disabled."""
    gpus = [{"index": 0, "name": "RTX 3090", "enabled": True, "raw": "Enabled"}]
    v = pm.classify(daemon_up=False, gpus=gpus)
    assert v["verdict"] == "off_with_manual"
    assert "survives until reboot" in v["reason"]


# ── per_gpu_persistence ────────────────────────────────────────────────


def test_per_gpu_no_smi(monkeypatch):
    monkeypatch.setattr(pm.shutil, "which", lambda x: None)
    assert pm.per_gpu_persistence() is None


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_smi():
    with patch.object(pm, "per_gpu_persistence", return_value=None):
        with patch.object(pm, "daemon_running", return_value=False):
            s = pm.status()
    assert s["ok"] is False
    assert s["gpus"] == []


def test_status_daemon_running_all_enabled():
    fake_gpus = [{"index": 0, "name": "RTX 3090",
                   "enabled": True, "raw": "Enabled"}]
    with patch.object(pm, "per_gpu_persistence", return_value=fake_gpus):
        with patch.object(pm, "daemon_running", return_value=True):
            with patch.object(pm, "daemon_pid", return_value=1234):
                s = pm.status()
    assert s["ok"] is True
    assert s["daemon_running"] is True
    assert s["verdict"]["verdict"] == "ok"


def test_status_daemon_off_all_disabled():
    fake_gpus = [{"index": 0, "name": "RTX 3090",
                   "enabled": False, "raw": "Disabled"}]
    with patch.object(pm, "per_gpu_persistence", return_value=fake_gpus):
        with patch.object(pm, "daemon_running", return_value=False):
            with patch.object(pm, "daemon_pid", return_value=None):
                s = pm.status()
    assert s["verdict"]["verdict"] == "off"
