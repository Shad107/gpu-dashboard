"""R&D #12.3 — LAN peer discovery UDP beacon tests."""
import json
import socket
import threading
import time
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import peer_beacon as pb


SECRET = b"shared-cluster-secret-32-bytes-xxx"


# ── make_beacon + verify_beacon ────────────────────────────────────────────


def test_make_beacon_includes_all_fields():
    p = pb.make_beacon("rig-1", 1, "RTX 3090", 9999, "0.3.0", SECRET)
    assert p["host"] == "rig-1"
    assert p["gpu_count"] == 1
    assert p["gpu_model"] == "RTX 3090"
    assert p["port"] == 9999
    assert p["version"] == "0.3.0"
    assert "ts" in p
    assert "sig" in p


def test_make_beacon_no_signature_when_no_secret():
    p = pb.make_beacon("rig-1", 1, "RTX 3090", 9999, "0.3.0", secret=None)
    assert "sig" not in p


def test_verify_signed_beacon_roundtrip():
    p = pb.make_beacon("rig-1", 1, "RTX 3090", 9999, "0.3.0", SECRET)
    assert pb.verify_beacon(p, SECRET) is True


def test_verify_signed_beacon_with_wrong_secret_fails():
    p = pb.make_beacon("rig-1", 1, "RTX 3090", 9999, "0.3.0", SECRET)
    assert pb.verify_beacon(p, b"wrong-secret") is False


def test_verify_beacon_open_mode():
    """No secret on either side → accept anything that has the shape."""
    p = pb.make_beacon("rig-1", 1, "RTX 3090", 9999, "0.3.0", secret=None)
    assert pb.verify_beacon(p, None) is True


def test_verify_beacon_rejects_old_timestamp():
    p = pb.make_beacon("rig-1", 1, "RTX 3090", 9999, "0.3.0", secret=None)
    p["ts"] = int(time.time()) - 9999
    assert pb.verify_beacon(p, None) is False


def test_verify_beacon_rejects_missing_fields():
    assert pb.verify_beacon({}, None) is False
    assert pb.verify_beacon({"host": "x"}, None) is False  # no port
    assert pb.verify_beacon("not-a-dict", None) is False


# ── PeerRegistry ───────────────────────────────────────────────────────────


def test_registry_upsert_then_list():
    reg = pb.PeerRegistry()
    reg.upsert(pb.make_beacon("rig-1", 1, "RTX 3090", 9999, "0.3.0"), "10.0.0.5")
    out = reg.list()
    assert len(out) == 1
    assert out[0]["host"] == "rig-1"
    assert out[0]["ip"] == "10.0.0.5"


def test_registry_upsert_replaces_same_host_port():
    reg = pb.PeerRegistry()
    reg.upsert(pb.make_beacon("rig-1", 1, "RTX 3090", 9999, "0.3.0"), "10.0.0.5")
    time.sleep(0.01)
    reg.upsert(pb.make_beacon("rig-1", 2, "RTX 3090", 9999, "0.3.0"), "10.0.0.5")
    out = reg.list()
    assert len(out) == 1
    assert out[0]["gpu_count"] == 2


def test_registry_multiple_peers_sorted():
    reg = pb.PeerRegistry()
    reg.upsert(pb.make_beacon("bravo", 1, "RTX 3090", 9999, "0.3.0"), "10.0.0.5")
    reg.upsert(pb.make_beacon("alpha", 1, "RTX 4090", 9999, "0.3.0"), "10.0.0.6")
    hosts = [p["host"] for p in reg.list()]
    assert hosts == ["alpha", "bravo"]


def test_registry_prune_removes_stale():
    reg = pb.PeerRegistry()
    reg.upsert(pb.make_beacon("rig-1", 1, "RTX 3090", 9999, "0.3.0"), "10.0.0.5")
    # Manually expire
    for p in reg._peers.values():
        p["last_seen_ts"] = int(time.time()) - 9999
    removed = reg.prune(ttl_s=30)
    assert removed == 1
    assert reg.list() == []


def test_registry_list_filters_by_ttl():
    reg = pb.PeerRegistry()
    reg.upsert(pb.make_beacon("fresh", 1, "RTX 3090", 9999, "0.3.0"), "10.0.0.5")
    reg.upsert(pb.make_beacon("stale", 1, "RTX 4090", 9999, "0.3.0"), "10.0.0.6")
    for p in reg._peers.values():
        if p["host"] == "stale":
            p["last_seen_ts"] = int(time.time()) - 60
    out = reg.list(ttl_s=30)
    assert len(out) == 1
    assert out[0]["host"] == "fresh"


def test_registry_signature_not_returned():
    """The 'sig' field is internal — shouldn't leak in the listing."""
    reg = pb.PeerRegistry()
    reg.upsert(pb.make_beacon("rig-1", 1, "RTX 3090", 9999, "0.3.0", SECRET), "10.0.0.5")
    out = reg.list()
    assert "sig" not in out[0]


# ── send_beacon ────────────────────────────────────────────────────────────


def test_send_beacon_handles_socket_error():
    """When SO_BROADCAST fails (no privilege), returns False — no exception."""
    with patch.object(socket, "socket", side_effect=OSError("permission denied")):
        ok = pb.send_beacon({"host": "x"}, port=9998)
    assert ok is False


def test_send_beacon_writes_json_payload():
    """Verify the sock.sendto receives JSON-encoded payload."""
    captured = {}
    class FakeSock:
        def setsockopt(self, *a, **k): pass
        def settimeout(self, *a, **k): pass
        def sendto(self, data, dest):
            captured["data"] = data
            captured["dest"] = dest
        def close(self): pass
    with patch.object(socket, "socket", return_value=FakeSock()):
        ok = pb.send_beacon({"host": "rig-1", "ts": 1}, port=9998)
    assert ok is True
    assert captured["dest"][1] == 9998
    decoded = json.loads(captured["data"])
    assert decoded["host"] == "rig-1"
