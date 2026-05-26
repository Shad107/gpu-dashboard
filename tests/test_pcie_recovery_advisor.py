"""Tests for modules/pcie_recovery_advisor.py — F4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import pcie_recovery_advisor as adv


# ── helpers to build a fake /sys/bus/pci/devices/<bdf>/ ─────────────


def _mk_dev(root, bdf, *,
            vendor="0x10de", class_="0x030000",
            power_state="D0",
            current_link_speed="16.0 GT/s PCIe",
            max_link_speed="16.0 GT/s PCIe",
            current_link_width="16",
            max_link_width="16",
            with_flr=True,
            aer_correctable="RxErr 0\nBadTLP 0\n",
            aer_fatal="Undefined 0\nDLP 0\n",
            aer_nonfatal="Undefined 0\nDLP 0\n"):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text(vendor)
    (d / "class").write_text(class_)
    (d / "power_state").write_text(power_state)
    (d / "current_link_speed").write_text(current_link_speed)
    (d / "max_link_speed").write_text(max_link_speed)
    (d / "current_link_width").write_text(current_link_width)
    (d / "max_link_width").write_text(max_link_width)
    (d / "d3cold_allowed").write_text("1")
    (d / "aer_dev_correctable").write_text(aer_correctable)
    (d / "aer_dev_fatal").write_text(aer_fatal)
    (d / "aer_dev_nonfatal").write_text(aer_nonfatal)
    if with_flr:
        (d / "reset").write_text("")


# ── find_nvidia_bdf ─────────────────────────────────────────────────


def test_find_returns_none_when_empty(tmp_path):
    assert adv.find_nvidia_bdf(str(tmp_path)) is None


def test_find_skips_non_nvidia(tmp_path):
    _mk_dev(tmp_path, "0000:00:1f.0", vendor="0x8086")
    assert adv.find_nvidia_bdf(str(tmp_path)) is None


def test_find_skips_nvidia_audio_function(tmp_path):
    """NVIDIA cards have a .1 HDA audio function — class 0x040300.
    The advisor must pick the GPU function (class 0x030000), not
    the audio one."""
    _mk_dev(tmp_path, "0000:01:00.1",
            vendor="0x10de", class_="0x040300")
    _mk_dev(tmp_path, "0000:01:00.0",
            vendor="0x10de", class_="0x030000")
    assert adv.find_nvidia_bdf(str(tmp_path)) == "0000:01:00.0"


# ── gather_pci_state ────────────────────────────────────────────────


def test_gather_reads_all_fields(tmp_path):
    _mk_dev(tmp_path, "0000:01:00.0",
            current_link_speed="2.5 GT/s PCIe",
            current_link_width="4",
            aer_nonfatal="Undefined 0\nDLP 3\nTLP 1\n")
    s = adv.gather_pci_state("0000:01:00.0", str(tmp_path))
    assert s["bdf"] == "0000:01:00.0"
    assert s["current_link_speed"] == "2.5 GT/s PCIe"
    assert s["current_link_width"] == "4"
    assert s["max_link_speed"] == "16.0 GT/s PCIe"
    assert s["flr_supported"] is True
    assert s["aer"]["aer_dev_nonfatal"] == 4  # 3 + 1


def test_gather_handles_missing_flr(tmp_path):
    _mk_dev(tmp_path, "0000:01:00.0", with_flr=False)
    s = adv.gather_pci_state("0000:01:00.0", str(tmp_path))
    assert s["flr_supported"] is False


# ── classify_state ──────────────────────────────────────────────────


def test_classify_healthy():
    pci = {"current_link_speed": "16.0 GT/s PCIe",
           "current_link_width": "16",
           "power_state": "D0",
           "aer": {"aer_dev_correctable": 0,
                   "aer_dev_fatal": 0,
                   "aer_dev_nonfatal": 0}}
    d = adv.classify_state(pci, nvml_verdict="ok")
    assert d["broken"] is False
    assert d["severity"] == "ok"
    assert d["signals"] == []


def test_classify_link_down():
    """The exact pattern observed on the dev VM: link speed
    'Unknown' + width 63 + NVML handle fail."""
    pci = {"current_link_speed": "Unknown",
           "current_link_width": "63",
           "power_state": "D0",
           "aer": {"aer_dev_correctable": 0,
                   "aer_dev_fatal": 0, "aer_dev_nonfatal": 0}}
    d = adv.classify_state(pci, nvml_verdict="device_handle_unavailable")
    assert d["broken"] is True
    assert d["severity"] == "err"
    assert "link_speed_unknown" in d["signals"]
    assert "link_width_invalid" in d["signals"]
    assert "nvml_handle_unavailable" in d["signals"]


def test_classify_aer_fatal():
    pci = {"current_link_speed": "16.0 GT/s PCIe",
           "current_link_width": "16",
           "power_state": "D0",
           "aer": {"aer_dev_correctable": 0,
                   "aer_dev_fatal": 5,
                   "aer_dev_nonfatal": 0}}
    d = adv.classify_state(pci, nvml_verdict="ok")
    assert d["broken"] is True
    assert d["severity"] == "err"
    assert "aer_fatal_errors" in d["signals"]


def test_classify_power_state_anomaly():
    pci = {"current_link_speed": "16.0 GT/s PCIe",
           "current_link_width": "16",
           "power_state": "D3cold",
           "aer": {"aer_dev_correctable": 0,
                   "aer_dev_fatal": 0, "aer_dev_nonfatal": 0}}
    d = adv.classify_state(pci, nvml_verdict="ok")
    assert d["broken"] is True
    assert "power_state_D3cold" in d["signals"]


# ── build_recovery_plan ─────────────────────────────────────────────


def test_plan_includes_baremetal_basics():
    plan = adv.build_recovery_plan("0000:01:00.0", "bare", True)
    ids = [s["id"] for s in plan]
    # Always include the in-guest basics + FLR
    assert "persistence_restart" in ids
    assert "module_reload" in ids
    assert "pcie_remove_rescan" in ids
    assert "flr" in ids
    # Never include host-side steps on bare metal
    assert "vm_restart" not in ids
    assert "host_vfio_rebind" not in ids
    # Always include the physical fallback
    assert "reseat_cable" in ids


def test_plan_drops_flr_when_unsupported():
    plan = adv.build_recovery_plan("0000:01:00.0", "bare", False)
    ids = [s["id"] for s in plan]
    assert "flr" not in ids


def test_plan_includes_vm_steps_under_kvm():
    plan = adv.build_recovery_plan("0000:01:00.0", "kvm", True)
    ids = [s["id"] for s in plan]
    assert "vm_restart" in ids
    assert "host_vfio_rebind" in ids


def test_plan_substitutes_bdf_in_commands():
    plan = adv.build_recovery_plan("0000:42:00.0", "bare", True)
    flr = next(s for s in plan if s["id"] == "flr")
    assert "0000:42:00.0" in flr["command"]


def test_plan_step_shape():
    """Every step must carry id/label/command/scope/safety/why."""
    plan = adv.build_recovery_plan("0000:01:00.0", "kvm", True)
    required = {"id", "label", "command", "scope", "safety", "why"}
    for s in plan:
        assert required <= set(s.keys())
        assert s["scope"] in ("guest", "host", "physical")
        assert s["safety"] in ("safe", "kills_workloads",
                                "needs_host_access", "manual")


# ── status() integration ────────────────────────────────────────────


def test_status_no_gpu(tmp_path):
    s = adv.status(pci_root=str(tmp_path))
    assert s["ok"] is False
    assert s["verdict"]["verdict"] == "no_nvidia_gpu"


def test_status_healthy_gpu(tmp_path, monkeypatch):
    _mk_dev(tmp_path, "0000:01:00.0")
    monkeypatch.setattr(adv, "detect_virt", lambda: "bare")
    # Pretend NVML reports ok so the diagnosis is healthy.
    from gpu_dashboard.modules import _nvml
    monkeypatch.setattr(_nvml, "status",
                          lambda cfg=None: {
                              "verdict": {"verdict": "ok"}})
    s = adv.status(pci_root=str(tmp_path))
    assert s["ok"] is True
    assert s["bdf"] == "0000:01:00.0"
    assert s["diagnosis"]["broken"] is False
    assert s["verdict"]["verdict"] == "ok"
    # Plan still surfaced — operator may want to read it even
    # when nothing is broken right now.
    assert len(s["plan"]) >= 4


def test_status_broken_kvm_gpu(tmp_path, monkeypatch):
    """End-to-end: VM with broken link reproduces the exact case
    observed live on the dev host."""
    _mk_dev(tmp_path, "0000:01:00.0",
            current_link_speed="Unknown",
            current_link_width="63")
    monkeypatch.setattr(adv, "detect_virt", lambda: "kvm")
    from gpu_dashboard.modules import _nvml
    monkeypatch.setattr(_nvml, "status",
                          lambda cfg=None: {
                              "verdict": {"verdict":
                                          "device_handle_unavailable"}})
    s = adv.status(pci_root=str(tmp_path))
    assert s["verdict"]["verdict"] == "recovery_recommended"
    assert s["virt"] == "kvm"
    ids = [step["id"] for step in s["plan"]]
    # VM-only steps surface
    assert "vm_restart" in ids
    assert "host_vfio_rebind" in ids
    # Physical fallback always last-ish
    assert ids[-1] == "reseat_cable"
