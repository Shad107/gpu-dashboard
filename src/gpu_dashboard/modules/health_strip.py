"""F5.1 — Health Strip aggregator.

Runs a curated set of high-signal audit modules in parallel and
returns a normalized {ok, warn, err, unknown} summary so the main
dashboard can show "411 modules d'audit" value at a glance instead
of burying every verdict under Settings.

This is NOT exhaustive (we have ~400 audit modules) — it's the
top ~15 picks calibrated against the homelab single-GPU LLM use
case. The user can drill from the strip into Settings →
Integrations for the long tail.

Each registered check provides:
  - id            short stable identifier
  - label         short human-readable title
  - status_fn     callable returning the audit's raw status dict
  - verdict_map   dict mapping known verdict values → severity
                  (one of: ok / warn / err / unknown). Verdicts
                  not present in the map default to "unknown".
  - severity_path optional dotted path into the result for the
                  verdict value. Default is "verdict.verdict" or
                  "verdict".
"""
from __future__ import annotations

import concurrent.futures
import time
from typing import Any, Callable, Dict, List, Optional, Tuple


# Severity ladder ordering used for aggregate summary + max calc.
SEVERITY_ORDER = ("err", "warn", "ok", "unknown")


def _ladder_max(seen: List[str]) -> str:
    """Return the worst severity in the list per SEVERITY_ORDER."""
    for sev in SEVERITY_ORDER:
        if sev in seen:
            return sev
    return "unknown"


def _dig(obj: Any, path: str) -> Any:
    """Walk a dotted path through a dict or list, returning None on
    miss. Integer parts index into lists; string parts key into
    dicts."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


# ---------------------------------------------------------------- registry

# Each entry: (id, label, "module.path", "function_name", classifier)
# where classifier is a callable taking the raw status dict and
# returning a (severity, verdict_label) tuple. severity must be
# one of err/warn/ok/unknown.
#
# Modules are imported lazily so a missing optional dep never
# crashes the strip — failures land as severity=unknown.


def _from_path(path: str, verdict_map: Dict[str, str]):
    """Convenience builder for the common case: classifier digs a
    dotted path, looks the value up in verdict_map."""
    def cls(raw):
        v = _dig(raw, path)
        return verdict_map.get(str(v), "unknown"), v
    return cls


def _worst_row_severity(raw, rows_key="rows"):
    """For audits that return a `rows` array with per-row
    `severity` fields, take the worst severity in the list."""
    rows = (raw or {}).get(rows_key) or []
    severities = [str(r.get("severity", "unknown")) for r in rows]
    worst = _ladder_max(severities)
    # Surface a count instead of a single verdict label so the UI
    # can show "3/8 warn" without us having to invent a name.
    counts = {s: severities.count(s) for s in set(severities)}
    label = ", ".join(f"{n} {s}" for s, n in
                       sorted(counts.items(),
                              key=lambda kv: SEVERITY_ORDER.index(kv[0])))
    return worst, label or "no_rows"


def _classify_airgap(raw):
    # airgap.status() returns {enabled, lan_allowed, blocked_count_24h, ...}
    # No real verdict here — this is informational. We surface
    # "ok" if blocked_count_24h is 0, else "warn" to flag activity.
    n = (raw or {}).get("blocked_count_24h", 0) or 0
    return ("warn" if n > 0 else "ok",
             f"{n} blocked / 24h")


def _classify_trim(raw):
    # trim_audit returns {audits: [{has_discard_mount, on_ssd, ...}, ...]}
    audits = (raw or {}).get("audits") or []
    if not audits:
        return "unknown", "no_audits"
    ssd_no_discard = [a for a in audits
                       if a.get("on_ssd") and not a.get("has_discard_mount")]
    if ssd_no_discard:
        return "warn", f"{len(ssd_no_discard)} SSD(s) without discard"
    return "ok", f"{len(audits)} paths audited"


CHECKS: List[Tuple[str, str, str, str, Callable[[Any], Tuple[str, Any]]]] = [
    ("pstate", "P-state",
     "gpu_dashboard.modules.pstate_audit", "status",
     _from_path("gpus.0.verdict.verdict",
                 {"ok": "ok", "power_save_idle": "ok",
                  "silent_downshift": "warn", "clock_locked": "warn"})),
    ("rebar", "Resizable BAR",
     "gpu_dashboard.modules.rebar_audit", "status",
     _from_path("cards.0.verdict.verdict",
                 {"rebar_on": "ok", "rebar_off": "warn",
                  "partial": "warn"})),
    ("thp", "Transparent Huge Pages",
     "gpu_dashboard.modules.thp_audit", "status",
     _from_path("verdict.verdict",
                 {"madvise_balanced": "ok", "madvise_default": "ok",
                  "always_aggressive": "warn", "never": "warn"})),
    ("cpuidle", "cpuidle / C-states",
     "gpu_dashboard.modules.cpuidle_audit", "status",
     _from_path("verdict.verdict",
                 {"deep_states_active": "ok", "balanced": "ok",
                  "haltpoll_optimal": "ok",
                  "shallow_only": "warn", "disabled_driver": "warn"})),
    ("smt", "SMT / Hyperthreading",
     "gpu_dashboard.modules.smt_audit", "status",
     _from_path("verdict.verdict",
                 {"smt_on": "ok", "smt_off": "warn",
                  "smt_forced_off": "warn",
                  "smt_not_supported": "ok"})),
    ("clocksource", "TSC clocksource",
     "gpu_dashboard.modules.clocksource_audit", "status",
     _from_path("verdict.verdict",
                 {"tsc_stable": "ok", "optimal": "ok",
                  "hpet_fallback": "warn",
                  "acpi_pm_fallback": "warn"})),
    ("vm_sysctl", "vm sysctls",
     "gpu_dashboard.modules.vm_sysctl_audit", "status",
     lambda r: _worst_row_severity(r, "rows")),
    ("net_sysctl", "net sysctls",
     "gpu_dashboard.modules.net_sysctl_audit", "status",
     lambda r: _worst_row_severity(r, "rows")),
    ("pcie_rpm", "PCIe runtime PM",
     "gpu_dashboard.modules.pcie_rpm_audit", "status",
     _from_path("cards.0.verdict.verdict",
                 {"ok": "ok", "active": "ok",
                  "auto_with_d3cold": "ok",
                  "blocked_by_upstream": "warn"})),
    ("limits", "rlimits",
     "gpu_dashboard.modules.limits_audit", "status",
     _from_path("verdict.verdict",
                 {"ok": "ok", "default": "ok",
                  "nofile_low": "warn", "memlock_low": "warn"})),
    ("fs_mount", "filesystem mounts",
     "gpu_dashboard.modules.fs_mount_audit", "status",
     _from_path("verdict.verdict",
                 {"ok": "ok",
                  "model_dir_noatime_missing": "warn"})),
    ("trim", "fstrim / SSD discard",
     "gpu_dashboard.modules.trim_audit", "status",
     _classify_trim),
    ("airgap", "air-gap pinning",
     "gpu_dashboard.modules.airgap", "status",
     _classify_airgap),
    ("proc_static", "static /proc state",
     "gpu_dashboard.modules.proc_static_audit", "status",
     _from_path("cards.0.verdict.verdict",
                 {"ok": "ok", "clean": "ok",
                  "drifted": "warn"})),
]


def _run_one(entry, cfg) -> Dict[str, Any]:
    cid, label, mod_path, fn_name, classifier = entry
    start = time.monotonic()
    raw: Optional[Any] = None
    try:
        mod = __import__(mod_path, fromlist=[fn_name])
        fn: Callable = getattr(mod, fn_name)
        # Most status() functions accept cfg as a single arg.
        # Some accept zero args. Try the cfg variant first; fall
        # back to no-arg on TypeError.
        try:
            raw = fn(cfg)
        except TypeError:
            raw = fn()
    except Exception as e:
        return {
            "id": cid, "label": label,
            "severity": "unknown",
            "verdict": None,
            "error": f"{type(e).__name__}: {e}",
            "elapsed_ms": round((time.monotonic() - start) * 1000, 1),
        }
    try:
        severity, verdict_label = classifier(raw)
    except Exception as e:
        return {
            "id": cid, "label": label,
            "severity": "unknown",
            "verdict": None,
            "error": f"classifier: {type(e).__name__}: {e}",
            "elapsed_ms": round((time.monotonic() - start) * 1000, 1),
        }
    return {
        "id": cid, "label": label,
        "severity": severity,
        "verdict": verdict_label,
        "elapsed_ms": round((time.monotonic() - start) * 1000, 1),
    }


def aggregate(cfg, *,
                max_workers: int = 8,
                timeout_per_check: float = 4.0) -> Dict[str, Any]:
    """Run every registered check in parallel, return a summary.

    Each check has its own timeout — if any check exceeds it we
    mark it severity=unknown and move on, so a single slow audit
    can't stall the whole strip."""
    started = time.monotonic()
    results: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers
    ) as pool:
        future_map = {pool.submit(_run_one, entry, cfg): entry
                       for entry in CHECKS}
        for fut, entry in future_map.items():
            cid, label = entry[0], entry[1]
            try:
                results.append(fut.result(timeout=timeout_per_check))
            except concurrent.futures.TimeoutError:
                results.append({
                    "id": cid, "label": label,
                    "severity": "unknown",
                    "verdict": None,
                    "error": "timeout",
                    "elapsed_ms": round(timeout_per_check * 1000, 1),
                })
            except Exception as e:
                results.append({
                    "id": cid, "label": label,
                    "severity": "unknown",
                    "verdict": None,
                    "error": f"{type(e).__name__}: {e}",
                    "elapsed_ms": 0,
                })
    results.sort(key=lambda r: (
        SEVERITY_ORDER.index(r.get("severity", "unknown")),
        r["id"],
    ))
    summary = {"err": 0, "warn": 0, "ok": 0, "unknown": 0,
                "total": len(results)}
    for r in results:
        summary[r["severity"]] = summary.get(r["severity"], 0) + 1
    overall = _ladder_max([r["severity"] for r in results])
    return {
        "ok": True,
        "summary": summary,
        "overall": overall,
        "checks": results,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }
