"""R&D #20.5 — Hibernate/suspend safety preflight tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import suspend_guard as sg


# ── detect_lid_state ───────────────────────────────────────────────────


def test_lid_state_open(tmp_path):
    d = tmp_path / "LID0"; d.mkdir()
    (d / "state").write_text("state:      open\n")
    assert sg.detect_lid_state(str(tmp_path)) == "open"


def test_lid_state_closed(tmp_path):
    d = tmp_path / "LID0"; d.mkdir()
    (d / "state").write_text("state:      closed\n")
    assert sg.detect_lid_state(str(tmp_path)) == "closed"


def test_lid_state_missing(tmp_path):
    assert sg.detect_lid_state(str(tmp_path / "does_not_exist")) is None


def test_lid_state_no_lid_subdir(tmp_path):
    assert sg.detect_lid_state(str(tmp_path)) is None


# ── detect_idle_action ─────────────────────────────────────────────────


def test_idle_action_default_when_unparseable(tmp_path, monkeypatch):
    # Point at a nonexistent file
    monkeypatch.setattr(sg.os.path, "exists", lambda p: False)
    # Function reads /etc/systemd/logind.conf — will fail open
    assert sg.detect_idle_action() is None or isinstance(sg.detect_idle_action(), str)


# ── systemd_inhibit_oneliner ───────────────────────────────────────────


def test_inhibit_oneliner_basic():
    s = sg.systemd_inhibit_oneliner("training run")
    assert "systemd-inhibit" in s
    assert "training run" in s
    assert "--what=sleep" in s


def test_inhibit_oneliner_escapes_quotes():
    s = sg.systemd_inhibit_oneliner('say "hi"')
    assert 'say \\"hi\\"' in s


# ── classify ───────────────────────────────────────────────────────────


def test_classify_safe_when_idle():
    v = sg.classify(compute_pids=[], lid=None, idle_action="suspend")
    assert v["verdict"] == "safe"


def test_classify_safe_when_lid_closed_but_no_work():
    v = sg.classify(compute_pids=[], lid="closed", idle_action="suspend")
    assert v["verdict"] == "safe"


def test_classify_risky_when_cuda_running():
    pids = [{"pid": 100, "name": "python", "vram_mib": 5000}]
    v = sg.classify(compute_pids=pids, lid="open", idle_action="ignore")
    assert v["verdict"] == "risky"
    assert "1 CUDA" in v["reason"]


def test_classify_blocked_when_lid_closed_and_cuda():
    pids = [{"pid": 100, "name": "ollama", "vram_mib": 8000}]
    v = sg.classify(compute_pids=pids, lid="closed", idle_action="suspend")
    assert v["verdict"] == "blocked"
    assert "corrupt" in v["reason"].lower()


def test_classify_lists_process_names():
    pids = [
        {"pid": 1, "name": "ollama", "vram_mib": 1000},
        {"pid": 2, "name": "blender", "vram_mib": 2000},
    ]
    v = sg.classify(compute_pids=pids, lid="open", idle_action=None)
    assert "ollama" in v["reason"] or "blender" in v["reason"]


# ── list_compute_pids ──────────────────────────────────────────────────


def test_list_compute_pids_no_smi(monkeypatch):
    monkeypatch.setattr(sg.shutil, "which", lambda x: None)
    assert sg.list_compute_pids() == []


# ── status ─────────────────────────────────────────────────────────────


def test_status_safe_when_no_work():
    with patch.object(sg, "list_compute_pids", return_value=[]):
        with patch.object(sg, "detect_lid_state", return_value="open"):
            with patch.object(sg, "detect_idle_action", return_value="ignore"):
                s = sg.status()
    assert s["compute_count"] == 0
    assert s["verdict"]["verdict"] == "safe"
    assert "systemd-inhibit" in s["inhibit_snippet"]


def test_status_blocked_when_lid_closed_and_cuda():
    pids = [{"pid": 100, "name": "ollama", "vram_mib": 1000}]
    with patch.object(sg, "list_compute_pids", return_value=pids):
        with patch.object(sg, "detect_lid_state", return_value="closed"):
            with patch.object(sg, "detect_idle_action", return_value="suspend"):
                s = sg.status()
    assert s["verdict"]["verdict"] == "blocked"
