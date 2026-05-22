"""Tests for /api/sysreport/bundle (R&D #3.1, cycle 134)."""
import io
import json
import tarfile
from unittest.mock import patch, MagicMock

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    cfg_path = tmp_path / "config.env"
    cfg_path.write_text(
        "DASHBOARD_PORT=9999\n"
        "TELEGRAM_BOT_TOKEN=secret123\n"
        "WEBHOOK_URL=https://hooks.slack.com/services/AAA/BBB/CCC\n"
        "ELECTRICITY_PRICE_EUR_PER_KWH=0.25\n"
    )
    cfg = Config(defaults={"LOG_FILE": ""})
    yield {"storage": s, "config": cfg, "config_path": str(cfg_path)}
    s.close()


def _open_tar(payload: bytes) -> tarfile.TarFile:
    return tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz")


def test_returns_tar_gz(ctx):
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")):
        result = api.handle_sysreport_bundle(ctx)
    assert len(result) == 4
    code, payload, ctype, fname = result
    assert code == 200
    assert ctype == "application/gzip"
    assert fname.startswith("gpu-dashboard-sysreport-")
    assert fname.endswith(".tar.gz")
    # Valid gzip-tar
    tar = _open_tar(payload)
    names = tar.getnames()
    tar.close()
    assert "sysreport.json" in names
    assert "events.json" in names


def test_bundle_includes_config_env(ctx):
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")):
        _, payload, _, _ = api.handle_sysreport_bundle(ctx)
    tar = _open_tar(payload)
    assert "config.env" in tar.getnames()
    tar.close()


def test_config_env_redacts_secrets(ctx):
    """Secret keys must be replaced with ***REDACTED***."""
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")):
        _, payload, _, _ = api.handle_sysreport_bundle(ctx)
    tar = _open_tar(payload)
    f = tar.extractfile("config.env")
    config_content = f.read().decode("utf-8") if f else ""
    tar.close()
    # Original secrets must NOT appear
    assert "secret123" not in config_content
    assert "AAA/BBB/CCC" not in config_content
    # Redacted markers must appear
    assert "REDACTED" in config_content
    # Non-secret keys must remain
    assert "ELECTRICITY_PRICE_EUR_PER_KWH=0.25" in config_content


def test_redact_env_file_unit():
    raw = (
        "TELEGRAM_BOT_TOKEN=abc\n"
        "DASHBOARD_PORT=9999\n"
        "WEBHOOK_URL=https://hook\n"
        "# a comment\n"
        "VAPID_PRIVATE_KEY=xyz\n"
    )
    out = api._redact_env_file(raw)
    assert "abc" not in out
    assert "xyz" not in out
    assert "https://hook" not in out
    assert "DASHBOARD_PORT=9999" in out
    assert "# a comment" in out  # comments preserved


def test_bundle_includes_recent_events(ctx):
    """Events from storage should be included in events.json."""
    ctx["storage"].record_event("profile_switch", {"to": "boost"})
    ctx["storage"].record_event("alert", {"kind": "gpu_temp_high"})
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")):
        _, payload, _, _ = api.handle_sysreport_bundle(ctx)
    tar = _open_tar(payload)
    f = tar.extractfile("events.json")
    events = json.loads(f.read())
    tar.close()
    assert len(events) == 2


def test_bundle_handles_missing_config(tmp_path):
    """If config.env doesn't exist, bundle still works (just no config.env)."""
    cfg = Config(defaults={})
    storage = Storage(str(tmp_path / "metrics.db"))
    ctx_local = {"storage": storage, "config": cfg,
                 "config_path": str(tmp_path / "missing.env")}
    with patch("gpu_dashboard.api.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="")):
        result = api.handle_sysreport_bundle(ctx_local)
    code, payload, _, _ = result
    assert code == 200
    tar = _open_tar(payload)
    names = tar.getnames()
    tar.close()
    assert "sysreport.json" in names
    assert "config.env" not in names  # missing → not added
    storage.close()
