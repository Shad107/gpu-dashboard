"""Tests for modules/irq_rates_audit.py — R&D #43.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import irq_rates_audit as mod


# Sample /proc/interrupts (3 CPUs) -----------------------------------

INTERRUPTS_SAMPLE = """\
           CPU0       CPU1       CPU2
  0:         33          0          0  IO-APIC   2-edge      timer
  1:          0          0          9  IO-APIC   1-edge      i8042
 20:          0          0     106033  IO-APIC  20-fasteoi   virtio1
 29:         50         60         55  PCI-MSIX-...   1-edge      virtio2
LOC:    1000000     950000     980000  Local timer interrupts
RES:        100        100        100  Rescheduling interrupts
ERR:          0
"""

SOFTIRQS_SAMPLE = """\
                    CPU0       CPU1       CPU2
          HI:          3          1          3
       TIMER:    1000000     900000     950000
      NET_TX:        100         50         30
      NET_RX:     800000      10000       8000
"""


# --- parse_interrupts ----------------------------------------------

def test_parse_interrupts_basic():
    rows = mod.parse_interrupts(INTERRUPTS_SAMPLE)
    # Skip ERR (no per-CPU counts beyond CPU0)
    assert any(r["irq"] == "0" for r in rows)
    assert any(r["irq"] == "20" for r in rows)
    row20 = next(r for r in rows if r["irq"] == "20")
    assert row20["counts"] == [0, 0, 106033]
    assert "virtio1" in row20["device"]
    assert "IO-APIC" in row20["chip"]


def test_parse_interrupts_named_irqs_included():
    rows = mod.parse_interrupts(INTERRUPTS_SAMPLE)
    loc = next((r for r in rows if r["irq"] == "LOC"), None)
    assert loc is not None
    assert loc["counts"] == [1000000, 950000, 980000]


def test_parse_interrupts_empty():
    assert mod.parse_interrupts("") == []


def test_parse_interrupts_no_header():
    assert mod.parse_interrupts("not a real header\n") == []


def test_parse_interrupts_skips_malformed_count_lines():
    txt = ("           CPU0       CPU1\n"
           "  1:  oops  nope\n"
           "  2:  100  200  IO-APIC  2-edge  good\n")
    rows = mod.parse_interrupts(txt)
    assert len(rows) == 1
    assert rows[0]["irq"] == "2"


# --- parse_softirqs ------------------------------------------------

def test_parse_softirqs_basic():
    rows = mod.parse_softirqs(SOFTIRQS_SAMPLE)
    assert any(r["type"] == "TIMER" for r in rows)
    net_rx = next(r for r in rows if r["type"] == "NET_RX")
    assert net_rx["counts"] == [800000, 10000, 8000]


def test_parse_softirqs_empty():
    assert mod.parse_softirqs("") == []


# --- hot_cpu -------------------------------------------------------

def test_hot_cpu_zero_total():
    assert mod.hot_cpu([0, 0, 0]) == (-1, 0.0)


def test_hot_cpu_basic():
    idx, share = mod.hot_cpu([10, 80, 10])
    assert idx == 1
    assert 0.79 < share < 0.81


# --- classify ------------------------------------------------------

def test_classify_unknown_empty():
    v = mod.classify([], [])
    assert v["verdict"] == "unknown"


def test_classify_no_irqs():
    irqs = [{"irq": "0", "counts": [], "device": "x", "chip": "",
              "total": 0}]
    v = mod.classify(irqs, [])
    assert v["verdict"] == "no_irqs"


def test_classify_cpu_pinned():
    irqs = [
        {"irq": "20", "counts": [0, 0, 106033],
         "device": "virtio1", "chip": "IO-APIC", "total": 106033},
        {"irq": "1", "counts": [10, 10, 10],
         "device": "i8042", "chip": "IO-APIC", "total": 30},
    ]
    v = mod.classify(irqs, [])
    assert v["verdict"] == "cpu_pinned"
    assert "virtio1" in v["reason"]
    assert "CPU2" in v["reason"]


def test_classify_cpu_pinned_skips_low_volume():
    # Even 100% on one CPU, if total < 10k, don't classify.
    irqs = [{"irq": "1", "counts": [0, 100, 0],
              "device": "x", "chip": "", "total": 100}]
    v = mod.classify(irqs, [])
    assert v["verdict"] == "ok"


def test_classify_softirq_imbalance():
    irqs = [{"irq": "1", "counts": [50, 50, 50],
              "device": "x", "chip": "", "total": 150}]
    softirqs = [{"type": "NET_RX",
                  "counts": [800000, 10000, 8000],
                  "total": 818000}]
    v = mod.classify(irqs, softirqs)
    assert v["verdict"] == "softirq_imbalance"
    assert "NET_RX" in v["reason"]


def test_classify_irq_pinned_wins_over_softirq():
    irqs = [{"irq": "20", "counts": [0, 0, 106033],
              "device": "virtio1", "chip": "", "total": 106033}]
    softirqs = [{"type": "NET_RX",
                  "counts": [800000, 10000, 8000],
                  "total": 818000}]
    v = mod.classify(irqs, softirqs)
    assert v["verdict"] == "cpu_pinned"


def test_classify_ok():
    irqs = [{"irq": "20", "counts": [50000, 60000, 55000],
              "device": "virtio1", "chip": "", "total": 165000}]
    v = mod.classify(irqs, [])
    assert v["verdict"] == "ok"


# --- status integration -------------------------------------------

def test_status_with_isolated_files(monkeypatch, tmp_path):
    (tmp_path / "interrupts").write_text(INTERRUPTS_SAMPLE)
    (tmp_path / "softirqs").write_text(SOFTIRQS_SAMPLE)
    monkeypatch.setattr(mod, "_PROC_INTERRUPTS",
                        str(tmp_path / "interrupts"))
    monkeypatch.setattr(mod, "_PROC_SOFTIRQS",
                        str(tmp_path / "softirqs"))
    out = mod.status()
    assert out["ok"] is True
    assert out["cpu_count"] == 3
    # virtio1 IRQ 20 is pinned to CPU2 with > 60 % share.
    assert out["verdict"]["verdict"] == "cpu_pinned"
    # top_irqs has the high-volume LOC first.
    assert out["top_irqs"][0]["irq"] == "LOC"


def test_status_unknown_when_no_proc(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_INTERRUPTS",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_PROC_SOFTIRQS",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
