"""Tests for modules/hw_watchdog.py — R&D #37.3 hardware watchdog audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import hw_watchdog


def _mk_watchdog(root: Path, n: int, *, identity: str = "i6300ESB",
                    timeout: str = "30",
                    bootstatus: str = "0",
                    nowayout: str = "0",
                    state: str = "active",
                    pretimeout: str | None = "10",
                    status: str | None = "0x8000"):
    d = root / f"watchdog{n}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "identity").write_text(identity + "\n")
    (d / "timeout").write_text(timeout + "\n")
    (d / "bootstatus").write_text(bootstatus + "\n")
    (d / "nowayout").write_text(nowayout + "\n")
    (d / "state").write_text(state + "\n")
    if pretimeout is not None:
        (d / "pretimeout").write_text(pretimeout + "\n")
    if status is not None:
        (d / "status").write_text(status + "\n")


# --- list_watchdogs ----------------------------------------------

def test_list_watchdogs_empty(tmp_path):
    assert hw_watchdog.list_watchdogs(str(tmp_path / "absent")) == []


def test_list_watchdogs_sorted(tmp_path):
    _mk_watchdog(tmp_path, 1)
    _mk_watchdog(tmp_path, 0)
    assert hw_watchdog.list_watchdogs(str(tmp_path)) == ["watchdog0",
                                                              "watchdog1"]


def test_list_watchdogs_ignores_non_watchdog(tmp_path):
    _mk_watchdog(tmp_path, 0)
    (tmp_path / "weird").mkdir()
    assert hw_watchdog.list_watchdogs(str(tmp_path)) == ["watchdog0"]


# --- read_watchdog ----------------------------------------------

def test_read_watchdog_full_payload(tmp_path):
    _mk_watchdog(tmp_path, 0, identity="i6300ESB", timeout="30",
                   bootstatus="0", nowayout="1")
    w = hw_watchdog.read_watchdog(str(tmp_path), "watchdog0")
    assert w["identity"] == "i6300ESB"
    assert w["timeout"] == 30
    assert w["bootstatus"] == 0
    assert w["nowayout"] == 1


def test_read_watchdog_missing_optional_fields(tmp_path):
    d = tmp_path / "watchdog0"
    d.mkdir()
    (d / "identity").write_text("test\n")
    (d / "timeout").write_text("60\n")
    (d / "bootstatus").write_text("0\n")
    (d / "nowayout").write_text("0\n")
    (d / "state").write_text("active\n")
    # pretimeout + status absent
    w = hw_watchdog.read_watchdog(str(tmp_path), "watchdog0")
    assert w["pretimeout"] is None
    assert w["status"] is None


# --- classify --------------------------------------------------

def test_classify_no_watchdog():
    v = hw_watchdog.classify(watchdogs=[])
    assert v["verdict"] == "no_watchdog"


def test_classify_bootstatus_set_warns():
    # bootstatus != 0 → last reboot was triggered by watchdog
    watchdogs = [{"watchdog": "watchdog0", "identity": "i6300ESB",
                   "timeout": 30, "bootstatus": 1, "nowayout": 0}]
    v = hw_watchdog.classify(watchdogs)
    assert v["verdict"] == "bootstatus_set"
    assert "previous" in v["reason"].lower() or "reboot" in v["reason"].lower()


def test_classify_active_clean():
    watchdogs = [{"watchdog": "watchdog0", "identity": "i6300ESB",
                   "timeout": 30, "bootstatus": 0, "nowayout": 1}]
    v = hw_watchdog.classify(watchdogs)
    assert v["verdict"] == "active"


def test_classify_unpinged():
    # timeout = 0 → device exists but not configured / not pinged
    watchdogs = [{"watchdog": "watchdog0", "identity": "i6300ESB",
                   "timeout": 0, "bootstatus": 0, "nowayout": 0}]
    v = hw_watchdog.classify(watchdogs)
    assert v["verdict"] == "unpinged"


def test_classify_recipe_for_active_documents_pinger():
    watchdogs = [{"watchdog": "watchdog0", "identity": "iTCO_wdt",
                   "timeout": 30, "bootstatus": 0, "nowayout": 0}]
    v = hw_watchdog.classify(watchdogs)
    # The recipe should mention either wd_keepalive or systemd RuntimeWatchdogSec
    rec = v["recommendation"]
    assert ("wd_keepalive" in rec.lower() or
            "runtimewatchdog" in rec.lower() or
            "systemd" in rec.lower())


def test_classify_picks_bootstatus_over_active():
    watchdogs = [
        {"watchdog": "watchdog0", "identity": "iTCO", "timeout": 30,
         "bootstatus": 0, "nowayout": 1},
        {"watchdog": "watchdog1", "identity": "softdog", "timeout": 60,
         "bootstatus": 1, "nowayout": 0},
    ]
    v = hw_watchdog.classify(watchdogs)
    assert v["verdict"] == "bootstatus_set"


# --- status ----------------------------------------------------

def test_status_vm_no_watchdog(tmp_path, monkeypatch):
    # The live-rig case
    monkeypatch.setattr(hw_watchdog, "_WATCHDOG_ROOT",
                          str(tmp_path / "absent"))
    s = hw_watchdog.status()
    assert s["ok"] is True
    assert s["watchdog_count"] == 0
    assert s["verdict"]["verdict"] == "no_watchdog"


def test_status_single_active(tmp_path, monkeypatch):
    _mk_watchdog(tmp_path, 0, identity="iTCO_wdt", timeout="30",
                   bootstatus="0", nowayout="0")
    monkeypatch.setattr(hw_watchdog, "_WATCHDOG_ROOT", str(tmp_path))
    s = hw_watchdog.status()
    assert s["watchdog_count"] == 1
    assert s["watchdogs"][0]["identity"] == "iTCO_wdt"
    assert s["verdict"]["verdict"] == "active"


def test_status_bootstatus_alarm(tmp_path, monkeypatch):
    _mk_watchdog(tmp_path, 0, identity="iTCO_wdt", timeout="30",
                   bootstatus="1", nowayout="0")
    monkeypatch.setattr(hw_watchdog, "_WATCHDOG_ROOT", str(tmp_path))
    s = hw_watchdog.status()
    assert s["verdict"]["verdict"] == "bootstatus_set"


def test_status_multi_watchdog(tmp_path, monkeypatch):
    _mk_watchdog(tmp_path, 0, identity="iTCO_wdt", timeout="30")
    _mk_watchdog(tmp_path, 1, identity="softdog", timeout="60")
    monkeypatch.setattr(hw_watchdog, "_WATCHDOG_ROOT", str(tmp_path))
    s = hw_watchdog.status()
    assert s["watchdog_count"] == 2
    identities = [w["identity"] for w in s["watchdogs"]]
    assert "iTCO_wdt" in identities
    assert "softdog" in identities
