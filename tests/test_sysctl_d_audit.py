"""Tests for modules/sysctl_d_audit.py — R&D #39.2 sysctl.d drift."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import sysctl_d_audit


def _mk_conf(root: Path, name: str, text: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(text)


def _mk_proc_sys(root: Path, *, kvs: dict | None = None):
    """kvs = {"vm.swappiness": "10", "net.core.rmem_max": "16777216"}"""
    for k, v in (kvs or {}).items():
        parts = k.split(".")
        d = root.joinpath(*parts[:-1])
        d.mkdir(parents=True, exist_ok=True)
        (d / parts[-1]).write_text(str(v) + "\n")


# --- parse_sysctl_line ---------------------------------------

def test_parse_sysctl_line_basic():
    rec = sysctl_d_audit.parse_sysctl_line("vm.swappiness = 10")
    assert rec == {"key": "vm.swappiness", "value": "10"}


def test_parse_sysctl_line_no_spaces():
    rec = sysctl_d_audit.parse_sysctl_line("net.core.rmem_max=16777216")
    assert rec == {"key": "net.core.rmem_max", "value": "16777216"}


def test_parse_sysctl_line_with_extra_spaces():
    rec = sysctl_d_audit.parse_sysctl_line("  kernel.core_pattern   =   /var/crash/core  ")
    assert rec == {"key": "kernel.core_pattern", "value": "/var/crash/core"}


def test_parse_sysctl_line_comment():
    assert sysctl_d_audit.parse_sysctl_line("# a comment") is None
    assert sysctl_d_audit.parse_sysctl_line(";also a comment") is None


def test_parse_sysctl_line_empty():
    assert sysctl_d_audit.parse_sysctl_line("") is None
    assert sysctl_d_audit.parse_sysctl_line("  ") is None


def test_parse_sysctl_line_dash_disable():
    # Lines starting with "-" mean "don't error if missing" — same key
    rec = sysctl_d_audit.parse_sysctl_line("-fs.protected_fifos = 1")
    assert rec == {"key": "fs.protected_fifos", "value": "1"}


def test_parse_sysctl_line_malformed():
    assert sysctl_d_audit.parse_sysctl_line("just garbage") is None


# --- collect_settings_from_dirs ------------------------------

def test_collect_settings_basic(tmp_path):
    _mk_conf(tmp_path, "99-llm.conf",
              "# LLM tuning\nvm.swappiness = 10\nnet.core.rmem_max = 16777216\n")
    rules = sysctl_d_audit.collect_settings_from_dirs(
        [str(tmp_path)])
    assert rules["vm.swappiness"]["value"] == "10"
    assert rules["net.core.rmem_max"]["value"] == "16777216"


def test_collect_settings_later_file_overrides(tmp_path):
    # sysctl --system orders lexicographically across dirs ; later wins
    _mk_conf(tmp_path, "10-base.conf", "vm.swappiness = 60\n")
    _mk_conf(tmp_path, "99-override.conf", "vm.swappiness = 10\n")
    rules = sysctl_d_audit.collect_settings_from_dirs(
        [str(tmp_path)])
    assert rules["vm.swappiness"]["value"] == "10"


def test_collect_settings_filters_non_conf(tmp_path):
    _mk_conf(tmp_path, "README.sysctl", "this is not a real conf\n")
    _mk_conf(tmp_path, "99-llm.conf", "vm.swappiness = 10\n")
    rules = sysctl_d_audit.collect_settings_from_dirs(
        [str(tmp_path)])
    assert "vm.swappiness" in rules
    # README content shouldn't have leaked in as a key


def test_collect_settings_multi_dir(tmp_path):
    d1 = tmp_path / "lib"
    d2 = tmp_path / "etc"
    _mk_conf(d1, "00-defaults.conf", "vm.swappiness = 60\n")
    _mk_conf(d2, "99-llm.conf", "vm.swappiness = 10\n")
    # /etc wins over /usr/lib
    rules = sysctl_d_audit.collect_settings_from_dirs(
        [str(d1), str(d2)])
    assert rules["vm.swappiness"]["value"] == "10"


def test_collect_settings_empty(tmp_path):
    assert sysctl_d_audit.collect_settings_from_dirs(
        [str(tmp_path)]) == {}


# --- runtime read ----------------------------------------------

def test_read_runtime_value(tmp_path):
    _mk_proc_sys(tmp_path, kvs={"vm.swappiness": "10"})
    assert sysctl_d_audit.read_runtime_value(
        str(tmp_path), "vm.swappiness") == "10"


def test_read_runtime_value_missing(tmp_path):
    assert sysctl_d_audit.read_runtime_value(
        str(tmp_path), "vm.nonsense") is None


def test_read_runtime_value_multiline(tmp_path):
    # Some sysctls have multi-value lines (e.g. tcp_rmem)
    parts = ["net", "ipv4", "tcp_rmem"]
    d = tmp_path.joinpath(*parts[:-1])
    d.mkdir(parents=True)
    (d / parts[-1]).write_text("4096\t131072\t33554432\n")
    val = sysctl_d_audit.read_runtime_value(
        str(tmp_path), "net.ipv4.tcp_rmem")
    # Single-line normalization — collapse tabs/spaces to single space
    assert val is not None
    assert "4096" in val


# --- classify ---------------------------------------------

def test_classify_no_config():
    v = sysctl_d_audit.classify(on_disk={}, runtime={})
    assert v["verdict"] == "no_config"


def test_classify_synced():
    on_disk = {"vm.swappiness": {"value": "10",
                                   "files": ["/etc/sysctl.d/99.conf"]}}
    runtime = {"vm.swappiness": "10"}
    v = sysctl_d_audit.classify(on_disk, runtime)
    assert v["verdict"] == "synced"


def test_classify_drift_value_mismatch():
    on_disk = {"vm.swappiness": {"value": "10",
                                   "files": ["/etc/sysctl.d/99.conf"]}}
    runtime = {"vm.swappiness": "60"}
    v = sysctl_d_audit.classify(on_disk, runtime)
    assert v["verdict"] == "drift"
    assert "vm.swappiness" in v["reason"]
    assert "sysctl" in v["recommendation"].lower()


def test_classify_drift_recipe_says_system_reload():
    on_disk = {"vm.swappiness": {"value": "10", "files": ["/x"]}}
    runtime = {"vm.swappiness": "60"}
    v = sysctl_d_audit.classify(on_disk, runtime)
    assert "sysctl --system" in v["recommendation"]


def test_classify_drift_with_multi_keys():
    on_disk = {
        "vm.swappiness": {"value": "10", "files": ["/x"]},
        "net.core.rmem_max": {"value": "16777216", "files": ["/x"]},
    }
    runtime = {"vm.swappiness": "60",
               "net.core.rmem_max": "16777216"}
    v = sysctl_d_audit.classify(on_disk, runtime)
    assert v["verdict"] == "drift"
    drift = v.get("drift_rows") or []
    drifted = [r for r in drift if r["key"] == "vm.swappiness"]
    assert len(drifted) == 1


# --- status ----------------------------------------------

def test_status_no_config(tmp_path, monkeypatch):
    # Empty dirs
    monkeypatch.setattr(sysctl_d_audit, "_SYSCTL_DIRS",
                          [str(tmp_path / "absent")])
    monkeypatch.setattr(sysctl_d_audit, "_PROC_SYS",
                          str(tmp_path / "proc_sys_absent"))
    s = sysctl_d_audit.status()
    assert s["ok"] is True
    assert s["verdict"]["verdict"] == "no_config"


def test_status_synced(tmp_path, monkeypatch):
    cfg = tmp_path / "sysctl.d"
    proc = tmp_path / "proc_sys"
    _mk_conf(cfg, "99-llm.conf",
              "vm.swappiness = 10\nnet.core.rmem_max = 16777216\n")
    _mk_proc_sys(proc, kvs={"vm.swappiness": "10",
                              "net.core.rmem_max": "16777216"})
    monkeypatch.setattr(sysctl_d_audit, "_SYSCTL_DIRS", [str(cfg)])
    monkeypatch.setattr(sysctl_d_audit, "_PROC_SYS", str(proc))
    s = sysctl_d_audit.status()
    assert s["verdict"]["verdict"] == "synced"


def test_status_drift(tmp_path, monkeypatch):
    cfg = tmp_path / "sysctl.d"
    proc = tmp_path / "proc_sys"
    _mk_conf(cfg, "99-llm.conf", "vm.swappiness = 10\n")
    _mk_proc_sys(proc, kvs={"vm.swappiness": "60"})
    monkeypatch.setattr(sysctl_d_audit, "_SYSCTL_DIRS", [str(cfg)])
    monkeypatch.setattr(sysctl_d_audit, "_PROC_SYS", str(proc))
    s = sysctl_d_audit.status()
    assert s["verdict"]["verdict"] == "drift"
    rows = s.get("drift_rows") or []
    assert any(r["key"] == "vm.swappiness" for r in rows)


def test_status_unreadable_runtime_treated_as_missing(tmp_path, monkeypatch):
    # If runtime value missing → skip (don't false-positive drift)
    cfg = tmp_path / "sysctl.d"
    _mk_conf(cfg, "99.conf", "vm.swappiness = 10\nvm.imaginary = 99\n")
    proc = tmp_path / "proc_sys"
    _mk_proc_sys(proc, kvs={"vm.swappiness": "10"})
    monkeypatch.setattr(sysctl_d_audit, "_SYSCTL_DIRS", [str(cfg)])
    monkeypatch.setattr(sysctl_d_audit, "_PROC_SYS", str(proc))
    s = sysctl_d_audit.status()
    # vm.swappiness matches ; vm.imaginary is missing at runtime — skipped
    assert s["verdict"]["verdict"] == "synced"
