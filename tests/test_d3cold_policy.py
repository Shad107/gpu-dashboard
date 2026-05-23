"""R&D #29.3 — parent-bridge D3cold-policy auditor tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import d3cold_policy as dp


# ── list_nvidia_bdfs ───────────────────────────────────────────────────


def test_list_nvidia(tmp_path):
    n = tmp_path / "0000:01:00.0"; n.mkdir()
    (n / "vendor").write_text("0x10de\n")
    other = tmp_path / "0000:02:00.0"; other.mkdir()
    (other / "vendor").write_text("0x1002\n")
    out = dp.list_nvidia_bdfs(sys_root=str(tmp_path))
    assert out == ["0000:01:00.0"]


# ── find_parent_bridge ─────────────────────────────────────────────────


def test_find_parent_via_symlink(tmp_path):
    """Symlink target encodes the parent bridge BDF."""
    real = tmp_path / "devices" / "pci0000:00" / "0000:00:1c.0" / "0000:01:00.0"
    real.mkdir(parents=True)
    link_dir = tmp_path / "bus"
    link_dir.mkdir()
    os.symlink(real, link_dir / "0000:01:00.0")
    parent = dp.find_parent_bridge("0000:01:00.0", sys_root=str(link_dir))
    assert parent == "0000:00:1c.0"


def test_find_parent_no_link(tmp_path):
    assert dp.find_parent_bridge("0000:99:00.0",
                                    sys_root=str(tmp_path)) is None


def test_find_parent_path_with_nested_bdfs(tmp_path):
    """A multi-level PCIe bridge tree."""
    real = (tmp_path / "devices" / "pci0000:00" / "0000:00:01.0"
            / "0000:01:00.0" / "0000:02:00.0")
    real.mkdir(parents=True)
    link_dir = tmp_path / "bus"; link_dir.mkdir()
    os.symlink(real, link_dir / "0000:02:00.0")
    parent = dp.find_parent_bridge("0000:02:00.0", sys_root=str(link_dir))
    assert parent == "0000:01:00.0"


# ── read_bridge_d3 ─────────────────────────────────────────────────────


def test_read_bridge(tmp_path):
    b = tmp_path / "0000:00:1c.0"; b.mkdir()
    (b / "d3cold_allowed").write_text("0\n")
    state = dp.read_bridge_d3("0000:00:1c.0", sys_root=str(tmp_path))
    assert state["d3cold_allowed"] == "0"
    assert state["d3cold_delay_ms"] is None


def test_read_bridge_missing(tmp_path):
    state = dp.read_bridge_d3("0000:99:99.0", sys_root=str(tmp_path))
    assert state["d3cold_allowed"] is None


# ── classify ───────────────────────────────────────────────────────────


def test_classify_aligned_on():
    v = dp.classify(gpu_control="on", bridge_d3cold_allowed="0")
    assert v["verdict"] == "aligned_on"


def test_classify_aligned_off():
    v = dp.classify(gpu_control="auto", bridge_d3cold_allowed="1")
    assert v["verdict"] == "aligned_off"


def test_classify_mismatched_strict():
    """GPU auto + bridge=0 → silent perf-no-savings tax."""
    v = dp.classify(gpu_control="auto", bridge_d3cold_allowed="0")
    assert v["verdict"] == "mismatched_strict"
    assert "spins idle" in v["reason"]


def test_classify_mismatched_eager():
    """GPU on + bridge=1 → bridge can drag GPU to D3cold."""
    v = dp.classify(gpu_control="on", bridge_d3cold_allowed="1")
    assert v["verdict"] == "mismatched_eager"
    assert "udev" in v["recommendation"]


def test_classify_unknown_missing_inputs():
    assert dp.classify(None, "1")["verdict"] == "unknown"
    assert dp.classify("on", None)["verdict"] == "unknown"


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_gpus():
    with patch.object(dp, "list_nvidia_bdfs", return_value=[]):
        s = dp.status()
    assert s["device_count"] == 0
    assert s["worst_verdict"] == "no_gpus"


def test_status_aligned_on(tmp_path):
    with patch.object(dp, "list_nvidia_bdfs",
                       return_value=["0000:01:00.0"]):
        with patch.object(dp, "find_parent_bridge",
                          return_value="0000:00:1c.0"):
            with patch.object(dp, "read_gpu_control", return_value="on"):
                with patch.object(dp, "read_bridge_d3",
                                  return_value={"bdf": "0000:00:1c.0",
                                                 "d3cold_allowed": "0",
                                                 "d3cold_delay_ms": None,
                                                 "power_control": "on"}):
                    s = dp.status()
    assert s["worst_verdict"] == "aligned_on"


def test_status_flag_mismatched_strict(tmp_path):
    with patch.object(dp, "list_nvidia_bdfs",
                       return_value=["0000:01:00.0"]):
        with patch.object(dp, "find_parent_bridge",
                          return_value="0000:00:1c.0"):
            with patch.object(dp, "read_gpu_control", return_value="auto"):
                with patch.object(dp, "read_bridge_d3",
                                  return_value={"bdf": "0000:00:1c.0",
                                                 "d3cold_allowed": "0",
                                                 "d3cold_delay_ms": None,
                                                 "power_control": "on"}):
                    s = dp.status()
    assert s["worst_verdict"] == "mismatched_strict"
