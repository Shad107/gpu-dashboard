"""Tests for modules/per_device_wakeup_attribution_audit.py
R&D #95.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    per_device_wakeup_attribution_audit as mod)


def _mk_device(tmp_path, path, *, wakeup="enabled",
                count="0", active_count="0",
                abort_count="0", max_ms="0",
                total_ms="0"):
    """Create /<tmp>/devices/<path>/power/<files>."""
    d = tmp_path / "devices" / path / "power"
    d.mkdir(parents=True, exist_ok=True)
    if wakeup is not None:
        (d / "wakeup").write_text(wakeup + "\n")
    if count is not None:
        (d / "wakeup_count").write_text(count + "\n")
    if active_count is not None:
        (d / "wakeup_active_count").write_text(
            active_count + "\n")
    if abort_count is not None:
        (d / "wakeup_abort_count").write_text(
            abort_count + "\n")
    if max_ms is not None:
        (d / "wakeup_max_time_ms").write_text(max_ms + "\n")
    if total_ms is not None:
        (d / "wakeup_total_time_ms").write_text(
            total_ms + "\n")
    return str(tmp_path / "devices")


# --- _kind_for_path --------------------------------------------

def test_kind_usb():
    assert mod._kind_for_path(
        "/sys/devices/pci/usb1/1-1") == "usb"


def test_kind_i2c():
    assert mod._kind_for_path(
        "/sys/devices/i2c-1") == "i2c"


def test_kind_pci():
    assert mod._kind_for_path(
        "/sys/devices/pci0000:00/0000:00:1d.1") == "pci"


def test_kind_other():
    assert mod._kind_for_path(
        "/sys/devices/platform/foo") == "other"


# --- walk_devices ----------------------------------------------

def test_walk_devices_missing(tmp_path):
    assert mod.walk_devices(str(tmp_path / "nope")) == []


def test_walk_devices_finds_wakeup(tmp_path):
    _mk_device(tmp_path, "pci0000:00/0000:00:1d.1",
                  wakeup="enabled", count="5")
    out = mod.walk_devices(str(tmp_path / "devices"))
    assert len(out) == 1
    assert out[0]["wakeup"] == "enabled"
    assert out[0]["count"] == 5


def test_walk_devices_skips_unset_wakeup(tmp_path):
    # A `power/wakeup` file whose content isn't enabled/disabled
    _mk_device(tmp_path, "platform/foo",
                  wakeup="")  # empty content
    out = mod.walk_devices(str(tmp_path / "devices"))
    assert out == []


# --- classify --------------------------------------------------

def _dev(*, path="p", kind="pci", wakeup="enabled",
         count=0, active_count=0, abort_count=0,
         max_ms=0, total_ms=0):
    return {"path": path, "kind": kind, "wakeup": wakeup,
            "count": count, "active_count": active_count,
            "abort_count": abort_count, "max_ms": max_ms,
            "total_ms": total_ms}


def test_classify_unknown_no_root():
    v = mod.classify([], False)
    assert v["verdict"] == "unknown"


def test_classify_unknown_empty():
    v = mod.classify([], True)
    assert v["verdict"] == "unknown"


def test_classify_storm_err():
    v = mod.classify(
        [_dev(path="xhci", count=500, max_ms=8000)],
        True)
    assert v["verdict"] == "wakeup_storm_blocking_suspend"


def test_classify_storm_needs_both_thresholds():
    # High count but low max_ms — not a storm
    v = mod.classify(
        [_dev(path="x", count=500, max_ms=1000)],
        True)
    assert v["verdict"] == "wakeup_attribution_clean"


def test_classify_aborts_warn():
    v = mod.classify(
        [_dev(path="rfkill", abort_count=25)],
        True)
    assert v["verdict"] == "wakeup_aborts_climbing"


def test_classify_unused_usb_accent():
    v = mod.classify(
        [_dev(path="usb1/1-1", kind="usb",
              wakeup="enabled", count=0)],
        True)
    assert v["verdict"] == "wakeup_enabled_on_unused_device"


def test_classify_unused_pci_is_ok():
    # PCI wakeup enabled but unused is normal (suspend wakes)
    v = mod.classify(
        [_dev(path="pci/0000:00:1d.1", kind="pci",
              wakeup="enabled", count=0)],
        True)
    assert v["verdict"] == "wakeup_attribution_clean"


def test_classify_clean():
    v = mod.classify(
        [_dev(path="usb1/1-1", kind="usb",
              wakeup="disabled"),
         _dev(path="pci/0000:00:1d.1", kind="pci",
              wakeup="enabled", count=3)],
        True)
    assert v["verdict"] == "wakeup_attribution_clean"


# Priority : storm > aborts > unused
def test_priority_storm_over_aborts():
    v = mod.classify(
        [_dev(path="x", count=500, max_ms=8000),
         _dev(path="y", abort_count=50)],
        True)
    assert v["verdict"] == "wakeup_storm_blocking_suspend"


def test_priority_aborts_over_unused():
    v = mod.classify(
        [_dev(path="y", abort_count=50),
         _dev(path="usb1/1", kind="usb",
              wakeup="enabled", count=0)],
        True)
    assert v["verdict"] == "wakeup_aborts_climbing"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_clean_synthetic(tmp_path):
    _mk_device(tmp_path, "pci0000:00/0000:00:1d.1",
                  wakeup="enabled", count="2")
    out = mod.status(None, str(tmp_path / "devices"))
    assert (out["verdict"]["verdict"]
            == "wakeup_attribution_clean")
    assert out["device_count"] == 1
    assert out["enabled_count"] == 1


def test_status_storm_synthetic(tmp_path):
    _mk_device(tmp_path, "pci0000:00/usb1/1-1",
                  wakeup="enabled", count="500",
                  max_ms="8000")
    out = mod.status(None, str(tmp_path / "devices"))
    assert (out["verdict"]["verdict"]
            == "wakeup_storm_blocking_suspend")
    assert out["ok"] is False
