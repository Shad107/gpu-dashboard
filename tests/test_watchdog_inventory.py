"""Tests for modules/watchdog_inventory.py — R&D #44.3."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import watchdog_inventory as mod


def _mk_wd(root: Path, name: str, *, identity: str = "iTCO_wdt",
             timeout: int = 30, bootstatus: int = 0,
             state: str = "active", nowayout: int = 0,
             pretimeout: int | None = None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "identity").write_text(identity + "\n")
    (d / "timeout").write_text(str(timeout) + "\n")
    (d / "bootstatus").write_text(str(bootstatus) + "\n")
    (d / "state").write_text(state + "\n")
    (d / "nowayout").write_text(str(nowayout) + "\n")
    if pretimeout is not None:
        (d / "pretimeout").write_text(str(pretimeout) + "\n")


# --- list_watchdogs ------------------------------------------------

def test_list_watchdogs_empty(tmp_path):
    assert mod.list_watchdogs(str(tmp_path / "nope")) == []


def test_list_watchdogs_basic(tmp_path):
    _mk_wd(tmp_path, "watchdog0")
    _mk_wd(tmp_path, "watchdog1")
    (tmp_path / "uevent").write_text("\n")  # decoy
    out = mod.list_watchdogs(str(tmp_path))
    assert out == ["watchdog0", "watchdog1"]


# --- read_watchdog -------------------------------------------------

def test_read_watchdog_full(tmp_path):
    _mk_wd(tmp_path, "watchdog0", identity="sp5100_tco",
              timeout=60, bootstatus=0)
    d = mod.read_watchdog(str(tmp_path), "watchdog0")
    assert d["name"] == "watchdog0"
    assert d["identity"] == "sp5100_tco"
    assert d["timeout"] == 60
    assert d["bootstatus"] == 0
    assert d["state"] == "active"


def test_read_watchdog_hex_bootstatus(tmp_path):
    _mk_wd(tmp_path, "watchdog0", bootstatus="0x41")
    # Custom: write a hex value manually since helper coerces to int().
    (tmp_path / "watchdog0" / "bootstatus").write_text("0x41\n")
    d = mod.read_watchdog(str(tmp_path), "watchdog0")
    assert d["bootstatus"] == 0x41


def test_read_watchdog_missing_fields(tmp_path):
    (tmp_path / "watchdog0").mkdir()
    d = mod.read_watchdog(str(tmp_path), "watchdog0")
    assert d["timeout"] is None
    assert d["identity"] is None


# --- describe_bootstatus -------------------------------------------

def test_describe_bootstatus_zero():
    assert mod.describe_bootstatus(0) == []


def test_describe_bootstatus_card_reset():
    bits = mod.describe_bootstatus(0x01)
    assert len(bits) == 1
    assert bits[0]["key"] == "card_reset"


def test_describe_bootstatus_multiple():
    bits = mod.describe_bootstatus(0x41)
    keys = sorted(b["key"] for b in bits)
    assert keys == ["card_reset", "magic_close_missing"]


# --- classify ------------------------------------------------------

def _dev(name="watchdog0", identity="iTCO_wdt", timeout=30,
          bootstatus=0):
    return {"name": name, "identity": identity, "timeout": timeout,
              "bootstatus": bootstatus, "pretimeout": None,
              "state": "active", "nowayout": 0, "fw_version": None}


def test_classify_no_watchdog():
    v = mod.classify([])
    assert v["verdict"] == "no_watchdog"
    assert "modprobe" in v["recommendation"]


def test_classify_ok_single():
    v = mod.classify([_dev()])
    assert v["verdict"] == "ok"
    assert v["recommendation"] == ""


def test_classify_boot_due_to_watchdog():
    v = mod.classify([_dev(bootstatus=0x01)])
    assert v["verdict"] == "boot_due_to_watchdog"
    assert "0x1" in v["reason"]
    assert "journalctl" in v["recommendation"]


def test_classify_multiple_watchdogs():
    v = mod.classify([_dev(name="watchdog0"),
                       _dev(name="watchdog1",
                             identity="ipmi_watchdog")])
    assert v["verdict"] == "multiple_watchdogs"
    assert "WatchdogDevice" in v["recommendation"]


def test_classify_boot_status_wins_over_multiple():
    v = mod.classify([_dev(name="watchdog0", bootstatus=0x01),
                       _dev(name="watchdog1")])
    assert v["verdict"] == "boot_due_to_watchdog"


# --- status integration -------------------------------------------

def test_status_no_watchdog_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_SYS_WATCHDOG",
                        str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_empty_watchdog_dir(monkeypatch, tmp_path):
    wd_dir = tmp_path / "wd"
    wd_dir.mkdir()
    monkeypatch.setattr(mod, "_SYS_WATCHDOG", str(wd_dir))
    out = mod.status()
    assert out["ok"] is True
    assert out["device_count"] == 0
    assert out["verdict"]["verdict"] == "no_watchdog"


def test_status_one_watchdog(monkeypatch, tmp_path):
    wd_dir = tmp_path / "wd"
    wd_dir.mkdir()
    _mk_wd(wd_dir, "watchdog0", identity="iTCO_wdt", timeout=30)
    monkeypatch.setattr(mod, "_SYS_WATCHDOG", str(wd_dir))
    out = mod.status()
    assert out["ok"] is True
    assert out["device_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_bootstatus_breakdown(monkeypatch, tmp_path):
    wd_dir = tmp_path / "wd"
    wd_dir.mkdir()
    _mk_wd(wd_dir, "watchdog0", bootstatus=0x05)  # card_reset + power_under
    monkeypatch.setattr(mod, "_SYS_WATCHDOG", str(wd_dir))
    out = mod.status()
    bits = out["devices"][0]["bootstatus_bits"]
    keys = sorted(b["key"] for b in bits)
    assert keys == ["card_reset", "power_under"]
