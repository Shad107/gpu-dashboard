"""Tests for modules/usb_role_switch_audit.py — R&D #71.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import usb_role_switch_audit as mod


def _mk_usb_role(root, name, role="host"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "role").write_text(role + "\n")


def _mk_typec_port(root, name, data_role="[host] device",
                       power_role="source"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "data_role").write_text(data_role + "\n")
    (d / "power_role").write_text(power_role + "\n")


# --- list_usb_roles --------------------------------------------

def test_list_usb_roles_missing(tmp_path):
    assert mod.list_usb_roles(str(tmp_path / "nope")) == []


def test_list_usb_roles(tmp_path):
    _mk_usb_role(tmp_path, "usbc-port0", "host")
    _mk_usb_role(tmp_path, "usbc-port1", "device")
    out = mod.list_usb_roles(str(tmp_path))
    assert len(out) == 2
    by_id = {r["id"]: r for r in out}
    assert by_id["usbc-port0"]["role"] == "host"
    assert by_id["usbc-port1"]["role"] == "device"


# --- list_typec_ports ------------------------------------------

def test_list_typec_ports_missing(tmp_path):
    assert mod.list_typec_ports(str(tmp_path / "nope")) == []


def test_list_typec_ports_skips_non_port(tmp_path):
    _mk_typec_port(tmp_path, "port0")
    (tmp_path / "alt0").mkdir()
    out = mod.list_typec_ports(str(tmp_path))
    assert len(out) == 1
    assert out[0]["id"] == "port0"


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], [], False, False)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(
        [{"id": "p0", "role": "host"}],
        [{"id": "port0", "data_role": "[host] device",
            "power_role": "source"}],
        True, True)
    assert v["verdict"] == "ok"


def test_classify_role_stuck_device():
    v = mod.classify(
        [{"id": "p0", "role": "device"},
          {"id": "p1", "role": "device"}],
        [],
        True, False)
    assert v["verdict"] == "role_stuck_device"


def test_classify_role_flapping():
    v = mod.classify(
        [{"id": "p0", "role": "host"},
          {"id": "p1", "role": "none"}],
        [], True, False)
    assert v["verdict"] == "role_flapping"


def test_classify_unexpected_host_role():
    # 1 usb_role host, 0 typec hosts → mismatch
    v = mod.classify(
        [{"id": "p0", "role": "host"}],
        [{"id": "port0", "data_role": "[device]",
            "power_role": "sink"}],
        True, True)
    assert v["verdict"] == "unexpected_host_role"


def test_classify_unexpected_host_role_skipped_without_typec():
    # No typec ports → no comparison
    v = mod.classify(
        [{"id": "p0", "role": "host"}],
        [], True, False)
    assert v["verdict"] == "ok"


# Priority : stuck > flapping > mismatch
def test_priority_stuck_over_flapping():
    v = mod.classify(
        [{"id": "p0", "role": "device"},
          {"id": "p1", "role": "device"}],
        [], True, False)
    # All-device pattern triggers stuck before flapping check.
    assert v["verdict"] == "role_stuck_device"


def test_priority_flapping_over_mismatch():
    v = mod.classify(
        [{"id": "p0", "role": "none"},
          {"id": "p1", "role": "host"}],
        [{"id": "port0", "data_role": "[device]",
            "power_role": "sink"}],
        True, True)
    assert v["verdict"] == "role_flapping"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_role"),
                          str(tmp_path / "no_typec"),
                          str(tmp_path / "no_intel"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    role_dir = tmp_path / "role"; role_dir.mkdir()
    _mk_usb_role(role_dir, "usbc-port0", "host")
    out = mod.status(None, str(role_dir),
                          str(tmp_path / "no_typec"),
                          str(tmp_path / "no_intel"))
    assert out["ok"] is True
    assert out["usb_role_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_stuck_synthetic(tmp_path):
    role_dir = tmp_path / "role"; role_dir.mkdir()
    _mk_usb_role(role_dir, "p0", "device")
    _mk_usb_role(role_dir, "p1", "device")
    out = mod.status(None, str(role_dir),
                          str(tmp_path / "no_typec"),
                          str(tmp_path / "no_intel"))
    assert out["verdict"]["verdict"] == "role_stuck_device"
