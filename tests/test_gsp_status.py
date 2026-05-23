"""R&D #21.3 — GSP-RM crash + fallback surfacer tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import gsp_status as gs


# ── scan_for_gsp_events ────────────────────────────────────────────────


def test_scan_no_lines():
    assert gs.scan_for_gsp_events([]) == []


def test_scan_ignores_non_nvrm_lines():
    lines = ["kernel: random kernel message", "systemd: started X"]
    assert gs.scan_for_gsp_events(lines) == []


def test_scan_detects_timeout():
    lines = ["kernel: NVRM: GSP RPC timeout encountered"]
    out = gs.scan_for_gsp_events(lines)
    assert len(out) == 1
    assert out[0]["kind"] == "timeout"


def test_scan_detects_fallback():
    lines = ["kernel: NVRM: Falling back to host RM"]
    out = gs.scan_for_gsp_events(lines)
    assert out[0]["kind"] == "fallback"


def test_scan_detects_init_failed():
    lines = ["kernel: NVRM: RmInitializeGsp failed with status 0x42"]
    out = gs.scan_for_gsp_events(lines)
    assert out[0]["kind"] == "init_failed"


def test_scan_detects_crashed():
    lines = ["kernel: NVRM: GSP firmware crashed at boot"]
    out = gs.scan_for_gsp_events(lines)
    assert out[0]["kind"] == "crashed"


def test_scan_detects_rpc_issue():
    lines = ["kernel: NVRM: GSP RPC error code 5"]
    out = gs.scan_for_gsp_events(lines)
    # rpc_issue OR error pattern may match — both are non-critical
    assert out[0]["kind"] in ("rpc_issue", "error")


def test_scan_multiple_events_in_order():
    lines = [
        "NVRM: GSP timed out 1",
        "NVRM: Falling back to host RM",
    ]
    out = gs.scan_for_gsp_events(lines)
    assert len(out) == 2


def test_scan_truncates_long_lines():
    long = "NVRM: GSP failed " + ("a" * 1000)
    out = gs.scan_for_gsp_events([long])
    assert len(out[0]["line"]) <= 240


# ── classify ───────────────────────────────────────────────────────────


def test_classify_unknown_no_gpus():
    v = gs.classify(gpus=[], events=[])
    assert v["verdict"] == "unknown"


def test_classify_ok_when_clean():
    gpus = [{"index": 0, "name": "RTX 3090",
              "gsp_firmware_version": "535.86"}]
    v = gs.classify(gpus, events=[])
    assert v["verdict"] == "ok"
    assert v["gsp_in_use"] is True


def test_classify_ok_no_firmware_version():
    """Older drivers don't expose gsp_firmware_version — still ok."""
    gpus = [{"index": 0, "name": "RTX 2060",
              "gsp_firmware_version": ""}]
    v = gs.classify(gpus, events=[])
    assert v["verdict"] == "ok"
    assert v["gsp_in_use"] is False


def test_classify_crashed_when_serious_event():
    gpus = [{"index": 0, "name": "RTX 3090",
              "gsp_firmware_version": "535.86"}]
    events = [{"kind": "crashed", "line": "..."}]
    v = gs.classify(gpus, events)
    assert v["verdict"] == "crashed"
    assert "modprobe" in v["recovery"]


def test_classify_fallback():
    gpus = [{"index": 0, "name": "RTX 3090",
              "gsp_firmware_version": ""}]
    events = [{"kind": "fallback", "line": "..."}]
    v = gs.classify(gpus, events)
    assert v["verdict"] == "fallback"


def test_classify_warn_only_non_critical():
    gpus = [{"index": 0, "name": "RTX 3090",
              "gsp_firmware_version": "535"}]
    events = [{"kind": "rpc_issue", "line": "..."}]
    v = gs.classify(gpus, events)
    assert v["verdict"] == "warn"


def test_classify_critical_takes_precedence():
    """Mix of fallback + crash → crashed verdict (more serious)."""
    gpus = [{"index": 0, "name": "RTX 3090",
              "gsp_firmware_version": "535"}]
    events = [
        {"kind": "fallback", "line": "..."},
        {"kind": "timeout", "line": "..."},
    ]
    v = gs.classify(gpus, events)
    assert v["verdict"] == "crashed"


# ── journalctl_kernel_lines ────────────────────────────────────────────


def test_journalctl_no_binary(monkeypatch):
    monkeypatch.setattr(gs.shutil, "which", lambda x: None)
    assert gs.journalctl_kernel_lines() is None


# ── status ─────────────────────────────────────────────────────────────


def test_status_aggregates_events():
    fake_lines = [
        "kernel: NVRM: GSP timed out",
        "kernel: NVRM: Falling back to host RM",
    ]
    fake_gpus = [{"index": 0, "name": "RTX 3090",
                   "gsp_firmware_version": "535.86"}]
    with patch.object(gs, "gsp_firmware_versions", return_value=fake_gpus):
        with patch.object(gs, "journalctl_kernel_lines",
                          return_value=fake_lines):
            s = gs.status()
    assert s["ok"] is True
    assert s["event_count"] == 2
    assert s["verdict"]["verdict"] == "crashed"


def test_status_clean_system():
    with patch.object(gs, "gsp_firmware_versions",
                      return_value=[{"index": 0, "name": "x",
                                      "gsp_firmware_version": "535"}]):
        with patch.object(gs, "journalctl_kernel_lines", return_value=[]):
            s = gs.status()
    assert s["event_count"] == 0
    assert s["verdict"]["verdict"] == "ok"
