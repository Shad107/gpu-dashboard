"""Module irq_rates_audit — /proc/interrupts + /proc/softirqs (R&D #43.1).

Shipped #38.4 gpu_irq_affinity covers the effective_affinity of GPU
IRQs ; shipped #40.4 nic_queue_affinity covers RPS/XPS masks. This
module covers the *rate* axis : "is one CPU eating > 60 % of the
IRQ volume?" — a common foot-gun on virtio guests + on cheap NICs
where the kernel default pins every queue's IRQ to CPU0.

/proc/interrupts format :
  <IRQ>:  cpu0_count  cpu1_count  ...  cpuN_count  <chip> <device>
The header row has N CPU columns. Each subsequent row is an IRQ
number or named-IRQ (LOC, RES, NMI, etc.).

/proc/softirqs format (similar but no device column) :
  TYPE:  cpu0_count  cpu1_count  ...  cpuN_count

Verdicts (priority-ordered) :
  cpu_pinned              ≥1 device IRQ has > 60 % of its count on
                          a single CPU AND total ≥ 10k. Recipe :
                          set /proc/irq/<N>/smp_affinity_list to a
                          range, or rely on irqbalance.
  softirq_imbalance       ≥1 softirq type has > 70 % on a single CPU
                          (looser threshold — softirqs follow IRQs).
  ok                      every device IRQ spread reasonably.
  no_irqs                 /proc/interrupts has no per-CPU columns
                          (UP kernel or weirdness).
  unknown                 /proc/interrupts unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "irq_rates_audit"


_PROC_INTERRUPTS = "/proc/interrupts"
_PROC_SOFTIRQS = "/proc/softirqs"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _cpu_count_from_header(header: str) -> int:
    """Count CPU columns in the /proc/interrupts header row."""
    return sum(1 for tok in header.split() if tok.startswith("CPU"))


def parse_interrupts(text: str) -> list:
    """Return rows : {irq, counts: [per-cpu int], device, chip}.

    `irq` is the leading token (without trailing colon).
    `device` is the trailing device label (best-effort).
    """
    if not text:
        return []
    lines = text.splitlines()
    if not lines:
        return []
    n_cpu = _cpu_count_from_header(lines[0])
    if n_cpu == 0:
        return []
    out: list = []
    for line in lines[1:]:
        # Split into max n_cpu + 1 (irq) + remaining (chip/device).
        parts = line.split(None, 1 + n_cpu)
        if len(parts) < 1 + n_cpu:
            continue
        head = parts[0]
        if not head.endswith(":"):
            continue
        irq = head[:-1]
        try:
            counts = [int(parts[i + 1]) for i in range(n_cpu)]
        except ValueError:
            # Some named IRQs (ERR, MIS) may have spaces ; skip.
            continue
        tail = parts[1 + n_cpu] if len(parts) > 1 + n_cpu else ""
        # Tail is "chip-type-... device1, device2, ..."
        # Best-effort split.
        chip = ""
        device = tail
        m = re.match(r"^\s*(\S+(?:\s+\S+)?)\s{2,}(.*)$", tail)
        if m:
            chip = m.group(1).strip()
            device = m.group(2).strip()
        out.append({"irq": irq, "counts": counts,
                      "chip": chip, "device": device,
                      "total": sum(counts)})
    return out


def parse_softirqs(text: str) -> list:
    """Return rows : {type, counts: [per-cpu int]}."""
    if not text:
        return []
    lines = text.splitlines()
    if not lines:
        return []
    n_cpu = _cpu_count_from_header(lines[0])
    if n_cpu == 0:
        return []
    out: list = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 1 + n_cpu:
            continue
        head = parts[0]
        if not head.endswith(":"):
            continue
        kind = head[:-1]
        try:
            counts = [int(parts[i + 1]) for i in range(n_cpu)]
        except ValueError:
            continue
        out.append({"type": kind, "counts": counts,
                      "total": sum(counts)})
    return out


def hot_cpu(counts: list) -> tuple:
    """Return (hot_cpu_index, hot_share) for a row."""
    total = sum(counts)
    if total == 0:
        return (-1, 0.0)
    hot_idx = max(range(len(counts)), key=lambda i: counts[i])
    return (hot_idx, counts[hot_idx] / total)


_IRQ_HOT_SHARE = 0.60
_IRQ_TOTAL_MIN = 10_000
_SOFTIRQ_HOT_SHARE = 0.70
_SOFTIRQ_TOTAL_MIN = 100_000


_RECIPE_PIN_IRQ = (
    "# An IRQ is pinned to one CPU. Spread it across the CPU group\n"
    "# closest to the device (e.g. for a GPU IRQ, the GPU's NUMA\n"
    "# local CPUs from shipped #37.2 gpu_cpu_affinity) :\n"
    "echo 0-11 | sudo tee /proc/irq/<N>/smp_affinity_list\n"
    "# Or simply enable irqbalance (it spreads automatically and is\n"
    "# generally fine for non-latency-critical workloads) :\n"
    "sudo apt install irqbalance\n"
    "sudo systemctl enable --now irqbalance"
)

_RECIPE_SOFTIRQ = (
    "# A softirq type is pinned to one CPU — usually a downstream\n"
    "# symptom of an IRQ being pinned (NET_RX softirq follows the\n"
    "# RX IRQ's affinity). Fix the underlying IRQ first ; if RPS\n"
    "# is the layer of choice, see shipped #40.4 nic_queue_affinity\n"
    "# to set rps_cpus per-RX-queue."
)


def classify(irqs: list, softirqs: list) -> dict:
    if not irqs and not softirqs:
        return {"verdict": "unknown",
                "reason": "/proc/interrupts unreadable.",
                "recommendation": ""}
    if not any(r.get("counts") for r in irqs):
        return {"verdict": "no_irqs",
                "reason": ("/proc/interrupts has no per-CPU counts — "
                           "UP kernel or unusual config."),
                "recommendation": ""}
    pinned: list = []
    for r in irqs:
        if r["total"] < _IRQ_TOTAL_MIN:
            continue
        hot_idx, share = hot_cpu(r["counts"])
        if share >= _IRQ_HOT_SHARE:
            pinned.append({
                "irq": r["irq"], "device": r["device"],
                "hot_cpu": hot_idx, "hot_share": share,
                "total": r["total"],
            })
    if pinned:
        names = ", ".join(
            f"IRQ {p['irq']} ({p['device'][:40]}) "
            f"= {p['hot_share']:.0%} on CPU{p['hot_cpu']}"
            for p in pinned[:5])
        return {"verdict": "cpu_pinned",
                "reason": (f"{len(pinned)} device IRQ(s) pin "
                           f"≥ {int(_IRQ_HOT_SHARE * 100)} % of "
                           f"their traffic on a single CPU. {names}"),
                "recommendation": _RECIPE_PIN_IRQ}
    softirq_pinned: list = []
    for r in softirqs:
        if r["total"] < _SOFTIRQ_TOTAL_MIN:
            continue
        hot_idx, share = hot_cpu(r["counts"])
        if share >= _SOFTIRQ_HOT_SHARE:
            softirq_pinned.append({
                "type": r["type"], "hot_cpu": hot_idx,
                "hot_share": share, "total": r["total"],
            })
    if softirq_pinned:
        names = ", ".join(
            f"{p['type']} = {p['hot_share']:.0%} on CPU{p['hot_cpu']}"
            for p in softirq_pinned[:5])
        return {"verdict": "softirq_imbalance",
                "reason": (f"{len(softirq_pinned)} softirq type(s) "
                           f"≥ {int(_SOFTIRQ_HOT_SHARE * 100)} % on "
                           f"a single CPU. {names}"),
                "recommendation": _RECIPE_SOFTIRQ}
    return {"verdict": "ok",
            "reason": (f"{len(irqs)} IRQ rows + "
                       f"{len(softirqs)} softirq types ; no single "
                       f"CPU eats ≥ {int(_IRQ_HOT_SHARE * 100)} % of "
                       f"any high-traffic line."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    text_i = _read(_PROC_INTERRUPTS) or ""
    text_s = _read(_PROC_SOFTIRQS) or ""
    irqs = parse_interrupts(text_i)
    softirqs = parse_softirqs(text_s)
    verdict = classify(irqs, softirqs)
    # Trim payload for the UI : drop zero-total rows, top-N pinned.
    nonzero = [r for r in irqs if r["total"] > 0]
    # Annotate each row with hot-CPU + share for UI rendering.
    for r in nonzero:
        hot_idx, share = hot_cpu(r["counts"])
        r["hot_cpu"] = hot_idx
        r["hot_share"] = round(share, 3)
    nonzero.sort(key=lambda r: r["total"], reverse=True)
    return {
        "ok": bool(irqs),
        "cpu_count": len(irqs[0]["counts"]) if irqs else 0,
        "irq_row_count": len(irqs),
        "nonzero_irq_count": len(nonzero),
        "top_irqs": nonzero[:30],
        "softirqs": softirqs,
        "verdict": verdict,
    }
