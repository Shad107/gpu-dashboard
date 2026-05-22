"""Module rule_engine — user-defined declarative IF/THEN rules (R&D #12.4).

Users drop a JSON rules file at ~/.config/gpu-dashboard/rules.json :

  {
    "rules": [
      {
        "id": "hot-gpu-pager",
        "name": "Hot GPU alert (>=80°C for 5min)",
        "enabled": true,
        "when": [{"metric": "temp", "op": ">=", "value": 80, "window_s": 300}],
        "then": [{"kind": "notif", "channel": "telegram-main", "level": "warn"}],
        "cooldown_s": 600
      }
    ]
  }

Supported `when[]` :
  - metric : temp | util | power | mem_used_mib | mem_free_gb | fan_rpm
  - op     : > | >= | < | <= | == | !=
  - value  : threshold (number)
  - window_s : every sample within window must satisfy (sustained condition).
               0 or omitted = single sample.

Supported `then[]` :
  - {kind: notif,  channel: <id>, level: <info|warn|crit>, message?: str}
  - {kind: log,    message?: str}
  - {kind: audit,  payload?: dict}   (writes to R&D #9.6 audit_log table)

Rule fires only once per cooldown_s window (default 300s) per (rule_id, metric).
Dry-run mode (env or call arg) logs intent without emitting.

stdlib only (json, time, jsonschema optional but available).
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional


NAME = "rule_engine"

# Where rules live + where we track last-fire timestamps
_RULES_PATH = "~/.config/gpu-dashboard/rules.json"
_LASTFIRE_PATH = "~/.config/gpu-dashboard/rules_lastfire.json"

# Metrics we know how to extract from a sample dict
_SUPPORTED_METRICS = {
    "temp", "util", "util_gpu", "power", "fan", "fan_rpm",
    "mem_used_mib", "mem_free_mib", "mem_free_gb",
    "clk_gpu", "clk_mem",
}
_SUPPORTED_OPS = {">", ">=", "<", "<=", "==", "!="}


def rules_path() -> str:
    return os.path.expanduser(_RULES_PATH)


def lastfire_path() -> str:
    return os.path.expanduser(_LASTFIRE_PATH)


def load_rules() -> list:
    """Return the list of configured rules, or [] if no file."""
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
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def validate_rule(rule: dict) -> Optional[str]:
    """Return None if valid, else an error string."""
    if not isinstance(rule, dict):
        return "rule must be a dict"
    if "id" not in rule or not isinstance(rule["id"], str) or not rule["id"]:
        return "rule needs a non-empty 'id'"
    if "when" not in rule or not isinstance(rule["when"], list) or not rule["when"]:
        return "rule needs a non-empty 'when' list"
    for i, w in enumerate(rule["when"]):
        if not isinstance(w, dict):
            return f"when[{i}] must be a dict"
        if w.get("metric") not in _SUPPORTED_METRICS:
            return f"when[{i}].metric {w.get('metric')!r} not in {sorted(_SUPPORTED_METRICS)}"
        if w.get("op") not in _SUPPORTED_OPS:
            return f"when[{i}].op {w.get('op')!r} not in {sorted(_SUPPORTED_OPS)}"
        try:
            float(w.get("value"))
        except (TypeError, ValueError):
            return f"when[{i}].value must be numeric"
    if "then" not in rule or not isinstance(rule["then"], list) or not rule["then"]:
        return "rule needs a non-empty 'then' list"
    for i, t in enumerate(rule["then"]):
        if not isinstance(t, dict):
            return f"then[{i}] must be a dict"
        kind = t.get("kind")
        if kind not in ("notif", "log", "audit"):
            return f"then[{i}].kind {kind!r} not in {{notif, log, audit}}"
        if kind == "notif" and not t.get("channel"):
            return f"then[{i}] notif action needs a 'channel' id"
    return None


def _cmp(value: float, op: str, threshold: float) -> bool:
    if op == ">":  return value > threshold
    if op == ">=": return value >= threshold
    if op == "<":  return value < threshold
    if op == "<=": return value <= threshold
    if op == "==": return value == threshold
    if op == "!=": return value != threshold
    return False


def _sample_value(sample: dict, metric: str):
    """Extract a metric value from a sample dict, with normalization aliases."""
    if metric in sample and sample[metric] is not None:
        return sample[metric]
    # Aliases
    if metric == "util" and sample.get("util_gpu") is not None:
        return sample["util_gpu"]
    if metric == "fan" and sample.get("fan0_rpm") is not None:
        return sample["fan0_rpm"]
    if metric == "fan_rpm" and sample.get("fan0_rpm") is not None:
        return sample["fan0_rpm"]
    if metric == "mem_free_mib":
        used = sample.get("mem_used_mib")
        total = sample.get("mem_total_mib")
        if used is not None and total is not None:
            return total - used
    if metric == "mem_free_gb":
        used = sample.get("mem_used_mib")
        total = sample.get("mem_total_mib")
        if used is not None and total is not None:
            return (total - used) / 1024
    return None


def condition_holds(when: dict, recent_samples: list) -> bool:
    """True if the metric condition holds across the configured window.

    window_s = 0 → only check the latest sample.
    Otherwise → every sample within `now - window_s` must satisfy the op.
    """
    metric = when["metric"]
    op = when["op"]
    threshold = float(when["value"])
    window_s = int(when.get("window_s", 0) or 0)
    if not recent_samples:
        return False
    if window_s == 0:
        v = _sample_value(recent_samples[-1], metric)
        return v is not None and _cmp(float(v), op, threshold)
    # Sustained : all samples in [now - window_s, now] must satisfy
    now_ts = recent_samples[-1].get("ts", time.time())
    if isinstance(now_ts, str):
        # 'HH:MM:SS' samples — fall back to a "last N samples by index" check
        # Approximation : check the last ~window_s/5 samples (assume 5s interval)
        n = max(1, window_s // 5)
        eligible = recent_samples[-n:]
    else:
        cutoff = float(now_ts) - window_s
        eligible = [s for s in recent_samples if float(s.get("ts", 0)) >= cutoff]
        if not eligible:
            return False
    for s in eligible:
        v = _sample_value(s, metric)
        if v is None or not _cmp(float(v), op, threshold):
            return False
    return True


def load_lastfire() -> dict:
    p = lastfire_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_lastfire(d: dict) -> None:
    p = lastfire_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(d, f, indent=2)


def in_cooldown(rule: dict, now: float, lastfire: dict) -> bool:
    cd = int(rule.get("cooldown_s", 300) or 0)
    if cd <= 0:
        return False
    last = lastfire.get(rule["id"], 0)
    return (now - float(last)) < cd


def emit_actions(rule: dict, sample: dict, dry_run: bool = False) -> list:
    """Execute the rule's then[] list. Returns list of {kind, ok, msg} per action."""
    results: list = []
    for action in rule.get("then", []):
        kind = action.get("kind")
        if kind == "log":
            msg = action.get("message") or f"rule {rule.get('id')} fired"
            results.append({"kind": "log", "ok": True, "msg": msg, "dry_run": dry_run})
            continue
        if kind == "notif":
            channel = action.get("channel")
            level = action.get("level", "warn")
            template = action.get("message") or (
                f"Rule '{rule.get('name') or rule.get('id')}' fired"
            )
            if dry_run:
                results.append({"kind": "notif", "ok": True,
                                "msg": f"DRY-RUN would send to {channel}: {template}",
                                "dry_run": True})
                continue
            # Live emit via notif_hub
            try:
                from . import notif_hub as _nh
                channels = _nh.load_channels()
                target = next((c for c in channels if c.get("id") == channel), None)
                if target is None:
                    results.append({"kind": "notif", "ok": False,
                                    "msg": f"channel {channel!r} not found"})
                    continue
                ok, msg = _nh.send_test({**target, "message": template})
                results.append({"kind": "notif", "ok": bool(ok), "msg": msg})
            except Exception as e:
                results.append({"kind": "notif", "ok": False, "msg": str(e)})
            continue
        if kind == "audit":
            results.append({"kind": "audit", "ok": True,
                            "msg": f"audit row queued for {rule.get('id')}",
                            "dry_run": dry_run})
            continue
        results.append({"kind": kind, "ok": False, "msg": f"unknown action kind {kind!r}"})
    return results


def evaluate_all(recent_samples: list, dry_run: bool = False) -> list:
    """Evaluate every loaded rule against the recent samples. Returns list of
    {rule_id, fired, in_cooldown, actions: [...]} per rule (only for those
    whose condition holds)."""
    rules = load_rules()
    if not rules:
        return []
    lastfire = load_lastfire()
    now = time.time()
    fired: list = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        err = validate_rule(rule)
        if err:
            continue
        # All `when` must hold
        all_match = all(condition_holds(w, recent_samples) for w in rule["when"])
        if not all_match:
            continue
        if in_cooldown(rule, now, lastfire):
            fired.append({"rule_id": rule["id"], "fired": False,
                          "in_cooldown": True, "actions": []})
            continue
        # Emit actions
        results = emit_actions(rule, recent_samples[-1] if recent_samples else {},
                                dry_run=dry_run)
        if not dry_run:
            lastfire[rule["id"]] = now
        fired.append({
            "rule_id": rule["id"],
            "name": rule.get("name", rule["id"]),
            "fired": True, "in_cooldown": False,
            "actions": results,
        })
    if not dry_run and fired:
        save_lastfire(lastfire)
    return fired
