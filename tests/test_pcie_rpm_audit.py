"""R&D #28.1 — PCIe runtime-PM auditor tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import pcie_rpm_audit as pr


# ── list_nvidia_bdfs ───────────────────────────────────────────────────


def test_list_nvidia(tmp_path):
    n = tmp_path / "0000:01:00.0"; n.mkdir()
    (n / "vendor").write_text("0x10de\n")
    other = tmp_path / "0000:02:00.0"; other.mkdir()
    (other / "vendor").write_text("0x1002\n")
    out = pr.list_nvidia_bdfs(sys_root=str(tmp_path))
    assert out == ["0000:01:00.0"]


def test_list_empty(tmp_path):
    assert pr.list_nvidia_bdfs(sys_root=str(tmp_path)) == []


# ── read_rpm_state ─────────────────────────────────────────────────────


def test_read_rpm_state(tmp_path):
    bdf = tmp_path / "0000:01:00.0"; bdf.mkdir()
    pw = bdf / "power"; pw.mkdir()
    (pw / "control").write_text("auto\n")
    (pw / "runtime_status").write_text("active\n")
    state = pr.read_rpm_state("0000:01:00.0", sys_root=str(tmp_path))
    assert state["control"] == "auto"
    assert state["runtime_status"] == "active"


def test_read_rpm_missing(tmp_path):
    state = pr.read_rpm_state("0000:99:00.0", sys_root=str(tmp_path))
    assert state["control"] is None


# ── classify ───────────────────────────────────────────────────────────


def test_classify_active_on():
    v = pr.classify({"control": "on", "runtime_status": "active"})
    assert v["verdict"] == "active"
    assert "control=on" in v["reason"]


def test_classify_auto_active():
    v = pr.classify({"control": "auto", "runtime_status": "active"})
    assert v["verdict"] == "active"
    assert "control=on" in v["recommendation"]


def test_classify_suspended_now():
    v = pr.classify({"control": "auto", "runtime_status": "suspended"})
    assert v["verdict"] == "suspended_now"
    assert "wake stall" in v["reason"]
    assert "control" in v["recommendation"]


def test_classify_error():
    v = pr.classify({"control": "auto", "runtime_status": "error"})
    assert v["verdict"] == "error"
    assert "driver bug" in v["reason"]


def test_classify_unknown_when_no_sysfs():
    v = pr.classify({"control": None, "runtime_status": None})
    assert v["verdict"] == "unknown"


def test_classify_unknown_unexpected_state():
    v = pr.classify({"control": "wat", "runtime_status": "huh"})
    assert v["verdict"] == "unknown"


# ── systemd_dropin_recipe ──────────────────────────────────────────────


def test_dropin_recipe_contains_bdf():
    r = pr.systemd_dropin_recipe("0000:01:00.0")
    assert "0000:01:00.0" in r
    assert "ATTR" in r
    assert "0x10de" in r
    assert "udevadm" in r


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_gpus():
    with patch.object(pr, "list_nvidia_bdfs", return_value=[]):
        s = pr.status()
    assert s["device_count"] == 0
    assert s["worst_verdict"] == "no_gpus"


def test_status_all_active():
    with patch.object(pr, "list_nvidia_bdfs",
                       return_value=["0000:01:00.0"]):
        with patch.object(pr, "read_rpm_state",
                          return_value={"bdf": "0000:01:00.0",
                                         "control": "on",
                                         "runtime_status": "active"}):
            s = pr.status()
    assert s["worst_verdict"] == "active"
    assert s["device_count"] == 1


def test_status_flags_suspended():
    with patch.object(pr, "list_nvidia_bdfs",
                       return_value=["0000:01:00.0"]):
        with patch.object(pr, "read_rpm_state",
                          return_value={"bdf": "0000:01:00.0",
                                         "control": "auto",
                                         "runtime_status": "suspended"}):
            s = pr.status()
    assert s["worst_verdict"] == "suspended_now"
    assert s["cards"][0]["udev_recipe"] != ""


def test_status_worst_picks_error_over_suspended():
    fake_states = [
        {"bdf": "0000:01:00.0", "control": "auto",
         "runtime_status": "suspended"},
        {"bdf": "0000:02:00.0", "control": "auto",
         "runtime_status": "error"},
    ]
    with patch.object(pr, "list_nvidia_bdfs",
                       return_value=["0000:01:00.0", "0000:02:00.0"]):
        with patch.object(pr, "read_rpm_state",
                          side_effect=fake_states):
            s = pr.status()
    assert s["worst_verdict"] == "error"
