"""R&D #26.5 — PCIe link-width watcher tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import pcie_width_watcher as pw


# ── parse_width ────────────────────────────────────────────────────────


def test_parse_width_int():
    assert pw.parse_width("16") == 16
    assert pw.parse_width("8") == 8


def test_parse_width_unknown():
    assert pw.parse_width("Unknown") is None
    assert pw.parse_width(None) is None
    assert pw.parse_width("") is None


def test_parse_width_garbage():
    assert pw.parse_width("xx") is None


# ── parse_speed_gts ────────────────────────────────────────────────────


def test_parse_speed_pcie4():
    assert pw.parse_speed_gts("16.0 GT/s PCIe") == 16.0


def test_parse_speed_pcie3():
    assert pw.parse_speed_gts("8.0 GT/s PCIe") == 8.0


def test_parse_speed_pcie5():
    assert pw.parse_speed_gts("32.0 GT/s PCIe") == 32.0


def test_parse_speed_unknown():
    assert pw.parse_speed_gts("Unknown") is None
    assert pw.parse_speed_gts(None) is None
    assert pw.parse_speed_gts("") is None


# ── gen_from_gts ───────────────────────────────────────────────────────


def test_gen_pcie3():
    assert pw.gen_from_gts(8.0) == 3


def test_gen_pcie4():
    assert pw.gen_from_gts(16.0) == 4


def test_gen_pcie5():
    assert pw.gen_from_gts(32.0) == 5


def test_gen_unknown():
    assert pw.gen_from_gts(None) is None
    assert pw.gen_from_gts(99.9) is None


# ── list_nvidia_bdfs ───────────────────────────────────────────────────


def test_list_nvidia_skip_non_nvidia(tmp_path):
    a = tmp_path / "0000:01:00.0"; a.mkdir()
    (a / "vendor").write_text("0x10de\n")
    b = tmp_path / "0000:02:00.0"; b.mkdir()
    (b / "vendor").write_text("0x1002\n")  # AMD
    out = pw.list_nvidia_bdfs(sys_root=str(tmp_path))
    assert out == ["0000:01:00.0"]


def test_list_nvidia_empty(tmp_path):
    assert pw.list_nvidia_bdfs(sys_root=str(tmp_path)) == []


# ── read_link ──────────────────────────────────────────────────────────


def test_read_link_full(tmp_path):
    bdf = tmp_path / "0000:01:00.0"; bdf.mkdir()
    (bdf / "current_link_width").write_text("16\n")
    (bdf / "max_link_width").write_text("16\n")
    (bdf / "current_link_speed").write_text("16.0 GT/s PCIe\n")
    (bdf / "max_link_speed").write_text("16.0 GT/s PCIe\n")
    out = pw.read_link("0000:01:00.0", sys_root=str(tmp_path))
    assert out["current_width"] == 16
    assert out["max_width"] == 16
    assert out["current_speed_gts"] == 16.0
    assert out["current_gen"] == 4


def test_read_link_missing(tmp_path):
    bdf = tmp_path / "0000:01:00.0"; bdf.mkdir()
    out = pw.read_link("0000:01:00.0", sys_root=str(tmp_path))
    assert out["current_width"] is None
    assert out["max_width"] is None


# ── classify_link ──────────────────────────────────────────────────────


def test_classify_ok():
    v = pw.classify_link({
        "current_width": 16, "max_width": 16,
        "current_speed_gts": 16.0, "max_speed_gts": 16.0,
        "current_gen": 4, "max_gen": 4,
    })
    assert v["verdict"] == "ok"


def test_classify_unknown_when_unreadable():
    v = pw.classify_link({"current_width": None, "max_width": None})
    assert v["verdict"] == "unknown"


def test_classify_unknown_out_of_spec():
    """63 is the spurious value we observed on this rig with GPU off-bus."""
    v = pw.classify_link({
        "current_width": 63, "max_width": 63,
        "current_speed_gts": None, "max_speed_gts": None,
        "current_gen": None, "max_gen": None,
    })
    assert v["verdict"] == "unknown"
    assert "Out-of-spec" in v["reason"]
    assert "modprobe" in v["recovery"]


def test_classify_width_downgrade():
    v = pw.classify_link({
        "current_width": 8, "max_width": 16,
        "current_speed_gts": 16.0, "max_speed_gts": 16.0,
        "current_gen": 4, "max_gen": 4,
    })
    assert v["verdict"] == "downgraded_width"
    assert "Reseat" in v["recovery"]


def test_classify_speed_downgrade():
    v = pw.classify_link({
        "current_width": 16, "max_width": 16,
        "current_speed_gts": 8.0, "max_speed_gts": 16.0,
        "current_gen": 3, "max_gen": 4,
    })
    assert v["verdict"] == "downgraded_speed"
    assert "pcie_aspm" in v["recovery"]


def test_classify_both_downgraded():
    v = pw.classify_link({
        "current_width": 4, "max_width": 16,
        "current_speed_gts": 8.0, "max_speed_gts": 16.0,
        "current_gen": 3, "max_gen": 4,
    })
    assert v["verdict"] == "downgraded_both"
    assert "x4" in v["reason"]


# ── status ────────────────────────────────────────────────────────────


def test_status_no_gpus():
    with patch.object(pw, "list_nvidia_bdfs", return_value=[]):
        s = pw.status()
    assert s["device_count"] == 0
    assert s["worst_verdict"] == "no_gpus"


def test_status_aggregates_worst():
    """Mix of ok + downgraded_width → worst_verdict = downgraded_width."""
    def fake_read(bdf, sys_root=pw._PCI_ROOT):
        if bdf == "0000:01:00.0":
            return {"bdf": bdf, "current_width": 16, "max_width": 16,
                    "current_speed_gts": 16.0, "max_speed_gts": 16.0,
                    "current_gen": 4, "max_gen": 4}
        return {"bdf": bdf, "current_width": 4, "max_width": 16,
                "current_speed_gts": 16.0, "max_speed_gts": 16.0,
                "current_gen": 4, "max_gen": 4}
    with patch.object(pw, "list_nvidia_bdfs",
                      return_value=["0000:01:00.0", "0000:02:00.0"]):
        with patch.object(pw, "read_link", side_effect=fake_read):
            s = pw.status()
    assert s["worst_verdict"] == "downgraded_width"


def test_status_all_ok():
    def fake_read(bdf, sys_root=pw._PCI_ROOT):
        return {"bdf": bdf, "current_width": 16, "max_width": 16,
                "current_speed_gts": 16.0, "max_speed_gts": 16.0,
                "current_gen": 4, "max_gen": 4}
    with patch.object(pw, "list_nvidia_bdfs", return_value=["0000:01:00.0"]):
        with patch.object(pw, "read_link", side_effect=fake_read):
            s = pw.status()
    assert s["worst_verdict"] == "ok"
