"""R&D #12.5 — CI runner GPU tag endpoint tests."""
import pytest
import subprocess
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.api import ci_tag


def _snap(temp=50, util=20, power=80, name="NVIDIA GeForce RTX 3090",
          mem_used=8192, mem_total=24576, alive=True):
    return {
        "alive": alive, "name": name, "temp": temp,
        "util_gpu": util, "power": power, "power_limit": 350,
        "mem_used_mib": mem_used, "mem_total_mib": mem_total,
    }


def _ctx():
    return {}


# ── helpers ───────────────────────────────────────────────────────────────


def test_short_gpu_name_strips_prefix():
    assert ci_tag._short_gpu_name("NVIDIA GeForce RTX 3090") == "3090"
    assert ci_tag._short_gpu_name("NVIDIA A100") == "a100"
    assert ci_tag._short_gpu_name("") == "unknown"


def test_short_gpu_name_h100():
    assert ci_tag._short_gpu_name("NVIDIA H100 PCIe") == "h100-pcie"


def test_gpu_arch_classification():
    assert ci_tag._gpu_arch("NVIDIA GeForce RTX 4090") == "ada"
    assert ci_tag._gpu_arch("NVIDIA GeForce RTX 3090") == "ampere"
    assert ci_tag._gpu_arch("NVIDIA RTX 2080 Ti") == "turing"
    assert ci_tag._gpu_arch("NVIDIA H100") == "hopper"
    assert ci_tag._gpu_arch("NVIDIA A100") == "ampere"
    assert ci_tag._gpu_arch("Tesla T4") == "turing"
    assert ci_tag._gpu_arch("") == "unknown"


# ── handle_ci_tag ────────────────────────────────────────────────────────


def test_text_format_default():
    with patch.object(ci_tag._m, "_gpu_card_snapshot", return_value=_snap()), \
         patch.object(ci_tag._m, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ci_tag, "_query_cuda_driver", return_value=("12.4", "560.35")):
        code, body = api.handle_ci_tag(_ctx())
    assert code == 200
    # key=value per line
    assert "cuda=12.4" in body
    assert "driver=560.35" in body
    assert "gpu=3090" in body
    assert "arch=ampere" in body
    assert "gpu_count=1" in body
    assert "vram_total_gb=24.0" in body
    # 24576 - 8192 = 16384 MiB → 16.0 GiB free
    assert "vram_free_gb=16.0" in body
    assert "available=1" in body


def test_json_format():
    with patch.object(ci_tag._m, "_gpu_card_snapshot", return_value=_snap()), \
         patch.object(ci_tag._m, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ci_tag, "_query_cuda_driver", return_value=("12.4", "560")):
        code, body = api.handle_ci_tag(_ctx(), {"fmt": "json"})
    import json
    d = json.loads(body)
    assert d["ok"] is True
    assert d["labels"]["gpu"] == "3090"
    assert d["labels"]["cuda"] == "12.4"


def test_flat_format_comma_joined():
    with patch.object(ci_tag._m, "_gpu_card_snapshot", return_value=_snap()), \
         patch.object(ci_tag._m, "_gpus_available", return_value=[]), \
         patch.object(ci_tag, "_query_cuda_driver", return_value=(None, None)):
        code, body = api.handle_ci_tag(_ctx(), {"fmt": "flat"})
    # Single line of key=value,key=value
    assert "," in body
    assert body.count("\n") == 1  # trailing newline only
    assert "cuda=unknown" in body


def test_gate_503_when_vram_below_threshold():
    """Free VRAM 16 GiB → asking min_vram_gb=20 should 503."""
    with patch.object(ci_tag._m, "_gpu_card_snapshot", return_value=_snap()), \
         patch.object(ci_tag._m, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ci_tag, "_query_cuda_driver", return_value=("12.4", "560")):
        code, body = api.handle_ci_tag(_ctx(), {"min_vram_gb": "20"})
    assert code == 503
    assert "available=0" in body


def test_gate_200_when_vram_above_threshold():
    """Free VRAM 16 GiB → asking min_vram_gb=10 should 200."""
    with patch.object(ci_tag._m, "_gpu_card_snapshot", return_value=_snap()), \
         patch.object(ci_tag._m, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ci_tag, "_query_cuda_driver", return_value=("12.4", "560")):
        code, body = api.handle_ci_tag(_ctx(), {"min_vram_gb": "10"})
    assert code == 200
    assert "available=1" in body


def test_gpu_offline_returns_unavailable():
    with patch.object(ci_tag._m, "_gpu_card_snapshot", return_value=_snap(alive=False)), \
         patch.object(ci_tag._m, "_gpus_available", return_value=[]), \
         patch.object(ci_tag, "_query_cuda_driver", return_value=(None, None)):
        code, body = api.handle_ci_tag(_ctx(), {"min_vram_gb": "1"})
    assert code == 503
    assert "gpu=none" in body
    assert "available=0" in body


def test_cuda_driver_unknown_when_nvidia_smi_missing():
    with patch.object(ci_tag._m, "_gpu_card_snapshot", return_value=_snap()), \
         patch.object(ci_tag._m, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(subprocess, "run", side_effect=FileNotFoundError):
        code, body = api.handle_ci_tag(_ctx())
    assert "cuda=unknown" in body
    assert "driver=unknown" in body


def test_invalid_min_vram_param_defaults_to_zero():
    """Non-numeric min_vram_gb param ignored (no gate applied)."""
    with patch.object(ci_tag._m, "_gpu_card_snapshot", return_value=_snap()), \
         patch.object(ci_tag._m, "_gpus_available", return_value=[{"index": 0}]), \
         patch.object(ci_tag, "_query_cuda_driver", return_value=("12.4", "560")):
        code, body = api.handle_ci_tag(_ctx(), {"min_vram_gb": "abc"})
    assert code == 200
