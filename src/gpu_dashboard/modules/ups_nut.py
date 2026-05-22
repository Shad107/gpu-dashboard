"""Module ups_nut — talk to a local NUT (Network UPS Tools) server (R&D #7.5).

Stdlib socket client speaking NUT's plain-text protocol. NUT runs on
localhost:3493 typically. If unavailable, the dashboard silently shows
no UPS card.

Protocol summary (from upsd(8)) :
  client → "GET VAR <ups> <var>"        — returns one line
  client → "LIST VAR <ups>"             — multi-line, ends with "END LIST"
  client → "LIST UPS"                   — list available UPS names

Useful vars :
  ups.status         "OL" (online) / "OB" (on-battery) / "LB" (low battery)
  battery.charge     percentage 0-100
  battery.runtime    seconds estimated
  input.voltage      AC mains voltage
  battery.voltage    DC battery voltage
"""
from __future__ import annotations

import socket
from typing import Optional, Tuple


NAME = "ups_nut"


def _send(s: socket.socket, cmd: str) -> str:
    s.sendall((cmd + "\n").encode("utf-8"))
    return _recv_until(s, b"\n")


def _recv_until(s: socket.socket, sentinel: bytes, max_bytes: int = 16384) -> str:
    """Read from socket until sentinel appears or max_bytes."""
    buf = bytearray()
    while len(buf) < max_bytes:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
        if sentinel in buf:
            break
    return buf.decode("utf-8", errors="replace")


def parse_var_reply(line: str) -> Optional[str]:
    """Parse a single 'VAR <ups> <var> "<value>"' line, return the value."""
    if not line.startswith("VAR "):
        return None
    # Format : VAR <ups> <var> "<value>"
    idx = line.find('"')
    if idx < 0:
        return None
    end = line.rfind('"')
    if end <= idx:
        return None
    return line[idx + 1:end]


def parse_list_ups(text: str) -> list:
    """Parse 'LIST UPS' reply :
        BEGIN LIST UPS
        UPS apc "APC Smart-UPS"
        UPS eaton "Eaton 5P"
        END LIST UPS
    Returns list of UPS names (strings)."""
    names: list = []
    for line in text.splitlines():
        if line.startswith("UPS "):
            parts = line.split(maxsplit=2)
            if len(parts) >= 2:
                names.append(parts[1])
    return names


def query(host: str = "localhost", port: int = 3493, ups: Optional[str] = None,
          timeout: float = 2.0) -> dict:
    """Connect to NUT, query the first UPS (or specified `ups`), return dict.

    Returned shape on success :
      {ok: True, available: True, ups: <name>, status, charge_pct, runtime_s,
       on_battery: bool, low_battery: bool, raw: {...}}

    On failure :
      {ok: True, available: False, reason: "<msg>"}
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            # Discover UPS name if not given
            if not ups:
                reply = _send(s, "LIST UPS")
                # Read until END LIST UPS
                while "END LIST UPS" not in reply:
                    more = s.recv(4096).decode("utf-8", errors="replace")
                    if not more:
                        break
                    reply += more
                names = parse_list_ups(reply)
                if not names:
                    return {"ok": True, "available": False, "reason": "no UPS configured in NUT"}
                ups = names[0]

            raw: dict = {}
            for var in ("ups.status", "battery.charge", "battery.runtime",
                        "input.voltage", "battery.voltage"):
                resp = _send(s, f"GET VAR {ups} {var}")
                v = parse_var_reply(resp.splitlines()[0] if resp.splitlines() else "")
                if v is not None:
                    raw[var] = v

            status = raw.get("ups.status", "")
            try:
                charge_pct = int(float(raw.get("battery.charge", "0")))
            except (ValueError, TypeError):
                charge_pct = None
            try:
                runtime_s = int(float(raw.get("battery.runtime", "0")))
            except (ValueError, TypeError):
                runtime_s = None

            on_battery = "OB" in status.split()
            low_battery = "LB" in status.split()

            return {
                "ok": True, "available": True, "ups": ups,
                "status": status,
                "charge_pct": charge_pct,
                "runtime_s": runtime_s,
                "on_battery": on_battery,
                "low_battery": low_battery,
                "raw": raw,
            }
    except (socket.error, OSError) as e:
        return {"ok": True, "available": False, "reason": f"NUT unreachable: {e}"}
