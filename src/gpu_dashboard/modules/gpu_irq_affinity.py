"""Module gpu_irq_affinity — GPU MSI-X IRQ affinity advisor (R&D #38.4).

Where shipped #30.1 msi_inventory tells you *how many* MSI vectors
the GPU got, this module tells you *which CPUs* are actually
servicing them. On dual-CCD AMD or multi-socket Intel, leaving the
kernel auto-balance leaves all the GPU's IRQs on CPU0 — the classic
foot-gun where ksoftirqd/0 saturates under heavy DMA while the
other 23 cores idle.

Reads:
  /sys/bus/pci/devices/<gpu>/msi_irqs/         the MSI-X vector list
  /sys/bus/pci/devices/<gpu>/irq               legacy IRQ fallback
  /sys/bus/pci/devices/<gpu>/local_cpulist     the GPU's NUMA-local
                                                CPUs (companion to
                                                #37.2)
  /proc/irq/<n>/smp_affinity_list              allowed CPUs
  /proc/irq/<n>/effective_affinity_list        CPU(s) actually
                                                receiving interrupts

Verdicts (worst-pick across GPUs):
  cpu0_concentrated  ALL GPU IRQs effective on CPU0 — surface
                     irqbalance + per-IRQ echo recipe
  mismatch_local     GPU has a constrained local_cpulist but the
                     IRQ's effective CPU is outside it — fix via
                     `echo <local> > .../smp_affinity_list`
  single_cpu_pin     1-2 IRQs (typical single-MSI GPU), single CPU
                     — fine for low traffic, hotspot risk if DMA
                     load grows
  balanced           ≥4 IRQs spread across multiple CPUs — healthy
                     MSI-X distribution
  no_gpus            no NVIDIA VGA devices
  unknown            cannot read /proc/irq files

Recipe documents both `irqbalance` (the daemon) and the manual
`echo <cpulist> > /proc/irq/<n>/smp_affinity_list` per-IRQ path
for the user who wants explicit control.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "gpu_irq_affinity"


_PCI_ROOT = "/sys/bus/pci/devices"
_PROC_ROOT = "/proc"


_RANGE_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_cpu_list(s: Optional[str]) -> list:
    if not s:
        return []
    out: list = []
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


def find_nvidia_bdfs(pci_root: str = _PCI_ROOT) -> list:
    out: list = []
    try:
        for n in sorted(os.listdir(pci_root)):
            vp = os.path.join(pci_root, n, "vendor")
            cp = os.path.join(pci_root, n, "class")
            try:
                with open(vp) as f:
                    if f.read().strip().lower() != "0x10de":
                        continue
                with open(cp) as f:
                    klass = f.read().strip().lower()
                if klass.startswith("0x03"):
                    out.append(n)
            except OSError:
                continue
    except OSError:
        return []
    return out


def list_irqs_for_gpu(pci_root: str, bdf: str) -> list:
    """Prefer MSI-X vector list ; fall back to legacy /irq when
    msi_irqs/ is empty or missing."""
    msi_dir = os.path.join(pci_root, bdf, "msi_irqs")
    try:
        names = os.listdir(msi_dir)
    except OSError:
        names = []
    out: list = []
    for n in names:
        try:
            out.append(int(n))
        except ValueError:
            continue
    if out:
        return sorted(out)
    legacy = _read(os.path.join(pci_root, bdf, "irq"))
    if legacy:
        try:
            v = int(legacy)
            if v > 0:
                return [v]
        except ValueError:
            pass
    return []


def read_smp_affinity_list(proc_root: str, irq: int) -> Optional[str]:
    return _read(os.path.join(proc_root, "irq", str(irq),
                                "smp_affinity_list"))


def read_effective_affinity_list(proc_root: str,
                                    irq: int) -> Optional[str]:
    return _read(os.path.join(proc_root, "irq", str(irq),
                                "effective_affinity_list"))


_RECIPE_REBALANCE = (
    "# Quick path — let irqbalance do it dynamically:\n"
    "sudo apt install irqbalance\n"
    "sudo systemctl enable --now irqbalance\n"
    "# Manual path — pin each GPU IRQ to a specific CPU set:\n"
    "# Find the IRQs:\n"
    "ls /sys/bus/pci/devices/<gpu>/msi_irqs/\n"
    "# For each IRQ <N>, set the allowed CPUs (mask or list):\n"
    "echo <cpu_list> | sudo tee /proc/irq/<N>/smp_affinity_list\n"
    "# Companion modules: #30.1 msi_inventory (count + mode),\n"
    "# #37.2 gpu_cpu_affinity (which CPUs are local to this GPU)."
)


_RANK = {
    "no_gpus": 0, "balanced": 0, "unknown": 1,
    "single_cpu_pin": 1,
    "mismatch_local": 3, "cpu0_concentrated": 4,
}


def _per_card_verdict(card: dict) -> str:
    irqs = card.get("irqs") or []
    if not irqs:
        return "unknown"
    effectives = set()
    for irq in irqs:
        eff = irq.get("effective")
        if eff:
            for c in parse_cpu_list(eff):
                effectives.add(c)
    if not effectives:
        return "unknown"
    # cpu0_concentrated: every IRQ effective only on CPU0
    if effectives == {0}:
        return "cpu0_concentrated"
    # mismatch_local: card has a constrained local_cpulist and the
    # effective CPU is not in it
    local = set(parse_cpu_list(card.get("local_cpulist") or ""))
    if local and not effectives <= local:
        return "mismatch_local"
    if len(irqs) >= 4 and len(effectives) >= 2:
        return "balanced"
    return "single_cpu_pin"


def classify(cards: list) -> dict:
    if not cards:
        return {"verdict": "no_gpus",
                "reason": "No NVIDIA VGA devices found.",
                "recommendation": ""}
    worst = "balanced"
    worst_card = None
    for c in cards:
        v = _per_card_verdict(c)
        if _RANK.get(v, 0) > _RANK.get(worst, 0):
            worst = v
            worst_card = c
    if worst == "cpu0_concentrated":
        return {
            "verdict": "cpu0_concentrated",
            "reason": (f"GPU {worst_card['gpu_bdf']} has ALL "
                       f"{len(worst_card['irqs'])} IRQ(s) effective on "
                       f"CPU0 — ksoftirqd/0 will saturate under heavy "
                       f"DMA while other CPUs idle."),
            "recommendation": _RECIPE_REBALANCE,
        }
    if worst == "mismatch_local":
        effectives = sorted({
            c for irq in worst_card["irqs"]
            for c in parse_cpu_list(irq.get("effective") or "")
        })
        return {
            "verdict": "mismatch_local",
            "reason": (f"GPU {worst_card['gpu_bdf']} has its local "
                       f"CPUs as {worst_card.get('local_cpulist')} but "
                       f"effective IRQ CPU(s) are {effectives} — IRQs "
                       f"are servicing on non-local cores."),
            "recommendation": _RECIPE_REBALANCE,
        }
    if worst == "single_cpu_pin":
        return {
            "verdict": "single_cpu_pin",
            "reason": (f"GPU(s) have few IRQs landing on a single CPU. "
                       f"Typical for single-MSI legacy mode. Watch for "
                       f"hotspot if DMA load grows."),
            "recommendation": "",
        }
    if worst == "unknown":
        return {"verdict": "unknown",
                "reason": "Could not read /proc/irq/<n>/effective_affinity_list.",
                "recommendation": ""}
    return {"verdict": "balanced",
            "reason": (f"GPU IRQs spread across multiple CPUs — "
                       f"healthy MSI-X distribution."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    bdfs = find_nvidia_bdfs(_PCI_ROOT)
    cards: list = []
    total_irqs = 0
    for bdf in bdfs:
        irqs_list = list_irqs_for_gpu(_PCI_ROOT, bdf)
        local = _read(os.path.join(_PCI_ROOT, bdf, "local_cpulist"))
        irqs: list = []
        for irq in irqs_list:
            irqs.append({
                "irq": irq,
                "smp_list": read_smp_affinity_list(_PROC_ROOT, irq),
                "effective": read_effective_affinity_list(_PROC_ROOT, irq),
            })
        cards.append({
            "gpu_bdf": bdf,
            "local_cpulist": local,
            "irqs": irqs,
        })
        total_irqs += len(irqs)
    verdict = classify(cards)
    return {
        "ok": True,
        "gpu_count": len(cards),
        "total_irqs": total_irqs,
        "cards": cards,
        "verdict": verdict,
    }
