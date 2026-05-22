"""R&D #4.3 — ECC + memory health tests."""
import subprocess
from unittest.mock import patch
from gpu_dashboard import api
from gpu_dashboard.config import Config


def _ctx():
    return {"config": Config(defaults={})}


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_no_nvidia_smi_returns_available_false():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        code, body = api.handle_ecc_health(_ctx())
    assert code == 200
    assert body["available"] is False


def test_consumer_card_all_na_returns_available_false():
    # RTX 3090 / 4090 don't expose ECC
    out = "[N/A], [N/A], [N/A], [N/A], [N/A], [N/A], [N/A]"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_ecc_health(_ctx())
    assert code == 200
    assert body["available"] is False


def test_datacenter_clean_returns_ok():
    out = "Enabled, 0, 0, 0, 0, 0, 0"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_ecc_health(_ctx())
    assert code == 200
    assert body["available"] is True
    assert body["ecc_mode"] == "Enabled"
    assert body["verdict_kind"] == "ok"
    assert "healthy" in body["verdict_msg"].lower()


def test_corrected_errors_only_returns_watch():
    out = "Enabled, 5, 0, 0, 0, 0, 0"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_ecc_health(_ctx())
    assert code == 200
    assert body["verdict_kind"] == "watch"
    assert body["corrected_total"] == 5
    assert "5 corrected" in body["verdict_msg"]


def test_uncorrected_errors_returns_failing():
    out = "Enabled, 100, 2, 0, 0, 0, 0"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_ecc_health(_ctx())
    assert code == 200
    assert body["verdict_kind"] == "failing"
    assert "2 uncorrectable" in body["verdict_msg"]


def test_remapped_uncorrectable_returns_watch():
    out = "Enabled, 50, 0, 0, 3, 0, 0"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_ecc_health(_ctx())
    assert code == 200
    # Even with 50 corrected errors AND 3 remapped uncorr, the uncorr remap is more critical
    assert body["verdict_kind"] == "watch"
    assert "3 row(s) remapped" in body["verdict_msg"]


def test_remap_failure_is_critical():
    out = "Enabled, 0, 0, 0, 0, 0, 1"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_ecc_health(_ctx())
    assert code == 200
    assert body["verdict_kind"] == "failing"
    assert "row remap failed" in body["verdict_msg"].lower()


def test_pending_remap_returns_watch():
    out = "Enabled, 0, 0, 0, 0, 2, 0"
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        code, body = api.handle_ecc_health(_ctx())
    assert code == 200
    assert body["verdict_kind"] == "watch"
    assert "pending remap" in body["verdict_msg"].lower()
