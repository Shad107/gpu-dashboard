"""Tests for modules/limits_audit.py — PAM limits memlock audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import limits_audit


def _mk_limits(root: Path, *, conf_text: str = "",
                  d_files: dict | None = None):
    root.mkdir(parents=True, exist_ok=True)
    if conf_text:
        (root / "limits.conf").write_text(conf_text)
    if d_files:
        d_dir = root / "limits.d"
        d_dir.mkdir(exist_ok=True)
        for name, text in d_files.items():
            (d_dir / name).write_text(text)


# --- parse_limits_line --------------------------------------------

def test_parse_limits_line_basic():
    rec = limits_audit.parse_limits_line("*       hard    memlock    unlimited")
    assert rec["domain"] == "*"
    assert rec["type"] == "hard"
    assert rec["item"] == "memlock"
    assert rec["value"] == "unlimited"


def test_parse_limits_line_numeric():
    rec = limits_audit.parse_limits_line("user1   soft    memlock    65536")
    assert rec["value"] == "65536"


def test_parse_limits_line_comment_returns_none():
    assert limits_audit.parse_limits_line("# a comment") is None


def test_parse_limits_line_blank_returns_none():
    assert limits_audit.parse_limits_line("   ") is None
    assert limits_audit.parse_limits_line("") is None


def test_parse_limits_line_malformed_returns_none():
    assert limits_audit.parse_limits_line("garbage line") is None


def test_parse_limits_file_multiline():
    text = (
        "# This is a comment\n"
        "\n"
        "*  hard  nofile  4096\n"
        "@pipewire - rtprio 95\n"
        "* hard memlock unlimited\n"
    )
    rules = limits_audit.parse_limits_file(text)
    assert len(rules) == 3
    items = [r["item"] for r in rules]
    assert "memlock" in items
    assert "nofile" in items
    assert "rtprio" in items


# --- collect_memlock_rules ----------------------------------------

def test_collect_memlock_filters_only_memlock(tmp_path):
    _mk_limits(tmp_path,
               conf_text="* hard nofile 4096\n* hard memlock unlimited\n",
               d_files={"99-llm.conf": "user1 soft memlock 8589934592\n"})
    rules = limits_audit.collect_memlock_rules(str(tmp_path))
    items = [r["item"] for r in rules]
    assert items == ["memlock", "memlock"]
    domains = sorted(r["domain"] for r in rules)
    assert "*" in domains
    assert "user1" in domains


def test_collect_memlock_no_dir(tmp_path):
    assert limits_audit.collect_memlock_rules(str(tmp_path / "absent")) == []


def test_collect_memlock_no_rules(tmp_path):
    _mk_limits(tmp_path, conf_text="* hard nofile 4096\n")
    assert limits_audit.collect_memlock_rules(str(tmp_path)) == []


# --- value_to_bytes ----------------------------------------------

def test_value_to_bytes_unlimited():
    assert limits_audit.value_to_bytes("unlimited") == limits_audit.INFINITY


def test_value_to_bytes_numeric_kib():
    # PAM limits are in KiB by convention for memlock
    assert limits_audit.value_to_bytes("65536") == 65536 * 1024


def test_value_to_bytes_garbage():
    assert limits_audit.value_to_bytes("nope") is None


# --- classify ----------------------------------------------------

def test_classify_has_unlimited():
    rules = [{"domain": "*", "type": "hard", "item": "memlock",
              "value": "unlimited"}]
    v = limits_audit.classify(rules)
    assert v["verdict"] == "unlimited"


def test_classify_high_explicit():
    # explicit 8 GiB → ok
    rules = [{"domain": "*", "type": "hard", "item": "memlock",
              "value": str(8 * 1024 * 1024)}]   # 8 GiB in KiB
    v = limits_audit.classify(rules)
    assert v["verdict"] == "explicit_high"


def test_classify_low_explicit():
    # 8 MiB in KiB → warn
    rules = [{"domain": "*", "type": "hard", "item": "memlock",
              "value": "8192"}]
    v = limits_audit.classify(rules)
    assert v["verdict"] == "explicit_low"


def test_classify_default_when_no_rules():
    # No memlock rules → falls back to systemd default (typically 8 MiB)
    v = limits_audit.classify([])
    assert v["verdict"] == "default"


def test_classify_recipe_drops_99_llm_conf():
    v = limits_audit.classify([])
    assert "99-llm.conf" in v["recommendation"]
    assert "memlock" in v["recommendation"]


def test_classify_picks_hard_over_soft():
    # When both soft and hard memlock are set, hard is the cap
    rules = [
        {"domain": "*", "type": "soft", "item": "memlock", "value": "65536"},
        {"domain": "*", "type": "hard", "item": "memlock", "value": "unlimited"},
    ]
    v = limits_audit.classify(rules)
    assert v["verdict"] == "unlimited"


# --- status ----------------------------------------------------

def test_status_unconfigured(tmp_path, monkeypatch):
    # The live-rig case: limits.d exists but no memlock rule
    _mk_limits(tmp_path,
               conf_text="",
               d_files={
                   "10-coredump.conf": "* soft core 0\n* hard core infinity\n",
                   "25-pw-rlimits.conf": "# PipeWire RT\n",
               })
    monkeypatch.setattr(limits_audit, "_LIMITS_ROOT", str(tmp_path))
    s = limits_audit.status()
    assert s["ok"] is True
    assert s["memlock_rules"] == []
    assert s["verdict"]["verdict"] == "default"


def test_status_with_unlimited(tmp_path, monkeypatch):
    _mk_limits(tmp_path,
               conf_text="* hard memlock unlimited\n")
    monkeypatch.setattr(limits_audit, "_LIMITS_ROOT", str(tmp_path))
    s = limits_audit.status()
    assert len(s["memlock_rules"]) == 1
    assert s["verdict"]["verdict"] == "unlimited"


def test_status_no_limits_root(tmp_path, monkeypatch):
    monkeypatch.setattr(limits_audit, "_LIMITS_ROOT",
                          str(tmp_path / "absent"))
    s = limits_audit.status()
    assert s["ok"] is False
    assert s["error"] == "limits_unavailable"


def test_status_lists_loaded_files(tmp_path, monkeypatch):
    _mk_limits(tmp_path,
               conf_text="* hard memlock unlimited\n",
               d_files={"99-llm.conf": "user1 soft memlock 65536\n"})
    monkeypatch.setattr(limits_audit, "_LIMITS_ROOT", str(tmp_path))
    s = limits_audit.status()
    assert "limits.conf" in s["files"]
    assert "limits.d/99-llm.conf" in s["files"]
