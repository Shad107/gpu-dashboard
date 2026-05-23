"""Tests for modules/binfmt_misc_audit.py — R&D #63.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import binfmt_misc_audit as mod


REG_PYTHON = """\
enabled
interpreter /usr/bin/python3.13
flags:\x20
offset 0
magic f30d0d0a
"""

REG_QEMU_AARCH64 = """\
enabled
interpreter /usr/bin/qemu-aarch64-static
flags: OCF
offset 0
magic 7f454c460201010000000000000000000200b700
"""


# --- parse_registration -----------------------------------------

def test_parse_python():
    out = mod.parse_registration(REG_PYTHON)
    assert out["enabled"] is True
    assert out["interpreter"] == "/usr/bin/python3.13"
    assert out["magic"] == "f30d0d0a"


def test_parse_qemu_aarch64():
    out = mod.parse_registration(REG_QEMU_AARCH64)
    assert out["interpreter"] == "/usr/bin/qemu-aarch64-static"
    assert "F" in (out["flags"] or "")


def test_parse_empty():
    assert mod.parse_registration("")["enabled"] is None
    assert mod.parse_registration(None)["enabled"] is None


# --- is_qemu_user -----------------------------------------------

def test_is_qemu_user_by_name():
    assert mod.is_qemu_user("qemu-aarch64", None) is True


def test_is_qemu_user_by_interp():
    assert mod.is_qemu_user(
        "arm-fmt", "/usr/bin/qemu-arm-static") is True


def test_is_qemu_user_no():
    assert mod.is_qemu_user("python3.13",
                               "/usr/bin/python3.13") is False


# --- list_registrations -----------------------------------------

def test_list_registrations(tmp_path):
    (tmp_path / "python3.13").write_text(REG_PYTHON)
    (tmp_path / "qemu-aarch64").write_text(REG_QEMU_AARCH64)
    (tmp_path / "status").write_text("enabled\n")
    (tmp_path / "register").write_text("")  # skip
    out = mod.list_registrations(str(tmp_path))
    names = sorted(r["name"] for r in out)
    assert names == ["python3.13", "qemu-aarch64"]


# --- classify ---------------------------------------------------

def _reg(name="python3.13", interpreter="/usr/bin/python3.13",
          enabled=True, flags="", magic="f30d0d0a"):
    return {"name": name, "enabled": enabled,
              "interpreter": interpreter, "flags": flags,
              "offset": 0, "magic": magic}


def test_classify_unknown():
    v = mod.classify(None, [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify("enabled", [_reg()])
    assert v["verdict"] == "ok"


def test_classify_qemu_stale(tmp_path):
    # interpreter path doesn't exist on disk
    fake_path = str(tmp_path / "missing-qemu")
    v = mod.classify(
        "enabled",
        [_reg(name="qemu-aarch64",
                interpreter=fake_path, flags="OCF")])
    assert v["verdict"] == "qemu_user_interp_stale"


def test_classify_duplicate():
    v = mod.classify(
        "enabled",
        [_reg(name="python3.13", magic="f30d0d0a"),
         _reg(name="python3.13-dup", magic="f30d0d0a")])
    assert v["verdict"] == "duplicate_registration"


def test_classify_globally_disabled_with_buildx(tmp_path):
    fake_qemu = tmp_path / "qemu-aarch64"
    fake_qemu.write_text("")  # exists
    v = mod.classify(
        "disabled",
        [_reg(name="qemu-aarch64",
                interpreter=str(fake_qemu),
                flags="OCF", magic="aaaa")])
    assert v["verdict"] == "globally_disabled_with_buildx"


def test_classify_priority_stale_wins(tmp_path):
    fake_path = str(tmp_path / "missing-qemu")
    v = mod.classify(
        "disabled",
        [_reg(name="qemu-aarch64",
                interpreter=fake_path, flags="OCF",
                magic="dead"),
         _reg(name="qemu-aarch64-dup",
                interpreter=fake_path, flags="OCF",
                magic="dead")])  # also a dup but stale wins
    assert v["verdict"] == "qemu_user_interp_stale"


# --- status integration -----------------------------------------

def test_status_absent(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    (tmp_path / "status").write_text("enabled\n")
    (tmp_path / "python3.13").write_text(REG_PYTHON)
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["registration_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
