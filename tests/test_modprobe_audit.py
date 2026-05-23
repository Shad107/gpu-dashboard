"""Tests for modules/modprobe_audit.py — R&D #38.2 modprobe.d audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import modprobe_audit


def _mk_conf(root: Path, name: str, text: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)


def _mk_param(root: Path, module: str, name: str, value: str):
    d = root / module / "parameters"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(value + "\n")


# --- parse_options_line ---------------------------------------

def test_parse_options_line_basic():
    rec = modprobe_audit.parse_options_line(
        "options nvidia NVreg_EnableMSI=1")
    assert rec["module"] == "nvidia"
    assert rec["options"] == {"NVreg_EnableMSI": "1"}


def test_parse_options_line_multi_kv():
    rec = modprobe_audit.parse_options_line(
        "options nvidia NVreg_EnableMSI=1 NVreg_PreserveVideoMemoryAllocations=1")
    assert rec["module"] == "nvidia"
    assert rec["options"]["NVreg_EnableMSI"] == "1"
    assert rec["options"]["NVreg_PreserveVideoMemoryAllocations"] == "1"


def test_parse_options_line_with_paths():
    rec = modprobe_audit.parse_options_line(
        "options nvidia NVreg_TemporaryFilePath=/var")
    assert rec["options"]["NVreg_TemporaryFilePath"] == "/var"


def test_parse_options_line_comment_returns_none():
    assert modprobe_audit.parse_options_line("# comment") is None


def test_parse_options_line_blank_returns_none():
    assert modprobe_audit.parse_options_line("") is None
    assert modprobe_audit.parse_options_line("  ") is None


def test_parse_options_line_blacklist_not_options():
    assert modprobe_audit.parse_options_line("blacklist evbug") is None


def test_parse_options_line_alias_not_options():
    assert modprobe_audit.parse_options_line("alias net-pf-3 off") is None


# --- collect_options_from_dir -------------------------------

def test_collect_options_from_dir_filters_nvidia(tmp_path):
    _mk_conf(tmp_path, "alsa.conf",
              "install sound-slot-0 /sbin/modprobe snd-card-0\n"
              "options snd_hda intel_no_msi=1\n")
    _mk_conf(tmp_path, "nvidia.conf",
              "options nvidia NVreg_EnableMSI=1\n"
              "options nvidia_drm modeset=1\n")
    out = modprobe_audit.collect_options_from_dir(str(tmp_path),
                                                       only_modules=
                                                       ("nvidia", "nvidia_drm"))
    assert "nvidia" in out
    assert "nvidia_drm" in out
    assert "snd_hda" not in out


def test_collect_merges_options_across_files(tmp_path):
    _mk_conf(tmp_path, "a.conf", "options nvidia NVreg_EnableMSI=1\n")
    _mk_conf(tmp_path, "b.conf",
              "options nvidia NVreg_PreserveVideoMemoryAllocations=1\n")
    out = modprobe_audit.collect_options_from_dir(str(tmp_path),
                                                       only_modules=("nvidia",))
    opts = out["nvidia"]["options"]
    assert opts["NVreg_EnableMSI"] == "1"
    assert opts["NVreg_PreserveVideoMemoryAllocations"] == "1"


def test_collect_options_empty_dir(tmp_path):
    assert modprobe_audit.collect_options_from_dir(str(tmp_path)) == {}


# --- read_runtime_params ------------------------------------

def test_read_runtime_param(tmp_path):
    _mk_param(tmp_path, "nvidia", "NVreg_EnableMSI", "1")
    assert modprobe_audit.read_runtime_param(str(tmp_path),
                                                  "nvidia",
                                                  "NVreg_EnableMSI") == "1"


def test_read_runtime_param_missing(tmp_path):
    assert modprobe_audit.read_runtime_param(str(tmp_path), "nvidia",
                                                  "NVreg_X") is None


# --- classify -----------------------------------------------

def test_classify_no_options():
    v = modprobe_audit.classify(on_disk={}, runtime={})
    assert v["verdict"] == "no_options"


def test_classify_driver_not_loaded():
    # On-disk options exist but no runtime params readable (module unloaded)
    on_disk = {"nvidia": {"options": {"NVreg_EnableMSI": "1"}}}
    v = modprobe_audit.classify(on_disk=on_disk, runtime={"nvidia": {}})
    # When runtime is empty for all modules → driver_not_loaded
    assert v["verdict"] == "driver_not_loaded"


def test_classify_synced():
    on_disk = {"nvidia": {"options": {"NVreg_EnableMSI": "1"}}}
    runtime = {"nvidia": {"NVreg_EnableMSI": "1"}}
    v = modprobe_audit.classify(on_disk=on_disk, runtime=runtime)
    assert v["verdict"] == "synced"


def test_classify_drift_value_mismatch():
    # User set =1 on-disk but runtime still shows =0 (no initramfs rebuild)
    on_disk = {"nvidia": {"options": {"NVreg_EnableMSI": "1"}}}
    runtime = {"nvidia": {"NVreg_EnableMSI": "0"}}
    v = modprobe_audit.classify(on_disk=on_disk, runtime=runtime)
    assert v["verdict"] == "drift"
    assert "NVreg_EnableMSI" in v["reason"]


def test_classify_drift_recipe_includes_initramfs():
    on_disk = {"nvidia": {"options": {"NVreg_EnableMSI": "1"}}}
    runtime = {"nvidia": {"NVreg_EnableMSI": "0"}}
    v = modprobe_audit.classify(on_disk=on_disk, runtime=runtime)
    assert "initramfs" in v["recommendation"].lower()


def test_classify_synced_multi_module():
    on_disk = {
        "nvidia": {"options": {"NVreg_EnableMSI": "1"}},
        "nvidia_drm": {"options": {"modeset": "1"}},
    }
    runtime = {
        "nvidia": {"NVreg_EnableMSI": "1"},
        "nvidia_drm": {"modeset": "1"},
    }
    v = modprobe_audit.classify(on_disk=on_disk, runtime=runtime)
    assert v["verdict"] == "synced"


# --- status -------------------------------------------------

def test_status_no_modprobe_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(modprobe_audit, "_MODPROBE_ROOT",
                          str(tmp_path / "absent"))
    monkeypatch.setattr(modprobe_audit, "_SYS_MODULE_ROOT",
                          str(tmp_path / "absent2"))
    s = modprobe_audit.status()
    assert s["ok"] is False
    assert s["error"] == "modprobe_unavailable"


def test_status_live_nvidia_not_loaded(tmp_path, monkeypatch):
    # The live-rig case: nvidia options on-disk but driver unloaded
    md = tmp_path / "modprobe.d"
    sys_mod = tmp_path / "module"
    _mk_conf(md, "nvidia.conf",
              "options nvidia NVreg_PreserveVideoMemoryAllocations=1\n"
              "options nvidia_drm modeset=1\n")
    monkeypatch.setattr(modprobe_audit, "_MODPROBE_ROOT", str(md))
    monkeypatch.setattr(modprobe_audit, "_SYS_MODULE_ROOT", str(sys_mod))
    s = modprobe_audit.status()
    assert s["ok"] is True
    assert "nvidia" in s["on_disk"]
    assert s["verdict"]["verdict"] == "driver_not_loaded"


def test_status_drift_detected(tmp_path, monkeypatch):
    md = tmp_path / "modprobe.d"
    sys_mod = tmp_path / "module"
    _mk_conf(md, "nvidia.conf", "options nvidia NVreg_EnableMSI=1\n")
    _mk_param(sys_mod, "nvidia", "NVreg_EnableMSI", "0")
    monkeypatch.setattr(modprobe_audit, "_MODPROBE_ROOT", str(md))
    monkeypatch.setattr(modprobe_audit, "_SYS_MODULE_ROOT", str(sys_mod))
    s = modprobe_audit.status()
    assert s["verdict"]["verdict"] == "drift"


def test_status_synced(tmp_path, monkeypatch):
    md = tmp_path / "modprobe.d"
    sys_mod = tmp_path / "module"
    _mk_conf(md, "nvidia.conf", "options nvidia NVreg_EnableMSI=1\n")
    _mk_param(sys_mod, "nvidia", "NVreg_EnableMSI", "1")
    monkeypatch.setattr(modprobe_audit, "_MODPROBE_ROOT", str(md))
    monkeypatch.setattr(modprobe_audit, "_SYS_MODULE_ROOT", str(sys_mod))
    s = modprobe_audit.status()
    assert s["verdict"]["verdict"] == "synced"


def test_status_exposes_drift_diff(tmp_path, monkeypatch):
    md = tmp_path / "modprobe.d"
    sys_mod = tmp_path / "module"
    _mk_conf(md, "nvidia.conf",
              "options nvidia NVreg_EnableMSI=1 NVreg_X=2\n")
    _mk_param(sys_mod, "nvidia", "NVreg_EnableMSI", "0")
    _mk_param(sys_mod, "nvidia", "NVreg_X", "2")
    monkeypatch.setattr(modprobe_audit, "_MODPROBE_ROOT", str(md))
    monkeypatch.setattr(modprobe_audit, "_SYS_MODULE_ROOT", str(sys_mod))
    s = modprobe_audit.status()
    # The diff list should include the mismatched key
    assert any("NVreg_EnableMSI" in row["param"]
                for row in s["drift_rows"])
