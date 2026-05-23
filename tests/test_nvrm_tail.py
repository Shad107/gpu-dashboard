"""R&D #28.7 — kernel-NVRM/GSP log tailer tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import nvrm_tail as nt


# ── categorize ─────────────────────────────────────────────────────────


def test_categorize_rm_init():
    assert nt.categorize(
        "May 23 03:00:00 host kernel: NVRM: RmInitAdapter succeeded "
        "for 0 device(s).") == "rm_init"


def test_categorize_xid():
    assert nt.categorize(
        "NVRM: Xid (PCI:0000:01:00): 79, GPU has fallen off the bus") == "xid"


def test_categorize_gsp():
    assert nt.categorize("kernel: NVRM: GSP RPC timeout") == "gsp"


def test_categorize_nvkms():
    assert nt.categorize("nvidia-modeset: NvKmsKapiCreateDevice") == "nvkms"


def test_categorize_nvrm_other():
    assert nt.categorize("NVRM: some other message") == "nvrm_other"


def test_categorize_driver_other():
    assert nt.categorize("kernel: nvidia 0000:01:00.0: probe success") == "driver_other"


def test_categorize_other():
    assert nt.categorize("kernel: ext4 mounted") == "other"


def test_categorize_xid_beats_nvrm():
    """Xid pattern matched before nvrm_other catch-all."""
    line = "NVRM: Xid (PCI:0000:01:00): 79"
    assert nt.categorize(line) == "xid"


# ── parse_line ─────────────────────────────────────────────────────────


def test_parse_line_full():
    out = nt.parse_line(
        "May 23 03:00:00 desktop kernel: NVRM: Xid 79")
    assert out["ts"] == "May 23 03:00:00"
    assert "Xid 79" in out["body"]


def test_parse_line_no_ts():
    out = nt.parse_line("NVRM: just body")
    assert out["ts"] == ""
    assert out["body"] == "NVRM: just body"


def test_parse_line_empty():
    assert nt.parse_line("") is None
    assert nt.parse_line("   ") is None


# ── filter_nvidia_lines ────────────────────────────────────────────────


def test_filter_keeps_nvidia():
    lines = [
        "May 1 12:00:00 host kernel: NVRM: foo",
        "May 1 12:00:00 host kernel: ext4 mounted",
        "May 1 12:00:00 host kernel: GSP boot",
        "May 1 12:00:00 host kernel: NvKmsKapi event",
        "May 1 12:00:00 host kernel: nvidia 0000:01:00.0",
        "May 1 12:00:00 host kernel: random",
    ]
    out = nt.filter_nvidia_lines(lines)
    assert len(out) == 4


def test_filter_empty():
    assert nt.filter_nvidia_lines([]) == []


# ── tail_categorized ───────────────────────────────────────────────────


def test_tail_categorized_basic():
    fake_lines = [
        "May 23 03:00:00 host kernel: NVRM: RmInitAdapter succeeded",
        "May 23 03:01:00 host kernel: GSP RPC OK",
        "May 23 03:02:00 host kernel: ext4 mounted",
    ]
    with patch.object(nt, "run_journalctl", return_value=fake_lines):
        out = nt.tail_categorized(limit=10)
    assert len(out) == 2  # ext4 filtered out
    assert out[0]["category"] == "rm_init"
    assert out[1]["category"] == "gsp"


def test_tail_categorized_truncates_long_body():
    fake_lines = ["NVRM: " + ("a" * 500)]
    with patch.object(nt, "run_journalctl", return_value=fake_lines):
        out = nt.tail_categorized(limit=10)
    assert len(out[0]["body"]) <= 300


def test_tail_categorized_no_journalctl():
    with patch.object(nt, "run_journalctl", return_value=None):
        out = nt.tail_categorized(limit=10)
    assert out == []


def test_tail_categorized_respects_limit():
    fake_lines = [
        f"May 23 03:0{i}:00 host kernel: NVRM: foo {i}" for i in range(50)
    ]
    with patch.object(nt, "run_journalctl", return_value=fake_lines):
        out = nt.tail_categorized(limit=10)
    assert len(out) == 10
    # Most-recent last
    assert "49" in out[-1]["body"]


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_journalctl(monkeypatch):
    monkeypatch.setattr(nt.shutil, "which", lambda x: None)
    s = nt.status()
    assert s["ok"] is False


def test_status_aggregates_categories():
    fake_lines = [
        "May 1 12:00:00 h kernel: NVRM: Xid (PCI:0000:01:00): 79",
        "May 1 12:01:00 h kernel: NVRM: Xid (PCI:0000:01:00): 79",
        "May 1 12:02:00 h kernel: NVRM: RmInitAdapter ok",
        "May 1 12:03:00 h kernel: NVRM: GSP RPC error",
    ]
    with patch.object(nt, "run_journalctl", return_value=fake_lines):
        with patch.object(nt.shutil, "which",
                          return_value="/usr/bin/journalctl"):
            s = nt.status()
    cats = s["category_counts"]
    assert cats["xid"] == 2
    assert cats["rm_init"] == 1
    assert cats["gsp"] == 1


def test_status_uses_cfg():
    with patch.object(nt, "run_journalctl", return_value=[]):
        with patch.object(nt.shutil, "which",
                          return_value="/usr/bin/journalctl"):
            s = nt.status(cfg={"NVRM_TAIL_SINCE": "30 minutes ago",
                                "NVRM_TAIL_LIMIT": "50"})
    assert s["since"] == "30 minutes ago"
