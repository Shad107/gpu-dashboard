"""R&D #4.1 — /metrics OpenMetrics endpoint smoke tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard import api


def _ctx():
    """Minimal context for handle_prometheus_metrics."""
    from gpu_dashboard.config import Config
    return {"config": Config(defaults={}), "sampler": None, "profile": None}


def test_returns_text_with_help_and_type_lines():
    """OpenMetrics text format must include HELP + TYPE lines."""
    code, body = api.handle_prometheus_metrics(_ctx())
    assert code == 200
    assert isinstance(body, str)
    assert "# HELP gpu_temp_celsius" in body
    assert "# TYPE gpu_temp_celsius gauge" in body
    assert "# HELP gpu_dashboard_info" in body


def test_includes_build_info_with_version():
    """gpu_dashboard_info{version="..."} 1 line must be present."""
    code, body = api.handle_prometheus_metrics(_ctx())
    assert code == 200
    # info gauge has labels including version=
    assert "gpu_dashboard_info{" in body
    assert 'version="' in body


def test_text_ends_with_newline():
    """OpenMetrics specs require a trailing newline."""
    code, body = api.handle_prometheus_metrics(_ctx())
    assert body.endswith("\n")


def test_no_gpu_still_returns_200_and_skeleton():
    """When no GPU is detected (no nvidia-smi), only HELP/TYPE lines + info."""
    with patch.object(api._monolith, "_gpus_available", return_value=[]):
        code, body = api.handle_prometheus_metrics(_ctx())
    assert code == 200
    # No data series, just HELP/TYPE preamble + info gauge
    assert "gpu_temp_celsius{" not in body  # no data lines
    assert "gpu_dashboard_info{" in body    # info still present


def test_emits_data_when_gpu_alive():
    """With a mocked GPU, data lines for temp/power should appear."""
    fake_gpus = [{"index": 0, "name": "RTX 3090", "uuid": "GPU-AAAA"}]
    fake_snap = {
        "alive": True, "name": "RTX 3090", "uuid": "GPU-AAAA",
        "temp": 42, "util_gpu": 25, "power": 180.5, "power_limit": 250.0,
        "mem_used_mib": 8192, "mem_total_mib": 24576,
        "pcie_gen": 4, "pcie_width": 16,
    }
    with patch.object(api._monolith, "_gpus_available", return_value=fake_gpus), \
         patch.object(api._monolith, "_gpu_card_snapshot", return_value=fake_snap), \
         patch.object(api._monolith, "_per_fan_state", return_value=[]):
        code, body = api.handle_prometheus_metrics(_ctx())
    assert code == 200
    assert 'gpu_temp_celsius{gpu="0",name="RTX 3090",uuid="GPU-AAAA"} 42' in body
    assert 'gpu_power_watts{gpu="0",name="RTX 3090",uuid="GPU-AAAA"} 180.50' in body
    assert 'gpu_util_ratio{gpu="0",name="RTX 3090",uuid="GPU-AAAA"} 0.2500' in body
    assert 'gpu_pcie_link_gen{gpu="0",name="RTX 3090",uuid="GPU-AAAA"} 4' in body
    # VRAM 8192 MiB → 8192 * 1024 * 1024 bytes
    assert 'gpu_memory_used_bytes{gpu="0",name="RTX 3090",uuid="GPU-AAAA"} 8589934592' in body


def test_label_escaping_for_quotes_in_name():
    """A GPU name with quotes/backslashes must be escaped per OpenMetrics."""
    fake_gpus = [{"index": 0, "name": 'RTX "weird\\name"', "uuid": "U"}]
    fake_snap = {"alive": True, "name": 'RTX "weird\\name"', "uuid": "U", "temp": 50}
    with patch.object(api._monolith, "_gpus_available", return_value=fake_gpus), \
         patch.object(api._monolith, "_gpu_card_snapshot", return_value=fake_snap), \
         patch.object(api._monolith, "_per_fan_state", return_value=[]):
        code, body = api.handle_prometheus_metrics(_ctx())
    assert code == 200
    # double-quote → \"  ; backslash → \\
    assert 'name="RTX \\"weird\\\\name\\""' in body
