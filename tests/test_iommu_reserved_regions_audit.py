"""Tests for modules/iommu_reserved_regions_audit.py
R&D #88.3."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import (
    iommu_reserved_regions_audit as mod)


def _mk_group(tmp_path, gid, *, type_="DMA-FQ",
              regions="0x00000000 0x000fffff direct\n"
                      "0xfee00000 0xfeefffff msi\n"):
    d = tmp_path / "iommu_groups" / str(gid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "type").write_text(type_ + "\n")
    (d / "reserved_regions").write_text(regions)
    return d


def _mk_pci_device(tmp_path, bdf, *, cls="0x030000",
                    group_id="1"):
    d = tmp_path / "pci" / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "class").write_text(cls + "\n")
    link_target = tmp_path / "iommu_groups" / str(group_id)
    link_target.mkdir(parents=True, exist_ok=True)
    os.symlink(str(link_target), str(d / "iommu_group"))


# --- parse_reserved_regions ------------------------------------

def test_parse_empty():
    assert mod.parse_reserved_regions("") == []


def test_parse_typical():
    text = (
        "0x0000000000000000 0x00000000000fffff direct\n"
        "0x00000000fee00000 0x00000000feefffff msi\n")
    regs = mod.parse_reserved_regions(text)
    assert len(regs) == 2
    assert regs[0][0] == 0x0
    assert regs[0][2] == "direct"
    assert regs[1][2] == "msi"


def test_parse_garbage():
    text = "garbage line\n0x1 0x2 direct\nNOT HEX direct\n"
    regs = mod.parse_reserved_regions(text)
    assert len(regs) == 1


# --- regions_overlap_direct_msi --------------------------------

def test_overlap_none():
    regs = [(0, 0xfffff, "direct"),
            (0xfee00000, 0xfeefffff, "msi")]
    assert mod.regions_overlap_direct_msi(regs) is False


def test_overlap_yes():
    regs = [(0xfee00000, 0xfeeffffff, "direct"),
            (0xfee00000, 0xfeefffff, "msi")]
    assert mod.regions_overlap_direct_msi(regs) is True


def test_overlap_no_msi():
    regs = [(0, 0xfffff, "direct")]
    assert mod.regions_overlap_direct_msi(regs) is False


# --- _enabled --------------------------------------------------

def test_enabled_intel():
    assert mod._enabled(["intel_iommu=on", "iommu=pt"]) is True


def test_enabled_amd():
    assert mod._enabled(["amd_iommu=on"]) is True


def test_enabled_no_token():
    assert mod._enabled(["ro", "quiet"]) is False


# --- list_groups -----------------------------------------------

def test_list_groups_missing(tmp_path):
    assert mod.list_groups(str(tmp_path / "nope")) == []


def test_list_groups_sorted(tmp_path):
    _mk_group(tmp_path, "5")
    _mk_group(tmp_path, "2")
    _mk_group(tmp_path, "13")
    out = mod.list_groups(str(tmp_path / "iommu_groups"))
    assert out == ["2", "5", "13"]


# --- find_gpu_groups -------------------------------------------

def test_find_gpu_groups_none(tmp_path):
    assert mod.find_gpu_groups(
        str(tmp_path / "nope")) == set()


def test_find_gpu_groups_vga(tmp_path):
    _mk_pci_device(tmp_path, "0000:01:00.0",
                       cls="0x030000", group_id="1")
    _mk_pci_device(tmp_path, "0000:02:00.0",
                       cls="0x010000", group_id="2")
    gids = mod.find_gpu_groups(str(tmp_path / "pci"))
    assert gids == {"1"}


def test_find_gpu_groups_3d_compute(tmp_path):
    _mk_pci_device(tmp_path, "0000:03:00.0",
                       cls="0x030200", group_id="3")
    gids = mod.find_gpu_groups(str(tmp_path / "pci"))
    assert gids == {"3"}


# --- classify --------------------------------------------------

def test_classify_unknown_empty():
    v = mod.classify([], set(), {}, {}, [])
    assert v["verdict"] == "unknown"


def test_classify_iommu_off_but_groups_present():
    v = mod.classify(["1", "2"], set(),
                          {"1": "DMA", "2": "DMA"},
                          {"1": [], "2": []},
                          ["ro", "quiet"])
    assert v["verdict"] == "iommu_off_but_groups_present"


def test_classify_direct_map_on_gpu():
    v = mod.classify(
        ["1", "2"], {"1"},
        {"1": "identity", "2": "DMA-FQ"},
        {"1": [], "2": []},
        ["intel_iommu=on"])
    assert v["verdict"] == "direct_map_on_gpu_group"


def test_classify_msi_overlap():
    overlap = [(0xfee00000, 0xfeeffffff, "direct"),
                (0xfee00000, 0xfeefffff, "msi")]
    v = mod.classify(
        ["1"], set(),
        {"1": "DMA-FQ"},
        {"1": overlap},
        ["intel_iommu=on"])
    assert v["verdict"] == "reserved_region_overlap_msi"


def test_classify_dma_type_default():
    v = mod.classify(
        ["1", "2"], set(),
        {"1": "DMA", "2": "DMA"},
        {"1": [], "2": []},
        ["intel_iommu=on"])
    assert v["verdict"] == "dma_type_default"


def test_classify_ok_dma_fq():
    v = mod.classify(
        ["1", "2"], {"1"},
        {"1": "DMA-FQ", "2": "DMA-FQ"},
        {"1": [], "2": []},
        ["intel_iommu=on"])
    assert v["verdict"] == "iommu_dma_fq_ok"


# Priority : off > gpu_identity > overlap > dma_default
def test_priority_off_over_identity():
    v = mod.classify(
        ["1"], {"1"},
        {"1": "identity"},
        {"1": []},
        ["ro"])
    assert v["verdict"] == "iommu_off_but_groups_present"


def test_priority_identity_over_overlap():
    overlap = [(0xfee00000, 0xfeeffffff, "direct"),
                (0xfee00000, 0xfeefffff, "msi")]
    v = mod.classify(
        ["1"], {"1"},
        {"1": "identity"},
        {"1": overlap},
        ["intel_iommu=on"])
    assert v["verdict"] == "direct_map_on_gpu_group"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                     str(tmp_path / "nope_iommu"),
                     str(tmp_path / "nope_pci"),
                     str(tmp_path / "nope_cmdline"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_group(tmp_path, "1", type_="DMA-FQ")
    _mk_group(tmp_path, "2", type_="DMA-FQ")
    _mk_pci_device(tmp_path, "0000:01:00.0",
                       cls="0x030000", group_id="1")
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("ro intel_iommu=on\n")
    out = mod.status(None,
                     str(tmp_path / "iommu_groups"),
                     str(tmp_path / "pci"),
                     str(cmdline))
    assert out["verdict"]["verdict"] == "iommu_dma_fq_ok"
    assert out["group_count"] == 2
    assert out["gpu_group_count"] == 1
    assert out["ok"] is True


def test_status_dma_default_synthetic(tmp_path):
    _mk_group(tmp_path, "1", type_="DMA")
    _mk_group(tmp_path, "2", type_="DMA")
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("ro intel_iommu=on\n")
    out = mod.status(None,
                     str(tmp_path / "iommu_groups"),
                     str(tmp_path / "nope_pci"),
                     str(cmdline))
    assert out["verdict"]["verdict"] == "dma_type_default"
    assert out["ok"] is False
