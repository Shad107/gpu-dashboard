"""Tests for modules/msi_inventory.py — R&D #30.1 MSI-X vector inventory."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import msi_inventory


def _mk_pci_dev(root: Path, bdf: str, vendor: str, klass: str,
                 irq: str = "0", msi_irqs: list[str] | None = None):
    d = root / bdf
    d.mkdir(parents=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "class").write_text(klass + "\n")
    (d / "irq").write_text(irq + "\n")
    if msi_irqs is not None:
        (d / "msi_irqs").mkdir()
        for v in msi_irqs:
            (d / "msi_irqs" / v).write_text("")


# --- helpers ---------------------------------------------------------

def test_list_msi_vectors_returns_sorted_ints(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000",
                msi_irqs=["77", "78", "76"])
    vs = msi_inventory.list_msi_vectors(str(tmp_path), "0000:01:00.0")
    assert vs == [76, 77, 78]


def test_list_msi_vectors_empty_when_absent(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000", msi_irqs=None)
    assert msi_inventory.list_msi_vectors(str(tmp_path), "0000:01:00.0") == []


def test_list_msi_vectors_empty_dir(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000", msi_irqs=[])
    assert msi_inventory.list_msi_vectors(str(tmp_path), "0000:01:00.0") == []


def test_read_irq_returns_int(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000", irq="77")
    assert msi_inventory.read_irq(str(tmp_path), "0000:01:00.0") == 77


def test_read_irq_zero_means_none(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000", irq="0")
    # `irq=0` is sysfs's way of saying "no IRQ assigned" — treat as None
    assert msi_inventory.read_irq(str(tmp_path), "0000:01:00.0") is None


def test_read_irq_missing_returns_none(tmp_path):
    (tmp_path / "0000:01:00.0").mkdir()
    assert msi_inventory.read_irq(str(tmp_path), "0000:01:00.0") is None


# --- /proc/interrupts parsing ---------------------------------------

_SAMPLE_INTERRUPTS = """\
           CPU0       CPU1       CPU2       CPU3
  0:         33          0          0          0  IO-APIC   2-edge      timer
 77:          0          0          0         46 PCI-MSI-0000:01:00.0   0-edge      nvidia
 78:        100         50          0          0 PCI-MSI-X-0000:01:00.0   0-edge      nvidia
NMI:          0          0          0          0  Non-maskable interrupts
"""


def test_parse_interrupts_counts_per_vector():
    rows = msi_inventory.parse_interrupts(_SAMPLE_INTERRUPTS)
    assert rows[77]["count"] == 46
    assert rows[77]["controller"] == "PCI-MSI-0000:01:00.0"
    assert rows[77]["device"] == "nvidia"
    assert rows[78]["count"] == 150
    assert rows[78]["controller"] == "PCI-MSI-X-0000:01:00.0"


def test_parse_interrupts_handles_empty():
    assert msi_inventory.parse_interrupts("") == {}


def test_parse_interrupts_ignores_non_numeric_irq():
    # NMI, LOC, ERR are not vector numbers — skip
    rows = msi_inventory.parse_interrupts(_SAMPLE_INTERRUPTS)
    assert "NMI" not in rows
    assert all(isinstance(k, int) for k in rows)


# --- mode detection from controller text ----------------------------

def test_detect_mode_msix():
    assert msi_inventory.detect_mode("PCI-MSI-X-0000:01:00.0") == "MSI-X"


def test_detect_mode_msi():
    assert msi_inventory.detect_mode("PCI-MSI-0000:01:00.0") == "MSI"


def test_detect_mode_io_apic():
    assert msi_inventory.detect_mode("IO-APIC") == "legacy"


def test_detect_mode_unknown():
    assert msi_inventory.detect_mode("") == "unknown"
    assert msi_inventory.detect_mode(None) == "unknown"


# --- classify -------------------------------------------------------

def test_classify_msix_active():
    v = msi_inventory.classify(
        vectors=[10, 11, 12, 13, 14, 15, 16, 17],
        controllers=["PCI-MSI-X-0000:01:00.0"] * 8,
    )
    assert v["verdict"] == "msix_active"
    assert v["recommendation"] == ""


def test_classify_msi_active_single_vector():
    # The case this dashboard exists to catch: only 1 MSI vector instead
    # of an MSI-X array — ~10% CUDA copy latency tax.
    v = msi_inventory.classify(
        vectors=[77],
        controllers=["PCI-MSI-0000:01:00.0"],
    )
    assert v["verdict"] == "msi_active"
    assert "MSI-X" in v["reason"]
    assert "NVreg_EnableMSI" in v["recommendation"]


def test_classify_legacy_irq_no_msi_at_all():
    v = msi_inventory.classify(vectors=[], controllers=[])
    assert v["verdict"] == "legacy_irq"
    assert "pci=nomsi" in v["recommendation"]


def test_classify_msi_few_vectors_still_suboptimal():
    # 2 or 3 vectors is still not real MSI-X scaling — driver fallback
    v = msi_inventory.classify(
        vectors=[10, 11, 12],
        controllers=["PCI-MSI-0000:01:00.0"] * 3,
    )
    assert v["verdict"] == "msi_active"


# --- status ---------------------------------------------------------

def test_status_no_gpus(tmp_path, monkeypatch):
    monkeypatch.setattr(msi_inventory, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(msi_inventory, "_PROC_INTERRUPTS",
                          str(tmp_path / "interrupts"))
    s = msi_inventory.status()
    assert s["ok"] is True
    assert s["worst_verdict"] == "no_gpus"
    assert s["cards"] == []


def test_status_msix_active(tmp_path, monkeypatch):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000",
                irq="77",
                msi_irqs=[str(i) for i in range(77, 93)])  # 16 MSI-X vectors
    interrupts = tmp_path / "interrupts"
    rows = ["           CPU0       CPU1"]
    for v in range(77, 93):
        rows.append(f"{v:>3}:        100         50 PCI-MSI-X-0000:01:00.0  0-edge   nvidia")
    interrupts.write_text("\n".join(rows) + "\n")
    monkeypatch.setattr(msi_inventory, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(msi_inventory, "_PROC_INTERRUPTS", str(interrupts))
    s = msi_inventory.status()
    assert s["device_count"] == 1
    card = s["cards"][0]
    assert card["vector_count"] == 16
    assert card["mode"] == "MSI-X"
    assert card["verdict"]["verdict"] == "msix_active"


def test_status_msi_active_one_vector(tmp_path, monkeypatch):
    # The live-rig case: 1 MSI vector, controller PCI-MSI not PCI-MSI-X
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000",
                irq="77", msi_irqs=["77"])
    interrupts = tmp_path / "interrupts"
    interrupts.write_text(
        "           CPU0       CPU1\n"
        " 77:        100         50 PCI-MSI-0000:01:00.0  0-edge   nvidia\n"
    )
    monkeypatch.setattr(msi_inventory, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(msi_inventory, "_PROC_INTERRUPTS", str(interrupts))
    s = msi_inventory.status()
    card = s["cards"][0]
    assert card["vector_count"] == 1
    assert card["mode"] == "MSI"
    assert card["verdict"]["verdict"] == "msi_active"


def test_status_legacy_irq(tmp_path, monkeypatch):
    # No msi_irqs/ dir at all → legacy IRQ via /sys/.../irq
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000",
                irq="16", msi_irqs=None)
    interrupts = tmp_path / "interrupts"
    interrupts.write_text(
        "           CPU0       CPU1\n"
        " 16:        100         50 IO-APIC  16-fasteoi  nvidia\n"
    )
    monkeypatch.setattr(msi_inventory, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(msi_inventory, "_PROC_INTERRUPTS", str(interrupts))
    s = msi_inventory.status()
    card = s["cards"][0]
    assert card["vector_count"] == 0
    assert card["mode"] == "legacy"
    assert card["verdict"]["verdict"] == "legacy_irq"


def test_status_total_interrupts_sums_per_card(tmp_path, monkeypatch):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000",
                irq="77", msi_irqs=["77", "78"])
    interrupts = tmp_path / "interrupts"
    interrupts.write_text(
        "           CPU0       CPU1\n"
        " 77:        100         50 PCI-MSI-0000:01:00.0 nvidia\n"
        " 78:        200          0 PCI-MSI-0000:01:00.0 nvidia\n"
    )
    monkeypatch.setattr(msi_inventory, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(msi_inventory, "_PROC_INTERRUPTS", str(interrupts))
    s = msi_inventory.status()
    card = s["cards"][0]
    assert card["total_interrupts"] == 350


def test_status_picks_worst_across_gpus(tmp_path, monkeypatch):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000",
                irq="77",
                msi_irqs=[str(i) for i in range(77, 93)])
    _mk_pci_dev(tmp_path, "0000:02:00.0", "0x10de", "0x030000",
                irq="16", msi_irqs=None)
    interrupts = tmp_path / "interrupts"
    interrupts.write_text("           CPU0\n")
    monkeypatch.setattr(msi_inventory, "_PCI_ROOT", str(tmp_path))
    monkeypatch.setattr(msi_inventory, "_PROC_INTERRUPTS", str(interrupts))
    s = msi_inventory.status()
    assert s["device_count"] == 2
    assert s["worst_verdict"] == "legacy_irq"
