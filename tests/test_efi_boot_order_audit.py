"""Tests for modules/efi_boot_order_audit.py — R&D #55.4."""
from __future__ import annotations

import struct
import pytest

from gpu_dashboard.modules import efi_boot_order_audit as mod


_GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"


def _mk_var(root, name_with_guid, payload_bytes):
    root.mkdir(parents=True, exist_ok=True)
    # EFI variable layout : 4-byte attribute prefix + payload
    p = root / name_with_guid
    p.write_bytes(struct.pack("<I", 0x7) + payload_bytes)
    return p


def _mk_efi(root, *, boot_current=0x0007, boot_order=None,
             boot_next=None, secureboot=False, dbx=False,
             extra_boot_entries=None):
    root.mkdir(parents=True, exist_ok=True)
    _mk_var(root, f"BootCurrent-{_GLOBAL}",
              struct.pack("<H", boot_current))
    if boot_order is not None:
        _mk_var(root, f"BootOrder-{_GLOBAL}",
                  b"".join(struct.pack("<H", v) for v in boot_order))
    if boot_next is not None:
        _mk_var(root, f"BootNext-{_GLOBAL}",
                  struct.pack("<H", boot_next))
    _mk_var(root, f"SecureBoot-{_GLOBAL}",
              bytes([1 if secureboot else 0]))
    if dbx:
        _mk_var(root, f"dbx-{_GLOBAL}", b"\x00" * 16)
    for h in (extra_boot_entries or []):
        _mk_var(root, f"Boot{h:04X}-{_GLOBAL}", b"\x00" * 64)


# --- _read_uint16 -----------------------------------------------

def test_read_uint16(tmp_path):
    p = _mk_var(tmp_path, f"BootCurrent-{_GLOBAL}",
                  struct.pack("<H", 0x0007))
    assert mod._read_uint16(str(p)) == 0x0007


def test_read_uint16_short(tmp_path):
    p = tmp_path / "x"
    p.write_bytes(b"\x07\x00\x00")  # too short
    assert mod._read_uint16(str(p)) is None


def test_read_uint16_array(tmp_path):
    p = _mk_var(tmp_path, f"BootOrder-{_GLOBAL}",
                  b"".join(struct.pack("<H", v)
                              for v in [0x0007, 0x0002, 0x0001]))
    assert mod._read_uint16_array(str(p)) == [7, 2, 1]


def test_read_uint16_array_missing(tmp_path):
    assert mod._read_uint16_array(str(tmp_path / "nope")) == []


# --- list_boot_entries ------------------------------------------

def test_list_boot_entries_missing(tmp_path):
    assert mod.list_boot_entries(str(tmp_path / "nope")) == []


def test_list_boot_entries(tmp_path):
    _mk_var(tmp_path, f"Boot0000-{_GLOBAL}", b"\x00")
    _mk_var(tmp_path, f"Boot000A-{_GLOBAL}", b"\x00")
    _mk_var(tmp_path, f"BootOrder-{_GLOBAL}", b"\x00\x00")
    out = mod.list_boot_entries(str(tmp_path))
    assert out == [0, 10]


# --- read_state -------------------------------------------------

def test_read_state_missing(tmp_path):
    out = mod.read_state(str(tmp_path / "nope"))
    assert out == {"present": False}


def test_read_state_basic(tmp_path):
    _mk_efi(tmp_path, boot_current=7,
              boot_order=[7, 2, 1, 3],
              secureboot=False, dbx=False)
    out = mod.read_state(str(tmp_path))
    assert out["present"] is True
    assert out["BootCurrent"] == 7
    assert out["BootOrder"] == [7, 2, 1, 3]
    assert out["BootNext"] is None
    assert out["SecureBoot"] is False
    assert out["dbx_present"] is False


def test_read_state_with_bootnext(tmp_path):
    _mk_efi(tmp_path, boot_next=5)
    out = mod.read_state(str(tmp_path))
    assert out["BootNext"] == 5


# --- classify ---------------------------------------------------

def _state(present=True, boot_current=7, boot_order=None,
            boot_next=None, secureboot=False, dbx_present=False,
            varstore_total_bytes=2000):
    return {"present": present,
              "BootCurrent": boot_current,
              "BootOrder": boot_order or [7, 2, 1, 3],
              "BootNext": boot_next,
              "BootEntries": [0, 1, 2, 3, 4, 5, 6, 7],
              "SecureBoot": secureboot,
              "dbx_present": dbx_present,
              "varstore_total_bytes": varstore_total_bytes}


def test_classify_unknown():
    v = mod.classify({"present": False})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_state())
    assert v["verdict"] == "ok"


def test_classify_bootnext_pinned():
    v = mod.classify(_state(boot_next=5))
    assert v["verdict"] == "bootnext_pinned"


def test_classify_varstore_full():
    v = mod.classify(_state(varstore_total_bytes=40000))
    assert v["verdict"] == "varstore_near_full"


def test_classify_dbx_absent_with_secureboot():
    v = mod.classify(_state(secureboot=True, dbx_present=False))
    assert v["verdict"] == "dbx_absent_with_secureboot"


def test_classify_dbx_present_with_secureboot():
    v = mod.classify(_state(secureboot=True, dbx_present=True))
    assert v["verdict"] == "ok"


def test_classify_bootorder_drift():
    v = mod.classify(_state(boot_current=2,
                                boot_order=[7, 2, 1, 3]))
    assert v["verdict"] == "bootorder_drift"


def test_classify_priority_bootnext_wins():
    v = mod.classify(_state(boot_next=5,
                                varstore_total_bytes=40000))
    assert v["verdict"] == "bootnext_pinned"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    efi = tmp_path / "efi"
    _mk_efi(efi, boot_current=7, boot_order=[7, 2, 1, 3],
              secureboot=False)
    out = mod.status(None, str(efi))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"
    assert out["BootCurrent"] == 7
