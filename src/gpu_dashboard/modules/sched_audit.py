"""Module sched_audit — CFS runqueue-wait + sched-feature (R&D #47.1).

Parses /proc/schedstat (the userspace-readable, no-permission-
required surface) for per-CPU runqueue-wait time + task-switch
counts, then best-effort reads /sys/kernel/debug/sched/features
+ tunables (root-only) and degrades gracefully on permission
denial.

/proc/schedstat v15+ per-CPU line :
  cpu<N> <yld> <a> <b> <sched_count> <sched_goidle> <ttwu_count>
         <ttwu_local> <rq_cpu_time_ns> <run_delay_ns> <pcount>

The last three numeric fields are the load-bearing ones :
  rq_cpu_time_ns   cumulative ns CPU<N> spent running tasks
  run_delay_ns     cumulative ns tasks waited in the runqueue
  pcount           number of timeslices on this CPU

Avg per-slice wait = run_delay_ns / pcount → coarse but cheap
"how long does the average inference thread sit in the runqueue
before the scheduler picks it?". On a clean idle box this is
single-digit µs ; on a contended host with cgroup-pinned
inference workers competing with desktop apps it climbs to
hundreds of µs (visible as inter-token decode jitter).

Verdicts (priority-ordered) :
  runqueue_wait_pileup    ≥1 CPU with avg per-slice wait > 100 µs
                          AND ≥ 1k slices recorded (small samples
                          ignored).
  sched_feat_hostile      /sys/kernel/debug/sched/features readable
                          AND ≥1 known-hostile flag (e.g.
                          NO_WAKEUP_PREEMPTION) is set against
                          kernel-build defaults. (Best-effort —
                          skipped if debugfs not accessible.)
  ok                      no pile-up.
  no_schedstat            /proc/schedstat unreadable.
  unknown                 read succeeded but no CPU rows.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "sched_audit"


_PROC_SCHEDSTAT = "/proc/schedstat"
_DEBUGFS_SCHED = "/sys/kernel/debug/sched"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _has_permission_error(path: str) -> bool:
    try:
        with open(path):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


_CPU_RE = re.compile(r"^cpu(\d+)\s+(.*)$")


def parse_schedstat(text: Optional[str]) -> dict:
    """Returns {version: int, cpus: [{cpu, rq_cpu_time_ns,
    run_delay_ns, pcount, avg_wait_ns}]}.

    The last 3 fields of each cpu line are rq_cpu_time, run_delay,
    pcount in that order — this matches kernel sched/sched-stats.txt
    for v15-v17.
    """
    out: dict = {"version": None, "cpus": []}
    if not text:
        return out
    for line in text.splitlines():
        if line.startswith("version "):
            try:
                out["version"] = int(line.split()[1])
            except (ValueError, IndexError):
                pass
            continue
        m = _CPU_RE.match(line)
        if not m:
            continue
        try:
            cpu = int(m.group(1))
            parts = [int(t) for t in m.group(2).split()]
        except ValueError:
            continue
        if len(parts) < 3:
            continue
        rq_cpu_time = parts[-3]
        run_delay = parts[-2]
        pcount = parts[-1]
        avg_wait = (run_delay // pcount) if pcount > 0 else 0
        out["cpus"].append({
            "cpu": cpu,
            "rq_cpu_time_ns": rq_cpu_time,
            "run_delay_ns": run_delay,
            "pcount": pcount,
            "avg_wait_ns": avg_wait,
        })
    return out


def parse_sched_features(text: Optional[str]) -> dict:
    """`/sys/kernel/debug/sched/features` looks like :
       GENTLE_FAIR_SLEEPERS NO_NEXT_BUDDY WAKEUP_PREEMPTION ...
    where each token is either 'NAME' (enabled) or 'NO_NAME'
    (disabled)."""
    if not text:
        return {}
    out: dict = {}
    for tok in text.split():
        if tok.startswith("NO_"):
            out[tok[3:]] = False
        else:
            out[tok] = True
    return out


_HOSTILE_FOR_INFERENCE = {
    # Flag → expected default → hostile when ≠ default.
    # We surface drift, not strict "must be X".
    "WAKEUP_PREEMPTION": True,
}


_RECIPE_PILEUP = (
    "# Inference threads spending > 100 µs per slice in the\n"
    "# runqueue. Common causes :\n"
    "#  1. Cross-cgroup contention — your cgroup-pinned\n"
    "#     llama-server competes with Plex / Docker / Electron\n"
    "#     on the same CPUs.\n"
    "#  2. Hybrid CPU migrating P→E (shipped #42.2 catches that).\n"
    "# Snapshot top runqueue-waiters :\n"
    "for p in /proc/[0-9]*; do\n"
    "  read -r _ _ _ _ d _ < $p/schedstat 2>/dev/null\n"
    "  [ -n \"$d\" ] && [ \"$d\" -gt 1000000 ] && \\\n"
    "    echo \"$(basename $p) $(cat $p/comm) run_delay=$d ns\"\n"
    "done | sort -k3 -n | tail -10\n"
    "# Then pin inference to dedicated CPUs via systemd CPUAffinity=."
)

_RECIPE_FEAT_HOSTILE = (
    "# A scheduler-feature toggle drift was detected (e.g.\n"
    "# NO_WAKEUP_PREEMPTION set after a 'low-latency gaming'\n"
    "# tweak). Restore the kernel default :\n"
    "echo WAKEUP_PREEMPTION | \\\n"
    "  sudo tee /sys/kernel/debug/sched/features\n"
    "# Persistent : add `sched_features=...` to /etc/default/grub\n"
    "# GRUB_CMDLINE_LINUX_DEFAULT (rarely needed)."
)


_PILEUP_THRESHOLD_NS = 100_000          # 100 µs avg per slice
_PILEUP_MIN_SLICES = 1_000


def _is_hostile_feature(feats: dict) -> list:
    out: list = []
    for k, expected in _HOSTILE_FOR_INFERENCE.items():
        if k in feats and feats[k] != expected:
            out.append(k)
    return out


def classify(schedstat: dict, features: dict) -> dict:
    cpus = schedstat.get("cpus") or []
    if not cpus:
        return {"verdict": "no_schedstat",
                "reason": ("/proc/schedstat unreadable or empty."),
                "recommendation": ""}
    pileups = [c for c in cpus
                if c.get("pcount", 0) >= _PILEUP_MIN_SLICES
                and c.get("avg_wait_ns", 0) >= _PILEUP_THRESHOLD_NS]
    if pileups:
        worst = max(pileups, key=lambda c: c.get("avg_wait_ns", 0))
        return {"verdict": "runqueue_wait_pileup",
                "reason": (f"{len(pileups)} CPU(s) with avg "
                           f"runqueue-wait ≥ "
                           f"{_PILEUP_THRESHOLD_NS // 1000} µs. "
                           f"Worst : CPU{worst['cpu']} "
                           f"avg_wait={worst['avg_wait_ns'] / 1000:.0f} µs "
                           f"over {worst['pcount']} slices."),
                "recommendation": _RECIPE_PILEUP}
    hostile = _is_hostile_feature(features) if features else []
    if hostile:
        return {"verdict": "sched_feat_hostile",
                "reason": (f"Scheduler features drift from inference-"
                           f"safe defaults : {', '.join(hostile)}."),
                "recommendation": _RECIPE_FEAT_HOSTILE}
    return {"verdict": "ok",
            "reason": (f"{len(cpus)} CPU(s) ; avg runqueue-wait "
                       f"comfortably below "
                       f"{_PILEUP_THRESHOLD_NS // 1000} µs threshold."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    sched_text = _read(_PROC_SCHEDSTAT)
    if sched_text is None:
        return {
            "ok": False,
            "verdict": {"verdict": "no_schedstat",
                         "reason": "/proc/schedstat unreadable.",
                         "recommendation": ""},
            "cpu_count": 0, "cpus": [],
        }
    parsed = parse_schedstat(sched_text)
    # /sys/kernel/debug/sched/features needs CAP_SYS_ADMIN ; degrade.
    features_text = _read(os.path.join(_DEBUGFS_SCHED, "features"))
    features = parse_sched_features(features_text)
    verdict = classify(parsed, features)
    # Trim payload — only emit top-N CPUs by avg_wait.
    cpus_sorted = sorted(parsed.get("cpus", []),
                          key=lambda c: -(c.get("avg_wait_ns", 0)))
    return {
        "ok": True,
        "schedstat_version": parsed.get("version"),
        "cpu_count": len(parsed.get("cpus") or []),
        "top_cpus_by_wait": cpus_sorted[:16],
        "features": features,
        "features_readable": bool(features),
        "verdict": verdict,
    }
