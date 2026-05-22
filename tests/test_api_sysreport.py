"""Tests for /api/sysreport (R&D #2.1, cycle 128)."""
from unittest.mock import MagicMock, patch

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    cfg = Config(defaults={
        "MODULE_FAN_CURVE": "1",
        "MODULE_POWER_LIMIT": "1",
        "MODULE_AUTO_PROFILE": "0",
    })
    yield {"storage": s, "config": cfg}
    s.close()


def test_returns_ok_with_required_fields(ctx):
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="550.144.03\n")):
        code, body = api.handle_sysreport(ctx)
    assert code == 200
    assert body["ok"]
    assert "timestamp_iso" in body
    assert "dashboard_version" in body
    assert "schema_version" in body
    assert "modules_enabled" in body
    assert "system" in body
    assert "nvidia" in body


def test_modules_enabled_filters_correctly(ctx):
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")):
        _, body = api.handle_sysreport(ctx)
    assert "fan_curve" in body["modules_enabled"]
    assert "power_limit" in body["modules_enabled"]
    assert "auto_profile" not in body["modules_enabled"]


def test_system_block_has_kernel_python(ctx):
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")):
        _, body = api.handle_sysreport(ctx)
    assert body["system"]["kernel"]
    assert body["system"]["python"]
    # arch is machine() — 'x86_64' on most CI runners
    assert body["system"]["arch"]


def test_nvidia_driver_parsed_from_csv(ctx):
    """nvidia-smi --query-gpu=driver_version returns just the version on stdout."""
    def fake_run(cmd, **kw):
        if "--query-gpu=driver_version" in cmd:
            return MagicMock(returncode=0, stdout="550.144.03\n")
        # bare nvidia-smi : return text with CUDA Version
        if cmd == ["nvidia-smi"]:
            return MagicMock(returncode=0, stdout="""
+-----------------------------------------+
| NVIDIA-SMI 550.144.03   Driver: 550.144.03   CUDA Version: 12.4   |
+-----------------------------------------+
""")
        return MagicMock(returncode=1, stdout="")

    with patch("gpu_dashboard.api.subprocess.run", side_effect=fake_run):
        _, body = api.handle_sysreport(ctx)
    assert body["nvidia"]["driver"] == "550.144.03"
    assert body["nvidia"]["cuda"] == "12.4"


def test_handles_missing_nvidia_smi(ctx):
    with patch("gpu_dashboard.api.subprocess.run",
               side_effect=FileNotFoundError("nvidia-smi not found")):
        _, body = api.handle_sysreport(ctx)
    assert body["nvidia"]["driver"] is None
    assert body["nvidia"]["cuda"] is None


def test_disk_free_gb_present(ctx):
    """Should compute disk_free for the DB dir's filesystem."""
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")):
        _, body = api.handle_sysreport(ctx)
    # Either a positive number, or None on weird filesystems
    assert body["disk_free_gb_dashboard_data"] is None or body["disk_free_gb_dashboard_data"] > 0


def test_ram_total_present(ctx):
    """/proc/meminfo should give us RAM total on Linux CI."""
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")):
        _, body = api.handle_sysreport(ctx)
    # On Linux CI : a positive number. On non-Linux : None.
    assert body["ram_total_gb"] is None or body["ram_total_gb"] > 0


def test_works_without_storage():
    """sysreport should not crash when storage is missing."""
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")):
        code, body = api.handle_sysreport({"config": None})
    assert code == 200
    assert body["schema_version"] is None
