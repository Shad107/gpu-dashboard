"""Module psi_irq_full_audit — kernel >=5.13 PSI surfaces
the older psi_pressure_audit doesn't cover (R&D #98.1).

The existing psi_pressure_audit module parses memory.full
and io.full only. Two newer PSI surfaces matter on a
single-GPU desktop / homelab and are completely uncovered :

  /proc/pressure/irq          (kernel >= 5.13, CONFIG_PSI_IRQ)
  /proc/pressure/cpu  'full'  (kernel >= 5.13, surfaces a
                                cpu fully stuck in hardirq /
                                softirq with no userspace
                                making forward progress).

A wedged hardirq path (NIC MSI storm, NVMe interrupt loop)
keeps system load looking idle while one CPU drowns and
GPU/display latency spikes. None of the existing PSI / IRQ
modules look at PSI/irq or cpu.full.

Reads :

  /proc/pressure/irq          # some + full rows
  /proc/pressure/cpu          # full row only

Verdicts (worst-first) :

  irq_full_stall      err     irq full avg60 > 20 %.
  cpu_full_stall      warn    cpu full avg60 > 10 %.
  psi_irq_absent      accent  /proc/pressure/irq missing
                              (kernel built without
                              CONFIG_PSI_IRQ) — half the
                              signal is invisible.
  ok                          all PSI 'full' rows quiet.
  requires_root               files exist but unreadable.
  unknown                     /proc/pressure/cpu missing
                              (CONFIG_PSI not enabled).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "psi_irq_full_audit"

DEFAULT_PRESSURE_DIR = "/proc/pressure"

_RX = re.compile(
    r"^(?P<kind>some|full)\s+"
    r"avg10=(?P<a10>[\d.]+)\s+"
    r"avg60=(?P<a60>[\d.]+)\s+"
    r"avg300=(?P<a300>[\d.]+)\s+"
    r"total=(?P<total>\d+)")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_pressure(text: Optional[str]) -> dict:
    """Parse /proc/pressure/<resource>.

    Returns {"some": {a10,a60,a300,total}, "full": {...}}
    Missing rows produce empty sub-dicts.
    """
    out: dict = {"some": {}, "full": {}}
    if not text:
        return out
    for line in text.splitlines():
        m = _RX.match(line)
        if not m:
            continue
        d = m.groupdict()
        out[d["kind"]] = {
            "a10": float(d["a10"]),
            "a60": float(d["a60"]),
            "a300": float(d["a300"]),
            "total": int(d["total"]),
        }
    return out


def classify(cpu_present: bool,
             cpu_readable: bool,
             cpu_full: dict,
             irq_present: bool,
             irq_readable: bool,
             irq: dict) -> dict:
    if not cpu_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/pressure/cpu absent — CONFIG_PSI "
                    "disabled or kernel too old.")}
    if not cpu_readable or (
            irq_present and not irq_readable):
        return {"verdict": "requires_root",
                "reason": (
                    "/proc/pressure/* unreadable — re-run "
                    "as root.")}

    # err — irq full avg60 > 20 %
    irq_full = irq.get("full") or {}
    irq_a60 = irq_full.get("a60", 0.0)
    if irq_present and irq_a60 > 20.0:
        return {
            "verdict": "irq_full_stall",
            "reason": (
                f"irq.full avg60 = {irq_a60:.1f}% — a CPU "
                "is wedged in hardirq / softirq, latency-"
                "sensitive workloads will stall.")}

    # warn — cpu full avg60 > 10 %
    cpu_a60 = cpu_full.get("a60", 0.0)
    if cpu_a60 > 10.0:
        return {
            "verdict": "cpu_full_stall",
            "reason": (
                f"cpu.full avg60 = {cpu_a60:.1f}% — every "
                "runnable task on at least one CPU is "
                "stalling. Investigate hot CPU.")}

    # accent — irq PSI absent
    if not irq_present:
        return {
            "verdict": "psi_irq_absent",
            "reason": (
                "/proc/pressure/irq missing — kernel "
                "built without CONFIG_PSI_IRQ ; half the "
                "PSI signal is invisible.")}

    return {"verdict": "ok",
            "reason": (
                f"cpu.full avg60={cpu_a60:.2f}% ; "
                f"irq.full avg60={irq_a60:.2f}% — quiet.")}


def status(config: Optional[dict] = None,
           pressure_dir: str = DEFAULT_PRESSURE_DIR) -> dict:
    cpu_path = os.path.join(pressure_dir, "cpu")
    irq_path = os.path.join(pressure_dir, "irq")

    cpu_present = os.path.isfile(cpu_path)
    cpu_text = _read_text(cpu_path) if cpu_present else None
    cpu_readable = cpu_text is not None
    cpu = parse_pressure(cpu_text)

    irq_present = os.path.isfile(irq_path)
    irq_text = _read_text(irq_path) if irq_present else None
    irq_readable = (
        irq_text is not None if irq_present else True)
    irq = parse_pressure(irq_text)

    verdict = classify(
        cpu_present, cpu_readable, cpu.get("full") or {},
        irq_present, irq_readable, irq)

    return {
        "ok": verdict["verdict"] == "ok",
        "irq_present": irq_present,
        "cpu_full": cpu.get("full") or {},
        "irq_full": irq.get("full") or {},
        "verdict": verdict,
    }
