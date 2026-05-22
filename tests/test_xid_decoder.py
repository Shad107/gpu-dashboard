"""R&D #14.1 — Xid kernel-error decoder tests."""
import subprocess
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import xid_decoder as xd


SAMPLE_LOG = """\
2026-05-22T12:34:56+0200 host kernel: NVRM: Xid (PCI:0000:01:00): 13, pid=1234, name=python3, Graphics Engine Exception
2026-05-22T12:35:01+0200 host kernel: NVRM: Xid (PCI:0000:01:00): 79, GPU has fallen off the bus
2026-05-22T12:35:10+0200 host kernel: nvidia-modeset: Allocating control region.
2026-05-22T12:36:00+0200 host kernel: NVRM: Xid (PCI:0000:01:00): 999, unknown error
"""


# ── load_dict ────────────────────────────────────────────────────────────


def test_dict_loads_known_codes():
    d = xd.load_dict()
    assert "codes" in d
    # Spot check : 79 and 13 are known
    assert "79" in d["codes"]
    assert "13" in d["codes"]


def test_dict_has_required_fields():
    d = xd.load_dict()
    for code, entry in d["codes"].items():
        assert "name" in entry
        assert "severity" in entry
        assert entry["severity"] in ("info", "warn", "fail")
        assert "remediation" in entry


# ── decode ───────────────────────────────────────────────────────────────


def test_decode_known_xid_79():
    r = xd.decode(79)
    assert r["known"] is True
    assert r["severity"] == "fail"
    assert "fallen off the bus" in r["name"].lower()
    assert "reseat" in r["remediation"].lower()


def test_decode_known_xid_13():
    r = xd.decode(13)
    assert r["known"] is True
    assert r["severity"] == "warn"


def test_decode_unknown_xid():
    r = xd.decode(99999)
    assert r["known"] is False
    assert r["severity"] == "warn"
    assert "consult" in r["remediation"].lower()


# ── parse_log_lines ──────────────────────────────────────────────────────


def test_parse_log_extracts_3_events():
    events = xd.parse_log_lines(SAMPLE_LOG)
    assert len(events) == 3
    codes = [e["code"] for e in events]
    assert 13 in codes
    assert 79 in codes
    assert 999 in codes


def test_parse_log_no_xid_returns_empty():
    log = "some random log\nwithout xid markers\n"
    assert xd.parse_log_lines(log) == []


def test_parse_log_captures_gpu_address():
    events = xd.parse_log_lines(SAMPLE_LOG)
    assert all(e["gpu"] == "0000:01:00" for e in events)


# ── _run_journalctl ──────────────────────────────────────────────────────


def test_run_journalctl_handles_missing():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert xd._run_journalctl() == ""


def test_run_journalctl_handles_non_zero_exit():
    class FakeProc:
        stdout = ""
        returncode = 1
    with patch.object(subprocess, "run", return_value=FakeProc()):
        assert xd._run_journalctl() == ""


# ── decode_recent_journal ────────────────────────────────────────────────


def test_decode_recent_journal_returns_decoded_events():
    with patch.object(xd, "_run_journalctl", return_value=SAMPLE_LOG):
        events = xd.decode_recent_journal()
    assert len(events) == 3
    # First event should be Xid 13 (graphics engine exception, known)
    e13 = next(e for e in events if e["code"] == 13)
    assert e13["known"] is True
    assert e13["severity"] == "warn"
    # The unknown event (999) should have known=False
    e999 = next(e for e in events if e["code"] == 999)
    assert e999["known"] is False


def test_decode_recent_journal_empty_when_no_journal():
    with patch.object(xd, "_run_journalctl", return_value=""):
        assert xd.decode_recent_journal() == []


# ── stats ────────────────────────────────────────────────────────────────


def test_stats_counts_by_severity():
    events = [
        xd.decode(13),  # warn
        xd.decode(79),  # fail
        xd.decode(43),  # info
        xd.decode(13),  # warn
    ]
    s = xd.stats(events)
    assert s["total_24h"] == 4
    assert s["counts_by_severity"]["warn"] == 2
    assert s["counts_by_severity"]["fail"] == 1
    assert s["counts_by_severity"]["info"] == 1
    assert s["worst_severity"] == "fail"


def test_stats_all_clear():
    s = xd.stats([])
    assert s["worst_severity"] == "ok"
    assert s["total_24h"] == 0


def test_stats_only_info_returns_info_verdict():
    events = [xd.decode(43)]  # info severity
    s = xd.stats(events)
    assert s["worst_severity"] == "info"


def test_stats_caps_events_in_response():
    """Avoid bloating the response with thousands of Xids."""
    many = [xd.decode(13) for _ in range(100)]
    s = xd.stats(many)
    assert s["total_24h"] == 100  # full count preserved
    assert len(s["events"]) <= 50  # but events list capped
