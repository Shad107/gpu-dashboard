"""R&D #29.1 — NVIDIA kmod parameter auditor tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import kmod_params as kp


# ── read_param ─────────────────────────────────────────────────────────


def test_read_param_present(tmp_path):
    (tmp_path / "NVreg_X").write_text("1\n")
    assert kp.read_param("NVreg_X", root=str(tmp_path)) == "1"


def test_read_param_missing(tmp_path):
    assert kp.read_param("NVreg_X", root=str(tmp_path)) is None


# ── list_all_params ────────────────────────────────────────────────────


def test_list_all(tmp_path):
    (tmp_path / "NVreg_A").write_text("0\n")
    (tmp_path / "NVreg_B").write_text("1\n")
    out = kp.list_all_params(root=str(tmp_path))
    assert out == {"NVreg_A": "0", "NVreg_B": "1"}


def test_list_missing_root():
    assert kp.list_all_params(root="/nonexistent") == {}


# ── modprobe_dropin_recipe ─────────────────────────────────────────────


def test_recipe_contains_param_and_value():
    r = kp.modprobe_dropin_recipe("NVreg_PreserveVideoMemoryAllocations", "1")
    assert "NVreg_PreserveVideoMemoryAllocations=1" in r
    assert "modprobe.d" in r
    assert "update-initramfs" in r


# ── evaluate ───────────────────────────────────────────────────────────


def test_evaluate_clean():
    """All foot-gun rules satisfied."""
    params = {
        "NVreg_PreserveVideoMemoryAllocations": "1",
        "NVreg_EnableGpuFirmware": "1",
        "NVreg_DynamicPowerManagement": "0",
    }
    assert kp.evaluate(params) == []


def test_evaluate_flags_preserve_vmem_zero():
    """The canonical foot-gun."""
    params = {"NVreg_PreserveVideoMemoryAllocations": "0"}
    out = kp.evaluate(params)
    assert len(out) == 1
    assert out[0]["param"] == "NVreg_PreserveVideoMemoryAllocations"
    assert out[0]["current"] == "0"
    assert out[0]["recommended"] == "1"
    assert "modprobe.d" in out[0]["recipe"]


def test_evaluate_flags_gsp_disabled():
    params = {"NVreg_EnableGpuFirmware": "0"}
    out = kp.evaluate(params)
    assert any(f["param"] == "NVreg_EnableGpuFirmware" for f in out)


def test_evaluate_info_only_for_dynamic_pm_enabled():
    """DynamicPM=1 should fire info (no recipe), DynamicPM=0 should not fire."""
    params = {"NVreg_DynamicPowerManagement": "2"}
    out = kp.evaluate(params)
    assert len(out) == 1
    assert out[0]["severity"] == "info"
    # 0 → no fire
    params2 = {"NVreg_DynamicPowerManagement": "0"}
    assert kp.evaluate(params2) == []


def test_evaluate_skips_missing_params():
    """If a parameter isn't on this system, no rule fires for it."""
    params: dict = {}
    assert kp.evaluate(params) == []


def test_evaluate_multiple_footguns():
    params = {
        "NVreg_PreserveVideoMemoryAllocations": "0",
        "NVreg_EnableGpuFirmware": "0",
    }
    out = kp.evaluate(params)
    assert len(out) == 2


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_sysfs(tmp_path):
    """If /sys/module/nvidia/parameters is missing → ok=False."""
    with patch.object(kp, "_KMOD_PARAMS_ROOT",
                       str(tmp_path / "nonexistent")):
        s = kp.status()
    assert s["ok"] is False
    assert "not found" in s["reason"]


def test_status_clean(tmp_path):
    (tmp_path / "NVreg_PreserveVideoMemoryAllocations").write_text("1\n")
    (tmp_path / "NVreg_EnableGpuFirmware").write_text("1\n")
    with patch.object(kp, "_KMOD_PARAMS_ROOT", str(tmp_path)):
        s = kp.status()
    assert s["ok"] is True
    assert s["param_count"] == 2
    assert s["footgun_count"] == 0


def test_status_flags_preserve_vmem(tmp_path):
    (tmp_path / "NVreg_PreserveVideoMemoryAllocations").write_text("0\n")
    with patch.object(kp, "_KMOD_PARAMS_ROOT", str(tmp_path)):
        s = kp.status()
    assert s["footgun_count"] == 1
    assert s["worst_severity"] == "warn"


def test_status_returns_all_params(tmp_path):
    (tmp_path / "NVreg_Foo").write_text("42\n")
    (tmp_path / "NVreg_Bar").write_text("zzz\n")
    with patch.object(kp, "_KMOD_PARAMS_ROOT", str(tmp_path)):
        s = kp.status()
    assert s["params"]["NVreg_Foo"] == "42"
    assert s["params"]["NVreg_Bar"] == "zzz"
