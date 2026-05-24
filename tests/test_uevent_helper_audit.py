"""Tests for modules/uevent_helper_audit.py — R&D #72.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import uevent_helper_audit as mod


# --- read_uevent_helper ----------------------------------------

def test_read_missing(tmp_path):
    out = mod.read_uevent_helper(str(tmp_path / "nope"))
    assert out == {"present": False, "readable": False,
                      "value": None}


def test_read_empty(tmp_path):
    p = tmp_path / "uevent_helper"
    p.write_text("")
    out = mod.read_uevent_helper(str(p))
    assert out["present"] is True
    assert out["readable"] is True
    assert out["value"] == ""


def test_read_with_path(tmp_path):
    p = tmp_path / "uevent_helper"
    p.write_text("/usr/sbin/my-hotplug-handler\n")
    out = mod.read_uevent_helper(str(p))
    assert out["value"] == "/usr/sbin/my-hotplug-handler"


# --- classify ---------------------------------------------------

def _absent():
    return {"present": False, "readable": False, "value": None}


def _empty():
    return {"present": True, "readable": True, "value": ""}


def _set_to(path):
    return {"present": True, "readable": True, "value": path}


def test_classify_unknown():
    v = mod.classify(_absent(), _absent())
    assert v["verdict"] == "unknown"


def test_classify_ok_both_empty():
    v = mod.classify(_empty(), _empty())
    assert v["verdict"] == "ok"


def test_classify_uevent_helper_set():
    v = mod.classify(_set_to("/tmp/handler"), _empty())
    assert v["verdict"] == "uevent_helper_set_to_script"
    assert "/tmp/handler" in v["reason"]


def test_classify_hotplug_set():
    v = mod.classify(_empty(), _set_to("/usr/sbin/foo"))
    assert v["verdict"] == "hotplug_handler_set"


def test_classify_requires_root():
    ue = {"present": True, "readable": False, "value": None}
    v = mod.classify(ue, _empty())
    assert v["verdict"] == "requires_root"


# Priority : uevent_helper > hotplug > requires_root
def test_priority_ue_over_hp():
    v = mod.classify(_set_to("/a"), _set_to("/b"))
    assert v["verdict"] == "uevent_helper_set_to_script"


def test_priority_hp_over_requires_root():
    ue_unreadable = {"present": True, "readable": False,
                          "value": None}
    v = mod.classify(ue_unreadable, _set_to("/b"))
    assert v["verdict"] == "hotplug_handler_set"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_ue"),
                          str(tmp_path / "no_hp"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    ue = tmp_path / "uevent_helper"
    ue.write_text("")
    hp = tmp_path / "hotplug"
    hp.write_text("")
    out = mod.status(None, str(ue), str(hp))
    assert out["ok"] is True
    assert out["uevent_helper_value"] == ""
    assert out["verdict"]["verdict"] == "ok"


def test_status_uevent_set_synthetic(tmp_path):
    ue = tmp_path / "uevent_helper"
    ue.write_text("/tmp/handler\n")
    hp = tmp_path / "hotplug"
    hp.write_text("")
    out = mod.status(None, str(ue), str(hp))
    assert out["verdict"]["verdict"] == \
        "uevent_helper_set_to_script"
    assert out["uevent_helper_value"] == "/tmp/handler"
