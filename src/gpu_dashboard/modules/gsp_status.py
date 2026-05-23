"""Module gsp_status — GSP-RM crash + fallback surfacer (R&D #21.3).

NVIDIA introduced GPU System Processor (GSP) firmware with Turing.
Starting with the R555 open kernel driver, the entire resource
manager (RM) runs *inside* the GSP — the host driver is a thin shim.
When GSP firmware crashes (a real bug class on Ubuntu 24.04 / Fedora
41 right now), the driver falls back to the legacy in-kernel RM with
worse perf and missing features (MIG, MPS, certain pstate transitions).

The failure mode is **silent** — nvidia-smi still reports the device,
inference still runs, just slower. The only signals live in journald
("Falling back to host RM", "GSP timeout", "RmInitializeGsp failed").

This module :
  1. Reads gsp_firmware_version from nvidia-smi
  2. Scans `journalctl -k --since=24h` for GSP-related lines
  3. Classifies: ok / partial / fallback / crashed
  4. Emits the recovery one-liner ("modprobe -r nvidia* && modprobe …")

stdlib only.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional


NAME = "gsp_status"


# Match anything that looks like NVRM/GSP from journalctl
_GSP_PATTERNS = [
    # More specific patterns first
    (re.compile(r"NVRM:.*RmInitializeGsp.*fail", re.IGNORECASE), "init_failed"),
    (re.compile(r"NVRM:.*Falling back to (?:host|kernel) RM", re.IGNORECASE),
     "fallback"),
    (re.compile(r"NVRM:.*GSP.*crash", re.IGNORECASE), "crashed"),
    (re.compile(r"NVRM:.*GSP.*timed?\s*out", re.IGNORECASE), "timeout"),
    (re.compile(r"NVRM:.*GSP.*failed", re.IGNORECASE), "failed"),
    (re.compile(r"NVRM:.*GSP\s*RPC", re.IGNORECASE), "rpc_issue"),
    (re.compile(r"NVRM:.*GSP\s*error", re.IGNORECASE), "error"),
]


def _query_gpu(fields: list[str], timeout: float = 2.0) -> Optional[list[dict]]:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(fields)}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    out: list[dict] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(fields):
            continue
        out.append(dict(zip(fields, parts)))
    return out


def gsp_firmware_versions() -> list[dict]:
    """One row per GPU : {index, name, gsp_firmware_version, gsp_mode}."""
    # gsp_firmware_version is the user-readable string. Some drivers
    # also expose gpu_kernel_mode but most don't — best effort.
    rows = _query_gpu(["index", "name", "gsp_firmware_version"])
    if rows is None:
        return []
    return [{
        "index": int(r.get("index", "0")) if r.get("index", "").isdigit() else 0,
        "name": r.get("name", "?"),
        "gsp_firmware_version": r.get("gsp_firmware_version", "").strip(),
    } for r in rows]


def journalctl_kernel_lines(since: str = "24 hours ago",
                              timeout: float = 3.0) -> Optional[list[str]]:
    """`journalctl -k --since=<since>` lines, or None on failure."""
    if not shutil.which("journalctl"):
        return None
    try:
        r = subprocess.run(
            ["journalctl", "-k", f"--since={since}", "--no-pager",
             "--output=cat"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.splitlines()


def scan_for_gsp_events(lines: list[str]) -> list[dict]:
    """Match each kernel line against the GSP patterns. Returns event
    list with {kind, line}."""
    events: list[dict] = []
    for ln in lines:
        if "NVRM" not in ln and "nvidia" not in ln.lower():
            continue
        for pat, kind in _GSP_PATTERNS:
            if pat.search(ln):
                events.append({"kind": kind, "line": ln.strip()[:240]})
                break
    return events


def classify(gpus: list[dict], events: list[dict]) -> dict:
    """Return {verdict, reason, recovery, gsp_in_use}."""
    has_fw = any(g.get("gsp_firmware_version") for g in gpus)
    serious_kinds = {"crashed", "timeout", "failed", "init_failed"}
    has_serious = any(e["kind"] in serious_kinds for e in events)
    has_fallback = any(e["kind"] == "fallback" for e in events)
    if not gpus:
        return {"verdict": "unknown",
                "reason": "no GPUs visible to nvidia-smi.",
                "recovery": "", "gsp_in_use": False}
    if has_serious:
        return {
            "verdict": "crashed",
            "reason": (f"{len([e for e in events if e['kind'] in serious_kinds])}"
                       " GSP crash/timeout/init-failure line(s) found in dmesg. "
                       "Driver has fallen back to slower host RM."),
            "recovery": ("sudo modprobe -r nvidia_uvm nvidia_drm nvidia_modeset "
                          "nvidia && sudo modprobe nvidia"),
            "gsp_in_use": True,
        }
    if has_fallback:
        return {
            "verdict": "fallback",
            "reason": ("Driver-reported 'Falling back to host RM'. GSP is "
                       "off — perf will be ~5-10% lower and MIG/MPS may not "
                       "work."),
            "recovery": ("sudo modprobe -r nvidia && sudo modprobe nvidia "
                          "NVreg_EnableGpuFirmware=1"),
            "gsp_in_use": False,
        }
    if events:
        return {
            "verdict": "warn",
            "reason": (f"{len(events)} non-critical GSP-related line(s) in "
                       "dmesg. No fallback detected yet."),
            "recovery": "",
            "gsp_in_use": has_fw,
        }
    return {
        "verdict": "ok",
        "reason": ("No GSP errors in last 24 h of dmesg."
                   + (" GSP firmware loaded." if has_fw else "")),
        "recovery": "",
        "gsp_in_use": has_fw,
    }


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    gpus = gsp_firmware_versions()
    lines = journalctl_kernel_lines() or []
    events = scan_for_gsp_events(lines)
    return {
        "ok": True,
        "gpus": gpus,
        "gsp_events": events[-20:],
        "event_count": len(events),
        "verdict": classify(gpus, events),
    }
