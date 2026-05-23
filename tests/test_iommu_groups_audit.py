"""Tests for modules/iommu_groups_audit.py — R&D #59.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import iommu_groups_audit as mod


def _mk_group(root, gid, bdfs):
    d = root / str(gid) / "devices"
    d.mkdir(parents=True, exist_ok=True)
    for b in bdfs:
        (d / b).write_text("")


def _mk_pci_dev(root, bdf, vendor, klass):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "class").write_text(klass + "\n")


# --- list_groups ------------------------------------------------

def test_list_groups_missing(tmp_path):
    assert mod.list_groups(str(tmp_path / "nope")) == {}


def test_list_groups(tmp_path):
    _mk_group(tmp_path, 0, ["0000:00:00.0"])
    _mk_group(tmp_path, 5, ["0000:01:00.0", "0000:01:00.1"])
    out = mod.list_groups(str(tmp_path))
    assert out[0] == ["0000:00:00.0"]
    assert out[5] == ["0000:01:00.0", "0000:01:00.1"]


# --- cmdline_iommu_tokens ---------------------------------------

def test_cmdline_iommu_tokens(tmp_path):
    p = tmp_path / "cmdline"
    p.write_text("ro quiet intel_iommu=on iommu=pt rd.luks=0\n")
    out = mod.cmdline_iommu_tokens(str(p))
    assert "intel_iommu=on" in out
    assert "iommu=pt" in out


def test_cmdline_iommu_tokens_missing(tmp_path):
    assert mod.cmdline_iommu_tokens(str(tmp_path / "nope")) == []


# --- find_nvidia_gpus -------------------------------------------

def test_find_nvidia_gpus(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000")
    _mk_pci_dev(tmp_path, "0000:01:00.1", "0x10de", "0x040300")
    out = mod.find_nvidia_gpus(str(tmp_path))
    assert out == ["0000:01:00.0"]


# --- classify ---------------------------------------------------

def _state_pt():
    return {"groups": {0: ["0000:00:00.0"], 5: ["0000:01:00.0"]},
              "iommu_tokens": ["intel_iommu=on", "iommu=pt"],
              "nvidia_gpus": ["0000:01:00.0"],
              "gpu_groups": {"0000:01:00.0": 5},
              "group_classes": {0: [0x060000], 5: [0x030000]}}


def test_classify_iommu_disabled():
    v = mod.classify({}, [], [], {}, {})
    assert v["verdict"] == "iommu_disabled"


def test_classify_passthrough_off():
    s = _state_pt()
    s["iommu_tokens"] = ["intel_iommu=on"]
    v = mod.classify(s["groups"], s["iommu_tokens"],
                       s["nvidia_gpus"], s["gpu_groups"],
                       s["group_classes"])
    assert v["verdict"] == "passthrough_off"


def test_classify_gpu_shares_with_root():
    s = _state_pt()
    s["group_classes"][5] = [0x030000, 0x060000]  # GPU + bridge
    v = mod.classify(s["groups"], s["iommu_tokens"],
                       s["nvidia_gpus"], s["gpu_groups"],
                       s["group_classes"])
    assert v["verdict"] == "gpu_shares_group_with_root_complex"


def test_classify_many_groups_ok():
    s = _state_pt()
    # Make 15 groups
    for i in range(15):
        s["groups"][100 + i] = [f"0000:0a:0{i:x}.0"]
        s["group_classes"][100 + i] = [0x020000]
    v = mod.classify(s["groups"], s["iommu_tokens"],
                       s["nvidia_gpus"], s["gpu_groups"],
                       s["group_classes"])
    assert v["verdict"] == "many_groups_ok"


def test_classify_ok():
    s = _state_pt()
    v = mod.classify(s["groups"], s["iommu_tokens"],
                       s["nvidia_gpus"], s["gpu_groups"],
                       s["group_classes"])
    assert v["verdict"] == "ok"


def test_classify_priority_disabled_wins():
    s = _state_pt()
    s["groups"] = {}
    v = mod.classify(s["groups"], s["iommu_tokens"],
                       s["nvidia_gpus"], s["gpu_groups"],
                       s["group_classes"])
    assert v["verdict"] == "iommu_disabled"


# --- status integration -----------------------------------------

def test_status_disabled(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"),
                       str(tmp_path / "nocmd"),
                       str(tmp_path / "nopci"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "iommu_disabled"


def test_status_live_like(tmp_path):
    iom = tmp_path / "iommu"
    _mk_group(iom, 0, ["0000:00:00.0"])
    _mk_group(iom, 5, ["0000:01:00.0"])
    cmd = tmp_path / "cmdline"
    cmd.write_text("ro intel_iommu=on iommu=pt\n")
    pci = tmp_path / "pci"
    _mk_pci_dev(pci, "0000:00:00.0", "0x8086", "0x060000")
    _mk_pci_dev(pci, "0000:01:00.0", "0x10de", "0x030000")
    out = mod.status(None, str(iom), str(cmd), str(pci))
    assert out["ok"] is True
    assert out["group_count"] == 2
    assert out["verdict"]["verdict"] == "ok"
