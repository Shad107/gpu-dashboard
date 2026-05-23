"""Tests for modules/oomd_correlator.py — R&D #34.3 systemd-oomd."""
from __future__ import annotations

from unittest import mock

import pytest

from gpu_dashboard.modules import oomd_correlator


def _fake_run(stdout="", stderr="", returncode=0):
    """Returns a callable that mimics subprocess.run's CompletedProcess."""
    class _Result:
        def __init__(self):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode
    return lambda *a, **kw: _Result()


# --- systemctl is-active probe ----------------------------------

def test_check_active_returns_active(monkeypatch):
    monkeypatch.setattr(oomd_correlator, "_run_systemctl_is_active",
                          lambda: ("active", 0))
    assert oomd_correlator.check_active() == "active"


def test_check_active_returns_inactive(monkeypatch):
    monkeypatch.setattr(oomd_correlator, "_run_systemctl_is_active",
                          lambda: ("inactive", 3))
    assert oomd_correlator.check_active() == "inactive"


def test_check_active_returns_not_installed_when_unit_unknown(monkeypatch):
    # systemctl returns "Unit not found" with non-zero exit on Debian
    monkeypatch.setattr(oomd_correlator, "_run_systemctl_is_active",
                          lambda: ("Unit systemd-oomd.service could not be found.", 4))
    assert oomd_correlator.check_active() == "not_installed"


def test_check_active_systemctl_missing(monkeypatch):
    monkeypatch.setattr(oomd_correlator, "_run_systemctl_is_active",
                          lambda: (None, None))
    assert oomd_correlator.check_active() == "unknown"


# --- journal parsing --------------------------------------------

_SAMPLE_JOURNAL_JSON = """\
{"MESSAGE":"Killed /system.slice/llama-server.service due to memory pressure for /user.slice being 75.43% > 50.00% for > 20s with reclaim activity","_HOSTNAME":"box","__REALTIME_TIMESTAMP":"1716100000000000"}
{"MESSAGE":"Killed /system.slice/some-builder.service due to memory pressure","_HOSTNAME":"box","__REALTIME_TIMESTAMP":"1716200000000000"}
{"MESSAGE":"Watching memory.swap.events for /system.slice","_HOSTNAME":"box","__REALTIME_TIMESTAMP":"1716300000000000"}
"""


def test_parse_journal_extracts_kill_events():
    events = oomd_correlator.parse_journal(_SAMPLE_JOURNAL_JSON)
    assert len(events) == 2
    assert "llama-server.service" in events[0]["target"]
    assert "some-builder.service" in events[1]["target"]


def test_parse_journal_empty_returns_empty():
    assert oomd_correlator.parse_journal("") == []


def test_parse_journal_skips_non_kill_messages():
    events = oomd_correlator.parse_journal(_SAMPLE_JOURNAL_JSON)
    # The "Watching memory.swap.events" line should be excluded
    assert all("Watching" not in e["message"] for e in events)


def test_parse_journal_extracts_timestamp_us():
    events = oomd_correlator.parse_journal(_SAMPLE_JOURNAL_JSON)
    assert events[0]["timestamp_us"] == 1716100000000000


def test_parse_journal_handles_garbage_lines():
    txt = ("not json\n" +
           _SAMPLE_JOURNAL_JSON +
           '{"MESSAGE":"another non-kill"}\n')
    events = oomd_correlator.parse_journal(txt)
    assert len(events) == 2


# --- LLM-victim detection --------------------------------------

def test_is_llm_victim_llama_server():
    assert oomd_correlator.is_llm_victim("/system.slice/llama-server.service")


def test_is_llm_victim_ollama():
    assert oomd_correlator.is_llm_victim("/system.slice/ollama.service")


def test_is_llm_victim_vllm():
    assert oomd_correlator.is_llm_victim("/system.slice/vllm.service")


def test_is_llm_victim_rejects_other_unit():
    assert not oomd_correlator.is_llm_victim("/system.slice/postgres.service")


def test_is_llm_victim_empty():
    assert not oomd_correlator.is_llm_victim("")
    assert not oomd_correlator.is_llm_victim(None)


# --- classify --------------------------------------------------

def test_classify_not_installed_is_ok():
    v = oomd_correlator.classify(state="not_installed", events=[])
    assert v["verdict"] == "not_installed"


def test_classify_inactive_is_ok():
    v = oomd_correlator.classify(state="inactive", events=[])
    assert v["verdict"] == "inactive"


def test_classify_active_clean_no_events():
    v = oomd_correlator.classify(state="active", events=[])
    assert v["verdict"] == "active_clean"


def test_classify_active_killed_llm():
    events = [{"target": "/system.slice/llama-server.service",
                "message": "Killed ... due to memory pressure",
                "timestamp_us": 1716100000000000}]
    v = oomd_correlator.classify(state="active", events=events)
    assert v["verdict"] == "active_killed_llm"
    assert "llama-server" in v["reason"]
    assert "MemorySwapMax" in v["recommendation"] or "OOMScoreAdjust" in v["recommendation"]


def test_classify_active_killed_other_only():
    events = [{"target": "/system.slice/random.service",
                "message": "Killed ...",
                "timestamp_us": 1716100000000000}]
    v = oomd_correlator.classify(state="active", events=events)
    assert v["verdict"] == "active_killed_other"


def test_classify_active_picks_llm_over_other():
    events = [
        {"target": "/system.slice/random.service",
         "message": "...", "timestamp_us": 1716200000000000},
        {"target": "/system.slice/llama-server.service",
         "message": "...", "timestamp_us": 1716100000000000},
    ]
    v = oomd_correlator.classify(state="active", events=events)
    assert v["verdict"] == "active_killed_llm"


def test_classify_unknown_state():
    v = oomd_correlator.classify(state="unknown", events=[])
    assert v["verdict"] == "unknown"


# --- status ----------------------------------------------------

def test_status_not_installed(monkeypatch):
    monkeypatch.setattr(oomd_correlator, "check_active", lambda: "not_installed")
    monkeypatch.setattr(oomd_correlator, "fetch_recent_journal",
                          lambda **kw: "")
    s = oomd_correlator.status()
    assert s["ok"] is True
    assert s["state"] == "not_installed"
    assert s["verdict"]["verdict"] == "not_installed"
    assert s["events"] == []


def test_status_active_with_llm_kill(monkeypatch):
    monkeypatch.setattr(oomd_correlator, "check_active", lambda: "active")
    monkeypatch.setattr(oomd_correlator, "fetch_recent_journal",
                          lambda **kw: _SAMPLE_JOURNAL_JSON)
    s = oomd_correlator.status()
    assert s["state"] == "active"
    assert s["verdict"]["verdict"] == "active_killed_llm"
    assert len(s["events"]) == 2


def test_status_active_clean(monkeypatch):
    monkeypatch.setattr(oomd_correlator, "check_active", lambda: "active")
    monkeypatch.setattr(oomd_correlator, "fetch_recent_journal",
                          lambda **kw: "")
    s = oomd_correlator.status()
    assert s["verdict"]["verdict"] == "active_clean"


def test_status_inactive_skips_journal_fetch(monkeypatch):
    called = {"n": 0}
    def counted(**kw):
        called["n"] += 1
        return ""
    monkeypatch.setattr(oomd_correlator, "check_active", lambda: "inactive")
    monkeypatch.setattr(oomd_correlator, "fetch_recent_journal", counted)
    s = oomd_correlator.status()
    # Don't waste a subprocess call when oomd is inactive
    assert called["n"] == 0
    assert s["state"] == "inactive"
