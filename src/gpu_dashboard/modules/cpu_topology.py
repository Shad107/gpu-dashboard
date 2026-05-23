"""Module cpu_topology — CPU topology + governor inference advisor (R&D #31.3).

For LLM inference on a CPU+GPU mix, the host CPU runs the prompt-
processing pre-fill loop, scheduler, and KV-cache transfers. Three
common foot-guns that nvidia-smi never surfaces:

  1. **powersave governor**: a fresh laptop / desktop often ships
     with `scaling_governor=powersave`, capping the CPU at ~50 %
     of its turbo frequency. Prompt processing on llama.cpp loses
     30-40 % tokens/s before even touching the GPU.

  2. **Hybrid Alder Lake / Raptor Lake unawareness**: Intel hybrid
     CPUs split into P-cores (`intel_core`) and E-cores
     (`intel_atom`). Without explicit `taskset -c <p-cores>` or
     `CPUAffinity=` in the systemd unit, the scheduler happily
     migrates llama.cpp's hot threads onto the slow E-cores
     mid-inference, causing 2× run-to-run TTFT variance.

  3. **VM / kernel with no cpufreq**: in a guest VM (this rig is
     one) `/sys/devices/system/cpu/cpu*/cpufreq/` doesn't exist —
     so the dashboard can correctly report "host owns DVFS, advise
     tuning on the host" instead of pretending we can do something.

This module enumerates /sys/devices/system/cpu/, reads topology +
cpufreq + the hybrid CPU type buckets, classifies the system, and
emits copy-paste fixes (cpupower frequency-set / systemd CPUAffinity
Drop-In / taskset wrapper).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "cpu_topology"


_CPU_ROOT = "/sys/devices/system/cpu"


_RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_cpu_list(s: Optional[str]) -> list[int]:
    """Parse a Linux CPU list like '0-3,5,8-9' into a sorted int list."""
    if not s:
        return []
    out: list[int] = []
    for tok in s.strip().split(","):
        m = _RANGE_RE.match(tok.strip())
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        out.extend(range(a, b + 1))
    return sorted(set(out))


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def list_online_cpus(root: str = _CPU_ROOT) -> list[int]:
    """Prefer the canonical `online` mask, fall back to scanning cpu*/."""
    s = _read(os.path.join(root, "online"))
    if s:
        return parse_cpu_list(s)
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out: list[int] = []
    for n in names:
        m = re.match(r"^cpu(\d+)$", n)
        if m and os.path.isdir(os.path.join(root, n, "topology")):
            out.append(int(m.group(1)))
    return sorted(out)


def read_topology(root: str, n: int) -> dict:
    base = os.path.join(root, f"cpu{n}", "topology")
    return {
        "core_id": int(_read(os.path.join(base, "core_id")) or -1),
        "package_id": int(_read(os.path.join(base, "physical_package_id"))
                          or -1),
        "thread_siblings": parse_cpu_list(
            _read(os.path.join(base, "thread_siblings_list"))),
        "cluster_id": int(_read(os.path.join(base, "cluster_id")) or -1),
    }


def read_governor(root: str, n: int) -> Optional[str]:
    return _read(os.path.join(root, f"cpu{n}", "cpufreq", "scaling_governor"))


def read_max_freq_khz(root: str, n: int) -> Optional[int]:
    s = _read(os.path.join(root, f"cpu{n}", "cpufreq", "cpuinfo_max_freq"))
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def detect_hybrid(root: str = _CPU_ROOT) -> Optional[dict]:
    """Return {p_cores, e_cores} when /sys/devices/system/cpu/types/
    exists with intel_core + intel_atom (Alder Lake+). None otherwise."""
    types_dir = os.path.join(root, "types")
    if not os.path.isdir(types_dir):
        return None
    p = parse_cpu_list(_read(os.path.join(types_dir, "intel_core", "cpus")))
    e = parse_cpu_list(_read(os.path.join(types_dir, "intel_atom", "cpus")))
    if not p and not e:
        return None
    return {"p_cores": p, "e_cores": e}


_BALANCED_GOVERNORS = {"performance", "schedutil", "ondemand"}


def classify(cpus: list, hybrid: Optional[dict]) -> dict:
    govs = [c.get("governor") for c in cpus]
    distinct = set(g for g in govs if g)
    none_count = sum(1 for g in govs if g is None)
    if none_count == len(govs) and govs:
        return {
            "verdict": "missing_cpufreq",
            "reason": ("/sys/devices/system/cpu/cpu*/cpufreq/ absent — "
                       "no DVFS exposed to this host. Likely a VM where "
                       "the hypervisor owns CPU frequency, or a kernel "
                       "built without CONFIG_CPU_FREQ. Tune on the host."),
            "recommendation": "",
        }
    if "powersave" in distinct:
        return {
            "verdict": "powersave",
            "reason": (f"Governor 'powersave' active on at least one "
                       f"CPU (distinct = {sorted(distinct)}). On x86 "
                       f"this caps frequency well below turbo, losing "
                       f"30-40% prompt-processing tokens/s on llama.cpp."),
            "recommendation": (
                "# Switch all CPUs to the performance governor now:\n"
                "sudo cpupower frequency-set -g performance\n"
                "# Persist across reboots — edit GRUB cmdline and add:\n"
                "GRUB_CMDLINE_LINUX_DEFAULT=\"... "
                "cpufreq.default_governor=performance\"\n"
                "# (then sudo update-grub + reboot)"
            ),
        }
    if hybrid and (hybrid.get("p_cores") and hybrid.get("e_cores")):
        p_list = hybrid["p_cores"]
        p_range = _to_range_str(p_list)
        return {
            "verdict": "hybrid_unaware",
            "reason": (f"Hybrid CPU detected: {len(hybrid['p_cores'])} "
                       f"P-cores + {len(hybrid['e_cores'])} E-cores. "
                       f"Without explicit pinning the scheduler can "
                       f"migrate hot inference threads onto E-cores, "
                       f"costing 2x run-to-run TTFT variance."),
            "recommendation": (
                f"# One-shot launch on P-cores only:\n"
                f"taskset -c {p_range} llama-server --model ...\n\n"
                f"# Permanent via systemd Drop-In:\n"
                f"sudo mkdir -p /etc/systemd/system/llama-server.service.d\n"
                f"sudo tee "
                f"/etc/systemd/system/llama-server.service.d/cpu.conf "
                f"<<'EOF'\n[Service]\nCPUAffinity={p_range}\nEOF\n"
                f"sudo systemctl daemon-reload && "
                f"sudo systemctl restart llama-server.service"
            ),
        }
    return {
        "verdict": "balanced",
        "reason": (f"Governor(s) = {sorted(distinct)} — non-throttling. "
                   f"No hybrid foot-gun to pin around."),
        "recommendation": "",
    }


def _to_range_str(cpus: list) -> str:
    """Inverse of parse_cpu_list: collapse [0,1,2,3,5] → '0-3,5'."""
    if not cpus:
        return ""
    out: list = []
    s = sorted(cpus)
    start = prev = s[0]
    for n in s[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    out.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(out)


def status(cfg=None) -> dict:
    cpus_online = list_online_cpus(_CPU_ROOT)
    cpu_records: list = []
    by_core: dict = {}
    max_freqs: list = []
    smt_seen = False
    for n in cpus_online:
        topo = read_topology(_CPU_ROOT, n)
        gov = read_governor(_CPU_ROOT, n)
        max_f = read_max_freq_khz(_CPU_ROOT, n)
        if max_f:
            max_freqs.append(max_f)
        if len(topo["thread_siblings"]) > 1:
            smt_seen = True
        key = (topo["package_id"], topo["core_id"])
        by_core.setdefault(key, []).append(n)
        cpu_records.append({
            "id": n,
            "core_id": topo["core_id"],
            "package_id": topo["package_id"],
            "governor": gov,
            "max_freq_khz": max_f,
        })
    hybrid = detect_hybrid(_CPU_ROOT)
    verdict = classify(cpu_records, hybrid)
    return {
        "ok": True,
        "cpu_count": len(cpus_online),
        "physical_cores": len(by_core),
        "smt_enabled": smt_seen,
        "hybrid": hybrid,
        "max_freq_mhz": (max(max_freqs) // 1000) if max_freqs else None,
        "cpus": cpu_records,
        "verdict": verdict,
    }
