"""R&D #11.1b — Watchdog setup tests."""
import os
import tempfile
import subprocess
from unittest.mock import patch
from gpu_dashboard.modules import watchdog_setup as ws


def _redirect_systemd_dir(td):
    return patch.object(ws, "_systemd_user_dir", return_value=td)


def test_service_content_includes_port():
    content = ws._service_content(port=9998, strict=False)
    assert "localhost:9998/readyz" in content
    assert "?strict=1" not in content


def test_service_content_strict_appends_query():
    content = ws._service_content(port=9999, strict=True)
    assert "/readyz?strict=1" in content


def test_timer_content_uses_interval():
    content = ws._timer_content(interval_s=120)
    assert "OnUnitActiveSec=120s" in content


def test_is_installed_false_when_files_missing():
    with tempfile.TemporaryDirectory() as td, _redirect_systemd_dir(td):
        assert ws.is_installed() is False


def test_install_writes_both_unit_files():
    with tempfile.TemporaryDirectory() as td, _redirect_systemd_dir(td):
        with patch.object(ws, "_systemctl", return_value=(True, "ok")):
            ok, _ = ws.install(port=9999, strict=False)
        assert ok is True
        assert os.path.isfile(os.path.join(td, "gpu-dashboard-watchdog.service"))
        assert os.path.isfile(os.path.join(td, "gpu-dashboard-watchdog.timer"))


def test_install_called_systemctl_daemon_reload_and_enable():
    calls = []
    def fake_systemctl(*args):
        calls.append(args)
        return True, "ok"
    with tempfile.TemporaryDirectory() as td, _redirect_systemd_dir(td), \
         patch.object(ws, "_systemctl", side_effect=fake_systemctl):
        ws.install(port=9999, strict=False)
    flat = [c for args in calls for c in args]
    assert "daemon-reload" in flat
    assert "enable" in flat
    assert "--now" in flat


def test_install_systemctl_failure_returns_error():
    """If systemctl daemon-reload fails, install returns false."""
    with tempfile.TemporaryDirectory() as td, _redirect_systemd_dir(td), \
         patch.object(ws, "_systemctl", return_value=(False, "no systemd")):
        ok, msg = ws.install(port=9999)
    assert ok is False
    assert "daemon-reload" in msg


def test_uninstall_removes_files():
    with tempfile.TemporaryDirectory() as td, _redirect_systemd_dir(td):
        # First install
        with patch.object(ws, "_systemctl", return_value=(True, "ok")):
            ws.install(port=9999)
            assert ws.is_installed()
            ok, _ = ws.uninstall()
        assert ok is True
        assert ws.is_installed() is False


def test_status_when_not_installed():
    with tempfile.TemporaryDirectory() as td, _redirect_systemd_dir(td):
        s = ws.status()
    assert s["installed"] is False
    assert s["active"] is False


def test_status_when_installed_but_not_active():
    with tempfile.TemporaryDirectory() as td, _redirect_systemd_dir(td):
        # Write the files but don't enable
        os.makedirs(td, exist_ok=True)
        open(ws.service_path(), "w").write("dummy")
        open(ws.timer_path(), "w").write("dummy")
        # Simulate systemctl is-active failure (inactive)
        with patch.object(ws, "_systemctl", return_value=(False, "inactive")):
            s = ws.status()
    assert s["installed"] is True
    assert s["active"] is False
