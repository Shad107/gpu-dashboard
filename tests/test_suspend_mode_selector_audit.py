"""Tests for modules/suspend_mode_selector_audit.py R&D #88.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import suspend_mode_selector_audit as mod


def _mk_power(tmp_path, *, state="freeze mem disk",
              mem_sleep="s2idle [deep]",
              disk="[shutdown] reboot suspend test_resume",
              pm_test="[none] core processors"):
    d = tmp_path / "power"
    d.mkdir(parents=True, exist_ok=True)
    (d / "state").write_text(state + "\n")
    (d / "mem_sleep").write_text(mem_sleep + "\n")
    (d / "disk").write_text(disk + "\n")
    (d / "pm_test").write_text(pm_test + "\n")
    return str(d)


def _mk_swaps(tmp_path, *, present=True):
    p = tmp_path / "proc_swaps"
    if present:
        p.write_text(
            "Filename Type Size Used Priority\n"
            "/dev/sda2 partition 8388604 0 -2\n")
    else:
        p.write_text(
            "Filename Type Size Used Priority\n")
    return str(p)


# --- _selected -------------------------------------------------

def test_selected_present():
    assert mod._selected("s2idle [deep]") == "deep"


def test_selected_absent():
    assert mod._selected("no brackets here") == ""


def test_selected_empty():
    assert mod._selected("") == ""


# --- _tokens ---------------------------------------------------

def test_tokens_with_brackets():
    assert mod._tokens("[s2idle] deep") == ["s2idle", "deep"]


def test_tokens_empty():
    assert mod._tokens("") == []


# --- read_power_state ------------------------------------------

def test_read_power_missing(tmp_path):
    out = mod.read_power_state(str(tmp_path / "nope"))
    assert out["state"] == ""
    assert out["mem_sleep"] == ""


def test_read_power_populated(tmp_path):
    r = _mk_power(tmp_path)
    out = mod.read_power_state(r)
    assert out["state"] == "freeze mem disk"
    assert out["mem_sleep"] == "s2idle [deep]"


# --- has_swap --------------------------------------------------

def test_has_swap_yes(tmp_path):
    p = _mk_swaps(tmp_path, present=True)
    assert mod.has_swap(p) is True


def test_has_swap_no(tmp_path):
    p = _mk_swaps(tmp_path, present=False)
    assert mod.has_swap(p) is False


def test_has_swap_missing(tmp_path):
    assert mod.has_swap(str(tmp_path / "nope")) is False


# --- classify --------------------------------------------------

def test_classify_unknown_empty():
    v = mod.classify({"state": "", "mem_sleep": "",
                          "disk": "", "pm_test": ""}, False)
    assert v["verdict"] == "unknown"


def test_classify_no_suspend_support():
    v = mod.classify({"state": "freeze",
                          "mem_sleep": "", "disk": "",
                          "pm_test": ""}, False)
    assert v["verdict"] == "no_suspend_support"


def test_classify_pm_test_armed():
    v = mod.classify({
        "state": "freeze mem disk",
        "mem_sleep": "s2idle [deep]",
        "disk": "[shutdown]",
        "pm_test": "none core [devices] freezer"}, False)
    assert v["verdict"] == "pm_test_armed"


def test_classify_s2idle_only_no_deep():
    v = mod.classify({
        "state": "freeze mem disk",
        "mem_sleep": "[s2idle]",
        "disk": "[shutdown]",
        "pm_test": "[none]"}, False)
    assert v["verdict"] == "s2idle_only_no_deep"


def test_classify_s2idle_with_deep_available():
    v = mod.classify({
        "state": "freeze mem disk",
        "mem_sleep": "[s2idle] deep",
        "disk": "[shutdown]",
        "pm_test": "[none]"}, False)
    assert v["verdict"] == "mem_sleep_s2idle_with_deep"


def test_classify_hibernate_disabled_with_swap():
    v = mod.classify({
        "state": "freeze mem disk",
        "mem_sleep": "s2idle [deep]",
        "disk": "[disabled]",
        "pm_test": "[none]"}, True)
    assert v["verdict"] == "hibernate_disabled_with_swap"


def test_classify_hibernate_disabled_no_swap_is_ok():
    v = mod.classify({
        "state": "freeze mem disk",
        "mem_sleep": "s2idle [deep]",
        "disk": "[disabled]",
        "pm_test": "[none]"}, False)
    assert v["verdict"] == "mem_sleep_deep_selected"


def test_classify_deep_selected_ok():
    v = mod.classify({
        "state": "freeze mem disk",
        "mem_sleep": "s2idle [deep]",
        "disk": "[shutdown]",
        "pm_test": "[none]"}, True)
    assert v["verdict"] == "mem_sleep_deep_selected"


# Priority : pm_test > s2idle_only_no_deep > s2idle_with_deep
def test_priority_pm_test_over_s2idle_only():
    v = mod.classify({
        "state": "freeze mem disk",
        "mem_sleep": "[s2idle]",
        "disk": "[shutdown]",
        "pm_test": "[devices]"}, False)
    assert v["verdict"] == "pm_test_armed"


def test_priority_s2idle_only_over_with_deep():
    v = mod.classify({
        "state": "freeze mem disk",
        "mem_sleep": "[s2idle]",
        "disk": "[shutdown]",
        "pm_test": "[none]"}, False)
    assert v["verdict"] == "s2idle_only_no_deep"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                     str(tmp_path / "nope_power"),
                     str(tmp_path / "nope_swaps"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    r = _mk_power(tmp_path)
    s = _mk_swaps(tmp_path, present=True)
    out = mod.status(None, r, s)
    assert out["verdict"]["verdict"] == "mem_sleep_deep_selected"
    assert out["ok"] is True
    assert out["swap_present"] is True


def test_status_s2idle_only_synthetic(tmp_path):
    r = _mk_power(tmp_path, mem_sleep="[s2idle]")
    s = _mk_swaps(tmp_path, present=False)
    out = mod.status(None, r, s)
    assert out["verdict"]["verdict"] == "s2idle_only_no_deep"
    assert out["ok"] is False
