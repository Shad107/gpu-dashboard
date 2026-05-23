"""Module cpufreq_residency_audit — per-CPU cpufreq residency (R&D #65.2).

Reads /sys/devices/system/cpu/cpu*/cpufreq/stats/{time_in_state,
total_trans} for every CPU exposing the stats subdir.

Distinct from pstate_audit (HWP/EPP knobs) and cpu_boost (the
boost flag). This computes residency in each frequency bucket per
CPU and across all CPUs to detect governor pin-ups, transition
storms, and per-CPU divergence.

Why this matters on an LLM rig :

* `time_in_state` showing > 99 % residency in one bucket = the
  governor is effectively pinned. Either accidentally (userspace
  governor with no daemon), or by `cpupower frequency-set` left
  by a tuning script.
* Pinned at min → throttle ; pinned at max → wasted power.
* total_trans bumping > 1000 per second across CPUs = governor
  thrash on a host that's not bursty enough to warrant it.
* One CPU never reaching the max bucket while others do →
  asymmetric boost (per-core P-state cap).

Reads :
  /sys/devices/system/cpu/cpu*/cpufreq/stats/{time_in_state,
                                                  total_trans}

Verdicts (priority-ordered) :
  pinned_at_min                 ≥1 CPU with > 99 % time at the
                                lowest freq bucket.
  pinned_at_max                 ≥1 CPU with > 99 % time at the
                                highest freq bucket.
  transition_storm              total_trans across all CPUs >
                                100 000 (high churn).
  boost_unreachable             ≥1 CPU never accumulated time at
                                a freq bucket reachable by peers.
  ok                            residencies look balanced.
  unknown                       /sys/devices/system/cpu/cpu0/
                                cpufreq/stats absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple


NAME = "cpufreq_residency_audit"


_SYS_CPU = "/sys/devices/system/cpu"

_CPU_DIR_RE = re.compile(r"^cpu(\d+)$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def parse_time_in_state(text: Optional[str]
                          ) -> List[Tuple[int, int]]:
    """Parse 'freq jiffies' lines into [(freq_kHz, jiffies), ...]."""
    out: List[Tuple[int, int]] = []
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            out.append((int(parts[0]), int(parts[1])))
        except ValueError:
            continue
    return out


def list_cpu_stats(sys_cpu: str = _SYS_CPU) -> Dict[int, dict]:
    if not os.path.isdir(sys_cpu):
        return {}
    out: Dict[int, dict] = {}
    for name in sorted(os.listdir(sys_cpu)):
        m = _CPU_DIR_RE.match(name)
        if not m:
            continue
        idx = int(m.group(1))
        stats = os.path.join(sys_cpu, name, "cpufreq", "stats")
        if not os.path.isdir(stats):
            continue
        out[idx] = {
            "time_in_state": parse_time_in_state(
                _read(os.path.join(stats, "time_in_state"))),
            "total_trans": _read_int(
                os.path.join(stats, "total_trans")),
        }
    return out


def classify(cpu_stats: Dict[int, dict]) -> dict:
    if not cpu_stats:
        return {"verdict": "unknown",
                "reason": ("No /sys/devices/system/cpu/cpu*/cpufreq/"
                          "stats present — kernel built without "
                          "CONFIG_CPU_FREQ_STAT, or no cpufreq "
                          "driver."),
                "recommendation": ""}

    # Aggregate analyses.
    pinned_min: List[int] = []
    pinned_max: List[int] = []
    total_trans = 0
    max_freqs_per_cpu: Dict[int, set] = {}

    for cpu, s in cpu_stats.items():
        tis = s.get("time_in_state") or []
        if not tis:
            continue
        total = sum(j for _, j in tis)
        if total <= 0:
            continue
        sorted_by_freq = sorted(tis, key=lambda x: x[0])
        min_freq, min_j = sorted_by_freq[0]
        max_freq, max_j = sorted_by_freq[-1]
        if min_j / total > 0.99:
            pinned_min.append(cpu)
        if max_j / total > 0.99:
            pinned_max.append(cpu)
        total_trans += s.get("total_trans") or 0
        # Track buckets with non-zero time per CPU :
        max_freqs_per_cpu[cpu] = {f for f, j in tis if j > 0}

    # 1) pinned_at_min
    if pinned_min:
        return {"verdict": "pinned_at_min",
                "reason": (f"{len(pinned_min)} CPU(s) at > 99 % "
                          f"residency on minimum freq : "
                          f"{pinned_min[:6]}. Throttled."),
                "recommendation": _recipe_unpin()}

    # 2) pinned_at_max
    if pinned_max:
        return {"verdict": "pinned_at_max",
                "reason": (f"{len(pinned_max)} CPU(s) at > 99 % "
                          f"residency on maximum freq : "
                          f"{pinned_max[:6]}. Boost wasted "
                          f"during idle."),
                "recommendation": _recipe_unpin()}

    # 3) transition_storm
    if total_trans > 100_000:
        return {"verdict": "transition_storm",
                "reason": (f"total cpufreq transitions = "
                          f"{total_trans} across all CPUs. "
                          f"Governor thrashing."),
                "recommendation": _recipe_governor()}

    # 4) boost_unreachable — one CPU's freq set is a strict subset
    if max_freqs_per_cpu:
        # union of all CPUs' visited buckets
        union: set = set()
        for s in max_freqs_per_cpu.values():
            union |= s
        for cpu, freqs in max_freqs_per_cpu.items():
            if freqs and len(freqs) < len(union) and \
                    union - freqs and \
                    max(union) not in freqs:
                return {"verdict": "boost_unreachable",
                        "reason": (f"cpu{cpu} never reached "
                                  f"freq buckets that peers did : "
                                  f"max-peer={max(union)} kHz."),
                        "recommendation": _recipe_per_core_cap()}

    return {"verdict": "ok",
            "reason": (f"{len(cpu_stats)} CPU(s), cpufreq "
                      f"residency balanced."),
            "recommendation": ""}


def status(config=None, sys_cpu: str = _SYS_CPU) -> dict:
    cpu_stats = list_cpu_stats(sys_cpu)
    ok = bool(cpu_stats)
    verdict = classify(cpu_stats)
    sample = {}
    if cpu_stats:
        first = next(iter(cpu_stats))
        sample = cpu_stats[first]
    return {"ok": ok,
              "cpu_count": len(cpu_stats),
              "sample_cpu_index": (
                  next(iter(cpu_stats)) if cpu_stats else None),
              "sample_stats": sample,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_unpin() -> str:
    return ("# Inspect which CPUs are pinned :\n"
            "for c in /sys/devices/system/cpu/cpu*/cpufreq/stats/time_in_state; do\n"
            "  echo \"=== $c ===\" ; head -3 $c\n"
            "done | head -20\n"
            "# Set the governor to schedutil (or performance for max\n"
            "# perf) :\n"
            "echo schedutil | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor\n")


def _recipe_governor() -> str:
    return ("# Transition storm — governor is dithering. Try a\n"
            "# less-twitchy governor :\n"
            "echo schedutil | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor\n"
            "# Or raise the sampling period :\n"
            "echo 10000 | sudo tee /sys/devices/system/cpu/cpufreq/policy*/up_transition_latency_ns 2>/dev/null\n")


def _recipe_per_core_cap() -> str:
    return ("# Per-core P-state cap — vendor SKU may have lower\n"
            "# turbo on some cores. Verify :\n"
            "cat /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq\n"
            "# Some platforms expose 'preferred core' (ITD/ITMT) ;\n"
            "# others have a permanent thermal-binned cap.\n")
