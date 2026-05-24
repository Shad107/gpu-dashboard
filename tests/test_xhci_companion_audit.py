"""Tests for modules/xhci_companion_audit.py — R&D #81.1."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import xhci_companion_audit as mod


def _mk_pci_root(tmp_path, bdf):
    """Create /devices/pci.../<bdf>/ and return it."""
    d = tmp_path / "devices" / "pci0000:00" / bdf
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mk_hub(tmp_path, hub_name, *, version="2.00",
                speed=480, maxchild=4, bdf=None):
    """Create /sys/bus/usb/devices/<hub_name> symlinked into
    the PCI parent so realpath works."""
    bus_root = tmp_path / "sys" / "bus" / "usb" / "devices"
    bus_root.mkdir(parents=True, exist_ok=True)
    if bdf is not None:
        pci_d = _mk_pci_root(tmp_path, bdf)
        hub_real = pci_d / hub_name
        hub_real.mkdir(exist_ok=True)
        # symlink hub_name in /sys/bus/usb/devices to the real dir
        link_path = bus_root / hub_name
        if not link_path.exists():
            os.symlink(str(hub_real), str(link_path))
        target = hub_real
    else:
        target = bus_root / hub_name
        target.mkdir(exist_ok=True)
    (target / "version").write_text(f"{version}\n")
    (target / "speed").write_text(f"{speed}\n")
    (target / "maxchild").write_text(f"{maxchild}\n")
    return str(bus_root)


# --- _parse_version_major --------------------------------------

def test_parse_version_2():
    assert mod._parse_version_major("2.00") == 2


def test_parse_version_3():
    assert mod._parse_version_major(" 3.10") == 3


def test_parse_version_1():
    assert mod._parse_version_major("1.10") == 1


def test_parse_version_none():
    assert mod._parse_version_major(None) is None


def test_parse_version_garbage():
    assert mod._parse_version_major("foo") is None


# --- _parent_pci_bdf -------------------------------------------

def test_parent_pci_bdf_with_real_symlink(tmp_path):
    bus_root = _mk_hub(tmp_path, "usb1",
                          bdf="0000:00:14.0")
    out = mod._parent_pci_bdf(bus_root, "usb1")
    assert out == "0000:00:14.0"


def test_parent_pci_bdf_no_pci(tmp_path):
    bus_root = _mk_hub(tmp_path, "usb1", bdf=None)
    # When the hub is not symlinked through a PCI BDF dir,
    # realpath ends up under bus/usb/devices itself.
    out = mod._parent_pci_bdf(bus_root, "usb1")
    assert out is None


# --- list_root_hubs --------------------------------------------

def test_list_missing(tmp_path):
    assert mod.list_root_hubs(str(tmp_path / "nope")) == []


def test_list_basic(tmp_path):
    bus_root = _mk_hub(tmp_path, "usb1", version="2.00",
                          bdf="0000:00:14.0")
    _mk_hub(tmp_path, "usb2", version="3.10",
              speed=5000, bdf="0000:00:14.0")
    out = mod.list_root_hubs(bus_root)
    assert len(out) == 2
    by_node = {h["node"]: h for h in out}
    assert by_node["usb1"]["version_major"] == 2
    assert by_node["usb2"]["version_major"] == 3
    assert by_node["usb2"]["speed"] == 5000


def test_list_skips_child_devices(tmp_path):
    bus_root = _mk_hub(tmp_path, "usb1",
                          bdf="0000:00:14.0")
    # Create a child device dir (with hyphen) — should be
    # skipped because we only collect root hubs.
    (tmp_path / "sys" / "bus" / "usb" / "devices"
       / "1-1").mkdir()
    out = mod.list_root_hubs(bus_root)
    assert len(out) == 1
    assert out[0]["node"] == "usb1"


# --- classify --------------------------------------------------

def test_classify_unknown_empty():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def _hub(node, version="3.00", speed=5000, maxchild=4,
          bdf="0000:00:14.0"):
    return {"node": node, "version": version,
              "version_major": int(version.split(".")[0]),
              "speed": speed, "maxchild": maxchild,
              "pci_bdf": bdf}


def test_classify_ok_paired():
    v = mod.classify([
        _hub("usb1", version="2.00", speed=480),
        _hub("usb2", version="3.10", speed=5000)])
    assert v["verdict"] == "ok"


def test_classify_usb3_no_companion():
    # USB 3 hub on BDF A, but no USB 2 on the same BDF
    v = mod.classify([
        _hub("usb2", version="3.10", speed=5000,
              bdf="0000:00:14.0"),
        _hub("usb1", version="2.00", speed=480,
              bdf="0000:00:1d.7")])  # different BDF
    assert v["verdict"] == "usb3_root_no_companion"


def test_classify_usb3_speed_degraded():
    v = mod.classify([
        _hub("usb1", version="2.00", speed=480),
        _hub("usb2", version="3.10", speed=480)])
    assert v["verdict"] == "usb3_root_speed_degraded"


def test_classify_usb2_only_legacy():
    v = mod.classify([
        _hub("usb1", version="2.00", speed=480),
        _hub("usb3", version="1.10", speed=12)])
    assert v["verdict"] == "usb2_only_legacy"


# Priority : no_companion > speed_degraded > legacy
def test_priority_no_companion_over_speed():
    v = mod.classify([
        _hub("usb2", version="3.10", speed=480,
              bdf="0000:00:14.0"),
        _hub("usb1", version="2.00",
              bdf="0000:00:1d.7")])
    assert v["verdict"] == "usb3_root_no_companion"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_usb2_only_synthetic(tmp_path):
    bus_root = _mk_hub(tmp_path, "usb1", version="2.00",
                          bdf="0000:00:14.0")
    out = mod.status(None, bus_root)
    assert out["ok"] is True
    assert out["hub_count"] == 1
    assert out["usb3_count"] == 0
    assert out["usb2_count"] == 1
    assert out["verdict"]["verdict"] == "usb2_only_legacy"


def test_status_ok_synthetic(tmp_path):
    bus_root = _mk_hub(tmp_path, "usb1", version="2.00",
                          bdf="0000:00:14.0")
    _mk_hub(tmp_path, "usb2", version="3.10", speed=5000,
              bdf="0000:00:14.0")
    out = mod.status(None, bus_root)
    assert out["ok"] is True
    assert out["usb3_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_no_companion_synthetic(tmp_path):
    bus_root = _mk_hub(tmp_path, "usb2", version="3.10",
                          speed=5000,
                          bdf="0000:00:14.0")
    _mk_hub(tmp_path, "usb1", version="2.00",
              bdf="0000:00:1d.7")
    out = mod.status(None, bus_root)
    assert out["ok"] is False
    assert (out["verdict"]["verdict"]
            == "usb3_root_no_companion")
