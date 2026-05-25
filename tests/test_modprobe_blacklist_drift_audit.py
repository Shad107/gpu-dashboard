"""Tests for modules/modprobe_blacklist_drift_audit.py R&D #102.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import modprobe_blacklist_drift_audit as mod


# --- parse_conf ------------------------------------------------

def test_parse_conf_empty():
    out = mod.parse_conf("")
    assert out == {"blacklist": set(), "install_noop": set()}


def test_parse_conf_basic():
    text = (
        "# comment\n"
        "blacklist nouveau\n"
        "blacklist firewire-core\n"
        "install nvidia-drm /bin/true\n"
        "options nvidia NVreg_X=1\n")
    out = mod.parse_conf(text)
    assert out["blacklist"] == {"nouveau", "firewire-core"}
    assert out["install_noop"] == {"nvidia-drm"}


def test_parse_conf_strips_inline_comment():
    text = "blacklist nouveau   # NVIDIA conflict\n"
    out = mod.parse_conf(text)
    assert out["blacklist"] == {"nouveau"}


# --- walk_dirs -------------------------------------------------

def test_walk_dirs_missing(tmp_path):
    out = mod.walk_dirs((str(tmp_path / "nope"),))
    assert out["file_count"] == 0


def test_walk_dirs_basic(tmp_path):
    d = tmp_path / "modprobe.d"
    d.mkdir()
    (d / "blacklist-nvidia.conf").write_text(
        "blacklist nouveau\n")
    (d / "other.conf").write_text(
        "blacklist firewire-core\n")
    out = mod.walk_dirs((str(d),))
    assert out["file_count"] == 2
    assert out["blacklist"] == {"nouveau", "firewire-core"}


def test_walk_dirs_skips_non_conf(tmp_path):
    d = tmp_path / "modprobe.d"
    d.mkdir()
    (d / "ok.conf").write_text("blacklist foo\n")
    (d / "ignored.txt").write_text("blacklist bar\n")
    out = mod.walk_dirs((str(d),))
    assert out["blacklist"] == {"foo"}


# --- parse_proc_modules ----------------------------------------

def test_parse_modules_empty():
    assert mod.parse_proc_modules("") == set()


def test_parse_modules_basic():
    text = (
        "nvidia 53288960 1 nvidia_uvm, Live 0x0\n"
        "snd_intel_dspcfg 12345 1 snd_hda_intel, Live 0x0\n")
    out = mod.parse_proc_modules(text)
    assert "nvidia" in out
    # underscore→hyphen alias should also be present
    assert "snd-intel-dspcfg" in out


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, 0, set(), set(), set(), False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, 0, set(), set(), set(), False)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 5,
                          {"nouveau"}, set(),
                          {"nvidia"}, True)
    assert v["verdict"] == "ok"


def test_classify_blacklist_drift_err():
    v = mod.classify(True, 5,
                          {"nouveau"}, set(),
                          {"nouveau", "nvidia"}, True)
    assert v["verdict"] == "blacklist_drift"


def test_classify_install_noop_drift_warn():
    v = mod.classify(True, 5,
                          set(), {"nvidia-drm"},
                          {"nvidia-drm"}, True)
    assert v["verdict"] == "install_noop_drift"


def test_classify_no_files_accent():
    v = mod.classify(True, 0,
                          set(), set(),
                          {"nvidia"}, True)
    assert v["verdict"] == "no_blacklist_files"


# Priority : blacklist > install_noop > no_files
def test_priority_blacklist_over_install():
    v = mod.classify(True, 5,
                          {"foo"}, {"bar"},
                          {"foo", "bar"}, True)
    assert v["verdict"] == "blacklist_drift"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, (str(tmp_path / "nope"),),
                       str(tmp_path / "no_modules"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_drift_synthetic(tmp_path):
    d = tmp_path / "modprobe.d"
    d.mkdir()
    (d / "blacklist.conf").write_text("blacklist nouveau\n")
    pm = tmp_path / "modules"
    pm.write_text("nouveau 100 0 - Live 0x0\n")
    out = mod.status(None, (str(d),), str(pm))
    assert out["verdict"]["verdict"] == "blacklist_drift"
    assert out["blacklist_count"] == 1
