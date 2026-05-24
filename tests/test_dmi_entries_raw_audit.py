"""Tests for modules/dmi_entries_raw_audit.py — R&D #72.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import dmi_entries_raw_audit as mod


def _mk_entry(root, type_, instance):
    d = root / f"{type_}-{instance}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- list_entries ----------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_entries(str(tmp_path / "nope")) == []


def test_list_basic(tmp_path):
    _mk_entry(tmp_path, 1, 0)
    _mk_entry(tmp_path, 4, 0)
    _mk_entry(tmp_path, 17, 0)
    _mk_entry(tmp_path, 17, 1)
    _mk_entry(tmp_path, 38, 0)
    out = mod.list_entries(str(tmp_path))
    by_type = {(e["type"], e["instance"]): e for e in out}
    assert (17, 0) in by_type
    assert (17, 1) in by_type
    assert by_type[(4, 0)]["type_label"] == "Processor"
    assert by_type[(38, 0)]["type_label"] == "IPMI Device"


def test_list_skips_non_matching(tmp_path):
    _mk_entry(tmp_path, 1, 0)
    (tmp_path / "other-dir").mkdir()
    out = mod.list_entries(str(tmp_path))
    ids = [e["id"] for e in out]
    assert ids == ["1-0"]


# --- classify ---------------------------------------------------

def _ent(type_, instance=0):
    return {"id": f"{type_}-{instance}",
              "type": type_,
              "type_label": mod._TYPE_LABELS.get(type_, "unknown"),
              "instance": instance,
              "handle": None, "length": None,
              "type_readable": False}


def test_classify_unknown_path_missing():
    v = mod.classify([], False, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root_empty():
    v = mod.classify([], True, False)
    assert v["verdict"] == "requires_root"


def test_classify_ipmi():
    entries = [_ent(0), _ent(1), _ent(4), _ent(16),
                  _ent(17, 0), _ent(17, 1),
                  _ent(9, 0), _ent(9, 1),
                  _ent(38, 0)]
    v = mod.classify(entries, True, True)
    assert v["verdict"] == "ipmi_bmc_exposed"


def test_classify_dimm_slot_mismatch():
    # 4 DIMMs vs 2 slots
    entries = [_ent(0), _ent(1), _ent(4), _ent(16),
                  _ent(17, 0), _ent(17, 1),
                  _ent(17, 2), _ent(17, 3),
                  _ent(9, 0), _ent(9, 1)]
    v = mod.classify(entries, True, True)
    assert v["verdict"] == "dimm_slot_mismatch"


def test_classify_dimm_slot_match_ok():
    entries = [_ent(0), _ent(1), _ent(4), _ent(16),
                  _ent(17, 0), _ent(17, 1),
                  _ent(9, 0), _ent(9, 1)]
    v = mod.classify(entries, True, True)
    assert v["verdict"] == "ok"


def test_classify_smbios_truncated():
    # only 3 distinct types
    entries = [_ent(0), _ent(1), _ent(127)]
    v = mod.classify(entries, True, True)
    assert v["verdict"] == "smbios_truncated"


def test_classify_ok_full():
    entries = [_ent(0), _ent(1), _ent(4), _ent(16),
                  _ent(17, 0), _ent(19, 0), _ent(32),
                  _ent(127)]
    v = mod.classify(entries, True, True)
    assert v["verdict"] == "ok"


# Priority : ipmi > slot_mismatch > truncated
def test_priority_ipmi_over_truncated():
    entries = [_ent(1), _ent(38, 0), _ent(127)]
    v = mod.classify(entries, True, True)
    assert v["verdict"] == "ipmi_bmc_exposed"


def test_priority_slot_mismatch_over_truncated():
    entries = [_ent(0), _ent(1),
                  _ent(17, 0), _ent(17, 1),
                  _ent(9, 0)]
    v = mod.classify(entries, True, True)
    assert v["verdict"] == "dimm_slot_mismatch"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    for t in (0, 1, 4, 16, 17, 19, 32, 127):
        _mk_entry(tmp_path, t, 0)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["entry_count"] == 8
    assert out["distinct_type_count"] == 8
    assert out["verdict"]["verdict"] == "ok"


def test_status_ipmi_synthetic(tmp_path):
    for t in (0, 1, 4, 16, 17, 32, 38, 127):
        _mk_entry(tmp_path, t, 0)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "ipmi_bmc_exposed"


def test_status_truncated_synthetic(tmp_path):
    _mk_entry(tmp_path, 0, 0)
    _mk_entry(tmp_path, 127, 0)
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "smbios_truncated"
