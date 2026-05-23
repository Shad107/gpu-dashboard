"""Tests for modules/devlink_smartnic_audit.py — R&D #64.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import devlink_smartnic_audit as mod


def _mk_link(root, name, *, status="not tracked",
               runtime_pm=0, auto_remove_on="never",
               sync_state_only=0):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "status").write_text(status + "\n")
    (d / "runtime_pm").write_text(f"{runtime_pm}\n")
    (d / "auto_remove_on").write_text(auto_remove_on + "\n")
    (d / "sync_state_only").write_text(f"{sync_state_only}\n")


# --- list_devlinks ----------------------------------------------

def test_list_devlinks_missing(tmp_path):
    assert mod.list_devlinks(str(tmp_path / "nope")) == []


def test_list_devlinks(tmp_path):
    _mk_link(tmp_path, ":ata2--scsi:2:0:0:0",
                status="not tracked")
    _mk_link(tmp_path, ":pci:0000:01:00.0--pci:0000:01:00.1",
                status="active")
    out = mod.list_devlinks(str(tmp_path))
    assert len(out) == 2


# --- classify ---------------------------------------------------

def _l(name="x", status="active"):
    return {"id": name, "status": status,
              "runtime_pm": 0, "auto_remove_on": "never",
              "sync_state_only": 0}


def test_classify_unknown():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_l(status="active"),
                        _l(name="y", status="not tracked")])
    assert v["verdict"] == "ok"


def test_classify_snr():
    v = mod.classify([_l(status="supplier_not_ready")])
    assert v["verdict"] == "supplier_not_ready"


def test_classify_unbinding():
    v = mod.classify([_l(status="consumer_unbinding")])
    assert v["verdict"] == "consumer_unbinding"


def test_classify_dormant():
    v = mod.classify([_l(status="dormant")])
    assert v["verdict"] == "dormant_links_present"


def test_classify_priority_snr_wins():
    v = mod.classify(
        [_l(status="supplier_not_ready"),
         _l(name="y", status="dormant")])
    assert v["verdict"] == "supplier_not_ready"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    _mk_link(tmp_path, ":ata2--scsi:2:0:0:0",
                status="not tracked")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["link_count"] == 1
    assert out["status_histogram"]["not tracked"] == 1
    assert out["verdict"]["verdict"] == "ok"
