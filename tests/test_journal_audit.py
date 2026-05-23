"""Tests for modules/journal_audit.py — R&D #48.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import journal_audit as mod


CONF_SAMPLE = """\
# Comment header
[Journal]
Storage=persistent
RateLimitIntervalSec=30s
RateLimitBurst=10000
SystemMaxUse=2G
SystemKeepFree=200M
"""

CONF_RISKY = """\
[Journal]
RateLimitBurst=50
"""


# --- parse_journald_conf ----------------------------------------

def test_parse_journald_conf_basic():
    c = mod.parse_journald_conf(CONF_SAMPLE)
    assert c["Storage"] == "persistent"
    assert c["RateLimitBurst"] == "10000"
    assert c["SystemMaxUse"] == "2G"


def test_parse_journald_conf_skips_comments():
    txt = "# Comment\n[Journal]\n#Storage=auto\nStorage=none\n"
    c = mod.parse_journald_conf(txt)
    assert c["Storage"] == "none"


def test_parse_journald_conf_skips_non_journal_sections():
    txt = "[Service]\nKey=value\n[Journal]\nStorage=auto\n"
    c = mod.parse_journald_conf(txt)
    assert c == {"Storage": "auto"}


def test_parse_journald_conf_empty():
    assert mod.parse_journald_conf("") == {}
    assert mod.parse_journald_conf(None) == {}


# --- merge_conf -------------------------------------------------

def test_merge_conf_drop_in_overrides(tmp_path):
    main = tmp_path / "journald.conf"
    main.write_text("[Journal]\nStorage=auto\n")
    conf_d = tmp_path / "journald.conf.d"
    conf_d.mkdir()
    (conf_d / "99-override.conf").write_text(
        "[Journal]\nStorage=persistent\n")
    out = mod.merge_conf(str(main), str(conf_d))
    assert out["Storage"] == "persistent"


# --- parse_size_value -------------------------------------------

def test_parse_size_value():
    assert mod.parse_size_value("100") == 100
    assert mod.parse_size_value("2K") == 2048
    assert mod.parse_size_value("2M") == 2 * 1024**2
    assert mod.parse_size_value("4G") == 4 * 1024**3
    assert mod.parse_size_value("garbage") is None
    assert mod.parse_size_value(None) is None


def test_parse_interval_sec():
    assert mod._parse_interval_sec("30s") == 30
    assert mod._parse_interval_sec("5min") == 300
    assert mod._parse_interval_sec("1h") == 3600
    assert mod._parse_interval_sec("100") == 100
    assert mod._parse_interval_sec(None) is None


# --- dir_size_bytes ---------------------------------------------

def test_dir_size_bytes(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "f1").write_bytes(b"x" * 1000)
    (sub / "f2").write_bytes(b"x" * 2000)
    assert mod.dir_size_bytes(str(tmp_path)) == 3000


def test_dir_size_bytes_missing(tmp_path):
    assert mod.dir_size_bytes(str(tmp_path / "nope")) == 0


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify({}, 0, persistent_dir_exists=False)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify({"Storage": "persistent",
                       "RateLimitBurst": "10000",
                       "SystemMaxUse": "2G"},
                       1 * 1024**3,
                       persistent_dir_exists=True)
    assert v["verdict"] == "ok"


def test_classify_storage_disabled():
    v = mod.classify({"Storage": "none"}, 0,
                       persistent_dir_exists=True)
    assert v["verdict"] == "storage_disabled"


def test_classify_rate_limit_risky_low_burst():
    v = mod.classify({"RateLimitBurst": "50"}, 0,
                       persistent_dir_exists=True)
    assert v["verdict"] == "rate_limit_risky"


def test_classify_rate_limit_risky_long_interval():
    v = mod.classify({"RateLimitIntervalSec": "5min"}, 0,
                       persistent_dir_exists=True)
    assert v["verdict"] == "rate_limit_risky"


def test_classify_oversized():
    v = mod.classify({}, 5 * 1024**3,
                       persistent_dir_exists=True)
    assert v["verdict"] == "oversized"
    assert "5.0 GiB" in v["reason"]


def test_classify_oversized_skipped_when_capped():
    # SystemMaxUse set → don't flag even if currently > 4 GiB.
    v = mod.classify({"SystemMaxUse": "2G"}, 5 * 1024**3,
                       persistent_dir_exists=True)
    assert v["verdict"] == "ok"


def test_classify_no_persistent_storage():
    v = mod.classify({"Storage": "auto"}, 0,
                       persistent_dir_exists=True)
    assert v["verdict"] == "no_persistent_storage"


def test_classify_priority_storage_disabled_wins():
    v = mod.classify({"Storage": "none",
                       "RateLimitBurst": "5"}, 10 * 1024**3,
                       persistent_dir_exists=True)
    assert v["verdict"] == "storage_disabled"


def test_classify_priority_rate_limit_over_oversized():
    v = mod.classify({"RateLimitBurst": "5"}, 10 * 1024**3,
                       persistent_dir_exists=True)
    assert v["verdict"] == "rate_limit_risky"


# --- status integration -----------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    conf = tmp_path / "journald.conf"
    conf.write_text(CONF_SAMPLE)
    conf_d = tmp_path / "journald.conf.d"
    conf_d.mkdir()
    journal = tmp_path / "journal"
    journal.mkdir()
    (journal / "f").write_bytes(b"x" * 1000)
    monkeypatch.setattr(mod, "_JOURNALD_CONF", str(conf))
    monkeypatch.setattr(mod, "_JOURNALD_CONF_D", str(conf_d))
    monkeypatch.setattr(mod, "_VAR_LOG_JOURNAL", str(journal))
    out = mod.status()
    assert out["ok"] is True
    assert out["config"]["Storage"] == "persistent"
    assert out["journal_bytes"] == 1000
    assert out["verdict"]["verdict"] == "ok"
