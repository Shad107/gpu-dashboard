"""R&D #25.3 — NVIDIA bug-report ticket prepper tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import bug_report_prep as brp


# ── _safe_call ─────────────────────────────────────────────────────────


def test_safe_call_success():
    assert brp._safe_call(lambda x: x + 1, 1) == 2


def test_safe_call_handles_exception():
    def boom():
        raise RuntimeError("nope")
    assert brp._safe_call(boom) is None


# ── compose_template ───────────────────────────────────────────────────


def _minimal_ctx(**overrides):
    base = {
        "kernel": "6.5.0-26-generic",
        "xid_events_7d": [],
        "gsp_events": [],
        "gsp_verdict": {"verdict": "ok", "reason": "clean"},
        "gpus": [],
        "dkms": {"verdict": {"verdict": "ok"}},
        "driver": {"kernel_module_version": "555.42", "flavor": "open"},
    }
    base.update(overrides)
    return base


def test_template_kernel_appears():
    out = brp.compose_template(_minimal_ctx())
    assert "6.5.0-26-generic" in out


def test_template_driver_appears():
    out = brp.compose_template(_minimal_ctx())
    assert "555.42" in out
    assert "open" in out


def test_template_dkms_state():
    out = brp.compose_template(_minimal_ctx(
        dkms={"verdict": {"verdict": "rebuild_needed"}}
    ))
    assert "rebuild_needed" in out


def test_template_lists_gpus():
    out = brp.compose_template(_minimal_ctx(gpus=[
        {"model": "RTX 3090", "video_bios": "94.02",
         "gpu_firmware": "555.42"},
    ]))
    assert "RTX 3090" in out
    assert "94.02" in out


def test_template_no_gpus_placeholder():
    out = brp.compose_template(_minimal_ctx())
    assert "(no GPUs" in out


def test_template_xid_events_section():
    out = brp.compose_template(_minimal_ctx(xid_events_7d=[
        {"code": 79, "ts": "2026-05-23T10:00",
         "meaning": "GPU has fallen off the bus"},
    ]))
    assert "Xid 79" in out
    assert "fallen off" in out


def test_template_no_xid_events():
    out = brp.compose_template(_minimal_ctx())
    assert "## Recent XID events" in out
    assert "(none)" in out


def test_template_gsp_crashed_advice():
    out = brp.compose_template(_minimal_ctx(
        gsp_verdict={"verdict": "crashed", "reason": "RmInit failed"},
    ))
    assert "Reload nvidia modules" in out


def test_template_clean_system_no_signals_advice():
    out = brp.compose_template(_minimal_ctx())
    assert "No obvious crash signals" in out


def test_template_has_todo_placeholder():
    out = brp.compose_template(_minimal_ctx())
    assert "TODO" in out


def test_template_has_bug_report_command():
    out = brp.compose_template(_minimal_ctx())
    assert "nvidia-bug-report.sh" in out


# ── status ─────────────────────────────────────────────────────────────


def test_status_minimal():
    with patch.object(brp, "gather_system_context", return_value=_minimal_ctx()):
        s = brp.status()
    assert s["ok"] is True
    assert "template_text" in s
    assert s["bug_report_command"] == "sudo nvidia-bug-report.sh"


def test_status_context_summary():
    with patch.object(brp, "gather_system_context",
                       return_value=_minimal_ctx(
                           xid_events_7d=[{"code": 79}],
                           gpus=[{"model": "RTX 3090"}],
                       )):
        s = brp.status()
    assert s["context_summary"]["xid_event_count"] == 1
    assert s["context_summary"]["gpu_count"] == 1
    assert s["context_summary"]["driver_flavor"] == "open"


# ── gather_system_context integration (with mocked siblings) ─────────────


def test_gather_handles_missing_siblings():
    """If a sibling module errors out, gather still returns a dict."""
    out = brp.gather_system_context()
    assert isinstance(out, dict)
    assert "kernel" in out
    assert "xid_events_7d" in out
