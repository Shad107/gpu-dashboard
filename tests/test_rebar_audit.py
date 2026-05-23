"""R&D #27.1 — ReBAR / BAR-size auditor tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import rebar_audit as ra


# ── parse_bar_size ─────────────────────────────────────────────────────


def test_parse_size_256_mib():
    """0x10000000 - 0 + 1 = 256 MiB"""
    s = ra.parse_bar_size("0x0000000000000000", "0x000000000fffffff")
    assert s == 256 * 1024 * 1024


def test_parse_size_24_gib():
    """0x600000000 = 24 GiB"""
    s = ra.parse_bar_size("0x0", "0x5ffffffff")
    assert s == 24 * 1024 ** 3


def test_parse_size_unused_bar():
    assert ra.parse_bar_size("0x0", "0x0") is None


def test_parse_size_garbage():
    assert ra.parse_bar_size("xx", "yy") is None


# ── read_bars ──────────────────────────────────────────────────────────


def test_read_bars_six_rows(tmp_path):
    bdf = tmp_path / "0000:01:00.0"; bdf.mkdir()
    (bdf / "resource").write_text(
        # BAR0: 16 MiB MMIO
        "0x0000000080000000 0x0000000080ffffff 0x00040200\n"
        # BAR1: 256 MiB (small — no ReBAR)
        "0x0000380000000000 0x000038000fffffff 0x0014220c\n"
        # BAR2: unused
        "0x0000000000000000 0x0000000000000000 0x00000000\n"
    )
    bars = ra.read_bars("0000:01:00.0", sys_root=str(tmp_path))
    assert len(bars) == 3
    assert bars[0] == 16 * 1024 * 1024
    assert bars[1] == 256 * 1024 * 1024
    assert bars[2] is None


def test_read_bars_missing(tmp_path):
    assert ra.read_bars("0000:99:00.0", sys_root=str(tmp_path)) == []


# ── _normalize_bdf ─────────────────────────────────────────────────────


def test_normalize_8_digit_domain():
    assert ra._normalize_bdf("00000000:01:00.0") == "0000:01:00.0"


def test_normalize_already_normalized():
    assert ra._normalize_bdf("0000:01:00.0") == "0000:01:00.0"


# ── list_nvidia_bdfs ───────────────────────────────────────────────────


def test_list_nvidia(tmp_path):
    n = tmp_path / "0000:01:00.0"; n.mkdir()
    (n / "vendor").write_text("0x10de\n")
    other = tmp_path / "0000:02:00.0"; other.mkdir()
    (other / "vendor").write_text("0x8086\n")
    out = ra.list_nvidia_bdfs(sys_root=str(tmp_path))
    assert out == ["0000:01:00.0"]


# ── classify ───────────────────────────────────────────────────────────


def test_classify_rebar_on_24gib():
    """BAR1 = 24 GiB, total = 24 GiB → rebar_on."""
    v = ra.classify(24 * 1024 ** 3, 24 * 1024 ** 3)
    assert v["verdict"] == "rebar_on"
    assert v["bar1_pct_of_vram"] == 100.0


def test_classify_rebar_off_256mib_on_24gib():
    """BAR1 256 MiB, total 24 GiB → rebar_off."""
    v = ra.classify(256 * 1024 * 1024, 24 * 1024 ** 3)
    assert v["verdict"] == "rebar_off"
    assert v["bar1_pct_of_vram"] is not None
    assert "Above 4G" in v["recommendation"]


def test_classify_partial():
    """BAR1 ~10 GiB of 24 GiB → partial."""
    v = ra.classify(10 * 1024 ** 3, 24 * 1024 ** 3)
    assert v["verdict"] == "partial"


def test_classify_unknown_missing_inputs():
    assert ra.classify(None, 24 * 1024 ** 3)["verdict"] == "unknown"
    assert ra.classify(256 * 1024 * 1024, None)["verdict"] == "unknown"
    assert ra.classify(256 * 1024 * 1024, 0)["verdict"] == "unknown"


def test_classify_rebar_on_above_threshold():
    """BAR1 ≥ 80% of VRAM → rebar_on. Below → partial."""
    v_on = ra.classify(int(0.85 * 24 * 1024 ** 3), 24 * 1024 ** 3)
    v_partial = ra.classify(int(0.50 * 24 * 1024 ** 3), 24 * 1024 ** 3)
    assert v_on["verdict"] == "rebar_on"
    assert v_partial["verdict"] == "partial"


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_smi():
    with patch.object(ra, "list_nvidia_bdfs", return_value=[]):
        with patch.object(ra, "gpu_memory_total_bytes", return_value={}):
            s = ra.status()
    assert s["card_count"] == 0


def test_status_rebar_off_real_layout(tmp_path):
    bdf_dir = tmp_path / "0000:01:00.0"; bdf_dir.mkdir()
    (bdf_dir / "vendor").write_text("0x10de\n")
    (bdf_dir / "resource").write_text(
        "0x0000000080000000 0x0000000080ffffff 0x00040200\n"
        "0x0000380000000000 0x000038000fffffff 0x0014220c\n"  # BAR1 256 MiB
    )
    with patch.object(ra, "_PCI_ROOT", str(tmp_path)):
        with patch.object(ra, "list_nvidia_bdfs",
                          return_value=["0000:01:00.0"]):
            with patch.object(ra, "read_bars",
                              return_value=[16 * 1024 * 1024,
                                             256 * 1024 * 1024]):
                with patch.object(ra, "gpu_memory_total_bytes",
                                  return_value={"00000000:01:00.0":
                                                 24 * 1024 ** 3}):
                    s = ra.status()
    assert s["cards"][0]["verdict"]["verdict"] == "rebar_off"


def test_status_rebar_on(tmp_path):
    with patch.object(ra, "list_nvidia_bdfs",
                       return_value=["0000:01:00.0"]):
        with patch.object(ra, "read_bars",
                          return_value=[16 * 1024 * 1024,
                                          24 * 1024 ** 3]):  # BAR1 = 24 GiB
            with patch.object(ra, "gpu_memory_total_bytes",
                              return_value={"00000000:01:00.0":
                                             24 * 1024 ** 3}):
                s = ra.status()
    assert s["cards"][0]["verdict"]["verdict"] == "rebar_on"
    assert s["worst_verdict"] == "rebar_on"
