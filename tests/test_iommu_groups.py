"""Tests for modules/iommu_groups.py — R&D #30.2 IOMMU group auditor."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from gpu_dashboard.modules import iommu_groups


def _mk_pci_dev(root: Path, bdf: str, vendor: str, klass: str):
    d = root / bdf
    d.mkdir(parents=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "class").write_text(klass + "\n")


def _mk_group(iommu_root: Path, gnum: int, bdfs: list[str]):
    g = iommu_root / str(gnum) / "devices"
    g.mkdir(parents=True)
    for b in bdfs:
        # Real IOMMU dirs hold symlinks back to /sys/bus/pci/devices/<bdf>,
        # but for verdict purposes we only need the entries to exist.
        (g / b).write_text("")


# --- helpers ---------------------------------------------------------

def test_list_groups_returns_sorted_ints(tmp_path):
    _mk_group(tmp_path, 7, ["0000:00:00.0"])
    _mk_group(tmp_path, 14, ["0000:00:01.0"])
    _mk_group(tmp_path, 3, ["0000:00:02.0"])
    assert iommu_groups.list_groups(str(tmp_path)) == [3, 7, 14]


def test_list_groups_empty_when_missing(tmp_path):
    assert iommu_groups.list_groups(str(tmp_path / "absent")) == []


def test_list_groups_ignores_non_numeric(tmp_path):
    _mk_group(tmp_path, 1, ["0000:00:00.0"])
    (tmp_path / "weird").mkdir()
    assert iommu_groups.list_groups(str(tmp_path)) == [1]


def test_list_devices_in_group(tmp_path):
    _mk_group(tmp_path, 14, ["0000:01:00.0", "0000:00:1d.0"])
    devs = iommu_groups.list_devices_in_group(str(tmp_path), 14)
    assert sorted(devs) == ["0000:00:1d.0", "0000:01:00.0"]


def test_list_devices_in_group_missing(tmp_path):
    assert iommu_groups.list_devices_in_group(str(tmp_path), 99) == []


def test_find_nvidia_bdfs(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000")
    _mk_pci_dev(tmp_path, "0000:00:1f.3", "0x8086", "0x040300")
    bdfs = iommu_groups.find_nvidia_bdfs(str(tmp_path))
    assert bdfs == ["0000:01:00.0"]


def test_find_group_for_bdf(tmp_path):
    _mk_group(tmp_path, 14, ["0000:01:00.0", "0000:00:1d.0"])
    _mk_group(tmp_path, 7, ["0000:02:00.0"])
    assert iommu_groups.find_group_for_bdf(str(tmp_path), "0000:01:00.0") == 14
    assert iommu_groups.find_group_for_bdf(str(tmp_path), "0000:02:00.0") == 7


def test_find_group_for_bdf_absent(tmp_path):
    assert iommu_groups.find_group_for_bdf(str(tmp_path), "0000:99:99.9") is None


# --- device_kind class-code decoding --------------------------------

def test_device_kind_usb_xhci(tmp_path):
    _mk_pci_dev(tmp_path, "0000:00:14.0", "0x8086", "0x0c0330")
    assert iommu_groups.device_kind(str(tmp_path), "0000:00:14.0") == "USB"


def test_device_kind_sata(tmp_path):
    _mk_pci_dev(tmp_path, "0000:00:17.0", "0x8086", "0x010601")
    assert iommu_groups.device_kind(str(tmp_path), "0000:00:17.0") == "SATA"


def test_device_kind_nvme(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x144d", "0x010802")
    assert iommu_groups.device_kind(str(tmp_path), "0000:01:00.0") == "NVMe"


def test_device_kind_audio(tmp_path):
    _mk_pci_dev(tmp_path, "0000:00:1f.3", "0x8086", "0x040300")
    assert iommu_groups.device_kind(str(tmp_path), "0000:00:1f.3") == "Audio"


def test_device_kind_vga(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000")
    assert iommu_groups.device_kind(str(tmp_path), "0000:01:00.0") == "VGA"


def test_device_kind_bridge(tmp_path):
    _mk_pci_dev(tmp_path, "0000:00:01.1", "0x1022", "0x060400")
    assert iommu_groups.device_kind(str(tmp_path), "0000:00:01.1") == "Bridge"


def test_device_kind_unknown_class(tmp_path):
    _mk_pci_dev(tmp_path, "0000:00:14.2", "0x8086", "0x118000")
    assert iommu_groups.device_kind(str(tmp_path), "0000:00:14.2") == "Other"


def test_device_kind_unreadable(tmp_path):
    assert iommu_groups.device_kind(str(tmp_path), "0000:99:99.9") == "Unknown"


# --- classify --------------------------------------------------------

def test_classify_gpu_alone_is_clean():
    v = iommu_groups.classify(
        gpu_bdf="0000:01:00.0",
        siblings=[],
    )
    assert v["verdict"] == "clean"


def test_classify_gpu_with_bridge_only_is_clean():
    # GPU + parent bridge in same group — that's normal and passes fine.
    v = iommu_groups.classify(
        gpu_bdf="0000:01:00.0",
        siblings=[{"bdf": "0000:00:01.0", "kind": "Bridge"}],
    )
    assert v["verdict"] == "clean"


def test_classify_gpu_with_gpu_audio_only_is_clean():
    # The GPU's onboard HDA audio (same BDF function .1) doesn't count as
    # a "foreign" sibling — same card.
    v = iommu_groups.classify(
        gpu_bdf="0000:01:00.0",
        siblings=[{"bdf": "0000:01:00.1", "kind": "Audio"}],
    )
    assert v["verdict"] == "clean"


def test_classify_gpu_with_usb_sibling_is_chipset_shared():
    v = iommu_groups.classify(
        gpu_bdf="0000:01:00.0",
        siblings=[{"bdf": "0000:00:14.0", "kind": "USB"}],
    )
    assert v["verdict"] == "chipset_shared"
    assert "USB" in v["reason"]
    assert "pcie_acs_override" in v["recommendation"]


def test_classify_gpu_with_sata_sibling_is_chipset_shared():
    v = iommu_groups.classify(
        gpu_bdf="0000:01:00.0",
        siblings=[{"bdf": "0000:00:17.0", "kind": "SATA"}],
    )
    assert v["verdict"] == "chipset_shared"
    assert "SATA" in v["reason"]


def test_classify_lists_all_siblings():
    v = iommu_groups.classify(
        gpu_bdf="0000:01:00.0",
        siblings=[
            {"bdf": "0000:00:14.0", "kind": "USB"},
            {"bdf": "0000:00:17.0", "kind": "SATA"},
            {"bdf": "0000:00:01.0", "kind": "Bridge"},
        ],
    )
    assert v["verdict"] == "chipset_shared"
    assert "USB" in v["reason"]
    assert "SATA" in v["reason"]


# --- status ---------------------------------------------------------

def test_status_iommu_disabled_returns_specific_error(tmp_path, monkeypatch):
    monkeypatch.setattr(iommu_groups, "_IOMMU_ROOT", str(tmp_path / "missing"))
    monkeypatch.setattr(iommu_groups, "_PCI_ROOT", str(tmp_path))
    s = iommu_groups.status()
    assert s["ok"] is False
    assert s["error"] == "iommu_disabled"
    assert "intel_iommu" in s["reason"] or "amd_iommu" in s["reason"]


def test_status_iommu_present_no_nvidia(tmp_path, monkeypatch):
    iroot = tmp_path / "iommu"
    proot = tmp_path / "pci"
    proot.mkdir()
    _mk_group(iroot, 1, ["0000:00:00.0"])
    _mk_pci_dev(proot, "0000:00:00.0", "0x8086", "0x060000")
    monkeypatch.setattr(iommu_groups, "_IOMMU_ROOT", str(iroot))
    monkeypatch.setattr(iommu_groups, "_PCI_ROOT", str(proot))
    s = iommu_groups.status()
    assert s["ok"] is True
    assert s["worst_verdict"] == "no_gpus"
    assert s["cards"] == []


def test_status_clean_passthrough_friendly(tmp_path, monkeypatch):
    iroot = tmp_path / "iommu"
    proot = tmp_path / "pci"
    proot.mkdir()
    _mk_pci_dev(proot, "0000:01:00.0", "0x10de", "0x030000")
    _mk_pci_dev(proot, "0000:01:00.1", "0x10de", "0x040300")  # GPU's HDA
    _mk_pci_dev(proot, "0000:00:01.0", "0x1022", "0x060400")  # Bridge
    _mk_group(iroot, 14, ["0000:00:01.0", "0000:01:00.0", "0000:01:00.1"])
    monkeypatch.setattr(iommu_groups, "_IOMMU_ROOT", str(iroot))
    monkeypatch.setattr(iommu_groups, "_PCI_ROOT", str(proot))
    s = iommu_groups.status()
    assert s["ok"] is True
    assert s["worst_verdict"] == "clean"
    assert len(s["cards"]) == 1
    assert s["cards"][0]["verdict"]["verdict"] == "clean"


def test_status_chipset_shared_emits_acs_override(tmp_path, monkeypatch):
    iroot = tmp_path / "iommu"
    proot = tmp_path / "pci"
    proot.mkdir()
    _mk_pci_dev(proot, "0000:01:00.0", "0x10de", "0x030000")
    _mk_pci_dev(proot, "0000:00:14.0", "0x8086", "0x0c0330")  # USB xHCI
    _mk_group(iroot, 14, ["0000:01:00.0", "0000:00:14.0"])
    monkeypatch.setattr(iommu_groups, "_IOMMU_ROOT", str(iroot))
    monkeypatch.setattr(iommu_groups, "_PCI_ROOT", str(proot))
    s = iommu_groups.status()
    assert s["worst_verdict"] == "chipset_shared"
    card = s["cards"][0]
    assert any(sib["kind"] == "USB" for sib in card["siblings"])
    assert "pcie_acs_override" in card["verdict"]["recommendation"]


def test_status_picks_worst_across_multiple_gpus(tmp_path, monkeypatch):
    iroot = tmp_path / "iommu"
    proot = tmp_path / "pci"
    proot.mkdir()
    _mk_pci_dev(proot, "0000:01:00.0", "0x10de", "0x030000")
    _mk_pci_dev(proot, "0000:02:00.0", "0x10de", "0x030000")
    _mk_pci_dev(proot, "0000:00:14.0", "0x8086", "0x0c0330")
    _mk_group(iroot, 14, ["0000:01:00.0"])         # clean
    _mk_group(iroot, 15, ["0000:02:00.0",
                          "0000:00:14.0"])         # shared
    monkeypatch.setattr(iommu_groups, "_IOMMU_ROOT", str(iroot))
    monkeypatch.setattr(iommu_groups, "_PCI_ROOT", str(proot))
    s = iommu_groups.status()
    assert s["worst_verdict"] == "chipset_shared"
    assert len(s["cards"]) == 2
