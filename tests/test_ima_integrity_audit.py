"""Tests for modules/ima_integrity_audit.py — R&D #53.3."""
from __future__ import annotations

import struct
import pytest

from gpu_dashboard.modules import ima_integrity_audit as mod


def _mk_ima(root, *, count="42", violations="0", policy="",
              dir_name="ima"):
    d = root / dir_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "runtime_measurements_count").write_text(count + "\n")
    (d / "violations").write_text(violations + "\n")
    (d / "policy").write_text(policy)
    return d


def _mk_evm(root, value):
    (root / "evm").write_text(str(value) + "\n")


def _mk_secureboot(root, enabled):
    root.mkdir(parents=True, exist_ok=True)
    # EFI variable layout : 4-byte attribute prefix + 1 value byte.
    p = root / "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    p.write_bytes(struct.pack("<I", 0x7) + bytes([1 if enabled else 0]))


# --- read_ima ---------------------------------------------------

def test_read_ima_missing(tmp_path):
    out = mod.read_ima(str(tmp_path))
    assert out == {"available": False}


def test_read_ima_basic(tmp_path):
    _mk_ima(tmp_path, count="100", violations="0",
              policy="measure func=BPRM_CHECK\n")
    out = mod.read_ima(str(tmp_path))
    assert out["available"] is True
    assert out["runtime_measurements_count"] == 100
    assert out["violations"] == 0
    assert out["policy_readable"] is True
    assert out["policy_lines"] >= 1


def test_read_ima_empty_policy(tmp_path):
    _mk_ima(tmp_path, policy="")
    out = mod.read_ima(str(tmp_path))
    assert out["policy_lines"] == 0


# --- read_evm ---------------------------------------------------

def test_read_evm_missing(tmp_path):
    assert mod.read_evm(str(tmp_path)) == {"available": False}


def test_read_evm_armed(tmp_path):
    _mk_evm(tmp_path, "1")
    out = mod.read_evm(str(tmp_path))
    assert out["available"] is True
    assert out["armed"] is True


def test_read_evm_disarmed(tmp_path):
    _mk_evm(tmp_path, "0")
    out = mod.read_evm(str(tmp_path))
    assert out["armed"] is False


# --- read_secureboot --------------------------------------------

def test_read_secureboot_missing(tmp_path):
    out = mod.read_secureboot(str(tmp_path / "nope"))
    assert out == {"present": False, "enabled": None}


def test_read_secureboot_disabled(tmp_path):
    _mk_secureboot(tmp_path, enabled=False)
    out = mod.read_secureboot(str(tmp_path))
    assert out["present"] is True
    assert out["enabled"] is False


def test_read_secureboot_enabled(tmp_path):
    _mk_secureboot(tmp_path, enabled=True)
    out = mod.read_secureboot(str(tmp_path))
    assert out["enabled"] is True


# --- classify ---------------------------------------------------

def _ima(available=True, count=10, violations=0,
          policy_readable=True, policy_lines=1,
          permission_denied=False):
    return {"available": available,
              "runtime_measurements_count": count,
              "violations": violations,
              "policy_readable": policy_readable,
              "policy_lines": policy_lines,
              "permission_denied": permission_denied}


def _evm(available=True, armed=True):
    return {"available": available, "armed": armed,
              "raw": "1" if armed else "0",
              "permission_denied": False}


def _sb(present=True, enabled=True):
    return {"present": present, "enabled": enabled}


def test_classify_unknown():
    v = mod.classify({"available": False},
                       {"available": False},
                       {"present": False, "enabled": None})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_ima(), _evm(), _sb(enabled=True))
    assert v["verdict"] == "ok"


def test_classify_evm_disabled_sb_on():
    v = mod.classify(_ima(), _evm(armed=False), _sb(enabled=True))
    assert v["verdict"] == "evm_disabled_secureboot_on"


def test_classify_violations():
    v = mod.classify(_ima(violations=3), _evm(), _sb())
    assert v["verdict"] == "ima_violations_nonzero"
    assert "3" in v["reason"]


def test_classify_no_policy():
    v = mod.classify(_ima(policy_lines=0), _evm(), _sb(enabled=False))
    assert v["verdict"] == "ima_no_policy_loaded"


def test_classify_stagnant():
    v = mod.classify(_ima(count=0), _evm(), _sb(enabled=False))
    assert v["verdict"] == "measurement_log_stagnant"


def test_classify_requires_root():
    v = mod.classify(_ima(count=None, policy_readable=False,
                            policy_lines=None,
                            permission_denied=True),
                       _evm(), _sb(enabled=False))
    assert v["verdict"] == "requires_root"


def test_classify_priority_evm_wins():
    v = mod.classify(_ima(violations=3), _evm(armed=False),
                       _sb(enabled=True))
    assert v["verdict"] == "evm_disabled_secureboot_on"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nosec"),
                       str(tmp_path / "noefi"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_secureboot_with_evm_off(tmp_path):
    sec = tmp_path / "sec"
    _mk_ima(sec, count="42")
    _mk_evm(sec, "0")
    efi = tmp_path / "efi"
    _mk_secureboot(efi, enabled=True)
    out = mod.status(None, str(sec), str(efi))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "evm_disabled_secureboot_on"
