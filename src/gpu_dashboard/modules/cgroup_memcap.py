"""Module cgroup_memcap — cgroup-v2 memory-cap scanner (R&D #32.5).

systemd ships with conservative per-unit defaults. A common
foot-gun: `systemd-oomd` or a vendor unit sets `MemoryMax=4G` /
`MemoryHigh=3G` on llama-server.service, then the user loads a
16-GiB model and the kernel OOM-kills the process every few minutes
without an obvious cause in the logs.

This module walks /proc/<pid>/cgroup → resolves to /sys/fs/cgroup
<path>/ → reads:

  memory.max           hard cap (`max` = unbounded)
  memory.high          soft cap (kernel reclaims above this)
  memory.low           reserved floor
  memory.current       actual usage right now
  memory.swap.max      swap hard cap per cgroup
  memory.swap.current  swap currently used inside cgroup
  memory.events        cumulative {low, high, max, oom, oom_kill}

Verdicts (worst-pick):
  uncapped             memory.max=`max` AND no swap → ok
  capped_below_model   max < 8 GiB → likely below typical LLM size
  capped_tight         current within 20 % of max → pressure soon
  memory_high_throttle current > memory.high → kernel reclaiming
                       this daemon's pages
  swap_active          swap.current > 0 → host swap is hitting it
                       (cross-ref #32.4 swappiness + #29.8 mlock)
  oom_capped           memory.events.max > 0 → hit the cap recently
  oom_killed           memory.events.oom_kill > 0 → killed by kernel
                       OOM inside the cgroup

For non-uncapped cases the recipe surfaces a per-unit Drop-In:

  [Service]
  MemoryMax=infinity
  MemoryHigh=infinity
  MemorySwapMax=0      # belt-and-braces for inference rigs

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "cgroup_memcap"


_PROC = "/proc"
_CGROUP_ROOT = "/sys/fs/cgroup"


# `max` sysfs sentinel as an int we can compare
MAX_SENTINEL = 2 ** 63 - 1


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


_V2_LINE_RE = re.compile(r"^0::(\S+)\s*$", re.MULTILINE)


def parse_cgroup_path(text: str) -> Optional[str]:
    if not text:
        return None
    m = _V2_LINE_RE.search(text)
    return m.group(1) if m else None


def read_memory_field(cg_root: str, path: str,
                       field: str) -> Optional[int]:
    p = os.path.join(cg_root, path.lstrip("/"), field)
    try:
        with open(p) as f:
            s = f.read().strip()
    except OSError:
        return None
    if s == "max":
        return MAX_SENTINEL
    try:
        return int(s)
    except ValueError:
        return None


def read_memory_events(cg_root: str, path: str) -> dict:
    p = os.path.join(cg_root, path.lstrip("/"), "memory.events")
    try:
        with open(p) as f:
            txt = f.read()
    except OSError:
        return {}
    out: dict = {}
    for line in txt.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


# Below this the cap is suspicious for any LLM workload
_BELOW_MODEL_THRESHOLD = 8 * 1024 ** 3
# Within this fraction of max → tight
_TIGHT_FRACTION = 0.80


_RANK = {
    "uncapped": 0,
    "unknown": 1,
    "swap_active": 2,
    "memory_high_throttle": 3,
    "capped_tight": 3,
    "capped_below_model": 4,
    "oom_capped": 5,
    "oom_killed": 6,
}


def classify(memory_max: Optional[int],
              memory_high: Optional[int],
              memory_current: Optional[int],
              memory_swap_current: Optional[int],
              events: dict) -> dict:
    if memory_max is None or memory_current is None:
        return {"verdict": "unknown",
                "reason": ("Could not read cgroup memory.* — "
                            "resolved path may not exist."),
                "recommendation": ""}
    oom_kill = (events or {}).get("oom_kill", 0)
    max_hits = (events or {}).get("max", 0)
    if oom_kill > 0:
        return {"verdict": "oom_killed",
                "reason": (f"memory.events.oom_kill={oom_kill} — the "
                           f"kernel OOM-killer terminated processes "
                           f"inside this cgroup."),
                "recommendation": _recipe()}
    if max_hits > 0:
        return {"verdict": "oom_capped",
                "reason": (f"memory.events.max={max_hits} — the cgroup "
                           f"hit its memory.max limit (and was held "
                           f"under it via reclaim) since cgroup created."),
                "recommendation": _recipe()}
    if memory_max < MAX_SENTINEL:
        if memory_max < _BELOW_MODEL_THRESHOLD:
            gb = memory_max / 1024 ** 3
            return {"verdict": "capped_below_model",
                    "reason": (f"memory.max={gb:.1f} GiB is below the "
                               f"typical LLM model size (8 GiB+). "
                               f"Loading a 16 GiB model will OOM."),
                    "recommendation": _recipe()}
        if memory_current >= memory_max * _TIGHT_FRACTION:
            return {"verdict": "capped_tight",
                    "reason": (f"memory.current ({memory_current/1024**3:.1f} "
                               f"GiB) is within {(1-_TIGHT_FRACTION)*100:.0f} "
                               f"% of memory.max "
                               f"({memory_max/1024**3:.1f} GiB) — OOM "
                               f"likely on a bigger prompt."),
                    "recommendation": _recipe()}
    if (memory_high is not None and memory_high < MAX_SENTINEL
            and memory_current > memory_high):
        return {"verdict": "memory_high_throttle",
                "reason": (f"memory.current ({memory_current/1024**3:.1f} "
                           f"GiB) is above memory.high "
                           f"({memory_high/1024**3:.1f} GiB) — kernel "
                           f"is actively reclaiming from this cgroup."),
                "recommendation": _recipe()}
    if memory_swap_current and memory_swap_current > 0:
        return {"verdict": "swap_active",
                "reason": (f"memory.swap.current="
                           f"{memory_swap_current/1024**2:.0f} MiB — "
                           f"this daemon has pages in swap right now."),
                "recommendation": (
                    "# Daemon is swapping inside its cgroup. Fix chain:\n"
                    "# 1. #32.4 vm_sysctl_audit  vm.swappiness ≤ 10\n"
                    "# 2. #29.8 rlimit_audit     LimitMEMLOCK=infinity\n"
                    "# 3. To forbid this cgroup from using swap at all:\n"
                    f"sudo systemctl set-property <unit> MemorySwapMax=0\n"
                )}
    return {"verdict": "uncapped",
            "reason": (f"memory.max=max, memory.current="
                       f"{memory_current/1024**3:.1f} GiB, no swap."),
            "recommendation": ""}


def _recipe(unit: str = "<unit>.service") -> str:
    return (
        f"# Drop-In to remove the cap (replace <unit> with the actual unit name):\n"
        f"sudo mkdir -p /etc/systemd/system/{unit}.d\n"
        f"sudo tee /etc/systemd/system/{unit}.d/memcap.conf <<'EOF'\n"
        f"[Service]\n"
        f"MemoryMax=infinity\n"
        f"MemoryHigh=infinity\n"
        f"MemorySwapMax=0\n"
        f"EOF\n"
        f"sudo systemctl daemon-reload && sudo systemctl restart {unit}"
    )


def _resolve_unit_from_path(cg_path: Optional[str]) -> str:
    """Best-effort: /system.slice/foo.service → foo.service."""
    if not cg_path:
        return "<unit>.service"
    base = cg_path.rsplit("/", 1)[-1]
    return base if base.endswith(".service") else "<unit>.service"


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
        cgroup_text = _read(os.path.join(proc_root, str(pid), "cgroup")) or ""
        out.append({
            "pid": pid,
            "comm": comm,
            "cmdline_short": cmdline[:140],
            "cgroup_path": parse_cgroup_path(cgroup_text),
        })
    return out


def status(cfg=None) -> dict:
    procs = scan_llm_procs(_PROC)
    if not procs:
        return {"ok": True, "process_count": 0, "processes": [],
                "worst_verdict": "no_llm_procs"}
    enriched: list = []
    worst = "uncapped"
    for p in procs:
        cgp = p["cgroup_path"]
        if cgp:
            mmax = read_memory_field(_CGROUP_ROOT, cgp, "memory.max")
            mhigh = read_memory_field(_CGROUP_ROOT, cgp, "memory.high")
            mcur = read_memory_field(_CGROUP_ROOT, cgp, "memory.current")
            mswap = read_memory_field(_CGROUP_ROOT, cgp,
                                        "memory.swap.current")
            events = read_memory_events(_CGROUP_ROOT, cgp)
        else:
            mmax = mhigh = mcur = mswap = None
            events = {}
        v = classify(mmax, mhigh, mcur, mswap, events)
        # Re-template the unit name into the recipe so the copy-paste
        # line targets the actual service.
        if v["recommendation"] and "<unit>" in v["recommendation"]:
            unit = _resolve_unit_from_path(cgp)
            v["recommendation"] = v["recommendation"].replace(
                "<unit>.service", unit).replace("<unit>", unit.rsplit(".", 1)[0])
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
        enriched.append({
            "pid": p["pid"],
            "comm": p["comm"],
            "cmdline_short": p["cmdline_short"],
            "cgroup_path": cgp,
            "memory_max": mmax,
            "memory_high": mhigh,
            "memory_current": mcur,
            "memory_swap_current": mswap,
            "events": events,
            "verdict": v,
        })
    return {"ok": True, "process_count": len(enriched),
            "processes": enriched, "worst_verdict": worst}
