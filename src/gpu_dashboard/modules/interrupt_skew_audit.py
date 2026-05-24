"""Module interrupt_skew_audit — driver affinity_hint vs admin/
irqbalance override detector (R&D #87.4).

Three existing modules already cover most of the IRQ surface :

  * irq_rates_audit (R&D #43.1) — /proc/interrupts rates,
    softIRQ imbalance, "all IRQs on one CPU" (cpu_pinned).
  * gpu_irq_affinity (R&D #38.4) — GPU-specific
    effective_affinity vs smp_affinity correctness.
  * nic_queue_affinity (R&D #40.4) — RX/TX rps_cpus /
    xps_cpus / rps_flow_cnt per NIC.

This audit owns the ONE non-overlapping IRQ signal : driver-
supplied per-queue placement hints (/proc/irq/<N>/affinity_hint)
that are being silently overridden by irqbalance or by an
admin who echoed a different mask into smp_affinity_list.

When a multi-queue device driver loads, it tells the kernel via
affinity_hint which CPU each MSI-X vector ideally targets — this
is usually a 1:1 queue-to-CPU map keyed off the device's local
NUMA node. If irqbalance ignores those hints (its default in
many distros) the resulting smp_affinity_list spreads vectors
arbitrarily, and the driver's careful per-queue locality work is
lost. The fix is to either start irqbalance with the right
policy or echo the affinity_hint values back into
smp_affinity_list manually.

Reads :

  /proc/interrupts                          IRQ number enumeration
  /proc/irq/<N>/affinity_hint               hex mask, driver's
                                            requested CPU(s)
  /proc/irq/<N>/smp_affinity_list           comma list, actual
                                            allowed CPUs

Verdicts (worst-first) :

  affinity_hint_widely_overridden    ≥5 IRQs have a non-zero
                                     affinity_hint AND their
                                     smp_affinity_list disjoint
                                     from the hinted CPUs.
  affinity_hint_mismatch             1-4 IRQs in the same state
                                     (smaller-scale drift).
  ok                                 all hints honored, no hints
                                     set, or no MSI-X devices.
  unknown                            /proc/interrupts unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "interrupt_skew_audit"

DEFAULT_PROC_INTERRUPTS = "/proc/interrupts"
DEFAULT_PROC_IRQ_ROOT = "/proc/irq"

# Threshold separating warn from accent severity.
_WIDE_OVERRIDE_THRESHOLD = 5

_LEADING_IRQ = re.compile(r"^\s*(\d+):")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_irq_numbers(text: str) -> list:
    """Return numeric IRQ ids found in /proc/interrupts.

    Skips the header row, NMI/LOC/TLB/PMI-style symbolic rows,
    and any line that doesn't begin with `<digits>:`.
    """
    if not text:
        return []
    irqs: list = []
    for line in text.splitlines():
        m = _LEADING_IRQ.match(line)
        if m:
            irqs.append(int(m.group(1)))
    return irqs


def parse_hex_mask(text: str) -> set:
    """Parse Linux hex CPU mask (comma-separated 32-bit words)."""
    if not text:
        return set()
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        return set()
    try:
        value = int(cleaned, 16)
    except ValueError:
        return set()
    cpus: set = set()
    i = 0
    while value:
        if value & 1:
            cpus.add(i)
        value >>= 1
        i += 1
    return cpus


def parse_cpu_list(text: str) -> set:
    """Parse smp_affinity_list format e.g. '0-3,8,10-11'."""
    if not text:
        return set()
    cpus: set = set()
    for tok in text.strip().split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            try:
                lo, hi = tok.split("-", 1)
                lo_i, hi_i = int(lo), int(hi)
            except ValueError:
                continue
            if lo_i <= hi_i:
                cpus.update(range(lo_i, hi_i + 1))
        else:
            try:
                cpus.add(int(tok))
            except ValueError:
                continue
    return cpus


def read_irq_pair(irq_root: str, irq: int) -> dict:
    """Return {hint:set, smp:set} for one IRQ — empty sets when
    the file is missing or unreadable."""
    base = os.path.join(irq_root, str(irq))
    hint_raw = _read_text(os.path.join(base, "affinity_hint"))
    smp_raw = _read_text(os.path.join(base, "smp_affinity_list"))
    return {
        "hint": parse_hex_mask(hint_raw or ""),
        "smp": parse_cpu_list(smp_raw or ""),
    }


def find_mismatches(irq_root: str, irqs: list) -> list:
    """Return [(irq, hint_set, smp_set), ...] for each IRQ whose
    affinity_hint is non-empty AND disjoint from smp_affinity."""
    out: list = []
    for irq in irqs:
        pair = read_irq_pair(irq_root, irq)
        hint = pair["hint"]
        smp = pair["smp"]
        if not hint:
            continue
        if hint.isdisjoint(smp):
            out.append((irq, hint, smp))
    return out


def classify(irqs: list, mismatches: list) -> dict:
    if not irqs:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/interrupts unreadable or empty — "
                    "cannot enumerate IRQ vectors.")}
    n = len(mismatches)
    if n >= _WIDE_OVERRIDE_THRESHOLD:
        sample = sorted(m[0] for m in mismatches)[:5]
        return {
            "verdict": "affinity_hint_widely_overridden",
            "reason": (
                f"{n} IRQ vector(s) have a driver-supplied "
                "affinity_hint that is disjoint from their "
                "smp_affinity_list — irqbalance or an admin "
                "is overriding driver placement. Sample "
                f"IRQs: {sample}."),
            "mismatch_count": n}
    if n > 0:
        sample = sorted(m[0] for m in mismatches)
        return {
            "verdict": "affinity_hint_mismatch",
            "reason": (
                f"{n} IRQ vector(s) drift from their driver "
                f"affinity_hint (IRQs: {sample})."),
            "mismatch_count": n}
    return {"verdict": "ok",
            "reason": (
                f"{len(irqs)} IRQ vector(s) inspected ; all "
                "driver affinity_hints honored or absent.")}


def status(config: Optional[dict] = None,
           proc_interrupts: str = DEFAULT_PROC_INTERRUPTS,
           proc_irq_root: str = DEFAULT_PROC_IRQ_ROOT) -> dict:
    text = _read_text(proc_interrupts) or ""
    irqs = parse_irq_numbers(text)
    mismatches = find_mismatches(proc_irq_root, irqs)
    verdict = classify(irqs, mismatches)
    return {
        "ok": verdict["verdict"] not in (
            "affinity_hint_widely_overridden",
            "affinity_hint_mismatch", "unknown"),
        "irq_count": len(irqs),
        "mismatch_count": len(mismatches),
        "verdict": verdict,
    }
