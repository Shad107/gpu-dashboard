"""Tests for modules/fw_cfg_blob_audit.py — R&D #72.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import fw_cfg_blob_audit as mod


def _mk_entry(root, key, *, name=None, size=1024):
    d = root / str(key)
    d.mkdir(parents=True, exist_ok=True)
    if name is not None:
        (d / "name").write_text(name + "\n")
    (d / "size").write_text(f"{size}\n")
    (d / "key").write_text(f"{key}\n")


# --- list_entries ----------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_entries(str(tmp_path / "nope")) == []


def test_list_present(tmp_path):
    _mk_entry(tmp_path, 32, name="bootorder", size=64)
    _mk_entry(tmp_path, 33, name="etc/smbios/smbios-tables",
                  size=2048)
    out = mod.list_entries(str(tmp_path))
    assert len(out) == 2
    by_key = {e["key"]: e for e in out}
    assert by_key[32]["name"] == "bootorder"
    assert by_key[33]["size"] == 2048


def test_list_unreadable_names(tmp_path):
    _mk_entry(tmp_path, 32, name=None, size=64)
    out = mod.list_entries(str(tmp_path))
    assert len(out) == 1
    assert out[0]["name"] is None
    assert out[0]["size"] == 64


# --- classify ---------------------------------------------------

def test_classify_not_qemu():
    v = mod.classify(False, [])
    assert v["verdict"] == "ok"


def test_classify_unknown_empty():
    v = mod.classify(True, [])
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True,
                          [{"key": 32, "name": None, "size": 64},
                            {"key": 33, "name": None,
                              "size": 2048}])
    assert v["verdict"] == "requires_root"


def test_classify_nvidia_passthrough():
    v = mod.classify(True,
                          [{"key": 32, "name": "bootorder",
                              "size": 64},
                            {"key": 40, "name":
                              "genroms/10de_1234_nvidia.rom",
                              "size": 65536}])
    assert v["verdict"] == "nvidia_passthrough_vm"


def test_classify_opt_rom_libvirt():
    v = mod.classify(True,
                          [{"key": 32, "name": "bootorder",
                              "size": 64},
                            {"key": 40, "name":
                              "opt/com.redhat/x",
                              "size": 65536}])
    assert v["verdict"] == "qemu_guest_with_opt_rom"


def test_classify_opt_rom_genroms_non_nvidia():
    v = mod.classify(True,
                          [{"key": 40, "name":
                              "genroms/8086_intel.rom",
                              "size": 65536}])
    assert v["verdict"] == "qemu_guest_with_opt_rom"


def test_classify_qemu_guest_bare():
    v = mod.classify(True,
                          [{"key": 32, "name": "bootorder",
                              "size": 64},
                            {"key": 33, "name":
                              "etc/smbios/smbios-tables",
                              "size": 2048}])
    assert v["verdict"] == "qemu_guest_bare"


# Priority : nvidia > opt_rom > bare
def test_priority_nvidia_over_opt_rom():
    v = mod.classify(True,
                          [{"key": 40, "name":
                              "genroms/8086_intel.rom",
                              "size": 1024},
                            {"key": 41, "name":
                              "genroms/10de_nvidia_x.rom",
                              "size": 65536}])
    assert v["verdict"] == "nvidia_passthrough_vm"


# --- status integration -----------------------------------------

def test_status_not_qemu(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_fwcfg"),
                          str(tmp_path / "no_key"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "ok"


def test_status_qemu_guest_bare(tmp_path):
    fwcfg = tmp_path / "fw_cfg"; fwcfg.mkdir()
    by_key = fwcfg / "by_key"
    _mk_entry(by_key, 32, name="bootorder")
    _mk_entry(by_key, 33, name="etc/smbios/smbios-tables")
    out = mod.status(None, str(fwcfg), str(by_key))
    assert out["ok"] is True
    assert out["names_readable"] is True
    assert out["entry_count"] == 2
    assert out["verdict"]["verdict"] == "qemu_guest_bare"


def test_status_requires_root(tmp_path):
    fwcfg = tmp_path / "fw_cfg"; fwcfg.mkdir()
    by_key = fwcfg / "by_key"
    _mk_entry(by_key, 32, name=None)
    _mk_entry(by_key, 33, name=None)
    out = mod.status(None, str(fwcfg), str(by_key))
    assert out["names_readable"] is False
    assert out["verdict"]["verdict"] == "requires_root"
