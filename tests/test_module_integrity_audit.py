"""Tests for modules/module_integrity_audit.py — R&D #52.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import module_integrity_audit as mod


# --- decode_tainted ---------------------------------------------

def test_decode_tainted_empty():
    assert mod.decode_tainted(0) == []


def test_decode_tainted_nvidia_typical():
    # bit 12 = O, bit 13 = E
    mask = (1 << 12) | (1 << 13)
    assert mod.decode_tainted(mask) == ["O", "E"]


def test_decode_tainted_proprietary():
    assert mod.decode_tainted(1 << 0) == ["P"]


# --- list_tainted_modules ---------------------------------------

def _mk_mod(root, name, *, taint=None, srcversion=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    if taint is not None:
        (d / "taint").write_text(taint + "\n")
    if srcversion is not None:
        (d / "srcversion").write_text(srcversion + "\n")


def test_list_tainted_modules_empty(tmp_path):
    _mk_mod(tmp_path, "xfs")  # no taint file
    out = mod.list_tainted_modules(str(tmp_path))
    assert out == []


def test_list_tainted_modules_some(tmp_path):
    _mk_mod(tmp_path, "nvidia", taint="OE", srcversion="ABC")
    _mk_mod(tmp_path, "xfs")
    _mk_mod(tmp_path, "vbox", taint="OE", srcversion="DEF")
    out = mod.list_tainted_modules(str(tmp_path))
    names = sorted(m["name"] for m in out)
    assert names == ["nvidia", "vbox"]


def test_list_tainted_modules_missing(tmp_path):
    assert mod.list_tainted_modules(str(tmp_path / "nope")) == []


# --- nvidia_versions --------------------------------------------

def test_nvidia_versions_proprietary(tmp_path):
    nv = tmp_path / "nvidia"
    nv.mkdir(parents=True, exist_ok=True)
    (nv / "version").write_text("570.86.10\n")
    nvfile = tmp_path / "nvversion"
    nvfile.write_text(
        "NVRM version: NVIDIA UNIX Kernel Module for x86_64  "
        "570.86.10  Release Build\n")
    loaded, runtime = mod.nvidia_versions(str(tmp_path), str(nvfile))
    assert loaded == "570.86.10"
    assert runtime == "570.86.10"


def test_nvidia_versions_open_module(tmp_path):
    nv = tmp_path / "nvidia"
    nv.mkdir(parents=True, exist_ok=True)
    (nv / "version").write_text("590.48.01\n")
    nvfile = tmp_path / "nvversion"
    nvfile.write_text(
        "NVRM version: NVIDIA UNIX Open Kernel Module for "
        "x86_64  590.48.01  Release Build\n")
    loaded, runtime = mod.nvidia_versions(str(tmp_path), str(nvfile))
    assert loaded == "590.48.01"
    assert runtime == "590.48.01"


def test_nvidia_versions_missing(tmp_path):
    loaded, runtime = mod.nvidia_versions(str(tmp_path / "nope"),
                                              str(tmp_path / "noproc"))
    assert loaded is None
    assert runtime is None


# --- classify ---------------------------------------------------

def _nvmod(name="nvidia"):
    return {"name": name, "taint": "OE", "srcversion": "X"}


def test_classify_unknown():
    v = mod.classify(None, None, [], None, None)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(0, 0, [], None, None)
    assert v["verdict"] == "ok"


def test_classify_modules_disabled():
    v = mod.classify(0, 1, [], None, None)
    assert v["verdict"] == "modules_disabled"


def test_classify_nvidia_version_mismatch():
    v = mod.classify(12288, 0, [_nvmod()],
                       "570.86.10", "590.48.01")
    assert v["verdict"] == "nvidia_version_mismatch"


def test_classify_unsigned_unexpected():
    v = mod.classify(12288, 0,
                       [_nvmod(), {"name": "vbox", "taint": "OE",
                                     "srcversion": "Y"}],
                       "590.48.01", "590.48.01")
    assert v["verdict"] == "unsigned_modules_unexpected"
    assert "vbox" in v["reason"]


def test_classify_oot_nvidia_only():
    v = mod.classify(12288, 0, [_nvmod()], "590.48.01",
                       "590.48.01")
    assert v["verdict"] == "tainted_oot_nvidia_only"


def test_classify_priority_disabled_wins():
    v = mod.classify(12288, 1,
                       [_nvmod(), {"name": "x", "taint": "OE",
                                     "srcversion": "Z"}],
                       "1", "2")
    assert v["verdict"] == "modules_disabled"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope1"),
                       str(tmp_path / "nope2"),
                       str(tmp_path / "nomod"),
                       str(tmp_path / "noproc"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_nvidia_typical_homelab(tmp_path):
    # Synth tree mimicking live host : nvidia tainted OE, versions
    # agree.
    taint = tmp_path / "tainted"
    taint.write_text("12288\n")
    mods_dis = tmp_path / "mod_dis"
    mods_dis.write_text("0\n")
    sysmod = tmp_path / "sysmod"
    sysmod.mkdir()
    _mk_mod(sysmod, "nvidia", taint="OE", srcversion="X")
    (sysmod / "nvidia" / "version").write_text("590.48.01\n")
    nvfile = tmp_path / "nv"
    nvfile.write_text(
        "NVRM version: NVIDIA UNIX Open Kernel Module for "
        "x86_64  590.48.01  Release Build\n")
    out = mod.status(None, str(taint), str(mods_dis),
                       str(sysmod), str(nvfile))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "tainted_oot_nvidia_only"
    assert "O" in out["tainted_letters"]
    assert "E" in out["tainted_letters"]
