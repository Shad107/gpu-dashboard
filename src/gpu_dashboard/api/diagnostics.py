"""HTTP handlers for read-only diagnostics : drift / ECC / idle audit /
clock events / journalctl tail / status bar.

Extracted from the legacy monolith in cycle 7a of the api/ split.
Covers R&D #4.2 / #4.3 / #4.5 / #5.2 / #5.4 / #6.7.

Cycle 7b (next) moves thermal coach, sys-context, cgroup power, prom
metrics, and AlertManager rules into a sibling file (could keep in this
file or split out — TBD when we get there).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Optional, Tuple

from . import _monolith as _m
from .. import detect


Response = Tuple[int, dict]


# Forwarding stubs so tests patching api._monolith.X take effect here too.
def _gpu_card_snapshot(gpu_index: int = 0):
    return _m._gpu_card_snapshot(gpu_index)


def _gpus_available():
    return _m._gpus_available()


def _parse_gpu_index(params):
    return _m._parse_gpu_index(params)


# ─── R&D #6.7 — journalctl bridge with saved filters ─────────────────────────
# Pre-canned filters for GPU-relevant log noise. Each maps to a regex applied
# to journalctl messages.
_JOURNAL_FILTERS = {
    "nvidia":   r"nvidia|NVRM",
    "nvrm":     r"NVRM",
    "xid":      r"NVRM:.*Xid|\bXid\s*\(",
    "oom":      r"oom-killer|out of memory|OOM",
    "thermal":  r"thermal|throttle|over[- ]?temp",
    "pcieport": r"pcieport|PCIe|aer",
    "xorg":     r"\bXorg\b|nvidia-modeset",
    "all":      r".",
}


def handle_journal_tail(ctx: dict, params: Optional[dict] = None) -> Response:
    """Return recent journalctl entries matching a pre-canned filter.

    Query params :
      filter = nvidia (default) | nvrm | xid | oom | thermal | pcieport | xorg | all
      since  = '1h' (default), '15m', '24h', etc. (passed to --since)
      limit  = 100 (default), max 500
    """
    import re as _re
    params = params or {}
    fkey = params.get("filter", "nvidia")
    if fkey not in _JOURNAL_FILTERS:
        return 400, {"ok": False, "error": f"unknown filter '{fkey}'"}
    since = params.get("since", "1h")
    if not _re.match(r"^\d{1,4}(m|h|d|s)$", since):
        return 400, {"ok": False, "error": "since must be like '1h', '30m', '24h'"}
    try:
        limit = max(1, min(500, int(params.get("limit", 100))))
    except (ValueError, TypeError):
        limit = 100

    pattern = _JOURNAL_FILTERS[fkey]
    # Convert 'XX{m,h,d,s}' to journalctl-accepted 'X minute/hour/day/second ago'
    unit_map = {"m": "minutes", "h": "hours", "d": "days", "s": "seconds"}
    since_human = f"{since[:-1]} {unit_map[since[-1]]} ago"
    try:
        # Try kernel ring buffer first, fall back to all-journal on permission errors
        r = subprocess.run(
            ["journalctl", "-k", "--since", since_human, "-o", "short-iso", "--no-pager"],
            capture_output=True, text=True, timeout=4,
        )
        # If -k failed or returned nothing useful, retry without -k
        if r.returncode != 0 or "-- No entries --" in r.stdout:
            r = subprocess.run(
                ["journalctl", "--since", since_human, "-o", "short-iso", "--no-pager"],
                capture_output=True, text=True, timeout=4,
            )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return 200, {"ok": True, "available": False, "reason": f"journalctl unavailable: {e}"}
    if r.returncode != 0:
        return 200, {"ok": True, "available": False, "reason": "journalctl returned non-zero"}

    rx = _re.compile(pattern, _re.IGNORECASE)
    entries: list = []
    for line in r.stdout.splitlines():
        if not rx.search(line):
            continue
        # Parse 'YYYY-MM-DDTHH:MM:SS+0200 host kernel: msg' shape
        parts = line.split(" ", 3)
        if len(parts) < 4:
            entries.append({"ts": "", "host": "", "source": "", "msg": line})
            continue
        ts, host, source, msg = parts
        entries.append({"ts": ts, "host": host, "source": source.rstrip(":"), "msg": msg})
    # Keep the most recent `limit`
    entries = entries[-limit:]

    # Detect Xid errors specifically — useful for chart markers
    xids: list = []
    rx_xid = _re.compile(r"\bXid\s*\([^)]*\):\s*(\d+),?\s*(.*)$")
    for e in entries:
        m = rx_xid.search(e["msg"])
        if m:
            xids.append({"ts": e["ts"], "xid_code": int(m.group(1)), "summary": m.group(2)})

    return 200, {
        "ok": True,
        "available": True,
        "filter": fkey,
        "since": since,
        "count": len(entries),
        "entries": entries,
        "xid_events": xids,
        "filters_available": list(_JOURNAL_FILTERS.keys()),
    }


def _diff_snapshots(old: dict, new: dict) -> list:
    """Return list of {field, old, new} for fields that changed.
    Ignores 'ts' (always different)."""
    diffs: list = []
    keys = set(old.keys()) | set(new.keys())
    keys.discard("ts")
    for k in sorted(keys):
        if old.get(k) != new.get(k):
            diffs.append({"field": k, "old": old.get(k), "new": new.get(k)})
    return diffs


def detect_drift_on_startup() -> Optional[list]:
    """Called once at server boot. Reads current snapshot, compares with
    saved baseline, and if anything changed records a history entry +
    updates the baseline. Returns the diff list (empty = no drift).

    Returns None if drift_baseline doesn't exist yet (first ever boot —
    baseline gets created silently).
    """
    new = _read_drift_snapshot()
    if not new or len(new) <= 1:  # only ts
        return None
    baseline_p = _drift_snapshot_path()
    os.makedirs(os.path.dirname(baseline_p), exist_ok=True)
    if not os.path.exists(baseline_p):
        with open(baseline_p, "w") as f:
            json.dump(new, f, indent=2)
        return None
    try:
        with open(baseline_p) as f:
            old = json.load(f)
    except (OSError, json.JSONDecodeError):
        old = {}
    diffs = _diff_snapshots(old, new)
    if diffs:
        # Append to history
        history_p = _drift_history_path()
        try:
            history = []
            if os.path.exists(history_p):
                with open(history_p) as f:
                    history = json.load(f)
            history.append({"ts": new["ts"], "diffs": diffs,
                            "old_snapshot": old, "new_snapshot": new})
            # Cap history at 100 entries
            history = history[-100:]
            with open(history_p, "w") as f:
                json.dump(history, f, indent=2)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        # Update baseline
        with open(baseline_p, "w") as f:
            json.dump(new, f, indent=2)
    return diffs


def handle_drift_check(ctx: dict) -> Response:
    """Return the most recent drift entry + current snapshot.

    Response :
      {ok, has_baseline, current: {...}, last_drift: {ts, diffs, ...} | null,
       history_count: int}
    """
    new = _read_drift_snapshot()
    baseline_p = _drift_snapshot_path()
    history_p = _drift_history_path()
    has_baseline = os.path.exists(baseline_p)
    last_drift = None
    history_count = 0
    if os.path.exists(history_p):
        try:
            with open(history_p) as f:
                history = json.load(f)
            history_count = len(history)
            if history:
                last_drift = history[-1]
        except (OSError, json.JSONDecodeError):
            pass
    return 200, {
        "ok": True,
        "has_baseline": has_baseline,
        "current": new,
        "last_drift": last_drift,
        "history_count": history_count,
    }


# ─── R&D #5.4 — Bar JSON for Waybar/polybar/i3blocks/tmux ────────────────────
def handle_bar(ctx: dict, params: Optional[dict] = None) -> Tuple[int, Any]:
    """One-line GPU status formatted for desktop bars.

    Query params :
      fmt = waybar (default) | polybar | i3blocks | tmux | plain

    Returns shape depends on fmt :
      waybar  → JSON {"text", "tooltip", "class", "percentage"}
      polybar → text "..."  (color tags %{Fxxx})
      i3blocks → text "...\n...\nCOLOR"  (3 lines)
      tmux    → text "..."  (uses #[fg=...] tags)
      plain   → text "..."
    """
    fmt = (params or {}).get("fmt", "waybar")
    snap = _gpu_card_snapshot(gpu_index=0)
    if not snap or not snap.get("alive"):
        if fmt == "waybar":
            return 200, {"text": "GPU N/A", "tooltip": "GPU offline", "class": "off", "percentage": 0}
        return 200, "GPU N/A"

    temp = snap.get("temp", 0)
    util = snap.get("util_gpu", 0)
    power = snap.get("power", 0)
    mem_used_g = (snap.get("mem_used_mib", 0) or 0) / 1024
    mem_total_g = (snap.get("mem_total_mib", 0) or 0) / 1024

    # Classify status
    if temp >= 85:
        klass = "critical"; color = "#f87171"; polycolor = "F87171"; tmuxcolor = "red"
    elif temp >= 75:
        klass = "warning"; color = "#fbbf24"; polycolor = "FBBF24"; tmuxcolor = "yellow"
    else:
        klass = "ok"; color = "#4ade80"; polycolor = "4ADE80"; tmuxcolor = "green"

    text = f"{temp}°C {util}% {power:.0f}W"
    tooltip = (
        f"GPU : {snap.get('name', 'GPU')}\n"
        f"Temp : {temp}°C · Util : {util}% · Power : {power:.0f} W\n"
        f"VRAM : {mem_used_g:.1f} / {mem_total_g:.1f} GiB"
    )

    if fmt == "waybar":
        return 200, {
            "text": text,
            "tooltip": tooltip,
            "class": klass,
            "percentage": int(util),
        }
    elif fmt == "polybar":
        return 200, f"%{{F#{polycolor}}}{text}%{{F-}}"
    elif fmt == "i3blocks":
        # full text \n short text \n color
        return 200, f"{text}\n{temp}°\n{color}"
    elif fmt == "tmux":
        return 200, f"#[fg={tmuxcolor}]{text}#[default]"
    else:  # plain
        return 200, text


def handle_ecc_health(ctx: dict) -> Response:
    """ECC error counters + remapped rows health audit.

    Most consumer cards (RTX 3090/4090 etc.) don't expose ECC — returns
    available=false so the UI hides the panel. Datacenter cards (A100,
    H100, Tesla) expose the full counters.

    Response :
      {ok, available, ecc_mode, corrected_total, uncorrected_total,
       remapped_correctable, remapped_uncorrectable, remapped_pending,
       remapped_failure, verdict_kind ('ok'|'watch'|'failing'), verdict_msg}
    """
    fields = [
        "ecc.mode.current",
        "ecc.errors.corrected.aggregate.total",
        "ecc.errors.uncorrected.aggregate.total",
        "remapped_rows.correctable",
        "remapped_rows.uncorrectable",
        "remapped_rows.pending",
        "remapped_rows.failure",
    ]
    try:
        r = subprocess.run(
            ["nvidia-smi", "-i", "0", f"--query-gpu={','.join(fields)}",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"ok": True, "available": False}
    if r.returncode != 0 or not r.stdout.strip():
        return 200, {"ok": True, "available": False}

    parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
    if len(parts) < 7:
        return 200, {"ok": True, "available": False}

    # Heuristic : if EVERY field is N/A, the card doesn't expose ECC at all
    if all(_na(p) for p in parts):
        return 200, {"ok": True, "available": False}

    def _i(s: str) -> Optional[int]:
        if _na(s):
            return None
        try:
            return int(s)
        except ValueError:
            return None

    ecc_mode = None if _na(parts[0]) else parts[0]
    corr_total = _i(parts[1])
    uncorr_total = _i(parts[2])
    rem_corr = _i(parts[3])
    rem_uncorr = _i(parts[4])
    rem_pending = _i(parts[5])
    rem_failure = _i(parts[6])

    # Verdict logic
    if rem_failure and rem_failure > 0:
        verdict_kind = "failing"
        verdict_msg = f"Row remap FAILED on {rem_failure} row(s). VRAM is exhausted of spare rows — replace the card."
    elif uncorr_total and uncorr_total > 0:
        verdict_kind = "failing"
        verdict_msg = f"{uncorr_total} uncorrectable ECC error(s). Memory degrading — back up data + replace soon."
    elif rem_uncorr and rem_uncorr > 0:
        verdict_kind = "watch"
        verdict_msg = f"{rem_uncorr} row(s) remapped due to uncorrectable errors. Monitor closely."
    elif rem_pending and rem_pending > 0:
        verdict_kind = "watch"
        verdict_msg = f"{rem_pending} row(s) pending remap (reboot required to apply)."
    elif corr_total and corr_total > 0:
        verdict_kind = "watch"
        verdict_msg = f"{corr_total} corrected ECC error(s) since last reset. Normal at low counts, alarming if growing fast."
    else:
        verdict_kind = "ok"
        verdict_msg = "No ECC errors, no remapped rows. Memory healthy."

    return 200, {
        "ok": True,
        "available": True,
        "ecc_mode": ecc_mode,
        "corrected_total": corr_total,
        "uncorrected_total": uncorr_total,
        "remapped_correctable": rem_corr,
        "remapped_uncorrectable": rem_uncorr,
        "remapped_pending": rem_pending,
        "remapped_failure": rem_failure,
        "verdict_kind": verdict_kind,
        "verdict_msg": verdict_msg,
    }


def handle_idle_audit(ctx: dict) -> Response:
    """Audit idle-state power draw vs expected baseline for this GPU family.

    Logic :
      1. Read current util + power + pstate via nvidia-smi
      2. If util > 5% → status="busy" (audit only meaningful at idle)
      3. Compare power against family baseline (low_w .. high_w)
      4. Return verdict + list of checklist items if verdict is "high"

    Designed to be polled by the UI About tab; cheap (one nvidia-smi call).
    """
    try:
        r = subprocess.run(
            ["nvidia-smi", "-i", "0",
             "--query-gpu=name,utilization.gpu,power.draw,pstate,persistence_mode",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"ok": True, "available": False}
    if r.returncode != 0 or not r.stdout.strip():
        return 200, {"ok": True, "available": False}

    parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
    if len(parts) < 5:
        return 200, {"ok": True, "available": False}
    name = parts[0]
    try:
        util_gpu = int(parts[1])
        power = float(parts[2])
    except ValueError:
        return 200, {"ok": True, "available": False}
    pstate = parts[3]
    persistence = parts[4]

    baseline = _baseline_for(name)
    if baseline is None:
        return 200, {
            "ok": True, "available": True, "status": "unknown",
            "name": name, "power": power, "pstate": pstate,
            "persistence_mode": persistence,
            "verdict": "no baseline for this GPU family",
            "checklist": [],
        }
    low_w, high_w, family = baseline

    if util_gpu > 5:
        return 200, {
            "ok": True, "available": True, "status": "busy",
            "name": name, "power": power, "util_gpu": util_gpu,
            "pstate": pstate, "persistence_mode": persistence,
            "baseline": {"low": low_w, "high": high_w, "family": family},
            "verdict": "GPU is busy — re-check when idle",
            "checklist": [],
        }

    # Idle ; compare against baseline
    if power <= high_w:
        verdict = "ok"
        msg = f"Idle power {power:.1f} W within expected {low_w}-{high_w} W for {family}."
        checklist = []
    else:
        verdict = "high"
        excess = power - high_w
        msg = f"Idle power {power:.1f} W is {excess:.1f} W above expected high ({high_w} W) for {family}."
        checklist = []
        if persistence and "Disabled" in persistence:
            checklist.append({
                "key": "persistence_mode",
                "label": "Enable persistence mode",
                "hint": "Run : sudo nvidia-smi -pm 1   (prevents driver re-init on every nvidia-smi call)",
            })
        if pstate and pstate != "P8":
            checklist.append({
                "key": "pstate_high",
                "label": f"P-state is {pstate}, expected P8 at idle",
                "hint": "Compositor or background app holding the GPU. Check Xorg, browser hardware accel, OBS.",
            })
        # General checklist items always shown for 'high'
        checklist.append({
            "key": "compositor",
            "label": "Check display compositor / hardware accel",
            "hint": "Disable HW accel in Chrome / Electron apps. Test with `nvidia-smi pmon -c 1`.",
        })
        checklist.append({
            "key": "modeset",
            "label": "Enable kernel modesetting",
            "hint": "Add `nvidia-drm.modeset=1` to kernel cmdline if not already set.",
        })

    return 200, {
        "ok": True, "available": True, "status": "idle",
        "name": name, "power": power, "util_gpu": util_gpu,
        "pstate": pstate, "persistence_mode": persistence,
        "baseline": {"low": low_w, "high": high_w, "family": family},
        "verdict": msg,
        "verdict_kind": verdict,
        "checklist": checklist,
    }


def handle_clock_events(ctx: dict) -> Response:
    """Decode current throttle reasons from nvidia-smi clocks_event_reasons.

    Returns a list of reasons currently 'Active' with a plain-language label
    + mitigation hint. Empty list means no throttle.

    Response :
      {ok: bool, available: bool, reasons: [{key, label, hint}], raw: {...}}
    """
    fields = [f for f, _ in _CLOCK_EVENT_REASONS]
    try:
        r = subprocess.run(
            ["nvidia-smi", f"--query-gpu={','.join(fields)}", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"ok": True, "available": False, "reasons": [], "raw": {}}
    if r.returncode != 0 or not r.stdout.strip():
        return 200, {"ok": True, "available": False, "reasons": [], "raw": {}}
    parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
    raw: dict = {}
    reasons: list = []
    for (field, (key, label, hint)), value in zip(_CLOCK_EVENT_REASONS, parts):
        active = value.lower() == "active"
        raw[key] = active
        if active:
            reasons.append({"key": key, "label": label, "hint": hint})
    return 200, {"ok": True, "available": True, "reasons": reasons, "raw": raw}


# ── Helpers moved with the handlers ─────────────────────

def _read_drift_snapshot() -> dict:
    """Capture the current driver+kernel+ECC/MIG fingerprint."""
    snap: dict = {"ts": int(time.time())}
    # nvidia-smi side
    try:
        r = subprocess.run(
            ["nvidia-smi", "-i", "0", f"--query-gpu={','.join(_DRIFT_FIELDS_CMD)}",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
            for i, key in enumerate(_DRIFT_FIELDS_CMD):
                snap[key.replace(".", "_")] = parts[i] if i < len(parts) else ""
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    # Kernel side
    try:
        r = subprocess.run(["uname", "-r"], capture_output=True, text=True, timeout=1)
        if r.returncode == 0:
            snap["kernel_release"] = r.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return snap

def _drift_snapshot_path() -> str:
    return os.path.expanduser("~/.config/gpu-dashboard/drift_baseline.json")

def _drift_history_path() -> str:
    return os.path.expanduser("~/.config/gpu-dashboard/drift_history.json")

# ─── R&D #4.3 — ECC + memory health audit ────────────────────────────────────
def _na(s: str) -> bool:
    """nvidia-smi uses '[N/A]' or 'N/A' for unsupported fields."""
    return s.strip().upper() in ("N/A", "[N/A]", "", "NOT SUPPORTED")

def _baseline_for(name: str) -> Optional[tuple]:
    if not name:
        return None
    for needle, band in _IDLE_BASELINES:
        if needle.lower() in name.lower():
            return band
    return None


# Idle baselines reference table (used by _baseline_for + handle_idle_audit)
# ─── R&D #4.5 — Idle-state audit ─────────────────────────────────────────────
# Reference baselines : expected idle draw (W) per GPU family.
# Sources : community reports + datasheet idle figures, conservative bands.
_IDLE_BASELINES = [
    # (substring in name → (low_w, high_w, family_label))
    ("RTX 4090",  (15, 25,  "Ada/RTX 4090")),
    ("RTX 4080",  (12, 22,  "Ada/RTX 4080")),
    ("RTX 4070",  ( 8, 16,  "Ada/RTX 4070")),
    ("RTX 4060",  ( 6, 14,  "Ada/RTX 4060")),
    ("RTX 3090",  (15, 25,  "Ampere/RTX 3090")),
    ("RTX 3080",  (12, 22,  "Ampere/RTX 3080")),
    ("RTX 3070",  ( 8, 18,  "Ampere/RTX 3070")),
    ("RTX 3060",  ( 8, 16,  "Ampere/RTX 3060")),
    ("RTX 2080",  (10, 20,  "Turing/RTX 2080")),
    ("RTX 2070",  ( 8, 16,  "Turing/RTX 2070")),
    ("RTX 2060",  ( 7, 14,  "Turing/RTX 2060")),
    ("Tesla",     ( 8, 25,  "Tesla/datacenter")),
    ("A100",      (45, 75,  "A100 datacenter")),
    ("H100",      (60, 100, "H100 datacenter")),
]


# Module constants moved with handlers
# ─── R&D #5.2 — Driver/kernel drift detector ─────────────────────────────────
_DRIFT_FIELDS_CMD = ["driver_version", "vbios_version", "name",
                     "persistence_mode", "ecc.mode.current", "mig.mode.current"]

# ─── R&D #4.2 — Clocks-event-reasons decoder ────────────────────────────────
_CLOCK_EVENT_REASONS = [
    # field name in nvidia-smi → (key, label_short, mitigation_hint)
    ("clocks_event_reasons.gpu_idle",                ("gpu_idle",       "Idle",          "Normal — GPU is idle.")),
    ("clocks_event_reasons.applications_clocks_setting", ("apps_clocks", "Apps clocks",   "User-set application clocks limit.")),
    ("clocks_event_reasons.sw_power_cap",            ("sw_power_cap",   "Power cap",     "Power limit hit — raise --power-limit or reduce workload.")),
    ("clocks_event_reasons.hw_slowdown",             ("hw_slowdown",    "HW slowdown",   "Hardware throttle. Check temp + PSU + cables (over_temp / power_brake).")),
    ("clocks_event_reasons.sync_boost",              ("sync_boost",     "Sync boost",    "Boost is sync'd with another GPU in the same group.")),
    ("clocks_event_reasons.sw_thermal_slowdown",     ("sw_thermal",     "SW thermal",    "Driver-side thermal throttle. Improve cooling / lower clocks.")),
    ("clocks_event_reasons.hw_thermal_slowdown",     ("hw_thermal",     "HW thermal",    "Hardware thermal limit reached. Critical — clean fans, repaste, lower PL.")),
    ("clocks_event_reasons.hw_power_brake_slowdown", ("hw_power_brake", "Power brake",   "External PSU brake signal — bad cable / underspec'd PSU.")),
    # display_clock_setting removed — not exposed by driver 560+. Keep
    # the list to widely-supported fields to avoid 'invalid field' rc=2.
]
