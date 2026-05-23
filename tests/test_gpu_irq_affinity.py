"""Tests for modules/gpu_irq_affinity.py — R&D #38.4 GPU IRQ affinity."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import gpu_irq_affinity


def _mk_gpu(root: Path, bdf: str, *, vendor: str = "0x10de",
              klass: str = "0x030000",
              irqs: list | None = None,
              legacy_irq: str = "77",
              local_cpulist: str = "0-11"):
    d = root / bdf
    d.mkdir(parents=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "class").write_text(klass + "\n")
    (d / "irq").write_text(legacy_irq + "\n")
    (d / "local_cpulist").write_text(local_cpulist + "\n")
    if irqs is not None:
        (d / "msi_irqs").mkdir()
        for n in irqs:
            (d / "msi_irqs" / str(n)).write_text("")


def _mk_irq(proc_root: Path, irq: int, smp: str = "0-11",
              effective: str | None = None):
    d = proc_root / "irq" / str(irq)
    d.mkdir(parents=True)
    (d / "smp_affinity_list").write_text(smp + "\n")
    if effective is None:
        effective = smp.split(",")[0].split("-")[0]
    (d / "effective_affinity_list").write_text(effective + "\n")


# --- read helpers ----------------------------------------------

def test_read_smp_affinity_list(tmp_path):
    _mk_irq(tmp_path, 77, smp="0-7")
    assert gpu_irq_affinity.read_smp_affinity_list(str(tmp_path), 77) == "0-7"


def test_read_effective_affinity_list(tmp_path):
    _mk_irq(tmp_path, 77, smp="0-11", effective="4")
    assert gpu_irq_affinity.read_effective_affinity_list(str(tmp_path),
                                                            77) == "4"


def test_read_missing_returns_none(tmp_path):
    assert gpu_irq_affinity.read_smp_affinity_list(str(tmp_path), 99) is None


def test_list_irqs_for_gpu_msi(tmp_path):
    _mk_gpu(tmp_path, "0000:01:00.0", irqs=[77])
    out = gpu_irq_affinity.list_irqs_for_gpu(str(tmp_path), "0000:01:00.0")
    assert out == [77]


def test_list_irqs_for_gpu_legacy_fallback(tmp_path):
    # No msi_irqs/ → use legacy irq
    _mk_gpu(tmp_path, "0000:01:00.0", irqs=None, legacy_irq="16")
    out = gpu_irq_affinity.list_irqs_for_gpu(str(tmp_path), "0000:01:00.0")
    assert out == [16]


def test_list_irqs_for_gpu_msi_x_multi(tmp_path):
    _mk_gpu(tmp_path, "0000:01:00.0",
              irqs=[77, 78, 79, 80, 81, 82, 83, 84])
    out = gpu_irq_affinity.list_irqs_for_gpu(str(tmp_path), "0000:01:00.0")
    assert out == [77, 78, 79, 80, 81, 82, 83, 84]


# --- classify ----------------------------------------------------

def test_classify_no_gpus():
    v = gpu_irq_affinity.classify(cards=[])
    assert v["verdict"] == "no_gpus"


def test_classify_single_cpu_pin():
    # 1 MSI vector, effective on a single CPU — fine for low traffic
    cards = [{
        "gpu_bdf": "0000:01:00.0",
        "local_cpulist": "0-11",
        "irqs": [{"irq": 77, "smp_list": "0-11", "effective": "4"}],
    }]
    v = gpu_irq_affinity.classify(cards)
    assert v["verdict"] == "single_cpu_pin"


def test_classify_cpu0_concentrated():
    # ALL GPU IRQs concentrated on CPU 0 — the classic foot-gun
    cards = [{
        "gpu_bdf": "0000:01:00.0",
        "local_cpulist": "0-11",
        "irqs": [
            {"irq": 77, "smp_list": "0", "effective": "0"},
            {"irq": 78, "smp_list": "0", "effective": "0"},
            {"irq": 79, "smp_list": "0", "effective": "0"},
        ],
    }]
    v = gpu_irq_affinity.classify(cards)
    assert v["verdict"] == "cpu0_concentrated"
    assert "irqbalance" in v["recommendation"].lower() or "/proc/irq" in v["recommendation"]


def test_classify_balanced_multi_irq_multi_cpu():
    # 8 MSI-X vectors spread across 8 CPUs
    cards = [{
        "gpu_bdf": "0000:01:00.0",
        "local_cpulist": "0-11",
        "irqs": [{"irq": 77 + i, "smp_list": "0-11", "effective": str(i)}
                  for i in range(8)],
    }]
    v = gpu_irq_affinity.classify(cards)
    assert v["verdict"] == "balanced"


def test_classify_mismatch_local():
    # GPU's local_cpulist is 0-7 but IRQ ended up on CPU 8 (non-local)
    cards = [{
        "gpu_bdf": "0000:01:00.0",
        "local_cpulist": "0-7",
        "irqs": [{"irq": 77, "smp_list": "0-11", "effective": "8"}],
    }]
    v = gpu_irq_affinity.classify(cards)
    assert v["verdict"] == "mismatch_local"
    assert "8" in v["reason"] or "0-7" in v["reason"]


def test_classify_picks_worst():
    # One card cpu0_concentrated, another single_cpu_pin → cpu0 wins
    cards = [
        {"gpu_bdf": "0000:01:00.0", "local_cpulist": "0-11",
         "irqs": [{"irq": 77, "smp_list": "0-11", "effective": "4"}]},
        {"gpu_bdf": "0000:02:00.0", "local_cpulist": "0-11",
         "irqs": [{"irq": 80, "smp_list": "0", "effective": "0"},
                   {"irq": 81, "smp_list": "0", "effective": "0"}]},
    ]
    v = gpu_irq_affinity.classify(cards)
    assert v["verdict"] == "cpu0_concentrated"


def test_classify_recipe_includes_irqbalance_and_proc():
    cards = [{
        "gpu_bdf": "0000:01:00.0",
        "local_cpulist": "0-11",
        "irqs": [{"irq": 77, "smp_list": "0", "effective": "0"},
                  {"irq": 78, "smp_list": "0", "effective": "0"}],
    }]
    v = gpu_irq_affinity.classify(cards)
    rec = v["recommendation"]
    assert "irqbalance" in rec.lower() or "/proc/irq" in rec


# --- status ----------------------------------------------------

def test_status_no_gpus(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_irq_affinity, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(gpu_irq_affinity, "_PROC_ROOT",
                          str(tmp_path / "proc_missing"))
    s = gpu_irq_affinity.status()
    assert s["ok"] is True
    assert s["gpu_count"] == 0
    assert s["verdict"]["verdict"] == "no_gpus"


def test_status_live_single_msi(tmp_path, monkeypatch):
    pci = tmp_path / "pci"
    proc = tmp_path / "proc"
    _mk_gpu(pci, "0000:01:00.0", irqs=[77])
    _mk_irq(proc, 77, smp="0-11", effective="4")
    monkeypatch.setattr(gpu_irq_affinity, "_PCI_ROOT", str(pci))
    monkeypatch.setattr(gpu_irq_affinity, "_PROC_ROOT", str(proc))
    s = gpu_irq_affinity.status()
    assert s["gpu_count"] == 1
    card = s["cards"][0]
    assert len(card["irqs"]) == 1
    assert card["irqs"][0]["irq"] == 77
    assert card["irqs"][0]["effective"] == "4"
    assert s["verdict"]["verdict"] == "single_cpu_pin"


def test_status_cpu0_concentrated(tmp_path, monkeypatch):
    pci = tmp_path / "pci"
    proc = tmp_path / "proc"
    _mk_gpu(pci, "0000:01:00.0", irqs=[77, 78, 79])
    for n in [77, 78, 79]:
        _mk_irq(proc, n, smp="0", effective="0")
    monkeypatch.setattr(gpu_irq_affinity, "_PCI_ROOT", str(pci))
    monkeypatch.setattr(gpu_irq_affinity, "_PROC_ROOT", str(proc))
    s = gpu_irq_affinity.status()
    assert s["verdict"]["verdict"] == "cpu0_concentrated"


def test_status_includes_irq_count(tmp_path, monkeypatch):
    pci = tmp_path / "pci"
    proc = tmp_path / "proc"
    _mk_gpu(pci, "0000:01:00.0", irqs=[77, 78])
    _mk_irq(proc, 77, smp="0-11", effective="4")
    _mk_irq(proc, 78, smp="0-11", effective="5")
    monkeypatch.setattr(gpu_irq_affinity, "_PCI_ROOT", str(pci))
    monkeypatch.setattr(gpu_irq_affinity, "_PROC_ROOT", str(proc))
    s = gpu_irq_affinity.status()
    assert s["total_irqs"] == 2
