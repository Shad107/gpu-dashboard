"""Module oomd_correlator — systemd-oomd kill-event correlator (R&D #34.3).

systemd-oomd (PSI-driven userspace OOM killer, introduced in
systemd 247) can kill cgroups based on memory.pressure thresholds
that you set via `ManagedOOMMemoryPressure*=` directives or the
distro default. For an inference host the default Ubuntu/Fedora
config is often *aggressive* enough to kill llama-server during a
prompt spike — yet the only trace is a journal entry that the user
doesn't think to look at.

This module probes:

  systemctl is-active systemd-oomd
  journalctl -u systemd-oomd --since "1h" -o json

Parses any "Killed <cgroup-path> due to memory pressure" entries,
matches the cgroup against the known LLM unit list, and emits:

  not_installed       unit not on this host — clean, no concern
  inactive            installed but not running — clean
  active_clean        running, no kills in the last hour
  active_killed_llm   running, has killed an LLM daemon recently —
                      surface unit list + recipe (#32.5 memcap,
                      #33.6 cpuio elevation, #31.4 oom_priority)
  active_killed_other running, killed something non-LLM (FYI only)
  unknown             systemctl unavailable

Subprocess calls are wrapped with a 2 s timeout. When systemctl /
journalctl are missing (Alpine, BusyBox), we surface
verdict=unknown instead of throwing.

stdlib only.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Optional, Tuple


NAME = "oomd_correlator"


# Reuse the standard LLM unit-name catalog
LLM_UNIT_PATTERNS = (
    "ollama", "llama-server", "llama_server", "llama.cpp", "llamacpp",
    "vllm", "sglang", "exllamav2", "exllama", "comfyui",
)


def _run_systemctl_is_active() -> Tuple[Optional[str], Optional[int]]:
    """Subprocess wrapper, isolated for test injection."""
    if not shutil.which("systemctl"):
        return (None, None)
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "systemd-oomd"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return (None, None)
    out = (r.stdout + r.stderr).strip()
    return (out, r.returncode)


def check_active() -> str:
    out, rc = _run_systemctl_is_active()
    if out is None and rc is None:
        return "unknown"
    if out is None:
        return "unknown"
    low = out.lower()
    if "could not be found" in low or "not-found" in low or rc == 4:
        return "not_installed"
    if "active" == low.split()[0] if low.split() else "":
        return "active"
    if "inactive" in low or "failed" in low:
        return "inactive"
    return "unknown"


def fetch_recent_journal(unit: str = "systemd-oomd",
                          since: str = "1 hour ago",
                          timeout_s: float = 2.0) -> str:
    if not shutil.which("journalctl"):
        return ""
    try:
        r = subprocess.run(
            ["journalctl", "-u", unit, "--since", since,
             "-o", "json", "--no-pager"],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout


_KILL_RE = re.compile(
    r"^Killed\s+(\S+(?:\.service|\.scope|\.slice))\s+due to memory pressure",
    re.IGNORECASE,
)


def parse_journal(text: str) -> list:
    out: list = []
    if not text:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("MESSAGE", "")
        m = _KILL_RE.match(msg)
        if not m:
            continue
        target = m.group(1)
        ts_s = obj.get("__REALTIME_TIMESTAMP", "0")
        try:
            ts = int(ts_s)
        except ValueError:
            ts = 0
        out.append({"message": msg, "target": target, "timestamp_us": ts})
    return out


def is_llm_victim(target: Optional[str]) -> bool:
    if not target:
        return False
    low = target.lower()
    return any(p in low for p in LLM_UNIT_PATTERNS)


def classify(state: str, events: list) -> dict:
    if state == "not_installed":
        return {"verdict": "not_installed",
                "reason": ("systemd-oomd unit is not installed on this "
                           "host — no userspace OOM killer to worry about."),
                "recommendation": ""}
    if state == "inactive":
        return {"verdict": "inactive",
                "reason": ("systemd-oomd is installed but inactive — "
                           "won't kill anything based on PSI."),
                "recommendation": ""}
    if state == "unknown":
        return {"verdict": "unknown",
                "reason": "Could not determine systemd-oomd state.",
                "recommendation": ""}
    # active
    llm_kills = [e for e in events if is_llm_victim(e["target"])]
    if llm_kills:
        targets = sorted({e["target"] for e in llm_kills})
        return {
            "verdict": "active_killed_llm",
            "reason": (f"systemd-oomd killed LLM daemon(s) "
                       f"{', '.join(targets)} in the last hour. The "
                       f"daemon was selected because its cgroup's "
                       f"memory.pressure crossed the configured "
                       f"threshold."),
            "recommendation": (
                "# Multi-layer fix:\n"
                "# 1. #32.5 cgroup_memcap — set MemorySwapMax=0 +\n"
                "#                          MemoryMax=infinity on the unit\n"
                "# 2. #31.4 oom_priority  — OOMScoreAdjust=-500 makes\n"
                "#                          the kernel OOM-killer skip it\n"
                "# 3. ManagedOOMMemoryPressure=kill on the unit lets\n"
                "#    you opt OUT of oomd targeting this service.\n"
                "# Quick mute (root):\n"
                "sudo systemctl edit --force --full <unit>.service\n"
                "# add: ManagedOOMMemoryPressure=disabled"
            ),
        }
    if events:
        return {"verdict": "active_killed_other",
                "reason": (f"systemd-oomd active. "
                           f"{len(events)} kill event(s) in the last "
                           f"hour, none targeted an LLM daemon."),
                "recommendation": ""}
    return {"verdict": "active_clean",
            "reason": ("systemd-oomd active, no kill events in the "
                       "last hour."),
            "recommendation": ""}


_RANK = {
    "not_installed": 0,
    "inactive": 0,
    "active_clean": 0,
    "active_killed_other": 1,
    "unknown": 1,
    "active_killed_llm": 3,
}


def status(cfg=None) -> dict:
    state = check_active()
    events: list = []
    if state == "active":
        text = fetch_recent_journal()
        events = parse_journal(text)
    verdict = classify(state, events)
    return {
        "ok": True,
        "state": state,
        "event_count": len(events),
        "events": events,
        "verdict": verdict,
    }
