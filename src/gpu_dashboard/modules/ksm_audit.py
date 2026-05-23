"""Module ksm_audit — KSM + THP mm-knob auditor (R&D #52.1).

Surfaces two related kernel-MM subsystems whose defaults regularly
clash with LLM workloads on Linux :

  /sys/kernel/mm/ksm/                 Kernel Same-page Merging
  /sys/kernel/mm/transparent_hugepage/  Transparent Hugepages

Why this matters on a single-GPU LLM rig :

* THP=always + aggressive defrag triggers multi-second khugepaged
  stalls during large allocations (model load, KV-cache growth).
  llama.cpp / vLLM users routinely see "first-token latency
  variance" caused by khugepaged compaction, not the model itself.
* KSM left enabled with no MADV_MERGEABLE consumers (the common
  case on bare-metal LLM hosts — KSM is mainly useful for VM
  consolidation) just burns ksmd CPU for zero benefit.
* KSM aggressively scanning when pages_sharing is climbing fast
  ("ksm_thrashing") can wash the CPU cache during inference.

Verdicts (priority-ordered) :
  ksm_thrashing              KSM enabled AND pages_to_scan > 1000
                             AND high-frequency sleep (< 20 ms).
  thp_always_with_llm        /sys/kernel/mm/transparent_hugepage/enabled
                             active value is 'always' (rather than
                             'madvise') — host-wide THP not what
                             LLM runtimes ask for.
  thp_defrag_aggressive      THP defrag is 'always' or 'defer+madvise'
                             — synchronous compaction in allocation
                             path → latency spikes.
  ksm_disabled_with_madvise  KSM run=0 but THP=madvise (set up for
                             VM-style workload that won't ever
                             share) → no harm, but suggests
                             half-configured host.
  ok                         KSM off OR thoughtfully tuned ;
                             THP=madvise + defrag=madvise.
  unknown                    /sys/kernel/mm not readable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "ksm_audit"


_SYS_KSM = "/sys/kernel/mm/ksm"
_SYS_THP = "/sys/kernel/mm/transparent_hugepage"


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


def _read_active(p: str) -> Optional[str]:
    """Read a sysfs file like '[always] madvise never' and return
    the bracketed value, or None when unreadable."""
    t = _read(p)
    if t is None:
        return None
    for tok in t.split():
        if tok.startswith("[") and tok.endswith("]"):
            return tok[1:-1]
    return None


def read_ksm(sys_ksm: str = _SYS_KSM) -> dict:
    """Returns dict of KSM knobs + counters."""
    out: dict = {"available": os.path.isdir(sys_ksm)}
    if not out["available"]:
        return out
    out["run"] = _read_int(os.path.join(sys_ksm, "run"))
    out["pages_sharing"] = _read_int(os.path.join(sys_ksm,
                                                       "pages_sharing"))
    out["pages_shared"] = _read_int(os.path.join(sys_ksm,
                                                      "pages_shared"))
    out["pages_to_scan"] = _read_int(os.path.join(sys_ksm,
                                                       "pages_to_scan"))
    out["sleep_millisecs"] = _read_int(os.path.join(sys_ksm,
                                                         "sleep_millisecs"))
    out["merge_across_nodes"] = _read_int(os.path.join(sys_ksm,
                                                            "merge_across_nodes"))
    out["use_zero_pages"] = _read_int(os.path.join(sys_ksm,
                                                        "use_zero_pages"))
    return out


def read_thp(sys_thp: str = _SYS_THP) -> dict:
    """Returns dict of THP active values + khugepaged knobs."""
    out: dict = {"available": os.path.isdir(sys_thp)}
    if not out["available"]:
        return out
    out["enabled"] = _read_active(os.path.join(sys_thp, "enabled"))
    out["defrag"] = _read_active(os.path.join(sys_thp, "defrag"))
    khu = os.path.join(sys_thp, "khugepaged")
    if os.path.isdir(khu):
        out["khugepaged_defrag"] = _read_int(os.path.join(khu,
                                                                "defrag"))
        out["khugepaged_alloc_sleep_millisecs"] = _read_int(
            os.path.join(khu, "alloc_sleep_millisecs"))
    return out


def classify(ksm: dict, thp: dict) -> dict:
    """Return {verdict, reason, recommendation}."""
    if not ksm.get("available") and not thp.get("available"):
        return {"verdict": "unknown",
                "reason": "/sys/kernel/mm not readable.",
                "recommendation": ""}

    ksm_run = ksm.get("run") or 0
    sharing = ksm.get("pages_sharing") or 0
    scan = ksm.get("pages_to_scan") or 0
    sleep_ms = ksm.get("sleep_millisecs")
    thp_en = thp.get("enabled")
    thp_defrag = thp.get("defrag")

    # 1) ksm_thrashing
    if ksm_run == 1 and scan > 1000 and sleep_ms is not None \
            and sleep_ms < 20:
        return {"verdict": "ksm_thrashing",
                "reason": (f"KSM scanning {scan} pages every "
                          f"{sleep_ms} ms (currently sharing "
                          f"{sharing} pages). Aggressive ksmd "
                          f"churn evicts L3."),
                "recommendation": _recipe_ksm_calm()}

    # 2) thp_always_with_llm
    if thp_en == "always":
        return {"verdict": "thp_always_with_llm",
                "reason": ("/sys/kernel/mm/transparent_hugepage/"
                          "enabled = always. Host-wide THP causes "
                          "khugepaged stalls during big model loads."),
                "recommendation": _recipe_thp_madvise()}

    # 3) thp_defrag_aggressive
    if thp_defrag in ("always", "defer+madvise"):
        return {"verdict": "thp_defrag_aggressive",
                "reason": (f"THP defrag = '{thp_defrag}'. "
                          f"Synchronous compaction in the alloc "
                          f"path causes multi-second pauses."),
                "recommendation": _recipe_thp_madvise()}

    # 4) ksm_disabled_with_madvise — half-configured
    if ksm_run == 0 and thp_en == "madvise" and \
            ksm.get("available") and sharing == 0:
        return {"verdict": "ksm_disabled_with_madvise",
                "reason": ("KSM is off but THP is on madvise. "
                          "Likely a half-configured host — no harm, "
                          "but you may want to enable KSM for VM "
                          "workloads or remove madvise expectations."),
                "recommendation": _recipe_ksm_review()}

    return {"verdict": "ok",
            "reason": ("KSM and THP are tuned in a reasonable "
                      "way for an LLM host."),
            "recommendation": ""}


def status(config=None, sys_ksm: str = _SYS_KSM,
            sys_thp: str = _SYS_THP) -> dict:
    """Top-level status dict consumed by the HTTP handler."""
    ksm = read_ksm(sys_ksm)
    thp = read_thp(sys_thp)
    ok = bool(ksm.get("available") or thp.get("available"))
    verdict = classify(ksm, thp)
    return {"ok": ok, "ksm": ksm, "thp": thp, "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_ksm_calm() -> str:
    return ("# KSM is scanning very aggressively. Either disable it\n"
            "# (LLM hosts rarely benefit from KSM) :\n"
            "echo 0 | sudo tee /sys/kernel/mm/ksm/run\n"
            "# … or slow ksmd down :\n"
            "echo 100  | sudo tee /sys/kernel/mm/ksm/pages_to_scan\n"
            "echo 200  | sudo tee /sys/kernel/mm/ksm/sleep_millisecs\n")


def _recipe_thp_madvise() -> str:
    return ("# Switch THP from host-wide 'always' to per-process\n"
            "# 'madvise' so only callers that ask for hugepages\n"
            "# get them, and stop synchronous compaction :\n"
            "echo madvise | sudo tee /sys/kernel/mm/transparent_hugepage/enabled\n"
            "echo madvise | sudo tee /sys/kernel/mm/transparent_hugepage/defrag\n"
            "# Persist via /etc/tuned or a kernel cmdline param.\n")


def _recipe_ksm_review() -> str:
    return ("# Either KSM should be enabled (echo 1 > /sys/kernel/mm/ksm/run)\n"
            "# if you run multiple VMs/containers with deduplicable\n"
            "# pages, or THP madvise expectations are over-engineered\n"
            "# for this workload. Check `cat /sys/kernel/mm/ksm/pages_sharing`\n"
            "# after a few minutes of load before deciding.\n")
