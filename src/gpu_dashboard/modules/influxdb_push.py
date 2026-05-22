"""Module influxdb_push — push GPU metrics in InfluxDB line protocol (R&D #7.4).

Stdlib-only HTTP POST. Compatible with InfluxDB v1 (`/write?db=...`) and v2
(`/api/v2/write?org=...&bucket=...`). Batches one line per polling tick.

Line protocol shape :
  gpu_metrics,host=<H>,gpu=<I>,uuid=<U> temp=72,util=98,power=180.5,vram_used=8589934592i <ns>

Config in config.env :
  INFLUXDB_URL=https://my-influx:8086         # or http://localhost:8086
  INFLUXDB_TOKEN=<token>                       # v2 ; ignored for v1
  INFLUXDB_ORG=my-org                          # v2
  INFLUXDB_BUCKET=gpu-dashboard                # v2 → ?bucket= ; v1 → ?db=
  INFLUXDB_DATABASE=gpu_dashboard              # v1 alternative
  INFLUXDB_INTERVAL=15                         # secs between flushes
  INFLUXDB_HOST_LABEL=$(hostname)              # override the 'host' tag

If INFLUXDB_URL is empty → module disabled silently.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple


NAME = "influxdb_push"


def _escape_tag(v: str) -> str:
    """InfluxDB tag value escapes : commas, spaces, equals."""
    return v.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def format_line(measurement: str, tags: dict, fields: dict, ts_ns: Optional[int] = None) -> str:
    """Return a single InfluxDB line-protocol record.

    `fields` values must be Python primitives :
      - int        → 'k=42i'
      - float      → 'k=3.14'
      - bool       → 'k=true'
      - str        → 'k="quoted"'  (commas + quotes escaped)
    """
    tag_str = ",".join(f"{k}={_escape_tag(str(v))}" for k, v in sorted(tags.items()) if v is not None)
    parts = [measurement]
    if tag_str:
        parts.append(tag_str)
    field_parts = []
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, bool):
            field_parts.append(f"{k}={'true' if v else 'false'}")
        elif isinstance(v, int):
            field_parts.append(f"{k}={v}i")
        elif isinstance(v, float):
            field_parts.append(f"{k}={v}")
        else:
            esc = str(v).replace("\\", "\\\\").replace('"', '\\"')
            field_parts.append(f'{k}="{esc}"')
    if not field_parts:
        return ""  # no valid fields → skip
    line = ",".join(parts) + " " + ",".join(field_parts)
    if ts_ns is not None:
        line += f" {ts_ns}"
    return line


def build_endpoint(url: str, org: Optional[str] = None,
                   bucket: Optional[str] = None,
                   database: Optional[str] = None) -> str:
    """Pick v2 endpoint if org+bucket set, else v1 if database set."""
    base = url.rstrip("/")
    if bucket and org:
        return f"{base}/api/v2/write?org={org}&bucket={bucket}&precision=ns"
    if database:
        return f"{base}/write?db={database}&precision=ns"
    if bucket:
        # v2-style without explicit org (some installs)
        return f"{base}/api/v2/write?bucket={bucket}&precision=ns"
    return f"{base}/write?db=gpu-dashboard&precision=ns"


def push(lines: list, url: str, token: Optional[str] = None,
         timeout: float = 5.0) -> Tuple[bool, str]:
    """POST lines to InfluxDB. Returns (ok, msg)."""
    if not lines:
        return True, "no data"
    body = ("\n".join(lines) + "\n").encode("utf-8")
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Token {token}"
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ok = 200 <= r.status < 300
            return ok, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"net: {e.reason}"
    except (TimeoutError, OSError) as e:
        return False, f"err: {e}"


class InfluxPusher:
    """Background thread that snapshots the sampler + pushes every interval."""

    def __init__(self, sampler, cfg, interval_s: float = 15.0):
        self._sampler = sampler
        self._cfg = cfg
        self._interval = float(interval_s)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_status: dict = {"ok": True, "msg": "not yet pushed", "ts": 0}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="influx-push")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    @property
    def status(self) -> dict:
        return dict(self._last_status)

    def _build_lines(self) -> list:
        """Convert latest sample to InfluxDB line protocol."""
        if not self._sampler:
            return []
        snap = self._sampler.snapshot()
        if not snap:
            return []
        last = snap[-1]
        host = self._cfg.get("INFLUXDB_HOST_LABEL") or socket.gethostname()
        gpu_idx = last.get("gpu_index", 0)
        tags = {"host": host, "gpu": str(gpu_idx)}
        fields = {
            "temp": int(last["temp"]) if last.get("temp") is not None else None,
            "util": int(last["util_gpu"]) if last.get("util_gpu") is not None else None,
            "power": float(last["power"]) if last.get("power") is not None else None,
            "power_limit": float(last["power_limit"]) if last.get("power_limit") is not None else None,
            "mem_used_mib": int(last["mem_used_mib"]) if last.get("mem_used_mib") is not None else None,
            "fan_pct": int(last["fan"]) if last.get("fan") is not None else None,
            "clk_gpu": int(last["clk_gpu"]) if last.get("clk_gpu") is not None else None,
        }
        ts_ns = int(time.time() * 1_000_000_000)
        line = format_line("gpu_metrics", tags, fields, ts_ns)
        return [line] if line else []

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                lines = self._build_lines()
                if lines:
                    url = self._cfg.get("INFLUXDB_URL", "")
                    if url:
                        endpoint = build_endpoint(
                            url,
                            org=self._cfg.get("INFLUXDB_ORG"),
                            bucket=self._cfg.get("INFLUXDB_BUCKET"),
                            database=self._cfg.get("INFLUXDB_DATABASE"),
                        )
                        ok, msg = push(lines, endpoint,
                                       token=self._cfg.get("INFLUXDB_TOKEN") or None)
                        self._last_status = {"ok": ok, "msg": msg, "ts": int(time.time())}
            except Exception as e:
                self._last_status = {"ok": False, "msg": f"err: {e}", "ts": int(time.time())}
            self._stop.wait(self._interval)
