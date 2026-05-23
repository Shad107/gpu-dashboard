"""Tests for modules/gpu_pci_bind.py — R&D #40.1."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import gpu_pci_bind


# --- _mk_dev helpers -----------------------------------------------

def _mk_dev(devices_root: Path, bdf: str, *, vendor: str = "0x10de",
             device: str = "0x2204", class_hex: str = "0x030000",
             driver: str | None = "nvidia", enable: str = "1",
             driver_override: str = "(null)", numa_node: str = "-1",
             iommu_group: int | None = 12,
             drivers_root: Path | None = None):
    d = devices_root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "device").write_text(device + "\n")
    (d / "class").write_text(class_hex + "\n")
    (d / "enable").write_text(enable + "\n")
    (d / "driver_override").write_text(driver_override + "\n")
    (d / "numa_node").write_text(numa_node + "\n")
    (d / "power").mkdir(exist_ok=True)
    (d / "power" / "control").write_text("auto\n")
    if iommu_group is not None:
        ig = devices_root.parent.parent / "kernel" / "iommu_groups" / \
              str(iommu_group)
        ig.mkdir(parents=True, exist_ok=True)
        (d / "iommu_group").symlink_to(ig)
    if driver and drivers_root is not None:
        drv = drivers_root / driver
        drv.mkdir(parents=True, exist_ok=True)
        (d / "driver").symlink_to(drv)


def _mk_pci_root(tmp_path: Path) -> Path:
    sys_pci = tmp_path / "sys" / "bus" / "pci"
    (sys_pci / "devices").mkdir(parents=True, exist_ok=True)
    (sys_pci / "drivers").mkdir(parents=True, exist_ok=True)
    return sys_pci


# --- classify_function ---------------------------------------------

def test_classify_function_display():
    assert gpu_pci_bind.classify_function(0x030000) == "display"
    assert gpu_pci_bind.classify_function(0x030200) == "display"


def test_classify_function_audio():
    assert gpu_pci_bind.classify_function(0x040300) == "audio"


def test_classify_function_serial():
    # USB-C XHCI on Turing+ GPUs
    assert gpu_pci_bind.classify_function(0x0c0330) == "serial"


def test_classify_function_unknown():
    assert gpu_pci_bind.classify_function(None) == "unknown"
    assert gpu_pci_bind.classify_function(0x010000) == "other"


# --- list_nvidia_devices -------------------------------------------

def test_list_nvidia_devices_finds_gpu(tmp_path):
    sys_pci = _mk_pci_root(tmp_path)
    devs_root = sys_pci / "devices"
    drvs_root = sys_pci / "drivers"
    _mk_dev(devs_root, "0000:01:00.0", drivers_root=drvs_root)
    out = gpu_pci_bind.list_nvidia_devices(str(sys_pci))
    assert len(out) == 1
    assert out[0]["bdf"] == "0000:01:00.0"
    assert out[0]["driver"] == "nvidia"
    assert out[0]["enable"] == 1
    assert out[0]["function_role"] == "display"
    assert out[0]["driver_override"] is None


def test_list_nvidia_devices_skips_other_vendors(tmp_path):
    sys_pci = _mk_pci_root(tmp_path)
    devs_root = sys_pci / "devices"
    drvs_root = sys_pci / "drivers"
    _mk_dev(devs_root, "0000:01:00.0", vendor="0x8086",
              drivers_root=drvs_root)
    assert gpu_pci_bind.list_nvidia_devices(str(sys_pci)) == []


def test_list_nvidia_devices_includes_audio_function(tmp_path):
    sys_pci = _mk_pci_root(tmp_path)
    devs_root = sys_pci / "devices"
    drvs_root = sys_pci / "drivers"
    _mk_dev(devs_root, "0000:01:00.0", drivers_root=drvs_root)
    _mk_dev(devs_root, "0000:01:00.1", device="0x1aef",
              class_hex="0x040300", driver="snd_hda_intel",
              drivers_root=drvs_root)
    out = gpu_pci_bind.list_nvidia_devices(str(sys_pci))
    assert len(out) == 2
    roles = sorted(f["function_role"] for f in out)
    assert roles == ["audio", "display"]


def test_list_nvidia_devices_no_driver(tmp_path):
    sys_pci = _mk_pci_root(tmp_path)
    devs_root = sys_pci / "devices"
    drvs_root = sys_pci / "drivers"
    _mk_dev(devs_root, "0000:01:00.0", driver=None,
              drivers_root=drvs_root)
    out = gpu_pci_bind.list_nvidia_devices(str(sys_pci))
    assert out[0]["driver"] is None


def test_list_nvidia_devices_driver_override_set(tmp_path):
    sys_pci = _mk_pci_root(tmp_path)
    devs_root = sys_pci / "devices"
    drvs_root = sys_pci / "drivers"
    _mk_dev(devs_root, "0000:01:00.0", driver_override="vfio-pci",
              drivers_root=drvs_root)
    out = gpu_pci_bind.list_nvidia_devices(str(sys_pci))
    assert out[0]["driver_override"] == "vfio-pci"


def test_list_nvidia_devices_missing_root(tmp_path):
    assert gpu_pci_bind.list_nvidia_devices(str(tmp_path / "nope")) == []


# --- group_by_slot --------------------------------------------------

def test_group_by_slot_merges_functions():
    devs = [{"bdf": "0000:01:00.0"}, {"bdf": "0000:01:00.1"},
              {"bdf": "0000:02:00.0"}]
    g = gpu_pci_bind.group_by_slot(devs)
    assert set(g.keys()) == {"0000:01:00", "0000:02:00"}
    assert len(g["0000:01:00"]) == 2


# --- list_drivers_present ------------------------------------------

def test_list_drivers_present(tmp_path):
    sys_pci = _mk_pci_root(tmp_path)
    (sys_pci / "drivers" / "nvidia").mkdir()
    p = gpu_pci_bind.list_drivers_present(str(sys_pci))
    assert p["nvidia"] is True
    assert p["vfio-pci"] is False
    assert p["nouveau"] is False


# --- classify ------------------------------------------------------

def _dev(bdf, driver, role="display", enable=1, **kw):
    base = {"bdf": bdf, "driver": driver, "function_role": role,
             "enable": enable}
    base.update(kw)
    return base


def test_classify_no_nvidia():
    v = gpu_pci_bind.classify([], {"vfio-pci": True})
    assert v["verdict"] == "no_nvidia_gpu"


def test_classify_host_bound_simple():
    devs = [_dev("0000:01:00.0", "nvidia")]
    v = gpu_pci_bind.classify(devs, {"vfio-pci": True, "nvidia": True})
    assert v["verdict"] == "host_bound"
    assert "0000:01:00.0" in v["recommendation"]


def test_classify_host_bound_with_audio_sibling():
    devs = [
        _dev("0000:01:00.0", "nvidia", role="display"),
        _dev("0000:01:00.1", "snd_hda_intel", role="audio"),
    ]
    v = gpu_pci_bind.classify(devs, {"vfio-pci": True, "nvidia": True})
    # Audio on a host driver alongside GPU on nvidia is the normal
    # case — not mixed_function_bind. Only flag when vfio + host mix.
    assert v["verdict"] == "host_bound"


def test_classify_vfio_bound():
    devs = [
        _dev("0000:01:00.0", "vfio-pci", role="display"),
        _dev("0000:01:00.1", "vfio-pci", role="audio"),
    ]
    v = gpu_pci_bind.classify(devs, {"vfio-pci": True, "nvidia": False})
    assert v["verdict"] == "vfio_bound"
    assert "stop any VM" in v["recommendation"]


def test_classify_mixed_function_bind():
    devs = [
        _dev("0000:01:00.0", "vfio-pci", role="display"),
        _dev("0000:01:00.1", "snd_hda_intel", role="audio"),
    ]
    v = gpu_pci_bind.classify(devs, {"vfio-pci": True, "nvidia": True})
    assert v["verdict"] == "mixed_function_bind"
    assert "IOMMU group" in v["reason"]


def test_classify_stuck_or_orphaned():
    devs = [_dev("0000:01:00.0", None, enable=0)]
    v = gpu_pci_bind.classify(devs, {"vfio-pci": True, "nvidia": True})
    assert v["verdict"] == "stuck_or_orphaned"
    assert "remove" in v["recommendation"]
    assert "rescan" in v["recommendation"]


def test_classify_no_vfio_module_appends_note():
    devs = [_dev("0000:01:00.0", "nvidia")]
    v = gpu_pci_bind.classify(devs, {"vfio-pci": False, "nvidia": True})
    assert v["verdict"] == "host_bound"
    assert "vfio-pci kernel module is not loaded" in v["recommendation"]


def test_classify_worst_of_multiple_slots():
    devs = [
        _dev("0000:01:00.0", "nvidia"),
        _dev("0000:02:00.0", None, enable=0),
    ]
    v = gpu_pci_bind.classify(devs, {"vfio-pci": True, "nvidia": True})
    # stuck > host_bound in the rank
    assert v["verdict"] == "stuck_or_orphaned"


# --- status integration --------------------------------------------

def test_status_real_root_replaced(monkeypatch, tmp_path):
    sys_pci = _mk_pci_root(tmp_path)
    devs_root = sys_pci / "devices"
    drvs_root = sys_pci / "drivers"
    (drvs_root / "nvidia").mkdir()
    _mk_dev(devs_root, "0000:01:00.0", drivers_root=drvs_root)
    monkeypatch.setattr(gpu_pci_bind, "_SYS_PCI", str(sys_pci))
    out = gpu_pci_bind.status()
    assert out["ok"] is True
    assert out["device_count"] == 1
    assert out["slot_count"] == 1
    assert out["verdict"]["verdict"] == "host_bound"


def test_status_no_pci_bus(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu_pci_bind, "_SYS_PCI",
                        str(tmp_path / "nope"))
    out = gpu_pci_bind.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
