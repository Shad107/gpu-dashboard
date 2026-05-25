"""Module collection_profile_audit — self-observability of module
collection time (Hardening #2).

Times every module under ``gpu_dashboard.modules`` whose ``status()``
accepts zero required positional args, returns the top-N slowest plus
aggregate stats (total / p50 / p95 / slowest). Surfaces modules that
silently inflate cold-start cost.

Verdicts (priority order) :
  module_too_slow    ≥1 module took longer than ``slow_module_ms``
                     (default 500 ms). Likely a path-walk under
                     /sys/devices/* that should be sampled or
                     cached.
  collection_slow    aggregate elapsed time > ``slow_total_ms``
                     (default 5000 ms). Even if no single module
                     is hot, the fleet has grown past a reasonable
                     cold-start budget.
  ok                 every module returned under budget.
  unknown            module enumeration failed (rare — packaging
                     bug).

Cost note : iterating every module's status() once takes ~8 s on
a current build. This endpoint should be lazy-loaded from the UI
(not in the cold-start autoload list).

stdlib only.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import time
from typing import Iterable, List, Optional


NAME = "collection_profile_audit"


_DEFAULT_TOP_N = 10
_DEFAULT_SLOW_MODULE_MS = 500
_DEFAULT_SLOW_TOTAL_MS = 5000


# Setup-helper modules legitimately do not emit an audit verdict —
# they manage external resources. Mirror the carve-out in
# tests/test_module_fleet_health.py.
_NON_AUDIT_SETUP_MODULES = frozenset({
    "watchdog_setup",
})


def _iter_module_names(pkg) -> Iterable[str]:
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.ispkg or info.name.startswith("_"):
            continue
        if info.name == NAME:
            # Don't profile ourselves — would recurse.
            continue
        yield info.name


def _status_takes_zero_required_args(fn) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for p in sig.parameters.values():
        if p.default is inspect.Parameter.empty and p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY):
            return False
    return True


def time_status(fn) -> float:
    """Return elapsed milliseconds for one ``status()`` call.
    Tracebacks bubble — the fleet health harness is responsible
    for catching crashes; this one assumes the fleet is healthy."""
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000.0


def profile_modules(pkg=None) -> List[dict]:
    """Walk the modules package and time every audit ``status()``
    call. Returns a list of ``{name, elapsed_ms, status,
    expected_slow}`` dicts, one per module. status is one of
    ``ok`` / ``skipped`` / ``error: <type>``. ``expected_slow``
    reflects the module's ``EXPECTED_SLOW`` attribute — flagged
    modules do not trigger ``module_too_slow`` but are still
    surfaced in ``top_slowest``."""
    if pkg is None:
        import gpu_dashboard.modules as pkg  # type: ignore
    out: List[dict] = []
    for name in sorted(_iter_module_names(pkg)):
        try:
            mod = importlib.import_module(
                f"gpu_dashboard.modules.{name}")
        except Exception as e:  # noqa: BLE001
            out.append({"name": name, "elapsed_ms": None,
                        "status": f"import-error:"
                                  f"{type(e).__name__}",
                        "expected_slow": False})
            continue
        expected = bool(getattr(mod, "EXPECTED_SLOW", False))
        fn = getattr(mod, "status", None)
        if not callable(fn):
            out.append({"name": name, "elapsed_ms": None,
                        "status": "no-status",
                        "expected_slow": expected})
            continue
        if not _status_takes_zero_required_args(fn):
            out.append({"name": name, "elapsed_ms": None,
                        "status": "skipped",
                        "expected_slow": expected})
            continue
        try:
            elapsed = time_status(fn)
        except Exception as e:  # noqa: BLE001
            out.append({"name": name, "elapsed_ms": None,
                        "status": f"error:{type(e).__name__}",
                        "expected_slow": expected})
            continue
        out.append({"name": name, "elapsed_ms": elapsed,
                    "status": "ok", "expected_slow": expected})
    return out


def aggregate(results: List[dict]) -> dict:
    ok = [r for r in results if r["status"] == "ok"
          and r["elapsed_ms"] is not None]
    times = sorted(r["elapsed_ms"] for r in ok)
    n = len(times)
    if not n:
        return {"module_count": 0, "total_ms": 0.0,
                "optimizable_total_ms": 0.0,
                "expected_slow_total_ms": 0.0,
                "p50_ms": None, "p95_ms": None,
                "slowest_ms": None}
    total = sum(times)
    expected_total = sum(r["elapsed_ms"] for r in ok
                          if r.get("expected_slow"))
    optimizable_total = total - expected_total
    p50 = times[int(0.50 * (n - 1))]
    p95 = times[int(0.95 * (n - 1))]
    return {"module_count": n, "total_ms": total,
            "optimizable_total_ms": optimizable_total,
            "expected_slow_total_ms": expected_total,
            "p50_ms": p50, "p95_ms": p95,
            "slowest_ms": times[-1]}


def classify(results: List[dict], agg: dict,
              slow_module_ms: float = _DEFAULT_SLOW_MODULE_MS,
              slow_total_ms: float = _DEFAULT_SLOW_TOTAL_MS) -> dict:
    if not agg["module_count"]:
        return {"verdict": "unknown",
                "reason": ("Module enumeration produced zero "
                           "timeable status() calls — packaging "
                           "or import-time failure."),
                "recommendation": ""}
    hot = [r for r in results
            if r["status"] == "ok"
               and r["elapsed_ms"] is not None
               and r["elapsed_ms"] >= slow_module_ms
               and not r.get("expected_slow")]
    if hot:
        sample = ", ".join(
            f"{r['name']}={r['elapsed_ms']:.0f}ms"
            for r in sorted(hot,
                              key=lambda x: -x["elapsed_ms"])[:3])
        return {"verdict": "module_too_slow",
                "reason": (f"{len(hot)} module(s) exceed the "
                           f"{slow_module_ms:.0f} ms per-module "
                           f"budget: {sample}."),
                "recommendation": _recipe_slow_module()}
    if agg["optimizable_total_ms"] >= slow_total_ms:
        return {"verdict": "collection_slow",
                "reason": (f"Optimizable cold-start collection "
                           f"time {agg['optimizable_total_ms']:.0f} ms "
                           f"≥ {slow_total_ms:.0f} ms budget across "
                           f"{agg['module_count']} modules "
                           f"(total {agg['total_ms']:.0f} ms minus "
                           f"{agg['expected_slow_total_ms']:.0f} ms "
                           f"intrinsic). slowest="
                           f"{agg['slowest_ms']:.0f} ms, "
                           f"p95={agg['p95_ms']:.0f} ms."),
                "recommendation": _recipe_collection_slow()}
    return {"verdict": "ok",
            "reason": (f"{agg['module_count']} modules collected in "
                       f"{agg['total_ms']:.0f} ms "
                       f"(optimizable "
                       f"{agg['optimizable_total_ms']:.0f} ms, "
                       f"intrinsic "
                       f"{agg['expected_slow_total_ms']:.0f} ms ; "
                       f"p50={agg['p50_ms']:.0f}, "
                       f"p95={agg['p95_ms']:.0f}, "
                       f"slowest={agg['slowest_ms']:.0f}). "
                       f"All under budget."),
            "recommendation": ""}


def status(cfg=None,
            slow_module_ms: float = _DEFAULT_SLOW_MODULE_MS,
            slow_total_ms: float = _DEFAULT_SLOW_TOTAL_MS) -> dict:
    """Per-module timing pass over the fleet.

    ``slow_module_ms`` / ``slow_total_ms`` are the verdict
    thresholds. Defaults match the per-module 500 ms and per-fleet
    5000 ms budgets used everywhere else in the codebase. The HTTP
    handler accepts query-string overrides so users on slow
    hardware can recalibrate without editing the source.
    """
    results = profile_modules()
    agg = aggregate(results)
    ok_results = [r for r in results if r["status"] == "ok"
                   and r["elapsed_ms"] is not None]
    top = sorted(ok_results,
                  key=lambda x: -(x["elapsed_ms"] or 0))
    top_n = top[:_DEFAULT_TOP_N]
    verdict = classify(results, agg,
                          slow_module_ms=slow_module_ms,
                          slow_total_ms=slow_total_ms)
    return {"ok": True,
            "module_count": agg["module_count"],
            "total_ms": agg["total_ms"],
            "optimizable_total_ms": agg["optimizable_total_ms"],
            "expected_slow_total_ms": agg["expected_slow_total_ms"],
            "slow_module_ms_budget": slow_module_ms,
            "slow_total_ms_budget": slow_total_ms,
            "p50_ms": agg["p50_ms"],
            "p95_ms": agg["p95_ms"],
            "slowest_ms": agg["slowest_ms"],
            "top_slowest": [
                {"name": r["name"], "elapsed_ms": r["elapsed_ms"],
                 "expected_slow": r.get("expected_slow", False)}
                for r in top_n],
            "skipped_count": sum(1 for r in results
                                  if r["status"] == "skipped"),
            "error_count": sum(1 for r in results
                                if r["status"].startswith("error")
                                  or r["status"].startswith(
                                      "import-error")),
            "verdict": verdict}


def _recipe_slow_module() -> str:
    return ("# A module exceeded the 500 ms per-call budget.\n"
            "# Likely causes: deep /sys walk without depth limit,\n"
            "# blocking subprocess call, or a regex on a large file.\n"
            "# Investigate :\n"
            "PYTHONPATH=src python3 -c \"\\\n"
            "from gpu_dashboard.modules import "
            "collection_profile_audit as m; \\\n"
            "import json; print(json.dumps(\\\n"
            "  m.status()['top_slowest'], indent=2))\"\n"
            "# Then profile the offender :\n"
            "PYTHONPATH=src python3 -m cProfile -s cumulative \\\n"
            "  -c \"from gpu_dashboard.modules import "
            "<offender>; <offender>.status()\" | head -30\n")


def _recipe_collection_slow() -> str:
    return ("# Aggregate cold-start time exceeds the 5 s budget.\n"
            "# The fleet has grown beyond what a single sequential\n"
            "# pass can deliver. Options :\n"
            "#  1. Run collection in a thread pool (threading +\n"
            "#     ThreadPoolExecutor).\n"
            "#  2. Cache per-module results for N seconds and\n"
            "#     refresh out-of-band.\n"
            "#  3. Trim or consolidate redundant modules — see\n"
            "#     R&D #112 survey notes on pair candidates.\n")
