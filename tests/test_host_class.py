"""Tests for modules/host_class.py — R&D #39.4 host classifier."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import host_class


def _mk_dmi(root: Path, **fields):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in fields.items():
        (root / k).write_text(v + "\n")


# --- chassis_kind ----------------------------------------------

def test_chassis_kind_desktop():
    for t in (3, 4, 5, 6, 7, 15, 24):
        assert host_class.chassis_kind(t) == "desktop"


def test_chassis_kind_laptop():
    for t in (8, 9, 10, 11, 14, 30, 31, 32):
        assert host_class.chassis_kind(t) == "laptop"


def test_chassis_kind_server():
    for t in (17, 23, 25):
        assert host_class.chassis_kind(t) == "server"


def test_chassis_kind_aio():
    assert host_class.chassis_kind(13) == "aio"


def test_chassis_kind_embedded():
    assert host_class.chassis_kind(28) == "mini_pc"
    assert host_class.chassis_kind(36) == "embedded"


def test_chassis_kind_other():
    # Type 1 (Other) or 2 (Unknown) → unknown_kind
    assert host_class.chassis_kind(1) == "unknown_kind"
    assert host_class.chassis_kind(2) == "unknown_kind"


# --- detect_virt -----------------------------------------------

def test_detect_virt_qemu_sys_vendor():
    info = host_class.detect_virt(sys_vendor="QEMU", bios_vendor="EDK")
    assert info["is_virt"] is True
    assert info["platform"] == "qemu"


def test_detect_virt_vmware():
    info = host_class.detect_virt(sys_vendor="VMware, Inc.",
                                       bios_vendor="Phoenix BIOS")
    assert info["is_virt"] is True
    assert info["platform"] == "vmware"


def test_detect_virt_xen():
    info = host_class.detect_virt(sys_vendor="Xen",
                                       bios_vendor="Xen")
    assert info["is_virt"] is True
    assert info["platform"] == "xen"


def test_detect_virt_proxmox_bios():
    # Proxmox's EDK II BIOS hint
    info = host_class.detect_virt(sys_vendor="QEMU",
                                       bios_vendor="Proxmox distribution of EDK II")
    assert info["is_virt"] is True
    assert info["platform"] == "qemu"


def test_detect_virt_bare_metal_intel(tmp_path):
    # firmware_root override prevents picking up the real
    # /sys/firmware/qemu_fw_cfg when running tests inside a VM
    info = host_class.detect_virt(sys_vendor="ASUSTeK COMPUTER INC.",
                                       bios_vendor="American Megatrends Inc.",
                                       firmware_root=str(tmp_path / "fw"))
    assert info["is_virt"] is False


def test_detect_virt_with_qemu_fw_cfg(tmp_path):
    # Even if sys_vendor doesn't say QEMU, presence of qemu_fw_cfg confirms VM
    (tmp_path / "qemu_fw_cfg").mkdir()
    info = host_class.detect_virt(sys_vendor="Dell Inc.",
                                       bios_vendor="Dell",
                                       firmware_root=str(tmp_path))
    assert info["is_virt"] is True


# --- classify ---------------------------------------------------

def test_classify_vm():
    v = host_class.classify(chassis="desktop",
                                 virt={"is_virt": True, "platform": "qemu"})
    assert v["verdict"] == "vm"
    assert "qemu" in v["reason"].lower() or "virtual" in v["reason"].lower()


def test_classify_laptop():
    v = host_class.classify(chassis="laptop",
                                 virt={"is_virt": False, "platform": None})
    assert v["verdict"] == "laptop"
    assert "battery" in v["recommendation"].lower() or "thermal" in v["recommendation"].lower()


def test_classify_server():
    v = host_class.classify(chassis="server",
                                 virt={"is_virt": False, "platform": None})
    assert v["verdict"] == "server"
    assert "watchdog" in v["recommendation"].lower() or "unattended" in v["recommendation"].lower()


def test_classify_desktop():
    v = host_class.classify(chassis="desktop",
                                 virt={"is_virt": False, "platform": None})
    assert v["verdict"] == "desktop"


def test_classify_unknown():
    v = host_class.classify(chassis="unknown_kind",
                                 virt={"is_virt": False, "platform": None})
    # When chassis is unclear but not virt, fall back to "unknown"
    assert v["verdict"] == "unknown"


def test_classify_vm_wins_over_chassis_unknown():
    # Live-rig case: chassis=1 (Other) but QEMU sys_vendor → still vm
    v = host_class.classify(chassis="unknown_kind",
                                 virt={"is_virt": True, "platform": "qemu"})
    assert v["verdict"] == "vm"


# --- status ----------------------------------------------------

def test_status_vm_proxmox_qemu(tmp_path, monkeypatch):
    # The live-rig case
    dmi = tmp_path / "dmi"
    fw = tmp_path / "firmware"
    _mk_dmi(dmi,
              chassis_type="1",
              sys_vendor="QEMU",
              product_name="Standard PC (Q35 + ICH9, 2009)",
              bios_vendor="Proxmox distribution of EDK II")
    (fw / "qemu_fw_cfg").mkdir(parents=True)
    monkeypatch.setattr(host_class, "_DMI_ROOT", str(dmi))
    monkeypatch.setattr(host_class, "_FIRMWARE_ROOT", str(fw))
    s = host_class.status()
    assert s["ok"] is True
    assert s["sys_vendor"] == "QEMU"
    assert s["virt"]["is_virt"] is True
    assert s["virt"]["platform"] == "qemu"
    assert s["verdict"]["verdict"] == "vm"


def test_status_bare_metal_laptop(tmp_path, monkeypatch):
    dmi = tmp_path / "dmi"
    _mk_dmi(dmi, chassis_type="10",  # Notebook
            sys_vendor="LENOVO",
            product_name="20XX",
            bios_vendor="LENOVO")
    monkeypatch.setattr(host_class, "_DMI_ROOT", str(dmi))
    monkeypatch.setattr(host_class, "_FIRMWARE_ROOT",
                          str(tmp_path / "fw_absent"))
    s = host_class.status()
    assert s["chassis_kind"] == "laptop"
    assert s["virt"]["is_virt"] is False
    assert s["verdict"]["verdict"] == "laptop"


def test_status_server_rack(tmp_path, monkeypatch):
    dmi = tmp_path / "dmi"
    _mk_dmi(dmi, chassis_type="23",  # Rack Mount
            sys_vendor="Dell Inc.",
            product_name="PowerEdge R750",
            bios_vendor="Dell Inc.")
    monkeypatch.setattr(host_class, "_DMI_ROOT", str(dmi))
    monkeypatch.setattr(host_class, "_FIRMWARE_ROOT",
                          str(tmp_path / "fw_absent"))
    s = host_class.status()
    assert s["verdict"]["verdict"] == "server"


def test_status_no_dmi(tmp_path, monkeypatch):
    monkeypatch.setattr(host_class, "_DMI_ROOT",
                          str(tmp_path / "absent"))
    monkeypatch.setattr(host_class, "_FIRMWARE_ROOT",
                          str(tmp_path / "fw_absent"))
    s = host_class.status()
    assert s["ok"] is False
    assert s["error"] == "dmi_unavailable"


def test_status_chassis_type_returned(tmp_path, monkeypatch):
    dmi = tmp_path / "dmi"
    _mk_dmi(dmi, chassis_type="3",  # Desktop
            sys_vendor="ASUSTeK",
            product_name="PRIME",
            bios_vendor="AMI")
    monkeypatch.setattr(host_class, "_DMI_ROOT", str(dmi))
    monkeypatch.setattr(host_class, "_FIRMWARE_ROOT",
                          str(tmp_path / "fw_absent"))
    s = host_class.status()
    assert s["chassis_type"] == 3
    assert s["chassis_kind"] == "desktop"
