"""R&D #18.2 — CUDA / cuDNN / driver compat matrix tests."""
import json
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import cuda_matrix as cm


# ── _normalize_version ─────────────────────────────────────────────────


def test_normalize_short():
    assert cm._normalize_version("12.4") == "12.4"


def test_normalize_with_patch():
    assert cm._normalize_version("12.4.1") == "12.4"


def test_normalize_strips_text():
    assert cm._normalize_version("12.4-rc1") == "12.4"


def test_normalize_garbage():
    assert cm._normalize_version("abc") == "abc"


# ── min_driver_for_cuda ────────────────────────────────────────────────


def test_min_driver_12_4():
    assert cm.min_driver_for_cuda("12.4") == 550.54


def test_min_driver_with_patch():
    assert cm.min_driver_for_cuda("12.4.1") == 550.54


def test_min_driver_unknown():
    assert cm.min_driver_for_cuda("99.9") is None


# ── compat_verdict ─────────────────────────────────────────────────────


def test_compat_ok_when_driver_above_required():
    v = cm.compat_verdict("550.54", "12.4")
    assert v["ok"] is True
    assert v["required_driver"] == 550.54


def test_compat_ok_when_driver_far_above():
    v = cm.compat_verdict("580.65", "12.4")
    assert v["ok"] is True


def test_compat_fail_when_driver_below_required():
    v = cm.compat_verdict("510.39", "12.4")
    assert v["ok"] is False
    assert "Upgrade driver" in v["reason"]


def test_compat_none_when_cuda_missing():
    v = cm.compat_verdict("550.54", None)
    assert v["ok"] is None


def test_compat_none_when_unknown_cuda():
    v = cm.compat_verdict("550.54", "99.9")
    assert v["ok"] is None
    assert "not in lookup" in v["reason"]


def test_compat_handles_unparseable_driver():
    v = cm.compat_verdict("garbage", "12.4")
    assert v["ok"] is None


# ── cuda_toolkit_version ───────────────────────────────────────────────


def test_cuda_toolkit_parses_version_json(tmp_path):
    p = tmp_path
    (p / "version.json").write_text(json.dumps({
        "cuda": {"version": "12.4.1", "name": "CUDA SDK"}
    }))
    out = cm.cuda_toolkit_version(cuda_root=str(p))
    assert out is not None
    assert out["version"] == "12.4.1"
    assert out["name"] == "CUDA SDK"


def test_cuda_toolkit_missing(tmp_path):
    assert cm.cuda_toolkit_version(cuda_root=str(tmp_path)) is None


def test_cuda_toolkit_falls_back_to_version_txt(tmp_path):
    (tmp_path / "version.txt").write_text("CUDA Version 11.8.0\n")
    out = cm.cuda_toolkit_version(cuda_root=str(tmp_path))
    assert out is not None
    assert out["version"] == "11.8.0"


def test_cuda_toolkit_malformed_json(tmp_path):
    (tmp_path / "version.json").write_text("{not json")
    assert cm.cuda_toolkit_version(cuda_root=str(tmp_path)) is None


# ── _parse_cudnn_header ────────────────────────────────────────────────


def test_parse_cudnn_header_full(tmp_path):
    h = tmp_path / "cudnn_version.h"
    h.write_text("""
#define CUDNN_MAJOR 9
#define CUDNN_MINOR 2
#define CUDNN_PATCHLEVEL 1
""")
    assert cm._parse_cudnn_header(str(h)) == "9.2.1"


def test_parse_cudnn_header_partial(tmp_path):
    h = tmp_path / "cudnn_version.h"
    h.write_text("#define CUDNN_MAJOR 8\n")
    assert cm._parse_cudnn_header(str(h)) == "8"


def test_parse_cudnn_header_missing(tmp_path):
    assert cm._parse_cudnn_header(str(tmp_path / "nope.h")) is None


def test_parse_cudnn_header_no_macros(tmp_path):
    h = tmp_path / "cudnn_version.h"
    h.write_text("/* empty file */\n")
    assert cm._parse_cudnn_header(str(h)) is None


# ── status ─────────────────────────────────────────────────────────────


def test_status_with_compatible_combo():
    with patch.object(cm, "driver_version", return_value="550.54"):
        with patch.object(cm, "cuda_toolkit_version",
                          return_value={"version": "12.4", "name": "CUDA"}):
            with patch.object(cm, "cudnn_version", return_value="9.2.1"):
                s = cm.status()
    assert s["driver_version"] == "550.54"
    assert s["cuda_toolkit"]["version"] == "12.4"
    assert s["cudnn_version"] == "9.2.1"
    assert s["compat"]["ok"] is True


def test_status_when_nothing_installed():
    with patch.object(cm, "driver_version", return_value=None):
        with patch.object(cm, "cuda_toolkit_version", return_value=None):
            with patch.object(cm, "cudnn_version", return_value=None):
                s = cm.status()
    assert s["driver_version"] is None
    assert s["cuda_toolkit"] is None
    assert s["compat"]["ok"] is None
