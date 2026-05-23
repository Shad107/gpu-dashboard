"""Tests for modules/drm_audit.py — R&D #50.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import drm_audit as mod


def _mk_connector(root, name, *, status="disconnected",
                    enabled="disabled", modes="", dpms="Off",
                    edid_bytes=0):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "status").write_text(status + "\n")
    (d / "enabled").write_text(enabled + "\n")
    (d / "dpms").write_text(dpms + "\n")
    (d / "modes").write_text(modes)
    (d / "edid").write_bytes(b"\x00" * edid_bytes)


def _mk_card(root, name):
    (root / name).mkdir(parents=True, exist_ok=True)


# --- list_cards / list_connectors --------------------------------

def test_list_cards(tmp_path):
    _mk_card(tmp_path, "card0")
    _mk_card(tmp_path, "card1")
    (tmp_path / "version").mkdir()
    out = mod.list_cards(str(tmp_path))
    assert out == ["card0", "card1"]


def test_list_connectors_basic(tmp_path):
    _mk_card(tmp_path, "card0")
    _mk_connector(tmp_path, "card0-DP-1", status="connected",
                    enabled="enabled",
                    modes="1920x1080 60.00\n2560x1440 75.00\n")
    _mk_connector(tmp_path, "card0-HDMI-A-1", status="disconnected")
    out = mod.list_connectors(str(tmp_path))
    assert len(out) == 2
    dp1 = next(c for c in out if c["name"] == "card0-DP-1")
    assert dp1["status"] == "connected"
    assert dp1["mode_count"] == 2


def test_list_connectors_missing(tmp_path):
    assert mod.list_connectors(str(tmp_path / "nope")) == []


# --- classify ----------------------------------------------------

def _conn(name="card0-DP-1", status="disconnected",
           enabled="disabled", modes=None):
    return {"name": name, "status": status, "enabled": enabled,
              "dpms": "Off", "modes": modes or [],
              "mode_count": len(modes or []), "edid_bytes": 0}


def test_classify_unknown():
    v = mod.classify([], [])
    assert v["verdict"] == "unknown"


def test_classify_no_displays():
    v = mod.classify(["card0"], [_conn(status="disconnected")])
    assert v["verdict"] == "no_displays"


def test_classify_ok():
    v = mod.classify(["card0"],
                       [_conn(status="connected", enabled="enabled")])
    assert v["verdict"] == "ok"


def test_classify_connector_disconnected_active():
    v = mod.classify(["card0"],
                       [_conn(name="card0-DP-2",
                                status="disconnected",
                                enabled="enabled")])
    assert v["verdict"] == "connector_disconnected_active"
    assert "DP-2" in v["reason"]


def test_classify_priority_active_wins_over_no_displays():
    # No connected display BUT one enabled-but-disconnected →
    # active wins (more actionable).
    v = mod.classify(["card0"],
                       [_conn(name="card0-DP-2",
                                status="disconnected",
                                enabled="enabled")])
    assert v["verdict"] == "connector_disconnected_active"


# --- status integration ------------------------------------------

def test_status_no_drm(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_CLASS_DRM",
                        str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_with_drm(monkeypatch, tmp_path):
    sysdrm = tmp_path / "drm"
    _mk_card(sysdrm, "card0")
    _mk_connector(sysdrm, "card0-DP-1",
                    status="disconnected",
                    enabled="enabled")
    _mk_connector(sysdrm, "card0-HDMI-A-1",
                    status="connected", enabled="enabled",
                    modes="1920x1080 60.00\n")
    monkeypatch.setattr(mod, "_SYS_CLASS_DRM", str(sysdrm))
    out = mod.status()
    assert out["ok"] is True
    assert out["card_count"] == 1
    assert out["connector_count"] == 2
    assert out["verdict"]["verdict"] == "connector_disconnected_active"
