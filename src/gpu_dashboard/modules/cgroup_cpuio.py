"""Module cgroup_cpuio — cgroup-v2 CPU/IO weight scanner (R&D #33.6).

Sibling of shipped #32.5 cgroup_memcap. systemd ships every service
unit with `CPUWeight=100` and `IOWeight=100` (the cgroup-v2 default).
On a single-purpose inference rig that's fine, but on a shared host
where backups, package upgrades, or compile jobs run in their own
services, *all* daemons fight for the same 100-weight slice — and
the LLM inference daemon, which has long-running CPU-heavy work,
loses to bursty system jobs.

The fix is to nudge LLM units to CPUWeight=200 / IOWeight=200,
which doubles their proportional share without applying hard caps.

For each LLM daemon:

  /proc/<pid>/cgroup           → /system.slice/<unit>.service
  /sys/fs/cgroup<path>/cpu.weight   default 100, range 1-10000
  /sys/fs/cgroup<path>/cpu.max      "max <period>" = no quota
                                    "<quota> <period>" = active cap
  /sys/fs/cgroup<path>/io.weight    "default 100" or bare "100"

Verdicts (worst-pick):
  default_weight     cpu.weight=100 AND io.weight=100 (systemd
                      default) — fine on a single-purpose box, but
                      worth elevating for inference priority
  elevated           cpu.weight or io.weight ≥ 200
  max_priority      cpu.weight or io.weight ≥ 500
  cpu_quota_active   cpu.max has a non-`max` quota — actively
                      throttling the daemon
  unknown            cannot read cgroup files

Recipe substitutes the actual unit name resolved from the cgroup
path, so copy-paste targets the right service.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "cgroup_cpuio"


_PROC = "/proc"
_CGROUP_ROOT = "/sys/fs/cgroup"


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


_IO_WEIGHT_RE = re.compile(r"(?:default\s+)?(\d+)")


def parse_io_weight(s: Optional[str]) -> Optional[int]:
    """io.weight format: 'default 100' or '100' (bare); we want the
    int after 'default' if present."""
    if not s:
        return None
    m = _IO_WEIGHT_RE.search(s.strip())
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def parse_cpu_max(s: Optional[str]) -> tuple:
    """cpu.max format: 'max <period>' (no quota) or '<quota> <period>'."""
    if not s:
        return (None, None)
    parts = s.strip().split()
    if len(parts) != 2:
        return (None, None)
    quota_s, period_s = parts
    quota = None if quota_s == "max" else (
        int(quota_s) if quota_s.isdigit() else None)
    period = int(period_s) if period_s.isdigit() else None
    return (quota, period)


def _read_int(p: str) -> Optional[int]:
    s = _read(p)
    if s is None:
        return None
    s = s.strip()
    try:
        return int(s)
    except ValueError:
        return None


_ELEVATED_THRESHOLD = 200
_MAX_PRIORITY_THRESHOLD = 500


_RANK = {
    "unknown": 1,
    "max_priority": 0,
    "elevated": 0,
    "default_weight": 1,
    "cpu_quota_active": 2,
}


def classify(cpu_weight: Optional[int], io_weight: Optional[int],
              cpu_max: tuple, unit: str = "<unit>.service") -> dict:
    quota, _period = cpu_max
    if cpu_weight is None and io_weight is None and quota is None:
        return {"verdict": "unknown",
                "reason": "Could not read any cgroup cpu/io fields.",
                "recommendation": ""}
    if quota is not None:
        return {"verdict": "cpu_quota_active",
                "reason": (f"cpu.max has an active quota — daemon CPU is "
                           f"hard-capped, hurting prompt-processing "
                           f"throughput regardless of weight."),
                "recommendation": _quota_recipe(unit)}
    cw = cpu_weight or 100
    iw = io_weight or 100
    high = max(cw, iw)
    if high >= _MAX_PRIORITY_THRESHOLD:
        return {"verdict": "max_priority",
                "reason": (f"cpu.weight={cw}, io.weight={iw} — daemon "
                           f"has aggressive priority over other units."),
                "recommendation": ""}
    if high >= _ELEVATED_THRESHOLD:
        return {"verdict": "elevated",
                "reason": (f"cpu.weight={cw}, io.weight={iw} — elevated "
                           f"above the systemd default 100."),
                "recommendation": ""}
    return {"verdict": "default_weight",
            "reason": (f"cpu.weight={cw}, io.weight={iw} — both at the "
                       f"systemd default. On a shared host, backup / "
                       f"build / package-manager jobs will compete with "
                       f"inference for the same proportional slice."),
            "recommendation": _weight_recipe(unit)}


def _weight_recipe(unit: str) -> str:
    return (
        f"# Elevate inference proportional share via systemd Drop-In:\n"
        f"sudo mkdir -p /etc/systemd/system/{unit}.d\n"
        f"sudo tee /etc/systemd/system/{unit}.d/priority.conf <<'EOF'\n"
        f"[Service]\n"
        f"CPUWeight=200\n"
        f"IOWeight=200\n"
        f"EOF\n"
        f"sudo systemctl daemon-reload && "
        f"sudo systemctl restart {unit}"
    )


def _quota_recipe(unit: str) -> str:
    return (
        f"# Remove the CPU quota cap:\n"
        f"sudo systemctl set-property {unit} CPUQuota=\n"
        f"# Or override via Drop-In:\n"
        f"sudo mkdir -p /etc/systemd/system/{unit}.d\n"
        f"sudo tee /etc/systemd/system/{unit}.d/cpu.conf <<'EOF'\n"
        f"[Service]\n"
        f"CPUQuota=\n"
        f"EOF\n"
        f"sudo systemctl daemon-reload && "
        f"sudo systemctl restart {unit}"
    )


def _resolve_unit(cg_path: Optional[str]) -> str:
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
    worst = "max_priority"
    for p in procs:
        cgp = p["cgroup_path"]
        if cgp:
            base = os.path.join(_CGROUP_ROOT, cgp.lstrip("/"))
            cpu_weight = _read_int(os.path.join(base, "cpu.weight"))
            cpu_max = parse_cpu_max(_read(os.path.join(base, "cpu.max")))
            io_weight = parse_io_weight(_read(os.path.join(base, "io.weight")))
        else:
            cpu_weight = io_weight = None
            cpu_max = (None, None)
        unit = _resolve_unit(cgp)
        v = classify(cpu_weight, io_weight, cpu_max, unit=unit)
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
        enriched.append({
            "pid": p["pid"],
            "comm": p["comm"],
            "cmdline_short": p["cmdline_short"],
            "cgroup_path": cgp,
            "cpu_weight": cpu_weight,
            "io_weight": io_weight,
            "cpu_max_quota": cpu_max[0],
            "cpu_max_period": cpu_max[1],
            "verdict": v,
        })
    return {"ok": True, "process_count": len(enriched),
            "processes": enriched, "worst_verdict": worst}
