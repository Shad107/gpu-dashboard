"""Module peer_beacon — LAN multi-host peer discovery via UDP broadcast (R&D #12.3).

Each dashboard instance optionally broadcasts a small JSON beacon on the
LAN every 5 s. Other instances on the same LAN receive the beacon and
maintain a 'known peers' dict — surfaced via /api/peers and the Fleet
tab in the UI.

Payload :
  {host, gpu_count, gpu_model, port, version, ts, sig}

Security :
  - sig is HMAC-SHA256 over (host|gpu_count|port|ts) using a shared
    cluster_secret. Beacons with bad sigs are dropped silently.
  - The cluster_secret is OPTIONAL : if PEER_CLUSTER_SECRET is empty,
    beacons are unsigned ('open mode') — useful for solo demos.
  - Beacons older than 30 s are pruned from the peer registry.

Stdlib only : socket + json + threading + hashlib + hmac.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
import time
from typing import Optional


NAME = "peer_beacon"

DEFAULT_PORT = 9998   # different from the dashboard HTTP port (9999)
BEACON_INTERVAL_S = 5
PEER_TTL_S = 30
BUFFER_SIZE = 4096


def _make_sig(payload: dict, secret: bytes) -> str:
    """HMAC-SHA256 over canonical fields."""
    body = "|".join([
        str(payload.get("host", "")),
        str(payload.get("gpu_count", 0)),
        str(payload.get("port", 0)),
        str(payload.get("ts", 0)),
    ])
    return hmac.new(secret, body.encode("utf-8"), hashlib.sha256).hexdigest()


def make_beacon(host: str, gpu_count: int, gpu_model: str, port: int,
                version: str, secret: Optional[bytes] = None) -> dict:
    """Build a beacon payload + optional signature."""
    p = {
        "host": host[:64],
        "gpu_count": int(gpu_count),
        "gpu_model": str(gpu_model)[:80],
        "port": int(port),
        "version": str(version)[:32],
        "ts": int(time.time()),
    }
    if secret:
        p["sig"] = _make_sig(p, secret)
    return p


def verify_beacon(payload: dict, secret: Optional[bytes]) -> bool:
    """Return True if the payload is valid (and signature matches if
    secret is set). Beacons older than PEER_TTL_S are rejected."""
    if not isinstance(payload, dict):
        return False
    if "host" not in payload or "port" not in payload:
        return False
    try:
        age = int(time.time()) - int(payload.get("ts", 0))
    except (ValueError, TypeError):
        return False
    if age > PEER_TTL_S or age < -10:
        return False
    if secret:
        provided = payload.get("sig", "")
        expected = _make_sig(payload, secret)
        if not hmac.compare_digest(provided, expected):
            return False
    return True


class PeerRegistry:
    """Thread-safe registry of recently-seen peers."""

    def __init__(self):
        self._peers: dict = {}
        self._lock = threading.Lock()

    def upsert(self, payload: dict, src_ip: str) -> None:
        key = f"{payload['host']}:{payload['port']}"
        entry = {**payload, "ip": src_ip, "last_seen_ts": int(time.time())}
        # Don't store the sig in the listing
        entry.pop("sig", None)
        with self._lock:
            self._peers[key] = entry

    def list(self, ttl_s: int = PEER_TTL_S) -> list:
        """Return peers seen within `ttl_s`, sorted by host."""
        now = int(time.time())
        with self._lock:
            return sorted(
                [p for p in self._peers.values()
                 if now - int(p.get("last_seen_ts", 0)) <= ttl_s],
                key=lambda p: p.get("host", ""),
            )

    def prune(self, ttl_s: int = PEER_TTL_S) -> int:
        """Remove peers older than ttl_s. Returns count removed."""
        now = int(time.time())
        with self._lock:
            stale = [k for k, p in self._peers.items()
                     if now - int(p.get("last_seen_ts", 0)) > ttl_s]
            for k in stale:
                del self._peers[k]
        return len(stale)

    def reset(self) -> None:
        with self._lock:
            self._peers.clear()


# Module-level shared registry — populated by the listener thread
_registry = PeerRegistry()


def registry() -> PeerRegistry:
    return _registry


def send_beacon(payload: dict, port: int = DEFAULT_PORT,
                broadcast_addr: str = "255.255.255.255") -> bool:
    """Send a single beacon UDP packet. Returns False on failure."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1.0)
        data = json.dumps(payload).encode("utf-8")
        sock.sendto(data, (broadcast_addr, port))
        sock.close()
        return True
    except OSError:
        return False


def listen_loop(port: int, secret: Optional[bytes], stop_event: threading.Event,
                registry_obj: PeerRegistry) -> None:
    """Blocking loop : bind UDP port, receive beacons, upsert valid ones.
    Caller passes a stop_event for clean shutdown."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)
    try:
        sock.bind(("", port))
    except OSError:
        return
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(BUFFER_SIZE)
        except socket.timeout:
            registry_obj.prune()
            continue
        except OSError:
            break
        try:
            payload = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not verify_beacon(payload, secret):
            continue
        registry_obj.upsert(payload, src_ip=addr[0])
    sock.close()


# Convenience : start a background beacon thread (sender + listener)
def start_threads(host: str, gpu_count: int, gpu_model: str, port_http: int,
                   version: str, port_udp: int = DEFAULT_PORT,
                   secret: Optional[bytes] = None,
                   stop_event: Optional[threading.Event] = None) -> threading.Event:
    """Spawn 2 daemon threads (send + listen). Returns the stop_event so
    caller can shut them down at server stop."""
    if stop_event is None:
        stop_event = threading.Event()

    def _sender():
        while not stop_event.is_set():
            payload = make_beacon(host, gpu_count, gpu_model, port_http, version, secret)
            send_beacon(payload, port=port_udp)
            stop_event.wait(BEACON_INTERVAL_S)

    threading.Thread(target=_sender, daemon=True, name="peer-beacon-send").start()
    threading.Thread(
        target=listen_loop, args=(port_udp, secret, stop_event, _registry),
        daemon=True, name="peer-beacon-recv",
    ).start()
    return stop_event
