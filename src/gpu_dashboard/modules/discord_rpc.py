"""Module discord_rpc — Discord Rich Presence bridge (R&D #15.7).

Pushes live GPU + LLM state to the user's Discord client via the LOCAL
IPC socket. NO outbound network traffic — Discord's own client relays
the presence update to its servers.

Air-gap-safe : if the user has Discord installed (with internet), it'll
show ; if not, the bridge silently no-ops.

Wire protocol (Discord RPC over Unix socket) :
  - Connect to $XDG_RUNTIME_DIR/discord-ipc-N (N = 0..9)
  - Frame : <op:u32-LE><length:u32-LE><payload-JSON-bytes>
  - op=0 : HANDSHAKE     {v:1, client_id:"<app_id>"}
  - op=1 : FRAME         {cmd, args, nonce}
  - op=2 : CLOSE
  - op=3 : PING
  - op=4 : PONG

To enable :
  1. Create a Discord application at https://discord.com/developers/applications
     and grab its Client ID (app_id).
  2. Configure DISCORD_APP_ID + DISCORD_RPC_ENABLED=1 in config.env.
  3. Restart the dashboard. The Rich Presence updates every 15s.

stdlib only : socket + struct + json + os.
"""
from __future__ import annotations

import glob
import json
import os
import socket
import struct
import threading
import time
import uuid
from typing import Optional


NAME = "discord_rpc"

_OP_HANDSHAKE = 0
_OP_FRAME = 1
_OP_CLOSE = 2

# Where Discord puts its IPC socket on Linux + macOS
_SOCKET_GLOB_LINUX = "/run/user/{uid}/discord-ipc-*"
_SOCKET_GLOB_TMP = "/tmp/discord-ipc-*"

# How often we push an update (Discord rate-limits at ~5s minimum)
DEFAULT_REFRESH_S = 15


def find_ipc_socket() -> Optional[str]:
    """Locate the first existing Discord IPC socket. Returns None if no
    Discord client is running."""
    uid = os.getuid()
    for pattern in (
        _SOCKET_GLOB_LINUX.format(uid=uid),
        _SOCKET_GLOB_TMP,
    ):
        candidates = sorted(glob.glob(pattern))
        if candidates:
            return candidates[0]
    return None


class DiscordRPC:
    """Minimal Discord RPC client (HANDSHAKE + SET_ACTIVITY only)."""

    def __init__(self, app_id: str, socket_path: Optional[str] = None,
                 timeout_s: float = 2.0):
        self.app_id = str(app_id)
        self.socket_path = socket_path or find_ipc_socket()
        self.timeout_s = timeout_s
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()

    def connect(self) -> bool:
        """Open the Unix socket + send the handshake. Returns False on failure."""
        if not self.socket_path:
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(self.timeout_s)
            s.connect(self.socket_path)
        except (OSError, socket.timeout):
            return False
        with self._lock:
            self._sock = s
        # Handshake
        if not self._send_frame(_OP_HANDSHAKE, {"v": 1, "client_id": self.app_id}):
            self.close()
            return False
        # Read READY response (optional ; we don't care about its content)
        try:
            self._recv_frame()
        except (OSError, socket.timeout):
            pass
        return True

    def _send_frame(self, op: int, payload: dict) -> bool:
        if not self._sock:
            return False
        data = json.dumps(payload).encode("utf-8")
        header = struct.pack("<II", int(op), len(data))
        try:
            with self._lock:
                self._sock.sendall(header + data)
            return True
        except (OSError, BrokenPipeError):
            return False

    def _recv_frame(self) -> Optional[tuple]:
        if not self._sock:
            return None
        try:
            header = self._sock.recv(8)
            if len(header) < 8:
                return None
            op, length = struct.unpack("<II", header)
            data = b""
            while len(data) < length:
                chunk = self._sock.recv(length - len(data))
                if not chunk:
                    break
                data += chunk
            return op, json.loads(data.decode("utf-8")) if data else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def set_activity(self, *, state: str = "", details: str = "",
                     start_ts: Optional[int] = None,
                     large_image: Optional[str] = None,
                     small_image: Optional[str] = None,
                     small_text: Optional[str] = None) -> bool:
        """Push an activity (the Rich Presence card)."""
        activity: dict = {}
        if state:
            activity["state"] = state[:128]
        if details:
            activity["details"] = details[:128]
        if start_ts:
            activity["timestamps"] = {"start": int(start_ts)}
        assets = {}
        if large_image: assets["large_image"] = large_image
        if small_image: assets["small_image"] = small_image
        if small_text:  assets["small_text"] = small_text[:128]
        if assets:
            activity["assets"] = assets
        payload = {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": activity},
            "nonce": str(uuid.uuid4()),
        }
        return self._send_frame(_OP_FRAME, payload)

    def clear_activity(self) -> bool:
        """Erase the activity (presence goes back to 'idle')."""
        payload = {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": None},
            "nonce": str(uuid.uuid4()),
        }
        return self._send_frame(_OP_FRAME, payload)

    def close(self) -> None:
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None


def build_activity(gpu_snap: Optional[dict],
                   llm_perf: Optional[dict] = None,
                   started_at: Optional[float] = None) -> dict:
    """Construct {state, details, start_ts, ...} from live GPU + LLM data.
    Caller passes this into DiscordRPC.set_activity(**).

    Format chosen to be informative without leaking sensitive details :
      details = 'GPU : 65°C · 250W · 80% util'
      state   = 'LLM : Qwen2.5-7B · 47 tok/s'
    """
    if not gpu_snap or not gpu_snap.get("alive"):
        return {"state": "GPU offline", "details": "no activity"}
    name = gpu_snap.get("name", "GPU")
    short = name.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")
    temp = gpu_snap.get("temp", 0)
    power = gpu_snap.get("power", 0)
    util = gpu_snap.get("util_gpu", 0)
    details = f"{short} : {temp}°C · {power:.0f} W · {util}% util"
    model = (gpu_snap.get("llm_model") or "").split("/")[-1][:40]
    if llm_perf and llm_perf.get("available"):
        tps = llm_perf.get("avg_tps_1m") or llm_perf.get("avg_tps_5m") or 0
        state = f"LLM : {model or '?'} · {tps:.1f} tok/s" if model else f"LLM : {tps:.1f} tok/s"
    elif model:
        state = f"Model : {model}"
    else:
        state = "Idle"
    return {
        "state": state,
        "details": details,
        "start_ts": int(started_at) if started_at else None,
    }


class PresenceUpdater(threading.Thread):
    """Daemon thread : pushes the Rich Presence card every `refresh_s` seconds.

    Auto-reconnects on transient socket errors. Stops when `stop_event` set.
    """

    def __init__(self, app_id: str, get_snapshot_fn, get_llm_perf_fn=None,
                 refresh_s: float = DEFAULT_REFRESH_S,
                 stop_event: Optional[threading.Event] = None):
        super().__init__(daemon=True, name="discord-rpc")
        self.app_id = app_id
        self.get_snapshot = get_snapshot_fn
        self.get_llm_perf = get_llm_perf_fn or (lambda: None)
        self.refresh_s = refresh_s
        self.stop_event = stop_event or threading.Event()
        self.started_at = time.time()
        self.client: Optional[DiscordRPC] = None
        self.last_error: Optional[str] = None
        self.connected = False
        self.last_push_ts: Optional[int] = None

    def _ensure_connected(self) -> bool:
        if self.client and self.connected:
            return True
        self.client = DiscordRPC(self.app_id)
        if self.client.connect():
            self.connected = True
            self.last_error = None
            return True
        self.connected = False
        self.last_error = "could not connect to Discord IPC"
        return False

    def run(self) -> None:
        while not self.stop_event.is_set():
            if self._ensure_connected():
                snap = self.get_snapshot()
                llm = self.get_llm_perf()
                act = build_activity(snap, llm, started_at=self.started_at)
                ok = self.client.set_activity(**act)
                if ok:
                    self.last_push_ts = int(time.time())
                else:
                    self.connected = False  # force reconnect next tick
                    self.last_error = "set_activity failed (socket closed?)"
            self.stop_event.wait(self.refresh_s)
        if self.client:
            try:
                self.client.clear_activity()
            except Exception:
                pass
            self.client.close()


def status(cfg) -> dict:
    """Top-level snapshot for the UI : whether Discord IPC is reachable + config."""
    socket_path = find_ipc_socket()
    app_id = ""
    enabled = False
    if cfg:
        app_id = str(cfg.get("DISCORD_APP_ID", "") or "")
        enabled = str(cfg.get("DISCORD_RPC_ENABLED", "0")).lower() in ("1", "true", "yes")
    return {
        "ok": True,
        "enabled": enabled,
        "discord_ipc_present": socket_path is not None,
        "socket_path": socket_path,
        "app_id_configured": bool(app_id),
    }
