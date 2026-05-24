"""Tests for modules/resctrl_audit.py — R&D #90.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import resctrl_audit as mod


def _mk_resctrl(tmp_path, *, default_schemata=None,
                 ctrl_groups=None):
    """Create a synthetic /sys/fs/resctrl tree.

    ctrl_groups is a dict {name: schemata_text}.
    """
    root = tmp_path / "resctrl"
    root.mkdir(parents=True, exist_ok=True)
    (root / "info").mkdir(exist_ok=True)
    (root / "info" / "L3").mkdir(exist_ok=True)
    if default_schemata is not None:
        (root / "schemata").write_text(default_schemata)
    if ctrl_groups:
        for name, text in ctrl_groups.items():
            g = root / name
            g.mkdir(exist_ok=True)
            (g / "schemata").write_text(text)
    return str(root)


# --- parse_schemata --------------------------------------------

def test_parse_schemata_empty():
    out = mod.parse_schemata("")
    assert out == {"L3": {}, "MB": {}}


def test_parse_schemata_typical():
    text = "L3:0=ffff;1=ffff\nMB:0=100;1=100\n"
    out = mod.parse_schemata(text)
    assert out["L3"] == {"0": "ffff", "1": "ffff"}
    assert out["MB"] == {"0": "100", "1": "100"}


def test_parse_schemata_only_l3():
    out = mod.parse_schemata("L3:0=ffff\n")
    assert out["L3"] == {"0": "ffff"}
    assert out["MB"] == {}


def test_parse_schemata_throttled_mb():
    out = mod.parse_schemata("L3:0=ffff\nMB:0=50\n")
    assert out["MB"] == {"0": "50"}


# --- _ctrl_groups ----------------------------------------------

def test_ctrl_groups_missing(tmp_path):
    assert mod._ctrl_groups(str(tmp_path / "nope")) == []


def test_ctrl_groups_only_default(tmp_path):
    r = _mk_resctrl(tmp_path,
                          default_schemata="L3:0=ffff\n")
    assert mod._ctrl_groups(r) == []


def test_ctrl_groups_skips_info_mon(tmp_path):
    r = _mk_resctrl(tmp_path,
                          default_schemata="L3:0=ffff\n")
    # info dir already exists ; create mon_groups + mon_data
    import os
    os.makedirs(os.path.join(r, "mon_groups"), exist_ok=True)
    os.makedirs(os.path.join(r, "mon_data"), exist_ok=True)
    assert mod._ctrl_groups(r) == []


def test_ctrl_groups_finds_custom(tmp_path):
    r = _mk_resctrl(tmp_path,
                          default_schemata="L3:0=ffff\n",
                          ctrl_groups={
                              "alpha": "L3:0=ffff\n",
                              "beta": "L3:0=ffff\n"})
    assert mod._ctrl_groups(r) == ["alpha", "beta"]


# --- classify --------------------------------------------------

def test_classify_unknown_not_mounted():
    v = mod.classify(False, None, [], {})
    assert v["verdict"] == "unknown"


def test_classify_requires_root_no_default():
    v = mod.classify(True, None, [], {})
    assert v["verdict"] == "requires_root"


def test_classify_resctrl_mounted_unused():
    v = mod.classify(True, "L3:0=ffff\nMB:0=100\n", [], {})
    assert v["verdict"] == "resctrl_mounted_unused"


def test_classify_cat_partitioned():
    default = "L3:0=ffff\nMB:0=100\n"
    g = "L3:0=00ff\nMB:0=100\n"  # half cache
    v = mod.classify(True, default, ["alpha"],
                          {"alpha": g})
    assert v["verdict"] == "cat_partitioned_non_default"
    assert v["group"] == "alpha"


def test_classify_mba_throttle():
    default = "L3:0=ffff\nMB:0=50\n"  # 50% bandwidth
    v = mod.classify(True, default, [], {})
    # No CTRL groups → would also be resctrl_mounted_unused.
    # MBA throttle priority should fire before that accent.
    assert v["verdict"] == "mba_throttle_active"


def test_classify_ok():
    default = "L3:0=ffff\nMB:0=100\n"
    g = "L3:0=ffff\nMB:0=100\n"
    v = mod.classify(True, default, ["alpha"],
                          {"alpha": g})
    assert v["verdict"] == "ok"


# Priority : cat > mba > mounted_unused
def test_priority_cat_over_mba():
    default = "L3:0=ffff\nMB:0=50\n"
    g = "L3:0=00ff\nMB:0=100\n"
    v = mod.classify(True, default, ["alpha"],
                          {"alpha": g})
    assert v["verdict"] == "cat_partitioned_non_default"


def test_priority_mba_over_unused():
    default = "L3:0=ffff\nMB:0=50\n"
    v = mod.classify(True, default, [], {})
    assert v["verdict"] == "mba_throttle_active"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"
    assert out["mounted"] is False


def test_status_mounted_unused_synthetic(tmp_path):
    r = _mk_resctrl(tmp_path,
                       default_schemata="L3:0=ffff\nMB:0=100\n")
    out = mod.status(None, r)
    assert out["verdict"]["verdict"] == "resctrl_mounted_unused"
    assert out["mounted"] is True
    assert out["ctrl_group_count"] == 0


def test_status_cat_partitioned_synthetic(tmp_path):
    r = _mk_resctrl(tmp_path,
                       default_schemata="L3:0=ffff\n",
                       ctrl_groups={"alpha": "L3:0=00ff\n"})
    out = mod.status(None, r)
    assert (out["verdict"]["verdict"]
            == "cat_partitioned_non_default")
    assert out["ok"] is False
