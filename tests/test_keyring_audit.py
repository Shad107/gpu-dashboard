"""Tests for modules/keyring_audit.py — R&D #46.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import keyring_audit as mod


KEY_USERS_SAMPLE = """\
    0:   149 148/148 118/1000000 2627/25000000
 1000:     6 6/6 6/200 64/20000
"""

KEYS_SAMPLE = """\
06dd916f I--Q---     4 perm 1f3f0000  1000 65534 keyring   _uid.1000: empty
0b2242f1 I--Q---   281 perm 3f030000  1000  1000 keyring   _ses: 1
15748cb8 I--Q---     2 perm 3f030000  1000  1000 keyring   _ses: 1
"""


# --- parse_key_users ----------------------------------------------

def test_parse_key_users_basic():
    out = mod.parse_key_users(KEY_USERS_SAMPLE)
    assert len(out) == 2
    root = out[0]
    assert root["uid"] == 0
    assert root["keys"] == 118
    assert root["maxkeys"] == 1000000
    user = out[1]
    assert user["uid"] == 1000
    assert user["keys"] == 6
    assert user["maxkeys"] == 200
    assert user["bytes"] == 64
    assert user["maxbytes"] == 20000


def test_parse_key_users_empty():
    assert mod.parse_key_users("") == []


def test_parse_key_users_skips_garbage():
    assert mod.parse_key_users("garbage\n") == []


# --- parse_keys ----------------------------------------------------

def test_parse_keys_basic():
    out = mod.parse_keys(KEYS_SAMPLE)
    assert len(out) == 3
    assert out[0]["type"] == "keyring"
    assert out[0]["uid"] == 1000
    assert "_ses" in out[1]["desc"] or out[1]["type"] == "keyring"


def test_parse_keys_empty():
    assert mod.parse_keys("") == []


# --- count_by_type (R&D #111.3) -----------------------------------

def test_count_by_type_empty():
    assert mod.count_by_type([]) == {}


def test_count_by_type_aggregates():
    keys = [
        {"uid": 0, "type": "asymmetric", "desc": "modsign"},
        {"uid": 0, "type": "asymmetric", "desc": "imakeys"},
        {"uid": 1000, "type": "logon", "desc": "nfs"},
        {"uid": 1000, "type": "keyring", "desc": "_ses"},
        {"uid": 1000, "type": "keyring", "desc": "_uid"},
        {"uid": 1000, "type": "user", "desc": "cred"}]
    counts = mod.count_by_type(keys)
    assert counts == {"asymmetric": 2, "logon": 1,
                       "keyring": 2, "user": 1}


def test_count_by_type_skips_missing_type():
    keys = [{"uid": 0, "desc": "x"},
              {"uid": 0, "type": "logon", "desc": "y"}]
    assert mod.count_by_type(keys) == {"logon": 1}


# --- classify ------------------------------------------------------

def _user(uid=1000, keys=10, maxkeys=200, bytes_=100, maxbytes=20000):
    return {"uid": uid, "total": keys, "used": keys, "refs": keys,
              "keys": keys, "maxkeys": maxkeys,
              "bytes": bytes_, "maxbytes": maxbytes}


def test_classify_unknown_when_empty():
    v = mod.classify([], [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_user(keys=10, maxkeys=200)], [])
    assert v["verdict"] == "ok"


def test_classify_keys_quota_approaching():
    v = mod.classify([_user(keys=180, maxkeys=200)], [])
    assert v["verdict"] == "uid_quota_approaching"
    assert "180/200" in v["reason"]


def test_classify_bytes_quota_approaching():
    v = mod.classify([_user(keys=5, maxkeys=200,
                              bytes_=18000, maxbytes=20000)], [])
    assert v["verdict"] == "uid_quota_approaching"


def test_classify_quota_skipped_below_threshold():
    v = mod.classify([_user(keys=100, maxkeys=200)], [])
    assert v["verdict"] == "ok"


def test_classify_many_session_keyrings():
    keys = [{"uid": 1000, "type": "keyring",
              "desc": f"_ses: {i}"} for i in range(60)]
    v = mod.classify([_user(keys=10)], keys)
    assert v["verdict"] == "many_session_keyrings"


def test_classify_session_skipped_when_few():
    keys = [{"uid": 1000, "type": "keyring",
              "desc": f"_ses: {i}"} for i in range(10)]
    v = mod.classify([_user(keys=10)], keys)
    assert v["verdict"] == "ok"


def test_classify_quota_wins_over_session():
    keys = [{"uid": 1000, "type": "keyring",
              "desc": f"_ses: {i}"} for i in range(60)]
    v = mod.classify([_user(keys=180, maxkeys=200)], keys)
    assert v["verdict"] == "uid_quota_approaching"


# --- status integration -------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    (tmp_path / "key-users").write_text(KEY_USERS_SAMPLE)
    (tmp_path / "keys").write_text(KEYS_SAMPLE)
    monkeypatch.setattr(mod, "_PROC_KEY_USERS",
                        str(tmp_path / "key-users"))
    monkeypatch.setattr(mod, "_PROC_KEYS", str(tmp_path / "keys"))
    out = mod.status()
    assert out["ok"] is True
    assert out["user_count"] == 2
    assert out["verdict"]["verdict"] == "ok"
    # R&D #111.3 — type_counts surfaced from /proc/keys.
    assert out["type_counts"] == {"keyring": 3}


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_KEY_USERS",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_PROC_KEYS",
                        str(tmp_path / "nope2"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
