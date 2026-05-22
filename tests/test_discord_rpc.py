"""R&D #15.7 — Discord Rich Presence bridge tests."""
import json
import socket
import struct
import threading
import pytest
from unittest.mock import patch, MagicMock
from gpu_dashboard.modules import discord_rpc as dr


# ── find_ipc_socket ──────────────────────────────────────────────────────


def test_find_ipc_socket_returns_first_match():
    """First socket found wins."""
    with patch("glob.glob", side_effect=[["/run/user/1000/discord-ipc-0"], []]):
        assert dr.find_ipc_socket() == "/run/user/1000/discord-ipc-0"


def test_find_ipc_socket_falls_back_to_tmp():
    """If /run/user/<uid> empty, try /tmp."""
    with patch("glob.glob", side_effect=[[], ["/tmp/discord-ipc-0"]]):
        assert dr.find_ipc_socket() == "/tmp/discord-ipc-0"


def test_find_ipc_socket_none_when_no_discord():
    with patch("glob.glob", return_value=[]):
        assert dr.find_ipc_socket() is None


# ── DiscordRPC.connect ───────────────────────────────────────────────────


def test_connect_returns_false_when_no_socket_path():
    c = dr.DiscordRPC("app", socket_path=None)
    with patch.object(dr, "find_ipc_socket", return_value=None):
        c2 = dr.DiscordRPC("app")
        assert c2.connect() is False


def test_connect_returns_false_on_socket_error():
    """Discord not running → ConnectionRefusedError on connect."""
    c = dr.DiscordRPC("app", socket_path="/nope")
    with patch.object(socket, "socket") as mk:
        inst = mk.return_value
        inst.connect.side_effect = ConnectionRefusedError
        assert c.connect() is False


# ── _send_frame / set_activity / clear_activity ─────────────────────────


def test_send_frame_writes_op_length_payload():
    """The wire format is <op:u32-LE><length:u32-LE><JSON bytes>."""
    c = dr.DiscordRPC("app", socket_path="/x")
    fake_sock = MagicMock()
    c._sock = fake_sock
    ok = c._send_frame(1, {"hello": "world"})
    assert ok is True
    sent = fake_sock.sendall.call_args[0][0]
    op, length = struct.unpack("<II", sent[:8])
    payload = json.loads(sent[8:])
    assert op == 1
    assert payload == {"hello": "world"}
    assert length == len(sent[8:])


def test_send_frame_returns_false_on_broken_pipe():
    c = dr.DiscordRPC("app", socket_path="/x")
    fake_sock = MagicMock()
    fake_sock.sendall.side_effect = BrokenPipeError
    c._sock = fake_sock
    assert c._send_frame(1, {}) is False


def test_set_activity_sends_correct_command():
    c = dr.DiscordRPC("app", socket_path="/x")
    fake_sock = MagicMock()
    c._sock = fake_sock
    c.set_activity(state="testing", details="hi", large_image="logo")
    sent = fake_sock.sendall.call_args[0][0]
    payload = json.loads(sent[8:])
    assert payload["cmd"] == "SET_ACTIVITY"
    assert payload["args"]["activity"]["state"] == "testing"
    assert payload["args"]["activity"]["details"] == "hi"
    assert payload["args"]["activity"]["assets"]["large_image"] == "logo"


def test_clear_activity_sends_null_activity():
    c = dr.DiscordRPC("app", socket_path="/x")
    fake_sock = MagicMock()
    c._sock = fake_sock
    c.clear_activity()
    sent = fake_sock.sendall.call_args[0][0]
    payload = json.loads(sent[8:])
    assert payload["args"]["activity"] is None


def test_set_activity_truncates_long_strings():
    c = dr.DiscordRPC("app", socket_path="/x")
    fake_sock = MagicMock()
    c._sock = fake_sock
    c.set_activity(state="x" * 500)
    sent = fake_sock.sendall.call_args[0][0]
    payload = json.loads(sent[8:])
    assert len(payload["args"]["activity"]["state"]) == 128


# ── build_activity ──────────────────────────────────────────────────────


def test_build_activity_offline_gpu():
    a = dr.build_activity({"alive": False})
    assert a["state"] == "GPU offline"


def test_build_activity_includes_temp_power_util():
    snap = {"alive": True, "name": "NVIDIA GeForce RTX 3090",
            "temp": 65, "power": 250, "util_gpu": 80,
            "mem_used_mib": 8192, "mem_total_mib": 24576}
    a = dr.build_activity(snap)
    assert "RTX 3090" in a["details"]
    assert "65°C" in a["details"]
    assert "250" in a["details"]
    assert "80%" in a["details"]


def test_build_activity_includes_llm_tps_when_available():
    snap = {"alive": True, "name": "RTX 3090", "temp": 50, "power": 100,
            "util_gpu": 20, "llm_model": "Qwen/Qwen2.5-7B",
            "mem_used_mib": 8192, "mem_total_mib": 24576}
    llm = {"available": True, "avg_tps_1m": 42.5}
    a = dr.build_activity(snap, llm)
    assert "Qwen2.5-7B" in a["state"]
    assert "42.5" in a["state"]


def test_build_activity_with_start_ts():
    snap = {"alive": True, "name": "x", "temp": 50, "power": 100,
            "util_gpu": 20, "mem_used_mib": 0, "mem_total_mib": 1}
    a = dr.build_activity(snap, started_at=1000)
    assert a["start_ts"] == 1000


# ── PresenceUpdater (single-cycle smoke test) ───────────────────────────


def test_updater_run_pushes_activity_then_stops():
    """Verifies the loop calls set_activity exactly once before stop."""
    snap_fn = lambda: {"alive": True, "name": "rtx 3090",
                        "temp": 50, "power": 100, "util_gpu": 20,
                        "mem_used_mib": 0, "mem_total_mib": 1}
    stop = threading.Event()
    pushed = []
    class FakeClient:
        def connect(self): return True
        def set_activity(self, **kw):
            pushed.append(kw); stop.set(); return True
        def clear_activity(self): pass
        def close(self): pass
    with patch.object(dr, "DiscordRPC", return_value=FakeClient()):
        u = dr.PresenceUpdater("test-app-id", snap_fn,
                                refresh_s=0.05, stop_event=stop)
        u.run()
    assert len(pushed) == 1
    assert "details" in pushed[0]


def test_updater_handles_connect_failure_gracefully():
    """If Discord IPC isn't running, updater doesn't crash."""
    snap_fn = lambda: {"alive": True, "name": "x", "temp": 50, "power": 100,
                        "util_gpu": 20, "mem_used_mib": 0, "mem_total_mib": 1}
    stop = threading.Event()
    class FailingClient:
        def connect(self): return False
        def set_activity(self, **kw): raise AssertionError("should not be called")
        def clear_activity(self): pass
        def close(self): pass
    with patch.object(dr, "DiscordRPC", return_value=FailingClient()):
        u = dr.PresenceUpdater("x", snap_fn, refresh_s=0.01, stop_event=stop)
        # Stop quickly — we just want to confirm no exception
        threading.Timer(0.05, stop.set).start()
        u.run()
    assert u.connected is False
    assert u.last_error and "could not connect" in u.last_error


# ── status() ─────────────────────────────────────────────────────────────


def test_status_no_discord_no_config():
    from gpu_dashboard.config import Config
    with patch.object(dr, "find_ipc_socket", return_value=None):
        s = dr.status(Config(defaults={}))
    assert s["discord_ipc_present"] is False
    assert s["enabled"] is False
    assert s["app_id_configured"] is False


def test_status_with_app_id_configured():
    from gpu_dashboard.config import Config
    with patch.object(dr, "find_ipc_socket",
                      return_value="/run/user/1000/discord-ipc-0"):
        cfg = Config(defaults={"DISCORD_APP_ID": "12345", "DISCORD_RPC_ENABLED": "1"})
        s = dr.status(cfg)
    assert s["discord_ipc_present"] is True
    assert s["enabled"] is True
    assert s["app_id_configured"] is True
