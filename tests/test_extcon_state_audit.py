"""Tests for modules/extcon_state_audit.py — R&D #85.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import extcon_state_audit as mod


def _mk_extcon_legacy(tmp_path, node, name, state_text):
    """Create extcon dir with legacy `state` file."""
    d = tmp_path / node
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    (d / "state").write_text(state_text + "\n")


def _mk_extcon_cables(tmp_path, node, name, cables):
    """cables: list of (cable_name, state_int)."""
    d = tmp_path / node
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    for i, (cname, cstate) in enumerate(cables):
        cdir = d / f"cable.{i}"
        cdir.mkdir(exist_ok=True)
        (cdir / "name").write_text(cname + "\n")
        (cdir / "state").write_text(f"{cstate}\n")


# --- list_extcon_devices ---------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_extcon_devices(
        str(tmp_path / "nope")) == []


def test_list_empty(tmp_path):
    assert mod.list_extcon_devices(str(tmp_path)) == []


def test_list_devices(tmp_path):
    _mk_extcon_legacy(tmp_path, "extcon0",
                          "USB", "USB=0")
    _mk_extcon_legacy(tmp_path, "extcon1",
                          "HDMI", "HDMI=0")
    (tmp_path / "non-extcon-dir").mkdir()
    out = mod.list_extcon_devices(str(tmp_path))
    assert out == ["extcon0", "extcon1"]


# --- _parse_state_text -----------------------------------------

def test_parse_state_legacy():
    text = "USB=0\nHDMI=1\nUSB-HOST=0\n"
    out = mod._parse_state_text(text)
    assert len(out) == 3
    by_name = {c["name"]: c for c in out}
    assert by_name["USB"]["asserted"] is False
    assert by_name["HDMI"]["asserted"] is True


def test_parse_state_invalid_value():
    text = "USB=garbage\n"
    out = mod._parse_state_text(text)
    assert out[0]["invalid"] is True


# --- read_extcon -----------------------------------------------

def test_read_extcon_legacy(tmp_path):
    _mk_extcon_legacy(tmp_path, "extcon0",
                          "USB-MUX0",
                          "USB=1\nHDMI=0")
    out = mod.read_extcon(str(tmp_path), "extcon0")
    assert out["label"] == "USB-MUX0"
    assert len(out["cables"]) == 2


def test_read_extcon_cables_subdir(tmp_path):
    _mk_extcon_cables(tmp_path, "extcon0", "DOCK",
                          [("USB", 1), ("HDMI", 0)])
    out = mod.read_extcon(str(tmp_path), "extcon0")
    assert out["label"] == "DOCK"
    assert len(out["cables"]) == 2
    by_name = {c["name"]: c for c in out["cables"]}
    assert by_name["USB"]["asserted"] is True


# --- _find_mux_conflict ----------------------------------------

def test_no_conflict():
    cables = [
        {"name": "USB", "asserted": True},
        {"name": "HDMI", "asserted": False},
    ]
    assert mod._find_mux_conflict(cables) is None


def test_conflict_hdmi_dp():
    cables = [
        {"name": "HDMI", "asserted": True},
        {"name": "DP", "asserted": True},
    ]
    out = mod._find_mux_conflict(cables)
    assert out is not None
    assert "HDMI" in out and "DP" in out


def test_conflict_usb_host():
    cables = [
        {"name": "USB", "asserted": True},
        {"name": "USB-HOST", "asserted": True},
    ]
    assert mod._find_mux_conflict(cables) is not None


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], extcon_present=False)
    assert v["verdict"] == "unknown"


def test_classify_na():
    v = mod.classify([], extcon_present=True)
    assert v["verdict"] == "n/a"


def _dev(node, label, cables):
    return {"node": node, "label": label, "cables": cables}


def test_classify_ok():
    v = mod.classify([
        _dev("extcon0", "USB-MUX0", [
            {"name": "USB", "asserted": True,
                "invalid": False},
            {"name": "HDMI", "asserted": False,
                "invalid": False},
        ]),
    ], extcon_present=True)
    assert v["verdict"] == "ok"


def test_classify_stuck():
    v = mod.classify([
        _dev("extcon0", "USB-MUX0", [
            {"name": "USB", "value": "garbage",
                "asserted": False, "invalid": True},
        ]),
    ], extcon_present=True)
    assert v["verdict"] == "stuck_extcon_state"


def test_classify_multiple_connectors():
    v = mod.classify([
        _dev("extcon0", "USB-MUX0", [
            {"name": "HDMI", "asserted": True,
                "invalid": False},
            {"name": "DP", "asserted": True,
                "invalid": False},
        ]),
    ], extcon_present=True)
    assert v["verdict"] == "multiple_connectors_asserted"


# Priority : stuck > multiple
def test_priority_stuck_over_multiple():
    v = mod.classify([
        _dev("extcon0", "USB-MUX0", [
            {"name": "USB", "value": "bad",
                "asserted": False, "invalid": True},
            {"name": "HDMI", "asserted": True,
                "invalid": False},
            {"name": "DP", "asserted": True,
                "invalid": False},
        ]),
    ], extcon_present=True)
    assert v["verdict"] == "stuck_extcon_state"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_na_empty_dir(tmp_path):
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "n/a"


def test_status_ok_synthetic(tmp_path):
    _mk_extcon_legacy(tmp_path, "extcon0",
                          "DOCK", "USB=1\nHDMI=0")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["device_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_multiple_connectors_synthetic(tmp_path):
    _mk_extcon_cables(tmp_path, "extcon0", "USB-MUX0",
                          [("HDMI", 1), ("DP", 1)])
    out = mod.status(None, str(tmp_path))
    assert (out["verdict"]["verdict"]
            == "multiple_connectors_asserted")
