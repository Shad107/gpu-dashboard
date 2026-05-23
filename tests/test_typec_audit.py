"""Tests for modules/typec_audit.py — R&D #51.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import typec_audit as mod


def _mk_port(root, name, *, power_operation_mode="usb_power_delivery",
                power_role="sink", data_role="device",
                pd_revision="3.0", with_partner=False,
                partner_supports_pd="no",
                alt_modes=()):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "power_operation_mode").write_text(power_operation_mode + "\n")
    (d / "power_role").write_text(power_role + "\n")
    (d / "data_role").write_text(data_role + "\n")
    (d / "pd_revision").write_text(pd_revision + "\n")
    if with_partner:
        pd = root / f"{name}-partner"
        pd.mkdir()
        (pd / "supports_usb_power_delivery").write_text(
            partner_supports_pd + "\n")
        (pd / "type").write_text("USB-C plug\n")
    for am in alt_modes:
        ad = root / f"{name}-partner.{am}"
        ad.mkdir(parents=True, exist_ok=True)


# --- list_typec_ports --------------------------------------------

def test_list_ports_missing(tmp_path):
    assert mod.list_typec_ports(str(tmp_path / "nope")) == []


def test_list_ports_basic(tmp_path):
    _mk_port(tmp_path, "port0")
    _mk_port(tmp_path, "port1", with_partner=True,
                partner_supports_pd="yes", alt_modes=("0",))
    out = mod.list_typec_ports(str(tmp_path))
    names = {p["name"] for p in out}
    assert names == {"port0", "port1"}
    p1 = next(p for p in out if p["name"] == "port1")
    assert p1["partner_attached"] is True
    assert p1["partner_supports_pd"] is True
    assert len(p1["alt_modes"]) == 1


def test_list_ports_skips_partner_subdir(tmp_path):
    _mk_port(tmp_path, "port0", with_partner=True)
    # The port0-partner subdir exists but should NOT appear as a port.
    out = mod.list_typec_ports(str(tmp_path))
    assert len(out) == 1
    assert out[0]["name"] == "port0"


# --- list_extcon -------------------------------------------------

def test_list_extcon_basic(tmp_path):
    d = tmp_path / "extcon0"
    d.mkdir(parents=True)
    (d / "state").write_text("USB=1\nUSB-HOST=0\n")
    out = mod.list_extcon(str(tmp_path))
    assert len(out) == 1
    assert "USB=1" in out[0]["state"]


def test_list_extcon_missing(tmp_path):
    assert mod.list_extcon(str(tmp_path / "nope")) == []


# --- classify ----------------------------------------------------

def _port(name="port0", power_operation_mode="usb_power_delivery",
           partner_attached=False, partner_supports_pd=False,
           alt_modes=None):
    return {"name": name,
              "power_operation_mode": power_operation_mode,
              "power_role": "sink", "data_role": "device",
              "pd_revision": "3.0", "partner_attached": partner_attached,
              "partner_supports_pd": partner_supports_pd,
              "alt_modes": alt_modes or []}


def test_classify_no_typec():
    v = mod.classify([], [])
    assert v["verdict"] == "no_typec"


def test_classify_ok():
    v = mod.classify([_port()], [])
    assert v["verdict"] == "ok"


def test_classify_pd_no_contract():
    v = mod.classify([_port(power_operation_mode="default",
                                partner_attached=True,
                                partner_supports_pd=True)],
                       [])
    assert v["verdict"] == "pd_no_contract"


def test_classify_alt_mode_active():
    v = mod.classify([_port(alt_modes=["port0-partner.0"])], [])
    assert v["verdict"] == "alt_mode_active"


def test_classify_priority_pd_over_alt():
    v = mod.classify([_port(power_operation_mode="default",
                                partner_attached=True,
                                partner_supports_pd=True,
                                alt_modes=["port0-partner.0"])],
                       [])
    assert v["verdict"] == "pd_no_contract"


def test_classify_no_partner_no_pd_flag():
    # No partner attached → don't flag PD missing.
    v = mod.classify([_port(power_operation_mode="default")], [])
    assert v["verdict"] == "ok"


# --- status integration ------------------------------------------

def test_status_no_typec(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_TYPEC", str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SYS_EXTCON", str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "no_typec"


def test_status_with_ports(monkeypatch, tmp_path):
    sysc = tmp_path / "typec"
    _mk_port(sysc, "port0")
    monkeypatch.setattr(mod, "_SYS_TYPEC", str(sysc))
    monkeypatch.setattr(mod, "_SYS_EXTCON", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is True
    assert out["port_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
