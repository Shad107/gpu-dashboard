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


def _per_fan_state(cfg):
    return _m._per_fan_state(cfg)


def _alert_consecutive_to_for(*args, **kw):
    from . import alerts as _alerts  # cycle 8 late import to avoid cycle
    return _alerts._alert_consecutive_to_for(*args, **kw)


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


# ── Cycle 7b additions ─────────────────────────────────

# ────────────────────────── GET /api/thermal-stats ─────────────────────────


def handle_thermal_stats(ctx: dict, params: Optional[dict] = None) -> Response:
    """Temperature aggregates over 24h + 24-point downsampled series."""
    storage = ctx.get("storage")
    if storage is None:
        return 503, {"ok": False, "error": "storage not available"}

    import time as _time
    now = int(_time.time())
    gpu = _parse_gpu_index(params or {})

    samples_24h = storage.get_samples(from_ts=now - 86400, to_ts=now, gpu_index=gpu)
    temps = [s["temp"] for s in samples_24h if s.get("temp") is not None]
    avg_temp = sum(temps) / len(temps) if temps else 0
    peak_temp = max(temps) if temps else 0

    samples_7d = storage.get_samples(from_ts=now - 7 * 86400, to_ts=now, gpu_index=gpu)
    above_80 = 0
    prev = None
    for s in samples_7d:
        if s.get("temp") is None:
            prev = None
            continue
        if s["temp"] > 80:
            if prev is not None and prev["temp"] > 80:
                dt = s["ts"] - prev["ts"]
                above_80 += min(dt, 300)
        prev = s

    series = []
    for h in range(24):
        bucket_start = now - (24 - h) * 3600
        bucket_end = bucket_start + 3600
        in_bucket = [s["temp"] for s in samples_24h
                     if bucket_start <= s["ts"] < bucket_end and s.get("temp") is not None]
        series.append(round(sum(in_bucket) / len(in_bucket), 1) if in_bucket else 0)

    return 200, {
        "ok": True,
        "avg_temp_24h": round(avg_temp, 1),
        "peak_temp_24h": peak_temp,
        "time_above_80c_seconds": above_80,
        "series_24h": series,
        "samples_count": len(samples_24h),
    }

# ────────────────────────── GET /api/prom ─────────────────────────────────


def handle_prom(ctx: dict) -> Response:
    """Prometheus text-format exporter. Pluggable into Grafana, VictoriaMetrics,
    Uptime Kuma, blackbox-exporter, etc.

    Returns (200, str). The server wraps with Content-Type: text/plain.
    """
    cfg = ctx["config"]
    gpu_index = cfg.get_int("GPU_INDEX", default=0)
    gpu = _gpu_card_snapshot(gpu_index=gpu_index)
    label = f'{{gpu="{gpu_index}",name="{gpu.get("name", "?")}"}}'

    lines = []

    def metric(name, help_text, mtype, value, lbl=label):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")
        lines.append(f"{name}{lbl} {value}")

    alive = 1 if gpu.get("alive") else 0
    metric("gpu_alive", "1 if nvidia-smi responded for this GPU", "gauge", alive)

    if gpu.get("alive"):
        metric("gpu_temp_celsius", "GPU temperature in Celsius",
               "gauge", gpu.get("temp", 0))
        metric("gpu_fan_percent", "GPU fan speed in percent",
               "gauge", gpu.get("fan_pct", 0))
        metric("gpu_power_watts", "GPU power draw in watts",
               "gauge", gpu.get("power", 0))
        metric("gpu_power_limit_watts", "GPU power limit in watts",
               "gauge", gpu.get("power_limit", 0))
        metric("gpu_util_percent", "GPU utilization in percent",
               "gauge", gpu.get("util_gpu", 0))
        # Memory in bytes (Prometheus convention) — convert from MiB
        mem_used = gpu.get("mem_used_mib", 0) * 1024 * 1024
        mem_total = gpu.get("mem_total_mib", 0) * 1024 * 1024
        metric("gpu_memory_used_bytes", "GPU VRAM in use, bytes",
               "gauge", mem_used)
        metric("gpu_memory_total_bytes", "GPU VRAM total, bytes",
               "gauge", mem_total)

    # OcuLink watchdog status if module is on
    if ctx.get("watchdog_drops") is not None:
        metric("gpu_oculink_drops_total", "OcuLink drop count since boot",
               "counter", ctx["watchdog_drops"])

    # Year-to-date energy + LLM tokens — for Grafana yearly-budget panels
    storage = ctx.get("storage")
    if storage is not None:
        try:
            from .cost import handle_power_stats as _hps  # cycle 6 late import
            _, ps = _hps(ctx)
            if ps.get("ok"):
                metric("gpu_dashboard_kwh_year",
                       "Cumulative energy since Jan 1 (kWh)", "gauge",
                       ps.get("kwh_year", 0))
                metric("gpu_dashboard_cost_year",
                       f"Cumulative cost since Jan 1 ({ps.get('currency','EUR')})",
                       "gauge", ps.get("cost_year", 0))
                metric("gpu_dashboard_kwh_today",
                       "Energy consumed today (kWh)", "gauge",
                       ps.get("kwh_today", 0))
            from .llm import handle_llm_lifetime as _hll  # cycle 4 — late import to avoid cycle
            _, ll = _hll(ctx)
            if ll.get("available"):
                metric("gpu_dashboard_tokens_year_total",
                       "LLM tokens generated since Jan 1", "counter",
                       ll.get("total_tokens_this_year", 0))
                metric("gpu_dashboard_tokens_lifetime_total",
                       "Cumulative LLM tokens since install", "counter",
                       ll.get("total_tokens_generated", 0))
        except Exception:
            pass  # never let metric collection break the endpoint

    # Latest alert age (seconds since most recent alert event), or -1 if none
    if storage is not None:
        try:
            import time as _t3
            alerts = storage.get_events(
                from_ts=int(_t3.time()) - 7 * 86400, kind="alert"
            )
            if alerts:
                age = int(_t3.time()) - max(a["ts"] for a in alerts)
                metric("gpu_dashboard_latest_alert_age_seconds",
                       "Seconds since the most recent alert (within last 7d)",
                       "gauge", age)
        except Exception:
            pass

    return 200, "\n".join(lines) + "\n"

# ────────────────────────── GET /api/logs ─────────────────────────────────


def handle_logs(ctx: dict, params: dict) -> Response:
    """Tail the dashboard log. Two backends in priority order :

    1. LOG_FILE  — plain text file (typical: stdout-redirected from get.sh)
    2. JOURNALCTL_UNIT — `journalctl --user -u <unit> -n <tail> --no-pager`

    Returns {ok, source, lines: [str]} or {ok: False, reason: str}.
    Tail defaults to 100 lines.
    """
    try:
        tail = int(params.get("tail", 100))
        if tail < 0:
            return 400, {"ok": False, "error": "tail must be >= 0"}
    except (ValueError, TypeError):
        return 400, {"ok": False, "error": "tail must be integer"}

    cfg = ctx.get("config")
    log_file = (cfg.get("LOG_FILE", "") if cfg else "").strip()
    journalctl_unit = (cfg.get("JOURNALCTL_UNIT", "") if cfg else "").strip()

    # Auto-detect : if neither is set, default to journalctl --user -u
    # gpu-dashboard.service (the canonical install path documented in README).
    # This avoids the "no log source configured" error for users who
    # installed via the standard systemd-user unit.
    if not log_file and not journalctl_unit:
        try:
            r = subprocess.run(
                ["systemctl", "--user", "is-active", "gpu-dashboard.service"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0 and r.stdout.strip() in ("active", "activating"):
                journalctl_unit = "gpu-dashboard.service"
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            pass

    if log_file:
        log_file = os.path.expanduser(log_file)
        if not os.path.isfile(log_file):
            return 200, {"ok": False, "reason": f"log file does not exist: {log_file}"}
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            return 200, {
                "ok": True,
                "source": "file",
                "path": log_file,
                "lines": all_lines[-tail:] if tail > 0 else [],
                "total": len(all_lines),
            }
        except OSError as e:
            return 200, {"ok": False, "reason": f"could not read log: {e}"}

    if journalctl_unit:
        try:
            r = subprocess.run(
                ["journalctl", "--user", "-u", journalctl_unit,
                 "-n", str(max(1, tail)), "--no-pager", "--output=short-iso"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return 200, {"ok": False, "reason": f"journalctl failed: {r.stderr.strip()}"}
            lines = [l + "\n" for l in r.stdout.splitlines() if l]
            return 200, {
                "ok": True,
                "source": "journalctl",
                "unit": journalctl_unit,
                "lines": lines[-tail:] if tail > 0 else [],
            }
        except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
            return 200, {"ok": False, "reason": f"journalctl unavailable: {e}"}

    return 200, {
        "ok": False,
        "reason": "no log source configured — neither LOG_FILE nor JOURNALCTL_UNIT is set, "
                  "and no `gpu-dashboard.service` user unit appears active. "
                  "Set LOG_FILE=/path/to/log or JOURNALCTL_UNIT=your-unit-name in "
                  "~/.config/gpu-dashboard/config.env, then restart the dashboard.",
    }

def build_alertmanager_rules_yaml(cfg) -> str:
    """Build a Prometheus rules.yaml from the current ALERT_* config.

    Maps each threshold to a corresponding alert rule on the metrics
    exposed by /metrics (R&D #4.1). Uses our gpu_<x> series names.

    Returns a complete YAML payload ready to drop into a Prom
    rule_files dir or referenced from prometheus.yml.
    """
    interval = cfg.get_int("ALERT_MONITOR_INTERVAL", default=30)
    min_n = cfg.get_int("ALERT_MIN_CONSECUTIVE", default=3)
    for_dur = _alert_consecutive_to_for(min_n, interval)

    gpu_temp = cfg.get_int("ALERT_GPU_TEMP_THRESHOLD", default=85)
    fan_pct = cfg.get_int("ALERT_FAN_PCT_THRESHOLD", default=95)
    vram_pct = cfg.get_int("ALERT_VRAM_PCT_THRESHOLD", default=90)

    lines = [
        "# Prometheus AlertManager rules — generated by gpu-dashboard (R&D #7.10).",
        "# Drop in prometheus rule_files or load with promtool check rules.",
        "groups:",
        "- name: gpu-dashboard",
        f"  interval: {interval}s",
        "  rules:",
        "",
        "  - alert: GpuTempHigh",
        f"    expr: gpu_temp_celsius > {gpu_temp}",
        f"    for: {for_dur}",
        "    labels:",
        "      severity: warning",
        "      source: gpu-dashboard",
        "    annotations:",
        f'      summary: "GPU {{{{ $labels.gpu }}}} temperature > {gpu_temp}°C ({{{{ $value }}}}°C)"',
        f'      description: "GPU {{{{ $labels.name }}}} has been above {gpu_temp}°C for {for_dur}. Check cooling / fan curve / workload."',
        "",
        "  - alert: GpuFanHigh",
        f"    expr: gpu_fan_speed_ratio > {fan_pct / 100.0:.2f}",
        f"    for: {for_dur}",
        "    labels:",
        "      severity: warning",
        "      source: gpu-dashboard",
        "    annotations:",
        f'      summary: "GPU {{{{ $labels.gpu }}}} fan > {fan_pct}% sustained"',
        f'      description: "Fan {{{{ $labels.fan }}}} on GPU {{{{ $labels.gpu }}}} stuck above {fan_pct}% — check thermal load."',
        "",
        "  - alert: GpuVramPctHigh",
        "    expr: (gpu_memory_used_bytes / gpu_memory_total_bytes) * 100 > " + str(vram_pct),
        f"    for: {for_dur}",
        "    labels:",
        "      severity: warning",
        "      source: gpu-dashboard",
        "    annotations:",
        f'      summary: "GPU {{{{ $labels.gpu }}}} VRAM > {vram_pct}%"',
        f'      description: "VRAM utilization above {vram_pct}% for {for_dur}. Risk of OOM-killed processes."',
        "",
        "  - alert: GpuOffBus",
        '    expr: absent(gpu_temp_celsius) or (rate(gpu_temp_celsius[1m]) == 0 and gpu_temp_celsius == 0)',
        "    for: 2m",
        "    labels:",
        "      severity: critical",
        "      source: gpu-dashboard",
        "    annotations:",
        '      summary: "GPU disappeared from bus or sampler stuck"',
        '      description: "No temperature sample in the last 2 minutes. nvidia-smi may have failed / PCIe link dropped."',
        "",
        "  - alert: GpuPcieDowngrade",
        '    expr: gpu_pcie_link_gen < on(gpu) group_left() (gpu_pcie_link_gen_max or vector(4))',
        "    for: 1m",
        "    labels:",
        "      severity: warning",
        "      source: gpu-dashboard",
        "    annotations:",
        '      summary: "PCIe link generation below maximum"',
        '      description: "GPU {{ $labels.gpu }} negotiated Gen {{ $value }} — investigate cable / slot / power-saving."',
    ]
    return "\n".join(lines) + "\n"

def handle_alertmanager_rules(ctx: dict) -> Tuple[int, str]:
    """GET /api/alertmanager/rules.yaml — text/yaml download."""
    return 200, build_alertmanager_rules_yaml(ctx["config"])

def _normalize_cgroup(path: str) -> str:
    """Reduce a cgroup path to its meaningful slice / scope name.
    Examples :
      /system.slice/llama-server.service → system.slice/llama-server.service
      /user.slice/user-1000.slice/user@1000.service/app.slice/firefox.service
        → user.slice/firefox.service
      / (root) → root
    """
    if not path or path == "/":
        return "root"
    p = path.strip("/")
    # Collapse user-XXXX.slice / user@XXXX.service noise — keep first slice + final unit
    parts = p.split("/")
    if len(parts) == 1:
        return parts[0]
    # First slice (system.slice / user.slice / ...) + last segment
    return f"{parts[0]}/{parts[-1]}"

def handle_cgroup_power(ctx: dict) -> Response:
    """Attribute total GPU power to cgroups via per-PID SM% share.

    Algorithm :
      1. Read total board power via nvidia-smi
      2. Read per-PID SM% via nvidia-smi pmon
      3. For each PID with SM% > 0, resolve cgroup
      4. Sum SM% per cgroup → est_watts = total * (cgroup_sm / total_sm)
      5. If total SM% == 0 (all idle), attribute proportionally by VRAM
         (fb column) so the user still sees who is HOLDING the GPU

    Response :
      {ok, available, total_power_w, total_sm_pct,
       cgroups: [{name, pids: [...], sm_pct, vram_mib, est_watts}]}
    """
    # Read total power
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"ok": True, "available": False}
    if r.returncode != 0 or not r.stdout.strip():
        return 200, {"ok": True, "available": False}
    try:
        total_power = float(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 200, {"ok": True, "available": False}

    # Read per-PID pmon (sm + fb columns)
    try:
        r = subprocess.run(
            ["nvidia-smi", "pmon", "-c", "1", "-s", "um"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 200, {"ok": True, "available": False}
    if r.returncode != 0:
        return 200, {"ok": True, "available": False}

    # Aggregate by cgroup
    cgroups: dict = {}
    total_sm = 0.0
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Columns : gpu pid type sm mem enc dec jpg ofa fb ccpm command
        parts = line.split()
        if len(parts) < 11:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        sm_pct = 0.0
        try:
            sm_pct = float(parts[3])
        except ValueError:
            pass  # "-" → 0
        vram_mib = 0
        try:
            vram_mib = int(parts[9])
        except ValueError:
            pass

        cg_path = _read_pid_cgroup(pid)
        cg_name = _normalize_cgroup(cg_path) if cg_path else f"pid:{pid}"
        cmd = parts[-1] if len(parts) > 11 else ""

        entry = cgroups.setdefault(cg_name, {
            "name": cg_name, "pids": [], "sm_pct": 0.0,
            "vram_mib": 0, "commands": [],
        })
        entry["pids"].append(pid)
        entry["sm_pct"] += sm_pct
        entry["vram_mib"] += vram_mib
        if cmd and cmd not in entry["commands"]:
            entry["commands"].append(cmd)
        total_sm += sm_pct

    # Compute est_watts
    total_vram = sum(c["vram_mib"] for c in cgroups.values()) or 1
    result_list = []
    for name, c in cgroups.items():
        if total_sm > 0:
            # Distribute by SM% share
            est_w = round(total_power * (c["sm_pct"] / total_sm), 2)
        else:
            # All idle — distribute by VRAM share so user sees who's holding
            est_w = round(total_power * (c["vram_mib"] / total_vram), 2)
        result_list.append({
            "name": name,
            "pids": sorted(c["pids"]),
            "commands": c["commands"],
            "sm_pct": round(c["sm_pct"], 2),
            "vram_mib": c["vram_mib"],
            "est_watts": est_w,
        })
    # Sort by est_watts desc
    result_list.sort(key=lambda x: x["est_watts"], reverse=True)

    return 200, {
        "ok": True,
        "available": True,
        "total_power_w": round(total_power, 2),
        "total_sm_pct": round(total_sm, 2),
        "cgroups": result_list,
    }

_LAST_CPU_LINE: Optional[list] = None

_LAST_CPU_TS: float = 0.0

_LAST_VMSTAT: dict = {}

_LAST_VMSTAT_TS: float = 0.0

def handle_sys_context(ctx: dict) -> Response:
    """Snapshot system metrics that correlate with GPU stalls.

    Returns CPU usage (incl iowait%), load avg, swap activity, RAM used.

    CPU% / iowait% computed as delta vs last call's /proc/stat snapshot.
    First call returns CPU%=null (no baseline yet) but everything else
    is fine.

    pswp_in_per_sec / pgmajfault_per_sec computed similarly from
    /proc/vmstat deltas.
    """
    global _LAST_CPU_LINE, _LAST_CPU_TS, _LAST_VMSTAT, _LAST_VMSTAT_TS

    now = time.time()
    cur_cpu = _read_proc_stat_cpu()
    cpu_pct = None
    iowait_pct = None
    if cur_cpu is not None and _LAST_CPU_LINE is not None:
        diffs = [cur - prev for cur, prev in zip(cur_cpu, _LAST_CPU_LINE)]
        total = sum(diffs)
        if total > 0:
            idle = diffs[3] if len(diffs) > 3 else 0
            iowait = diffs[4] if len(diffs) > 4 else 0
            cpu_pct = round(100.0 * (total - idle) / total, 1)
            iowait_pct = round(100.0 * iowait / total, 1)
    _LAST_CPU_LINE = cur_cpu
    _LAST_CPU_TS = now

    cur_vm = _read_proc_vmstat()
    pswpin_ps = None
    pgmajfault_ps = None
    if _LAST_VMSTAT and _LAST_VMSTAT_TS:
        elapsed = max(0.001, now - _LAST_VMSTAT_TS)
        if "pswpin" in cur_vm and "pswpin" in _LAST_VMSTAT:
            pswpin_ps = max(0, round((cur_vm["pswpin"] - _LAST_VMSTAT["pswpin"]) / elapsed, 2))
        if "pgmajfault" in cur_vm and "pgmajfault" in _LAST_VMSTAT:
            pgmajfault_ps = max(0, round((cur_vm["pgmajfault"] - _LAST_VMSTAT["pgmajfault"]) / elapsed, 2))
    _LAST_VMSTAT = {k: cur_vm.get(k, 0) for k in ("pswpin", "pswpout", "pgmajfault")}
    _LAST_VMSTAT_TS = now

    loadavg = _read_proc_loadavg()
    mem = _read_proc_meminfo()
    mem_total_kb = mem.get("MemTotal", 0)
    mem_free_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
    mem_used_pct = None
    if mem_total_kb > 0:
        mem_used_pct = round(100.0 * (mem_total_kb - mem_free_kb) / mem_total_kb, 1)
    swap_total_kb = mem.get("SwapTotal", 0)
    swap_free_kb = mem.get("SwapFree", 0)
    swap_used_pct = None
    if swap_total_kb > 0:
        swap_used_pct = round(100.0 * (swap_total_kb - swap_free_kb) / swap_total_kb, 1)

    return 200, {
        "ok": True,
        "available": True,
        "cpu_pct": cpu_pct,
        "iowait_pct": iowait_pct,
        "loadavg_1": loadavg[0] if loadavg else None,
        "loadavg_5": loadavg[1] if loadavg else None,
        "loadavg_15": loadavg[2] if loadavg else None,
        "mem_used_pct": mem_used_pct,
        "mem_total_kb": mem_total_kb,
        "swap_used_pct": swap_used_pct,
        "swap_total_kb": swap_total_kb,
        "pswpin_per_sec": pswpin_ps,
        "pgmajfault_per_sec": pgmajfault_ps,
    }

# ─── R&D #5.1 — Thermal headroom coach ───────────────────────────────────────
def _linear_fit(xs: list, ys: list) -> tuple:
    """Plain least-squares linear regression. Returns (slope, intercept).
    Returns (0, mean(ys)) for degenerate input (constant x or too few points)."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        return 0.0, mean_y
    cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = cov_xy / var_x
    intercept = mean_y - slope * mean_x
    return slope, intercept

def _r_squared(xs: list, ys: list, slope: float, intercept: float) -> float:
    """Coefficient of determination R² for the linear fit (0=no fit, 1=perfect).
    Used as the prediction confidence indicator for R&D #8.2."""
    n = len(ys)
    if n < 2:
        return 0.0
    mean_y = sum(ys) / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        return 1.0  # constant y → perfect fit by convention (no variance to explain)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    return max(0.0, 1.0 - ss_res / ss_tot)

def handle_thermal_coach(ctx: dict) -> Response:
    """Surfaces a decision : headroom + projected time-to-throttle from the
    last 5min of samples.

    Logic :
      - Pull last 60 samples (~5 min at 5s interval) from the sampler
      - Linear regression on temp(t)
      - Slowdown temp default 83°C (RTX 3090/4090 typical) — could be read
        from NVML in a future iteration
      - Headroom = slowdown_temp - current_temp
      - Time-to-throttle = (slowdown_temp - last_temp) / slope_per_sec
        (clamped : ∞ if slope <= 0, < 0 if already over)
      - Suggested fan delta : if headroom > 15°C and trend is flat/falling,
        fans can be 5-10% gentler (acoustic win)
    """
    sampler = ctx.get("sampler")
    if not sampler:
        return 200, {"ok": True, "available": False, "reason": "no sampler"}

    snap = sampler.snapshot()
    if not snap or len(snap) < 5:
        return 200, {"ok": True, "available": False,
                     "reason": "not enough samples yet (need 5+)"}

    # Last 5 minutes : assume 5s sampling → 60 samples max
    recent = snap[-60:] if len(snap) > 60 else snap
    # The in-memory ring buffer uses "ts" as a 'HH:MM:SS' string (not epoch).
    # Use sample INDEX as the x-axis. Sampling interval defaults to 5s.
    # slope is then °C per sample ; multiply by (60/interval) for °C/min,
    # and time-to-throttle is in samples → seconds.
    sample_interval_s = float(ctx.get("config", None).get_int("DASHBOARD_REFRESH_INTERVAL", default=5)
                              if ctx.get("config") else 5)
    xs = []
    ys = []
    for i, s in enumerate(recent):
        temp = s.get("temp")
        if temp is None:
            continue
        xs.append(float(i))
        ys.append(float(temp))
    if len(xs) < 3:
        return 200, {"ok": True, "available": False, "reason": "need 3+ valid temp samples"}

    slope_per_sample, intercept = _linear_fit(xs, ys)
    slope = slope_per_sample / sample_interval_s  # °C per second
    confidence = round(_r_squared(xs, ys, slope_per_sample, intercept), 3)
    current_temp = ys[-1]
    slowdown_temp = 83.0  # default for consumer Ampere/Ada
    headroom_c = round(slowdown_temp - current_temp, 1)

    # Time-to-throttle projection
    if slope <= 0 or current_temp >= slowdown_temp:
        projected_throttle_s = None  # not heating (or already over)
    else:
        projected_throttle_s = int((slowdown_temp - current_temp) / slope)

    # Decision : suggest fan delta
    suggested_fan_delta_pct = 0
    suggested_msg_key = "stable"
    if headroom_c > 25 and slope <= 0.005:
        suggested_fan_delta_pct = -10
        suggested_msg_key = "fan_can_be_gentler"
    elif headroom_c > 15 and slope <= 0.01:
        suggested_fan_delta_pct = -5
        suggested_msg_key = "fan_slight_gentler"
    elif headroom_c < 5 or (projected_throttle_s is not None and projected_throttle_s < 120):
        suggested_fan_delta_pct = +10
        suggested_msg_key = "fan_needs_help"
    elif projected_throttle_s is not None and projected_throttle_s < 600:
        suggested_fan_delta_pct = +5
        suggested_msg_key = "warming_up"

    # R&D #8.2 — fire notification hub if imminent throttle + high confidence
    if (projected_throttle_s is not None and projected_throttle_s < 120
            and confidence > 0.5):
        try:
            from ..modules import notif_hub as _nh
            _nh.send(
                level="warning",
                title="⚠️ GPU throttle imminent",
                body=(f"Projected throttle in {projected_throttle_s}s "
                      f"(slope {round(slope * 60, 2)}°C/min, R²={confidence}). "
                      f"Current {round(current_temp, 1)}°C, headroom {headroom_c}°C."),
            )
        except Exception:
            pass  # don't fail the API call on notification errors

    return 200, {
        "ok": True,
        "available": True,
        "current_temp_c": round(current_temp, 1),
        "slowdown_temp_c": slowdown_temp,
        "headroom_c": headroom_c,
        "slope_c_per_min": round(slope * 60, 3),
        "projected_throttle_s": projected_throttle_s,
        "confidence": confidence,
        "suggested_fan_delta_pct": suggested_fan_delta_pct,
        "suggested_msg_key": suggested_msg_key,
        "sample_count": len(xs),
    }

# ─── R&D #4.1 — Prometheus /metrics endpoint ───────────────────────────────
def handle_prometheus_metrics(ctx: dict) -> Tuple[int, str]:
    """OpenMetrics-formatted live snapshot for every detected GPU.

    Returns plain text (not JSON). Designed to be scrape-target for Prometheus,
    Grafana Agent, VictoriaMetrics or any OpenMetrics-compatible collector.

    Series exposed (one sample per GPU) :
      - gpu_temp_celsius{gpu="0",name="…",uuid="…"}
      - gpu_util_ratio{gpu="0",...}                # 0..1
      - gpu_power_watts{gpu="0",...}
      - gpu_power_limit_watts{gpu="0",...}
      - gpu_memory_used_bytes{gpu="0",...}
      - gpu_memory_total_bytes{gpu="0",...}
      - gpu_fan_speed_ratio{gpu="0",fan="0",...}   # 0..1
      - gpu_fan_rpm{gpu="0",fan="0",...}
      - gpu_pcie_link_gen{gpu="0",...}
      - gpu_pcie_link_width{gpu="0",...}
      - gpu_dashboard_info{version="0.3.0"}        # gauge=1, info via labels

    Permissive : missing fields are silently skipped (don't break the scrape).
    """
    from .. import __version__ as VERSION

    lines: list[str] = []

    def add(name: str, help_text: str, type_: str = "gauge") -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {type_}")

    def fmt_labels(d: dict) -> str:
        # Escape backslashes and double-quotes per OpenMetrics
        parts = []
        for k, v in d.items():
            sv = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            parts.append(f'{k}="{sv}"')
        return "{" + ",".join(parts) + "}"

    # Per-GPU live snapshot
    gpus = _gpus_available()
    add("gpu_temp_celsius", "GPU core temperature in °C")
    add("gpu_util_ratio", "GPU utilization (0..1)")
    add("gpu_power_watts", "Current power draw in Watts")
    add("gpu_power_limit_watts", "Current power limit in Watts")
    add("gpu_memory_used_bytes", "VRAM used in bytes")
    add("gpu_memory_total_bytes", "VRAM total in bytes")
    add("gpu_pcie_link_gen", "Current PCIe link generation")
    add("gpu_pcie_link_width", "Current PCIe link width")
    add("gpu_fan_speed_ratio", "Fan speed (0..1)")
    add("gpu_fan_rpm", "Fan speed in RPM")
    add("gpu_dashboard_info", "Build info (labels carry version)", "gauge")

    for gpu in gpus:
        idx = gpu.get("index", 0)
        snap = _gpu_card_snapshot(gpu_index=idx)
        if not snap or not snap.get("alive"):
            continue
        labels = {
            "gpu": str(idx),
            "name": snap.get("name", "unknown"),
            "uuid": snap.get("uuid", ""),
        }
        L = fmt_labels(labels)
        if snap.get("temp") is not None:
            lines.append(f"gpu_temp_celsius{L} {snap['temp']}")
        if snap.get("util_gpu") is not None:
            lines.append(f"gpu_util_ratio{L} {snap['util_gpu'] / 100.0:.4f}")
        if snap.get("power") is not None:
            lines.append(f"gpu_power_watts{L} {snap['power']:.2f}")
        if snap.get("power_limit") is not None:
            lines.append(f"gpu_power_limit_watts{L} {snap['power_limit']:.2f}")
        if snap.get("mem_used_mib") is not None:
            lines.append(f"gpu_memory_used_bytes{L} {int(snap['mem_used_mib']) * 1024 * 1024}")
        if snap.get("mem_total_mib") is not None:
            lines.append(f"gpu_memory_total_bytes{L} {int(snap['mem_total_mib']) * 1024 * 1024}")
        if snap.get("pcie_gen") is not None:
            lines.append(f"gpu_pcie_link_gen{L} {snap['pcie_gen']}")
        if snap.get("pcie_width") is not None:
            lines.append(f"gpu_pcie_link_width{L} {snap['pcie_width']}")
        # Per-fan series — only the GPU at gpu_index has fan detail
        for f in (_per_fan_state(ctx["config"]) if idx == 0 else []):
            fan_labels = {**labels, "fan": str(f.get("idx", 0))}
            FL = fmt_labels(fan_labels)
            if f.get("pct") is not None:
                lines.append(f"gpu_fan_speed_ratio{FL} {f['pct'] / 100.0:.4f}")
            if f.get("rpm") is not None:
                lines.append(f"gpu_fan_rpm{FL} {f['rpm']}")

    info_labels = fmt_labels({"version": VERSION})
    lines.append(f"gpu_dashboard_info{info_labels} 1")
    lines.append("")  # trailing newline per OpenMetrics
    return 200, "\n".join(lines)


# ── /proc parsing helpers (moved with sys_context + cgroup_power) ──

def _read_pid_cgroup(pid: int) -> Optional[str]:
    """Read /proc/<pid>/cgroup and return the cgroup v2 path (or v1 main path).

    Format examples :
      v2 : '0::/system.slice/llama-server.service'
      v1 : '12:memory:/user.slice/user-1000.slice/...'

    Returns the first sensible path or None on error.
    """
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            for line in f:
                parts = line.strip().split(":", 2)
                if len(parts) >= 3:
                    return parts[2]
    except OSError:
        pass
    return None


def _read_proc_stat_cpu() -> Optional[list]:
    """Return /proc/stat's aggregate cpu line as list of 10 ints, or None."""
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        if not line.startswith("cpu "):
            return None
        parts = line.split()[1:]
        return [int(p) for p in parts[:10]]  # user nice system idle iowait irq softirq steal guest guest_nice
    except (OSError, ValueError):
        return None


def _read_proc_loadavg() -> Optional[tuple]:
    """Return (load1, load5, load15) or None."""
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
        return float(parts[0]), float(parts[1]), float(parts[2])
    except (OSError, ValueError, IndexError):
        return None


def _read_proc_vmstat() -> dict:
    """Parse /proc/vmstat — pswpin / pswpout / pgmajfault are interesting."""
    out = {}
    try:
        with open("/proc/vmstat") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    out[parts[0]] = int(parts[1])
    except (OSError, ValueError):
        pass
    return out


def _read_proc_meminfo() -> dict:
    """Return current memory + swap usage in KB."""
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    try:
                        out[parts[0].rstrip(":")] = int(parts[1])
                    except ValueError:
                        pass
    except OSError:
        pass
    return out
