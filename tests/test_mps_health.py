"""R&D #19.6 — CUDA-MPS daemon health probe tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import mps_health as mh


# ── pipe_dir / log_dir ─────────────────────────────────────────────────


def test_pipe_dir_default(monkeypatch):
    monkeypatch.delenv("CUDA_MPS_PIPE_DIRECTORY", raising=False)
    assert mh.pipe_dir() == mh.DEFAULT_PIPE_DIR


def test_pipe_dir_overridden(monkeypatch):
    monkeypatch.setenv("CUDA_MPS_PIPE_DIRECTORY", "/run/mps")
    assert mh.pipe_dir() == "/run/mps"


def test_log_dir_default(monkeypatch):
    monkeypatch.delenv("CUDA_MPS_LOG_DIRECTORY", raising=False)
    assert mh.log_dir() == mh.DEFAULT_LOG_DIR


# ── control_socket_exists ──────────────────────────────────────────────


def test_control_socket_present(tmp_path):
    (tmp_path / "control").touch()
    assert mh.control_socket_exists(str(tmp_path)) is True


def test_control_socket_missing(tmp_path):
    assert mh.control_socket_exists(str(tmp_path)) is False


# ── find_mps_server_pids ───────────────────────────────────────────────


def test_find_server_pids(tmp_path):
    p1 = tmp_path / "1234"; p1.mkdir()
    (p1 / "comm").write_text("nvidia-cuda-mps\n")
    p2 = tmp_path / "5678"; p2.mkdir()
    (p2 / "comm").write_text("bash\n")
    pids = mh.find_mps_server_pids(proc_root=str(tmp_path))
    assert pids == [1234]


def test_find_no_server_pids(tmp_path):
    p1 = tmp_path / "111"; p1.mkdir()
    (p1 / "comm").write_text("firefox\n")
    assert mh.find_mps_server_pids(proc_root=str(tmp_path)) == []


# ── parse_server_list ──────────────────────────────────────────────────


def test_parse_server_list_pids():
    out = mh.parse_server_list("4567\n4568\n")
    assert out == [{"pid": 4567}, {"pid": 4568}]


def test_parse_server_list_empty():
    assert mh.parse_server_list("") == []


def test_parse_server_list_ignores_non_numeric():
    out = mh.parse_server_list("Server PID:\n4567\nDone.\n")
    assert out == [{"pid": 4567}]


# ── parse_client_list ──────────────────────────────────────────────────


def test_parse_client_list_pid_only():
    out = mh.parse_client_list("9876\n")
    assert out == [{"pid": 9876}]


def test_parse_client_list_with_uid_name():
    out = mh.parse_client_list("9876 1000 python3\n9877 1001 ollama\n")
    assert out == [
        {"pid": 9876, "uid": 1000, "name": "python3"},
        {"pid": 9877, "uid": 1001, "name": "ollama"},
    ]


def test_parse_client_list_empty():
    assert mh.parse_client_list("") == []


# ── parse_active_thread_percentage ─────────────────────────────────────


def test_parse_thread_pct():
    assert mh.parse_active_thread_percentage("50%") == 50.0


def test_parse_thread_pct_decimal():
    assert mh.parse_active_thread_percentage("33.3%") == 33.3


def test_parse_thread_pct_garbage():
    assert mh.parse_active_thread_percentage("???") is None


# ── status verdicts ────────────────────────────────────────────────────


def test_status_not_configured():
    with patch.object(mh, "has_control_binary", return_value=False):
        with patch.object(mh, "control_socket_exists", return_value=False):
            with patch.object(mh, "find_mps_server_pids", return_value=[]):
                s = mh.status()
    assert s["state"] == "not_configured"


def test_status_not_running():
    with patch.object(mh, "has_control_binary", return_value=True):
        with patch.object(mh, "control_socket_exists", return_value=False):
            with patch.object(mh, "find_mps_server_pids", return_value=[]):
                s = mh.status()
    assert s["state"] == "not_running"
    assert "Start with" in s["advice"]


def test_status_stalled():
    with patch.object(mh, "has_control_binary", return_value=True):
        with patch.object(mh, "control_socket_exists", return_value=True):
            with patch.object(mh, "find_mps_server_pids", return_value=[1234]):
                with patch.object(mh, "_talk_to_control", return_value=None):
                    s = mh.status()
    assert s["state"] == "stalled"
    assert "unresponsive" in s["advice"]


def test_status_running_with_clients():
    def fake_talk(cmds, timeout=1.5):
        c = cmds[0]
        if "server_list" in c: return "1234\n"
        if "client_list" in c: return "9876 1000 ollama\n"
        if "thread_percentage" in c: return "50%\n"
        return ""
    with patch.object(mh, "has_control_binary", return_value=True):
        with patch.object(mh, "control_socket_exists", return_value=True):
            with patch.object(mh, "find_mps_server_pids", return_value=[1234]):
                with patch.object(mh, "_talk_to_control",
                                  side_effect=fake_talk):
                    s = mh.status()
    assert s["state"] == "running"
    assert len(s["clients"]) == 1
    assert s["default_sm_share_pct"] == 50.0
