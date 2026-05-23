"""Tests for modules/virt_guest_detect_audit.py — R&D #60.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import virt_guest_detect_audit as mod


# --- helpers ----------------------------------------------------

def _mk_cpuinfo(tmp_path, hypervisor=True):
    p = tmp_path / "cpuinfo"
    flags = "fpu vme aes sse4_2"
    if hypervisor:
        flags += " hypervisor"
    p.write_text(f"processor : 0\nflags : {flags}\n")
    return p


def _mk_pci_nvidia(root, bdf="0000:01:00.0"):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text("0x10de\n")
    (d / "class").write_text("0x030000\n")
    return d


# --- has_qemu_fw_cfg --------------------------------------------

def test_has_qemu_fw_cfg(tmp_path):
    assert mod.has_qemu_fw_cfg(str(tmp_path / "nope")) is False
    (tmp_path / "qemu").mkdir()
    assert mod.has_qemu_fw_cfg(str(tmp_path / "qemu")) is True


# --- has_xen_hypervisor -----------------------------------------

def test_has_xen_hypervisor_missing(tmp_path):
    assert mod.has_xen_hypervisor(
        str(tmp_path / "nope")) is None


def test_has_xen_hypervisor_present(tmp_path):
    d = tmp_path / "hyper"
    d.mkdir()
    (d / "type").write_text("xen\n")
    assert mod.has_xen_hypervisor(str(d)) == "xen"


# --- has_hypervisor_cpu_flag ------------------------------------

def test_has_hypervisor_cpu_flag_yes(tmp_path):
    p = _mk_cpuinfo(tmp_path, hypervisor=True)
    assert mod.has_hypervisor_cpu_flag(str(p)) is True


def test_has_hypervisor_cpu_flag_no(tmp_path):
    p = _mk_cpuinfo(tmp_path, hypervisor=False)
    assert mod.has_hypervisor_cpu_flag(str(p)) is False


def test_has_hypervisor_cpu_flag_missing(tmp_path):
    assert mod.has_hypervisor_cpu_flag(
        str(tmp_path / "nope")) is False


# --- list_virtio_devices ----------------------------------------

def test_list_virtio_devices(tmp_path):
    (tmp_path / "virtio0").mkdir()
    (tmp_path / "virtio1").mkdir()
    out = mod.list_virtio_devices(str(tmp_path))
    assert out == ["virtio0", "virtio1"]


# --- has_nvidia_display_gpu -------------------------------------

def test_has_nvidia_display_gpu(tmp_path):
    _mk_pci_nvidia(tmp_path, "0000:01:00.0")
    d = tmp_path / "0000:01:00.1"
    d.mkdir()
    (d / "vendor").write_text("0x10de\n")
    (d / "class").write_text("0x040300\n")  # audio, not display
    out = mod.has_nvidia_display_gpu(str(tmp_path))
    assert out == ["0000:01:00.0"]


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, False, [], False, [], False)
    assert v["verdict"] == "unknown"


def test_classify_bare_metal():
    v = mod.classify(False, None, False, [], False, [], True)
    assert v["verdict"] == "bare_metal"


def test_classify_guest_generic_via_virtio():
    v = mod.classify(False, None, True, ["virtio0"],
                       False, [], True)
    assert v["verdict"] == "running_as_guest_generic"


def test_classify_guest_xen():
    v = mod.classify(False, "xen", True, [], False, [], True)
    assert v["verdict"] == "running_as_guest_generic"


def test_classify_kvm_passthrough_quirk():
    v = mod.classify(True, None, True, ["virtio0"],
                       False, ["0000:01:00.0"], True)
    assert v["verdict"] == (
        "running_as_kvm_guest_with_nvidia_passthrough_quirk")


def test_classify_nested_virt():
    v = mod.classify(True, None, True, ["virtio0"], True, [],
                       True)
    assert v["verdict"] == "nested_virt_detected"


def test_classify_priority_passthrough_wins_over_nested():
    # Passthrough quirk wins (more LLM-specific actionable).
    v = mod.classify(True, None, True, ["virtio0"], True,
                       ["0000:01:00.0"], True)
    assert v["verdict"] == (
        "running_as_kvm_guest_with_nvidia_passthrough_quirk")


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "noqemu"),
                       str(tmp_path / "nohyper"),
                       str(tmp_path / "nocpu"),
                       str(tmp_path / "novirtio"),
                       str(tmp_path / "nokvm"),
                       str(tmp_path / "nokvmmisc"),
                       str(tmp_path / "nopci"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like_proxmox(tmp_path):
    qemu = tmp_path / "qemu"
    qemu.mkdir()
    cpu = _mk_cpuinfo(tmp_path, hypervisor=True)
    virtio = tmp_path / "virtio"
    (virtio / "virtio0").mkdir(parents=True)
    pci = tmp_path / "pci"
    _mk_pci_nvidia(pci, "0000:01:00.0")
    out = mod.status(None,
                       str(qemu),
                       str(tmp_path / "nohyper"),
                       str(cpu),
                       str(virtio),
                       str(tmp_path / "nokvm"),
                       str(tmp_path / "nokvmmisc"),
                       str(pci))
    assert out["ok"] is True
    assert out["qemu_fw_cfg_present"] is True
    assert out["cpu_hypervisor_flag"] is True
    assert out["verdict"]["verdict"] == (
        "running_as_kvm_guest_with_nvidia_passthrough_quirk")
