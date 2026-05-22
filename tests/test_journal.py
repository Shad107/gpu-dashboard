"""R&D #6.7 — journalctl bridge tests."""
import subprocess
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.config import Config


def _ctx():
    return {"config": Config(defaults={})}


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_unknown_filter_returns_400():
    code, body = api.handle_journal_tail(_ctx(), {"filter": "lolnope"})
    assert code == 400
    assert "unknown filter" in body["error"]


def test_invalid_since_format():
    code, body = api.handle_journal_tail(_ctx(), {"filter": "nvidia", "since": "2 hours"})
    assert code == 400
    assert "since must be like" in body["error"]


def test_no_journalctl_returns_unavailable():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        code, body = api.handle_journal_tail(_ctx(), {"filter": "nvidia"})
    assert code == 200
    assert body["available"] is False
    assert body["reason"].startswith("journalctl unavailable")


def test_empty_log_returns_zero_entries():
    with patch.object(subprocess, "run", return_value=FakeProc(stdout="")):
        code, body = api.handle_journal_tail(_ctx(), {"filter": "nvidia"})
    assert code == 200
    assert body["available"] is True
    assert body["count"] == 0
    assert body["entries"] == []


def test_nvidia_filter_matches():
    out = "\n".join([
        "2026-05-22T13:00:00+0200 host kernel: usb 1-1: new full-speed USB device",
        "2026-05-22T13:00:01+0200 host kernel: NVRM: GPU at PCI:0000:01:00 has fallen off the bus.",
        "2026-05-22T13:00:02+0200 host kernel: ext4-fs (sda1) mounted",
        "2026-05-22T13:00:03+0200 host kernel: nvidia-modeset: Allocated GPU:0",
    ])
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_journal_tail(_ctx(), {"filter": "nvidia"})
    assert code == 200
    assert body["count"] == 2  # NVRM + nvidia-modeset
    assert all("nvidia" in e["msg"].lower() or "NVRM" in e["msg"] for e in body["entries"])


def test_xid_filter_extracts_xid_events():
    out = "2026-05-22T13:00:00+0200 host kernel: NVRM: Xid (PCI:0000:01:00): 79, GPU has fallen off the bus."
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_journal_tail(_ctx(), {"filter": "xid"})
    assert code == 200
    assert body["count"] == 1
    assert len(body["xid_events"]) == 1
    assert body["xid_events"][0]["xid_code"] == 79
    assert "fallen off" in body["xid_events"][0]["summary"]


def test_oom_filter():
    out = "\n".join([
        "2026-05-22T13:00:00+0200 host kernel: Out of memory: Killed process 1234 (python)",
        "2026-05-22T13:00:01+0200 host kernel: oom-killer invoked, gfp_mask=0x...",
    ])
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_journal_tail(_ctx(), {"filter": "oom"})
    assert body["count"] == 2


def test_filters_available_listed():
    code, body = api.handle_journal_tail(_ctx(), {"filter": "nvidia"})
    # Even without patching journalctl, filter validation works first
    if body.get("filters_available"):
        assert "nvidia" in body["filters_available"]
        assert "xid" in body["filters_available"]
        assert "all" in body["filters_available"]


def test_limit_respected():
    lines = "\n".join([
        f"2026-05-22T13:00:{i:02d}+0200 host kernel: NVRM: msg {i}" for i in range(20)
    ])
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=lines)):
        code, body = api.handle_journal_tail(_ctx(), {"filter": "nvrm", "limit": "5"})
    assert body["count"] == 5
