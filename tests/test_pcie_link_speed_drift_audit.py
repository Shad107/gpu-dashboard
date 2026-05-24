"""Tests for modules/pcie_link_speed_drift_audit.py R&D #89.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    pcie_link_speed_drift_audit as mod)


def _mk_dev(tmp_path, bdf, *, cls="0x010802",
              cur_speed="16.0 GT/s PCIe",
              max_speed="16.0 GT/s PCIe",
              cur_width="4", max_width="4"):
    d = tmp_path / "pci" / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "class").write_text(cls + "\n")
    (d / "current_link_speed").write_text(cur_speed + "\n")
    (d / "max_link_speed").write_text(max_speed + "\n")
    (d / "current_link_width").write_text(cur_width + "\n")
    (d / "max_link_width").write_text(max_width + "\n")
    return str(d)


# --- parse_speed_gts -------------------------------------------

def test_parse_speed_typical():
    assert mod.parse_speed_gts("16.0 GT/s PCIe") == 16.0


def test_parse_speed_short():
    assert mod.parse_speed_gts("8 GT/s") == 8.0


def test_parse_speed_garbage():
    assert mod.parse_speed_gts("hello") is None


def test_parse_speed_empty():
    assert mod.parse_speed_gts("") is None


# --- parse_width -----------------------------------------------

def test_parse_width_int():
    assert mod.parse_width("16") == 16


def test_parse_width_garbage():
    assert mod.parse_width("zzz") is None


# --- read_device -----------------------------------------------

def test_read_device(tmp_path):
    _mk_dev(tmp_path, "0000:01:00.0",
                 cls="0x010802",
                 cur_speed="8.0 GT/s PCIe",
                 max_speed="16.0 GT/s PCIe",
                 cur_width="4", max_width="4")
    d = mod.read_device(
        str(tmp_path / "pci"), "0000:01:00.0")
    assert d["class"] == "0x010802"
    assert d["current_speed_gts"] == 8.0
    assert d["max_speed_gts"] == 16.0


# --- helpers ---------------------------------------------------

def test_is_gpu():
    assert mod._is_gpu("0x030000") is True
    assert mod._is_gpu("0x030200") is True
    assert mod._is_gpu("0x010802") is False


def test_is_nvme():
    assert mod._is_nvme("0x010802") is True
    assert mod._is_nvme("0x030000") is False


def test_has_link_complete():
    d = {"current_speed_gts": 16.0, "max_speed_gts": 16.0,
         "current_width": 16, "max_width": 16}
    assert mod._has_link(d) is True


def test_has_link_missing():
    d = {"current_speed_gts": None, "max_speed_gts": 16.0,
         "current_width": 16, "max_width": 16}
    assert mod._has_link(d) is False


def test_is_degraded_speed():
    d = {"current_speed_gts": 8.0, "max_speed_gts": 16.0,
         "current_width": 16, "max_width": 16}
    assert mod._is_degraded(d) is True


def test_is_degraded_width():
    d = {"current_speed_gts": 16.0, "max_speed_gts": 16.0,
         "current_width": 8, "max_width": 16}
    assert mod._is_degraded(d) is True


def test_is_degraded_no():
    d = {"current_speed_gts": 16.0, "max_speed_gts": 16.0,
         "current_width": 16, "max_width": 16}
    assert mod._is_degraded(d) is False


# --- classify --------------------------------------------------

def _dev(*, cls, cs=16.0, ms=16.0, cw=4, mw=4, bdf="d"):
    return {"bdf": bdf, "class": cls,
            "current_speed_gts": cs, "max_speed_gts": ms,
            "current_width": cw, "max_width": mw}


def test_classify_unknown_no_devices():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_unknown_no_links():
    v = mod.classify(
        [_dev(cls="0x010802", cs=None, ms=None,
              cw=None, mw=None)])
    assert v["verdict"] == "unknown"


def test_classify_nvme_downgraded():
    v = mod.classify([
        _dev(cls="0x010802", cs=8.0, ms=16.0, bdf="nvme0"),
        _dev(cls="0x030000", cs=16.0, ms=16.0, bdf="gpu"),
    ])
    assert v["verdict"] == "nvme_link_downgraded"
    assert "nvme0" in v["devices"]


def test_classify_bridge_skipped():
    # PCI bridge (0x0604xx) advertises max wider than current —
    # normal bridge behavior, NOT a fault.
    v = mod.classify([
        _dev(cls="0x060400", cs=16.0, ms=16.0,
             cw=4, mw=32, bdf="bridge"),
    ])
    assert v["verdict"] == "links_at_max"


def test_classify_gpu_skipped_to_peripheral():
    # GPU degraded but ignored ; peripheral degraded fires
    v = mod.classify([
        _dev(cls="0x030000", cs=8.0, ms=16.0, bdf="gpu"),
        _dev(cls="0x020000", cs=2.5, ms=5.0, bdf="nic"),
    ])
    assert v["verdict"] == "peripheral_link_downgraded"


def test_classify_gpu_degraded_alone_is_ok():
    # GPU drift owned by pcie_width_watcher — we say ok
    v = mod.classify([
        _dev(cls="0x030000", cs=8.0, ms=16.0, bdf="gpu"),
    ])
    assert v["verdict"] == "links_at_max"


def test_classify_links_at_max():
    v = mod.classify([
        _dev(cls="0x010802", bdf="nvme0"),
        _dev(cls="0x020000", bdf="nic"),
        _dev(cls="0x030000", bdf="gpu"),
    ])
    assert v["verdict"] == "links_at_max"


# Priority : nvme > peripheral
def test_priority_nvme_over_peripheral():
    v = mod.classify([
        _dev(cls="0x010802", cs=8.0, ms=16.0, bdf="nvme0"),
        _dev(cls="0x020000", cs=2.5, ms=5.0, bdf="nic"),
    ])
    assert v["verdict"] == "nvme_link_downgraded"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_dev(tmp_path, "0000:01:00.0", cls="0x010802")
    out = mod.status(None, str(tmp_path / "pci"))
    assert out["verdict"]["verdict"] == "links_at_max"
    assert out["device_count"] == 1


def test_status_nvme_drift_synthetic(tmp_path):
    _mk_dev(tmp_path, "0000:01:00.0", cls="0x010802",
                 cur_speed="8.0 GT/s PCIe",
                 max_speed="16.0 GT/s PCIe")
    out = mod.status(None, str(tmp_path / "pci"))
    assert out["verdict"]["verdict"] == "nvme_link_downgraded"
    assert out["ok"] is False
