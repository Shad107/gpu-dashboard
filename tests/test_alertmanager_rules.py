"""R&D #7.10 — Prometheus AlertManager rules export tests."""
from gpu_dashboard import api
from gpu_dashboard.config import Config


def _cfg(**overrides):
    base = {
        "ALERT_MONITOR_INTERVAL": "30",
        "ALERT_MIN_CONSECUTIVE": "3",
        "ALERT_GPU_TEMP_THRESHOLD": "85",
        "ALERT_FAN_PCT_THRESHOLD": "95",
        "ALERT_VRAM_PCT_THRESHOLD": "90",
    }
    base.update({k: str(v) for k, v in overrides.items()})
    return Config(defaults=base)


def test_for_duration_from_consecutive():
    """3 samples × 30s interval = 90s for-clause."""
    assert api._alert_consecutive_to_for(3, 30) == "90s"
    assert api._alert_consecutive_to_for(5, 60) == "300s"


def test_yaml_has_required_top_level_keys():
    yaml = api.build_alertmanager_rules_yaml(_cfg())
    assert yaml.startswith("# Prometheus AlertManager rules")
    assert "groups:" in yaml
    assert "name: gpu-dashboard" in yaml
    assert "interval: 30s" in yaml


def test_yaml_includes_all_5_alerts():
    yaml = api.build_alertmanager_rules_yaml(_cfg())
    for name in ["GpuTempHigh", "GpuFanHigh", "GpuVramPctHigh", "GpuOffBus", "GpuPcieDowngrade"]:
        assert f"alert: {name}" in yaml


def test_temp_threshold_replaces_default():
    yaml = api.build_alertmanager_rules_yaml(_cfg(ALERT_GPU_TEMP_THRESHOLD=78))
    assert "gpu_temp_celsius > 78" in yaml


def test_fan_threshold_converts_to_ratio():
    """ALERT_FAN_PCT_THRESHOLD is 0-100, but /metrics exposes 0-1 ratio."""
    yaml = api.build_alertmanager_rules_yaml(_cfg(ALERT_FAN_PCT_THRESHOLD=80))
    assert "gpu_fan_speed_ratio > 0.80" in yaml


def test_vram_threshold_in_percent_expression():
    yaml = api.build_alertmanager_rules_yaml(_cfg(ALERT_VRAM_PCT_THRESHOLD=75))
    assert "* 100 > 75" in yaml


def test_severity_labels_present():
    yaml = api.build_alertmanager_rules_yaml(_cfg())
    assert "severity: warning" in yaml
    assert "severity: critical" in yaml  # only for GpuOffBus


def test_source_label_always_gpu_dashboard():
    yaml = api.build_alertmanager_rules_yaml(_cfg())
    # 5 alerts, all have source: gpu-dashboard
    assert yaml.count("source: gpu-dashboard") == 5


def test_for_duration_uses_actual_interval():
    """interval=60s + consecutive=5 → for: 300s."""
    yaml = api.build_alertmanager_rules_yaml(
        _cfg(ALERT_MONITOR_INTERVAL=60, ALERT_MIN_CONSECUTIVE=5)
    )
    assert "interval: 60s" in yaml
    assert "for: 300s" in yaml


def test_handler_returns_yaml_string():
    code, body = api.handle_alertmanager_rules({"config": _cfg()})
    assert code == 200
    assert isinstance(body, str)
    assert "groups:" in body
