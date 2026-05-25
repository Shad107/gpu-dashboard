"""Tests for modules/numa_demotion_enabled_audit.py R&D #109.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import numa_demotion_enabled_audit as mod


def test_parse_bool():
    assert mod._parse_bool("true") is True
    assert mod._parse_bool("false") is False
    assert mod._parse_bool("1") is True
    assert mod._parse_bool("0") is False
    assert mod._parse_bool(None) is None
    assert mod._parse_bool("garbage") is None


def test_is_multi_node():
    assert mod.is_multi_node("0\n") is False
    assert mod.is_multi_node("0-1\n") is True
    assert mod.is_multi_node("0,2") is True
    assert mod.is_multi_node("") is False


def test_classify_unknown():
    v = mod.classify(False, None, False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, None, False)
    assert v["verdict"] == "requires_root"


def test_classify_demotion_off_multi_warn():
    v = mod.classify(True, False, True)
    assert v["verdict"] == "demotion_off_with_tiered_memory"


def test_classify_demotion_on_single_accent():
    v = mod.classify(True, True, False)
    assert v["verdict"] == "demotion_on_single_node"


def test_classify_ok_single_off():
    v = mod.classify(True, False, False)
    assert v["verdict"] == "ok"


def test_classify_ok_multi_on():
    v = mod.classify(True, True, True)
    assert v["verdict"] == "ok"


def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope"),
                       str(tmp_path / "no_node"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_single_off(tmp_path):
    d = tmp_path / "demotion_enabled"
    d.write_text("false\n")
    n = tmp_path / "online"
    n.write_text("0\n")
    out = mod.status(None, str(d), str(n))
    assert out["verdict"]["verdict"] == "ok"
    assert out["multi_node"] is False


def test_status_multi_off_warn(tmp_path):
    d = tmp_path / "demotion_enabled"
    d.write_text("false\n")
    n = tmp_path / "online"
    n.write_text("0-1\n")
    out = mod.status(None, str(d), str(n))
    assert (out["verdict"]["verdict"]
            == "demotion_off_with_tiered_memory")
