"""Module vram_quota — per-process VRAM budget enforcer (R&D #13.3).

Catches one runaway model OOM-ing another. Rules are JSON-defined :

  {
    "rules": [
      {
        "id": "llama-server-cap",
        "process_regex": "llama-server|ollama",
        "max_vram_mib": 20000,
        "grace_s": 60,
        "action": "warn"
      }
    ]
  }

Actions :
  warn      log + audit + notif, no termination
  dry-run   same as warn (alias)
  term      escalation : warn → SIGTERM after grace_s → SIGKILL after 2×grace_s
  kill      immediate SIGKILL after grace_s (skip the SIGTERM step)

Defaults to warn-only — explicit opt-in needed for SIGTERM/SIGKILL.

Stdlib only : subprocess (nvidia-smi --query-compute-apps), os.kill, signal.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from typing import Optional


NAME = "vram_quota"

_RULES_PATH = "~/.config/gpu-dashboard/vram_quota.json"
_BREACH_STATE_PATH = "~/.config/gpu-dashboard/vram_quota_state.json"
_AUDIT_PATH = "~/.config/gpu-dashboard/vram_quota_audit.json"
_AUDIT_MAX = 200

_VALID_ACTIONS = {"warn", "dry-run", "term", "kill"}


def rules_path() -> str:
    return os.path.expanduser(_RULES_PATH)


def state_path() -> str:
    return os.path.expanduser(_BREACH_STATE_PATH)


def audit_path() -> str:
    return os.path.expanduser(_AUDIT_PATH)


def load_rules() -> list:
    p = rules_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            d = json.load(f)
        rules = d.get("rules") if isinstance(d, dict) else d
        return rules if isinstance(rules, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_rules(rules: list) -> None:
    p = rules_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump({"rules": rules}, f, indent=2)


def validate_rule(rule: dict) -> Optional[str]:
    if not isinstance(rule, dict):
        return "rule must be a dict"
    if not rule.get("id"):
        return "rule needs an 'id'"
    if not rule.get("process_regex"):
        return "rule needs a 'process_regex'"
    try:
        re.compile(rule["process_regex"])
    except re.error as e:
        return f"process_regex compile failed: {e}"
    try:
        int(rule.get("max_vram_mib", 0))
    except (ValueError, TypeError):
        return "max_vram_mib must be an integer"
    if rule.get("action", "warn") not in _VALID_ACTIONS:
        return f"action must be one of {sorted(_VALID_ACTIONS)}"
    return None


def probe_compute_apps() -> list:
    """Run nvidia-smi --query-compute-apps. Returns list of
    {pid, name, used_memory_mib}."""
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-compute-apps=pid,name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return []
    if r.returncode != 0 or not r.stdout:
        return []
    out: list = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            mib = int(parts[2])
        except ValueError:
            continue
        out.append({"pid": pid, "name": parts[1], "used_memory_mib": mib})
    return out


def load_state() -> dict:
    """Track first-breach timestamps per (rule_id, pid)."""
    p = state_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    p = state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(state, f, indent=2)


def append_audit(entry: dict) -> None:
    """Append an enforcement event to a bounded ring buffer on disk."""
    p = audit_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    log = []
    if os.path.exists(p):
        try:
            with open(p) as f:
                log = json.load(f)
            if not isinstance(log, list):
                log = []
        except (OSError, json.JSONDecodeError):
            log = []
    log.append(entry)
    log = log[-_AUDIT_MAX:]
    with open(p, "w") as f:
        json.dump(log, f, indent=2)


def load_audit(limit: int = 50) -> list:
    p = audit_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p) as f:
            log = json.load(f)
        return (log[-limit:] if isinstance(log, list) else [])
    except (OSError, json.JSONDecodeError):
        return []


def _send_signal(pid: int, sig) -> bool:
    """Best-effort os.kill. Returns False if process is already gone or
    we lack permission."""
    try:
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def evaluate(processes: Optional[list] = None, now: Optional[float] = None,
             dry_run_global: bool = False) -> list:
    """Walk every rule × every process. Returns list of breach events :
      [{rule_id, pid, name, used_mib, max_mib, action, escalation,
        breached_for_s, dry_run}]
    Side effects (when not dry-run) : update state file + potentially
    send SIGTERM/SIGKILL + audit row.
    """
    if processes is None:
        processes = probe_compute_apps()
    now = now if now is not None else time.time()
    rules = load_rules()
    if not rules or not processes:
        return []
    state = load_state()
    fires: list = []
    for rule in rules:
        if validate_rule(rule):
            continue
        try:
            rx = re.compile(rule["process_regex"])
        except re.error:
            continue
        max_mib = int(rule.get("max_vram_mib", 0))
        grace_s = int(rule.get("grace_s", 60) or 60)
        action = rule.get("action", "warn")
        dry_run = dry_run_global or action in ("warn", "dry-run")
        for proc in processes:
            name = proc.get("name", "")
            used = int(proc.get("used_memory_mib", 0))
            pid = int(proc.get("pid", 0))
            if not rx.search(name):
                continue
            if used <= max_mib:
                # Reset breach state when usage drops
                state.pop(f"{rule['id']}:{pid}", None)
                continue
            # In breach
            key = f"{rule['id']}:{pid}"
            first_ts = state.get(key, now)
            state[key] = first_ts
            elapsed = now - float(first_ts)
            escalation = "watching"
            if elapsed >= grace_s:
                if action == "term":
                    escalation = "term-sent" if not dry_run else "would-term"
                    if not dry_run:
                        _send_signal(pid, signal.SIGTERM)
                elif action == "kill":
                    escalation = "kill-sent" if not dry_run else "would-kill"
                    if not dry_run:
                        _send_signal(pid, signal.SIGKILL)
            if elapsed >= 2 * grace_s and action == "term" and not dry_run:
                escalation = "kill-sent-after-term"
                _send_signal(pid, signal.SIGKILL)
            event = {
                "rule_id": rule["id"], "pid": pid, "name": name,
                "used_mib": used, "max_mib": max_mib,
                "action": action, "escalation": escalation,
                "breached_for_s": int(elapsed),
                "dry_run": dry_run,
                "ts": int(now),
            }
            fires.append(event)
            if not dry_run or action in ("warn", "dry-run"):
                append_audit(event)
    save_state(state)
    return fires


def status() -> dict:
    """Top-level snapshot for the UI : rules + recent fires."""
    return {
        "ok": True,
        "rules": load_rules(),
        "audit": load_audit(50),
        "actions_supported": sorted(_VALID_ACTIONS),
    }
