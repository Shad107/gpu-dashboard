"""Module proc_sched — per-daemon scheduler stats (R&D #34.4).

`/proc/<pid>/sched` exposes per-task scheduler internals, including:

  nr_voluntary_switches    task gave up CPU on its own (IO wait,
                           sleep, futex) — healthy
  nr_involuntary_switches  scheduler preempted task — usually
                           because another task wants the CPU
  se.nr_migrations         times this task moved between CPUs
  se.sum_exec_runtime      total CPU time (ms)

For an LLM inference daemon, a high *ratio* of involuntary to
voluntary switches is direct evidence of CPU contention: every
preemption is a cache-flush, every migration trashes the L2/L3
caches, both add latency to the next inference token.

This is the per-process view of the same story that PSI (#32.1)
catches system-wide and cgroup cpu.weight (#33.6) addresses
structurally.

Verdicts:
  ok                  involuntary_ratio < 30 %
  contended           30-60 % — measurable but not severe
  severely_contended  >= 60 % — cache thrash territory
  unknown             cannot read /proc/<pid>/sched or status

`/proc/<pid>/status` has the same `voluntary_ctxt_switches` +
`nonvoluntary_ctxt_switches` fields (under different names) — used
as fallback when /sched is unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple


NAME = "proc_sched"


_PROC = "/proc"


LLM_COMM_PATTERNS = (
    "ollama", "llama-server", "llama_server", "llama.cpp", "llamacpp",
    "vllm", "sglang", "exllamav2", "exllama", "comfyui",
)
LLM_CMDLINE_HINTS = (
    "llama_cpp", "vllm.entrypoints", "ollama", "exllama",
    "text-generation-webui", "comfyui",
)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def read_comm(pid: int, proc_root: str = _PROC) -> str:
    t = _read(os.path.join(proc_root, str(pid), "comm"))
    return t.strip() if t else ""


def read_cmdline(pid: int, proc_root: str = _PROC) -> str:
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8",
                                                            errors="replace")
    except OSError:
        return ""


def is_llm_proc(comm: str, cmdline: str) -> bool:
    low = comm.lower()
    for pat in LLM_COMM_PATTERNS:
        if pat in low:
            return True
    if low.startswith("python") or low.startswith("uvicorn"):
        for h in LLM_CMDLINE_HINTS:
            if h in cmdline:
                return True
    return False


_HEADER_RE = re.compile(r"^\S.*\(\d+,\s*#threads:\s*(\d+)\s*\)", re.MULTILINE)
_KV_RE = re.compile(r"^\s*([A-Za-z][\w.]+)\s*:\s*([-\d.]+)\s*$", re.MULTILINE)


# Map /proc/<pid>/sched key → our dict key
_SCHED_FIELDS = {
    "nr_voluntary_switches": "voluntary_switches",
    "nr_involuntary_switches": "involuntary_switches",
    "nr_switches": "nr_switches",
    "se.nr_migrations": "nr_migrations",
}


def parse_sched(text: str) -> dict:
    if not text:
        return {}
    out: dict = {}
    hm = _HEADER_RE.search(text)
    if hm:
        out["threads"] = int(hm.group(1))
    for m in _KV_RE.finditer(text):
        k = m.group(1)
        v = m.group(2)
        if k == "se.sum_exec_runtime":
            try:
                out["sum_exec_runtime_ms"] = float(v)
            except ValueError:
                pass
            continue
        target = _SCHED_FIELDS.get(k)
        if target:
            try:
                out[target] = int(float(v))
            except ValueError:
                pass
    return out


_STATUS_VOL_RE = re.compile(r"^voluntary_ctxt_switches:\s+(\d+)",
                              re.MULTILINE)
_STATUS_NONVOL_RE = re.compile(r"^nonvoluntary_ctxt_switches:\s+(\d+)",
                                 re.MULTILINE)


def parse_status_switches(text: str) -> Tuple[Optional[int], Optional[int]]:
    if not text:
        return (None, None)
    v_m = _STATUS_VOL_RE.search(text)
    nv_m = _STATUS_NONVOL_RE.search(text)
    return (
        int(v_m.group(1)) if v_m else None,
        int(nv_m.group(1)) if nv_m else None,
    )


_CONTENDED_RATIO = 0.30
_SEVERE_RATIO = 0.60


_RANK = {
    "ok": 0,
    "unknown": 1,
    "contended": 2,
    "severely_contended": 3,
}


_RECIPE = (
    "# Scheduler contention on this daemon. Cross-references:\n"
    "# 1. #32.1 PSI pressure  → check cpu.some.avg10 (system-wide view)\n"
    "# 2. #33.6 cgroup_cpuio  → elevate CPUWeight=200 in systemd Drop-In\n"
    "# 3. #31.3 cpu_topology  → consider CPUAffinity to pin to fewer cores\n"
    "# 4. Stop competing CPU-heavy units while inference is hot\n"
)


def classify(voluntary: Optional[int], involuntary: Optional[int],
              nr_migrations: Optional[int],
              sum_exec_ms: Optional[float]) -> dict:
    if voluntary is None or involuntary is None:
        return {"verdict": "unknown",
                "reason": "Cannot read context-switch counters.",
                "recommendation": ""}
    total = voluntary + involuntary
    if total == 0:
        return {"verdict": "unknown",
                "reason": "Process has zero context switches recorded.",
                "recommendation": ""}
    ratio = involuntary / total
    if ratio >= _SEVERE_RATIO:
        return {
            "verdict": "severely_contended",
            "reason": (f"{ratio*100:.0f}% of context switches are "
                       f"involuntary ({involuntary:,}/{total:,}) — the "
                       f"scheduler is preempting this daemon constantly. "
                       f"Each preemption trashes cache, adding latency "
                       f"to the next inference token."),
            "recommendation": _RECIPE,
        }
    if ratio >= _CONTENDED_RATIO:
        return {
            "verdict": "contended",
            "reason": (f"{ratio*100:.0f}% of context switches are "
                       f"involuntary ({involuntary:,}/{total:,}) — "
                       f"measurable CPU contention."),
            "recommendation": _RECIPE,
        }
    return {
        "verdict": "ok",
        "reason": (f"{ratio*100:.0f}% involuntary "
                   f"({involuntary:,}/{total:,}) — healthy."),
        "recommendation": "",
    }


def scan_llm_procs(proc_root: str = _PROC) -> list:
    out: list = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        pid = int(n)
        comm = read_comm(pid, proc_root)
        cmdline = read_cmdline(pid, proc_root)
        if not is_llm_proc(comm, cmdline):
            continue
        sched_text = _read(os.path.join(proc_root, str(pid), "sched")) or ""
        status_text = _read(os.path.join(proc_root, str(pid), "status")) or ""
        sched = parse_sched(sched_text)
        # Fallback: if /sched lacked the fields (root-owned proc with
        # restricted PTRACE), pull voluntary/involuntary from /status.
        if ("voluntary_switches" not in sched or
                "involuntary_switches" not in sched):
            v, nv = parse_status_switches(status_text)
            if v is not None and nv is not None:
                sched["voluntary_switches"] = v
                sched["involuntary_switches"] = nv
        out.append({
            "pid": pid,
            "comm": comm,
            "cmdline_short": cmdline[:140],
            "voluntary_switches": sched.get("voluntary_switches"),
            "involuntary_switches": sched.get("involuntary_switches"),
            "nr_migrations": sched.get("nr_migrations"),
            "nr_switches": sched.get("nr_switches"),
            "sum_exec_runtime_ms": sched.get("sum_exec_runtime_ms"),
            "threads": sched.get("threads"),
        })
    return out


def status(cfg=None) -> dict:
    procs = scan_llm_procs(_PROC)
    if not procs:
        return {"ok": True, "process_count": 0, "processes": [],
                "worst_verdict": "no_llm_procs"}
    enriched: list = []
    worst = "ok"
    for p in procs:
        v = classify(p["voluntary_switches"], p["involuntary_switches"],
                      p["nr_migrations"], p["sum_exec_runtime_ms"])
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
        total = ((p["voluntary_switches"] or 0)
                  + (p["involuntary_switches"] or 0))
        ratio = ((p["involuntary_switches"] or 0) / total) if total else None
        enriched.append({**p, "involuntary_ratio": ratio, "verdict": v})
    return {"ok": True, "process_count": len(enriched),
            "processes": enriched, "worst_verdict": worst}
