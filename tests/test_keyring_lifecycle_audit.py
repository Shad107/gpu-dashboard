"""Tests for modules/keyring_lifecycle_audit.py R&D #98.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import keyring_lifecycle_audit as mod


# --- parse_etc_passwd_uids -------------------------------------

def test_parse_passwd_empty():
    assert mod.parse_etc_passwd_uids("") == set()


def test_parse_passwd_basic():
    text = (
        "root:x:0:0:root:/root:/bin/bash\n"
        "olivier:x:1000:1000:Olivier:/home/olivier:/bin/bash\n"
        "nobody:x:65534:65534:nobody:/nonexistent:/bin/false\n")
    out = mod.parse_etc_passwd_uids(text)
    assert out == {0, 1000, 65534}


# --- parse_proc_keys_uids --------------------------------------

def test_parse_keys_empty():
    assert mod.parse_proc_keys_uids("") == set()


def test_parse_keys_basic():
    text = (
        "0c1433b1 I--Q---   265 perm 3f030000  1000  1000 "
        "keyring   _ses: 1\n"
        "2d6f1492 I--Q---     4 perm 1f3f0000     0 65534 "
        "keyring   _uid.0: empty\n")
    out = mod.parse_proc_keys_uids(text)
    assert out == {1000, 0}


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, None, set(), set(), False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, 300, 259200, set(), set(), False)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, 300, 259200,
                          {0, 1000}, {0, 1000, 65534}, True)
    assert v["verdict"] == "ok"


def test_classify_ns_leak():
    # uid 5000 owns a keyring but isn't in /etc/passwd
    v = mod.classify(True, 300, 259200,
                          {1000, 5000}, {1000}, True)
    assert v["verdict"] == "ns_leak_unknown_uid"


def test_classify_ns_leak_ignores_nobody():
    # 65534 (nobody) is excluded — NSS-only uid is fine
    v = mod.classify(True, 300, 259200,
                          {1000, 65534}, {1000}, True)
    assert v["verdict"] == "ok"


def test_classify_ns_leak_ignores_system_uids():
    # uids < 1000 (system) are ignored
    v = mod.classify(True, 300, 259200,
                          {1000, 102}, {1000}, True)
    assert v["verdict"] == "ok"


def test_classify_persistent_no_expiry():
    v = mod.classify(True, 300, 0,
                          {1000}, {1000}, True)
    assert v["verdict"] == "persistent_keyring_no_expiry"


def test_classify_gc_delay_too_high():
    v = mod.classify(True, 7200, 259200,
                          {1000}, {1000}, True)
    assert v["verdict"] == "gc_delay_too_high"


# Priority : ns_leak > no_expiry > gc_delay
def test_priority_ns_leak_over_no_expiry():
    v = mod.classify(True, 300, 0,
                          {1000, 5000}, {1000}, True)
    assert v["verdict"] == "ns_leak_unknown_uid"


def test_priority_no_expiry_over_gc_delay():
    v = mod.classify(True, 7200, 0,
                          {1000}, {1000}, True)
    assert v["verdict"] == "persistent_keyring_no_expiry"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_keys"),
                       str(tmp_path / "no_proc_keys"),
                       str(tmp_path / "no_passwd"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "keys"
    d.mkdir()
    (d / "gc_delay").write_text("300\n")
    (d / "persistent_keyring_expiry").write_text("259200\n")
    pk = tmp_path / "proc_keys"
    pk.write_text(
        "abc I--Q--- 1 perm 0  1000  1000 keyring _ses: 1\n")
    passwd = tmp_path / "passwd"
    passwd.write_text("olivier:x:1000:1000::/:/bin/bash\n")
    out = mod.status(None, str(d), str(pk), str(passwd))
    assert out["verdict"]["verdict"] == "ok"
    assert out["gc_delay"] == 300
    assert out["persistent_keyring_expiry"] == 259200
