"""R&D #11.4 — Auto-service discovery tests."""
import subprocess
import urllib.error
from unittest.mock import patch, MagicMock
from gpu_dashboard.modules import service_discovery as sd


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


SS_SAMPLE = """\
State Recv-Q Send-Q Local Address:Port Peer Address:Port Process
LISTEN 0 512 0.0.0.0:8080 0.0.0.0:* users:(("llama-server",pid=1785,fd=16))
LISTEN 0 4096 127.0.0.1:11434 0.0.0.0:*
LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*
LISTEN 0 511 0.0.0.0:6333 0.0.0.0:* users:(("qdrant",pid=2200,fd=8))
LISTEN 0 1 127.0.0.1:9999 0.0.0.0:* users:(("python3",pid=3000,fd=4))
"""


# ── parse_ss_output ──────────────────────────────────────────────────────


def test_parse_ss_extracts_port_pid_name():
    out = sd.parse_ss_output(SS_SAMPLE)
    by_port = {x["port"]: x for x in out}
    assert 8080 in by_port
    assert by_port[8080]["pid"] == 1785
    assert by_port[8080]["name"] == "llama-server"
    assert 11434 in by_port  # ollama, no process info
    assert by_port[11434]["pid"] is None


def test_parse_ss_handles_empty():
    assert sd.parse_ss_output("") == []
    assert sd.parse_ss_output("State Recv-Q Send-Q\n") == []


# ── match_signature ──────────────────────────────────────────────────────


def test_match_signature_llama_server_by_cmdline():
    """llama-server on port 8080 with matching cmdline."""
    sig = sd.match_signature(8080, "/home/u/llama.cpp/build/bin/llama-server -m model.gguf")
    assert sig is not None
    assert sig["name"] == "llama.cpp server"


def test_match_signature_qdrant_by_cmdline():
    sig = sd.match_signature(6333, "/usr/local/bin/qdrant")
    assert sig is not None
    assert sig["name"] == "Qdrant"
    assert sig["category"] == "vector-db"


def test_match_signature_ollama_cmdline():
    sig = sd.match_signature(11434, "/usr/local/bin/ollama serve")
    assert sig["name"] == "ollama"


def test_match_signature_unknown_returns_none():
    """Port match but cmdline doesn't match any signature → None."""
    sig = sd.match_signature(22, "/usr/sbin/sshd -D")
    assert sig is None


def test_match_signature_self_detects_gpu_dashboard():
    sig = sd.match_signature(9999, "python3 -m gpu_dashboard")
    assert sig is not None
    assert sig["category"] == "self"


# ── probe_health ─────────────────────────────────────────────────────────


def test_probe_health_success():
    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda *a: None
    with patch("urllib.request.urlopen", return_value=fake_resp):
        h = sd.probe_health(8080, "/health")
    assert h["ok"] is True
    assert h["status"] == 200


def test_probe_health_connection_refused():
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
        h = sd.probe_health(8080, "/health")
    assert h["ok"] is False


def test_probe_health_404_still_not_ok():
    err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
    with patch("urllib.request.urlopen", side_effect=err):
        h = sd.probe_health(8080, "/lol")
    assert h["ok"] is False
    assert h["status"] == 404


# ── discover() top-level ─────────────────────────────────────────────────


def test_discover_no_ss_returns_unavailable():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        result = sd.discover()
    assert result["available"] is False


def test_discover_detects_llama_and_qdrant():
    """Mock ss output + cmdline reads + probes."""
    def fake_read_cmdline(pid):
        return {1785: "/llama-server -m m.gguf", 2200: "/usr/local/bin/qdrant", 3000: "python3 -m gpu_dashboard"}.get(pid, "")
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=SS_SAMPLE)), \
         patch.object(sd, "read_cmdline", side_effect=fake_read_cmdline), \
         patch.object(sd, "probe_health", return_value={"ok": True, "status": 200, "ms": 10}):
        result = sd.discover()
    assert result["available"] is True
    services_by_name = {s["service"]: s for s in result["services"]}
    assert "llama.cpp server" in services_by_name
    assert "Qdrant" in services_by_name
    assert "gpu-dashboard (self)" in services_by_name
    assert services_by_name["Qdrant"]["health"]["ok"] is True


def test_discover_lists_unknown_listeners():
    """Random app on port 38000 with no signature match → in unknown_listeners."""
    ss_out = ("State Recv-Q Send-Q Local Address:Port\n"
              'LISTEN 0 1 0.0.0.0:38000 0.0.0.0:* users:(("weirdapp",pid=9999,fd=4))\n')
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=ss_out)), \
         patch.object(sd, "read_cmdline", return_value="/usr/bin/weirdapp --listen"), \
         patch.object(sd, "probe_health", return_value={"ok": False}):
        result = sd.discover()
    assert result["unknown_count"] == 1
    assert result["unknown_listeners"][0]["port"] == 38000
    assert result["unknown_listeners"][0]["proc_name"] == "weirdapp"


def test_discover_skips_system_services():
    """SSH on port 22 shouldn't appear in services or unknown_listeners."""
    ss_out = ("State Recv-Q Send-Q Local Address:Port\n"
              'LISTEN 0 1 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=100,fd=3))\n')
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=ss_out)), \
         patch.object(sd, "read_cmdline", return_value="sshd"):
        result = sd.discover()
    assert result["services_count"] == 0
    assert result["unknown_count"] == 0
