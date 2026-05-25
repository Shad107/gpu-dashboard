"""Tests for modules/iommu_dma_strict_audit.py — R&D #92.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import iommu_dma_strict_audit as mod


# --- parse_cmdline_tokens --------------------------------------

def test_parse_cmdline_empty():
    out = mod.parse_cmdline_tokens("")
    assert out["passthrough"] is False
    assert out["strict"] is None


def test_parse_cmdline_passthrough_iommu_pt():
    out = mod.parse_cmdline_tokens("ro quiet iommu=pt")
    assert out["passthrough"] is True


def test_parse_cmdline_passthrough_token():
    out = mod.parse_cmdline_tokens(
        "ro iommu.passthrough=1 quiet")
    assert out["passthrough"] is True


def test_parse_cmdline_strict_zero():
    out = mod.parse_cmdline_tokens(
        "ro iommu.strict=0 quiet")
    assert out["strict"] == "0"


def test_parse_cmdline_strict_garbage_ignored():
    out = mod.parse_cmdline_tokens(
        "ro iommu.strict=zzz quiet")
    assert out["strict"] is None


# --- classify --------------------------------------------------

def _strict(intel=None, amd=None, generic=None,
            any_unreadable=False):
    return {"intel": intel, "amd": amd, "generic": generic,
            "any_unreadable": any_unreadable}


def _cmdline(passthrough=False, strict=None):
    return {"passthrough": passthrough, "strict": strict}


def test_classify_unknown_no_iommu_no_cmdline():
    v = mod.classify(_strict(), _cmdline(), [], False)
    assert v["verdict"] == "unknown"


def test_classify_passthrough_with_devices_err():
    v = mod.classify(
        _strict(intel="1"),
        _cmdline(passthrough=True),
        ["dma-fq"], True)
    assert v["verdict"] == "iommu_passthrough_with_pcie_devices"


def test_classify_intel_lazy_warn():
    v = mod.classify(
        _strict(intel="0"),
        _cmdline(),
        ["dma-fq"], True)
    assert v["verdict"] == "iommu_lazy_mode_active"


def test_classify_amd_lazy_warn():
    v = mod.classify(
        _strict(amd="lazy"),
        _cmdline(),
        ["dma"], True)
    assert v["verdict"] == "iommu_lazy_mode_active"


def test_classify_cmdline_strict_zero_warn():
    v = mod.classify(
        _strict(intel="1"),
        _cmdline(strict="0"),
        ["dma"], True)
    assert v["verdict"] == "iommu_lazy_mode_active"


def test_classify_requires_root():
    v = mod.classify(
        _strict(any_unreadable=True),
        _cmdline(),
        [], False)
    assert v["verdict"] == "requires_root"


def test_classify_mixed_group_types_accent():
    v = mod.classify(
        _strict(intel="1"),
        _cmdline(),
        ["dma", "dma-fq", "identity", "identity"],
        True)
    assert v["verdict"] == "mixed_group_types"


def test_classify_ok_uniform_dma():
    v = mod.classify(
        _strict(intel="1"),
        _cmdline(),
        ["dma-fq", "dma-fq"], True)
    assert v["verdict"] == "ok"


def test_classify_ok_uniform_identity():
    # All identity ; that's a VFIO host — not "mixed"
    v = mod.classify(
        _strict(intel="1"),
        _cmdline(),
        ["identity", "identity"], True)
    assert v["verdict"] == "ok"


# Priority : passthrough > lazy > requires_root > mixed
def test_priority_passthrough_over_lazy():
    v = mod.classify(
        _strict(intel="0"),
        _cmdline(passthrough=True),
        ["dma"], True)
    assert v["verdict"] == "iommu_passthrough_with_pcie_devices"


def test_priority_lazy_over_mixed():
    v = mod.classify(
        _strict(intel="0"),
        _cmdline(),
        ["dma", "identity"], True)
    assert v["verdict"] == "iommu_lazy_mode_active"


# --- read_strict_mode + sample_group_types via tmpfs -----------

def test_sample_group_types_missing(tmp_path):
    assert mod.sample_group_types(
        str(tmp_path / "nope")) == []


def test_sample_group_types_present(tmp_path):
    root = tmp_path / "iommu_groups"
    for gid, t in (("0", "DMA-FQ"), ("1", "identity"),
                    ("2", "DMA")):
        d = root / gid
        d.mkdir(parents=True)
        (d / "type").write_text(t + "\n")
    out = mod.sample_group_types(str(root))
    assert "dma-fq" in out
    assert "identity" in out
    assert "dma" in out


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("ro quiet\n")
    out = mod.status(None, str(cmdline),
                     str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_passthrough_with_groups(tmp_path):
    cmdline = tmp_path / "cmdline"
    cmdline.write_text("ro iommu=pt\n")
    root = tmp_path / "iommu_groups"
    d = root / "0"
    d.mkdir(parents=True)
    (d / "type").write_text("DMA-FQ\n")
    out = mod.status(None, str(cmdline), str(root))
    assert (out["verdict"]["verdict"]
            == "iommu_passthrough_with_pcie_devices")
