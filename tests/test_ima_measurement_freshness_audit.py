"""Tests for modules/ima_measurement_freshness_audit.py R&D #104.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import ima_measurement_freshness_audit as mod


# --- parse_log -------------------------------------------------

def test_parse_log_empty():
    out = mod.parse_log("")
    assert out == {"line_count": 0,
                    "has_boot_aggregate": False}


def test_parse_log_with_boot_aggregate():
    text = (
        "10 ABCD0001 ima-ng sha256:1111 boot_aggregate\n"
        "10 ABCD0002 ima-ng sha256:2222 /etc/passwd\n"
        "10 ABCD0003 ima-ng sha256:3333 /usr/bin/sshd\n")
    out = mod.parse_log(text)
    assert out["line_count"] == 3
    assert out["has_boot_aggregate"] is True


def test_parse_log_no_boot_aggregate():
    text = (
        "10 ABCD0001 ima-ng sha256:1111 /etc/passwd\n"
        "10 ABCD0002 ima-ng sha256:2222 /usr/bin/sshd\n")
    out = mod.parse_log(text)
    assert out["line_count"] == 2
    assert out["has_boot_aggregate"] is False


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(False, None, False, {})
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, 100, False, {})
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(
        True, 100, True,
        {"line_count": 100,
         "has_boot_aggregate": True})
    assert v["verdict"] == "ok"


def test_classify_log_missing_err():
    v = mod.classify(
        True, 100, True,
        {"line_count": 0,
         "has_boot_aggregate": False})
    assert v["verdict"] == "ima_log_missing"


def test_classify_no_boot_aggregate_warn():
    v = mod.classify(
        True, 50, True,
        {"line_count": 50,
         "has_boot_aggregate": False})
    assert v["verdict"] == "ima_boot_aggregate_absent"


def test_classify_empty_accent():
    v = mod.classify(
        True, 0, True,
        {"line_count": 0,
         "has_boot_aggregate": False})
    assert v["verdict"] == "ima_log_empty"


# Priority : missing > no_anchor > empty
def test_priority_missing_over_no_anchor():
    v = mod.classify(
        True, 100, True,
        {"line_count": 0,
         "has_boot_aggregate": False})
    assert v["verdict"] == "ima_log_missing"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    d = tmp_path / "ima"
    d.mkdir()
    (d / "runtime_measurements_count").write_text("3\n")
    (d / "ascii_runtime_measurements").write_text(
        "10 ABCD ima-ng sha256:11 boot_aggregate\n"
        "10 ABCD ima-ng sha256:22 /etc/passwd\n"
        "10 ABCD ima-ng sha256:33 /usr/bin/sshd\n")
    out = mod.status(None, str(d))
    assert out["verdict"]["verdict"] == "ok"
    assert out["log_line_count"] == 3
    assert out["has_boot_aggregate"] is True


def test_status_missing_boot_aggregate(tmp_path):
    d = tmp_path / "ima"
    d.mkdir()
    (d / "runtime_measurements_count").write_text("2\n")
    (d / "ascii_runtime_measurements").write_text(
        "10 ABCD ima-ng sha256:11 /etc/passwd\n"
        "10 ABCD ima-ng sha256:22 /usr/bin/sshd\n")
    out = mod.status(None, str(d))
    assert (out["verdict"]["verdict"]
            == "ima_boot_aggregate_absent")
