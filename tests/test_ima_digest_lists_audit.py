"""Tests for modules/ima_digest_lists_audit.py R&D #105.1."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import ima_digest_lists_audit as mod


# --- policy_enforces_appraise ---------------------------------

def test_policy_no_appraise():
    text = (
        "measure func=BPRM_CHECK\n"
        "measure func=FILE_MMAP mask=MAY_EXEC\n")
    assert mod.policy_enforces_appraise(text) is False


def test_policy_with_appraise():
    text = (
        "appraise func=BPRM_CHECK\n"
        "measure func=FILE_MMAP\n")
    assert mod.policy_enforces_appraise(text) is True


def test_policy_empty():
    assert mod.policy_enforces_appraise("") is False
    assert mod.policy_enforces_appraise(None) is False


# --- scan_digest_lists_perms -----------------------------------

def test_scan_perms_missing(tmp_path):
    out = mod.scan_digest_lists_perms(
        str(tmp_path / "nope"))
    assert out["count"] == 0


def test_scan_perms_mixed(tmp_path):
    d = tmp_path / "digest_lists"
    d.mkdir()
    f1 = d / "a.list"
    f1.write_text("")
    os.chmod(f1, 0o644)
    f2 = d / "b.list"
    f2.write_text("")
    os.chmod(f2, 0o600)
    out = mod.scan_digest_lists_perms(str(d))
    assert out["count"] == 2
    assert out["mode_644"] == 1
    assert out["mode_600"] == 1


# --- classify --------------------------------------------------

def _perms(*, count=0, m644=0, m600=0, other=0):
    return {"count": count, "mode_644": m644,
            "mode_600": m600, "other": other}


def test_classify_unknown_no_integrity():
    v = mod.classify(False, None, False, False, _perms())
    assert v["verdict"] == "unknown"


def test_classify_unknown_no_digest_lists_file():
    v = mod.classify(True, None, False, False, _perms())
    assert v["verdict"] == "unknown"


def test_classify_ok_with_lists():
    v = mod.classify(
        True, 5, True, True, _perms(count=5, m600=5))
    assert v["verdict"] == "ok"


def test_classify_appraise_no_lists_err():
    v = mod.classify(True, 0, True, True, _perms())
    assert v["verdict"] == "appraisal_no_digest_lists"


def test_classify_perm_drift_accent():
    v = mod.classify(
        True, 4, False, False,
        _perms(count=4, m644=2, m600=2))
    assert v["verdict"] == "digest_lists_world_readable_drift"


def test_classify_absent_evm_off_accent():
    v = mod.classify(True, 0, False, False, _perms())
    assert v["verdict"] == "digest_lists_absent_evm_off"


# Priority : appraise_no_lists > perm_drift > absent_evm_off
def test_priority_appraise_over_drift():
    v = mod.classify(True, 0, True, True,
                          _perms(count=4, m644=2, m600=2))
    assert v["verdict"] == "appraisal_no_digest_lists"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_integrity"),
                       str(tmp_path / "no_policy"),
                       str(tmp_path / "no_evm"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_with_lists(tmp_path):
    d = tmp_path / "integrity"
    d.mkdir()
    (d / "digest_lists_loaded").write_text("5\n")
    dl = d / "digest_lists"
    dl.mkdir()
    for n in ("a.list", "b.list"):
        f = dl / n
        f.write_text("")
        os.chmod(f, 0o600)
    policy = tmp_path / "policy"
    policy.write_text("appraise func=BPRM_CHECK\n")
    evm = tmp_path / "evm"
    evm.write_text("3\n")
    out = mod.status(None, str(d), str(policy), str(evm))
    assert out["verdict"]["verdict"] == "ok"
    assert out["digest_lists_loaded"] == 5
