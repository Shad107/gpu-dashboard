"""R&D #24.3 — DKMS rebuild status tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import dkms_status as ds


# ── parse_dkms_status ──────────────────────────────────────────────────


def test_parse_slash_format():
    """Modern DKMS: nvidia/535.86.05, 6.5.0-26-generic, x86_64: installed"""
    text = "nvidia/535.86.05, 6.5.0-26-generic, x86_64: installed\n"
    out = ds.parse_dkms_status(text)
    assert len(out) == 1
    assert out[0]["version"] == "535.86.05"
    assert out[0]["kernel"] == "6.5.0-26-generic"
    assert out[0]["state"] == "installed"


def test_parse_comma_format():
    """Older DKMS: nvidia, 535.86.05, 6.5.0-26-generic, x86_64: installed"""
    text = "nvidia, 535.86.05, 6.5.0-26-generic, x86_64: installed\n"
    out = ds.parse_dkms_status(text)
    assert out[0]["version"] == "535.86.05"
    assert out[0]["kernel"] == "6.5.0-26-generic"


def test_parse_multiple_kernels():
    text = ("nvidia/590.48.01, 6.17.0-23-generic, x86_64: installed\n"
            "nvidia/590.48.01, 6.17.0-29-generic, x86_64: installed\n")
    out = ds.parse_dkms_status(text)
    assert len(out) == 2
    assert {e["kernel"] for e in out} == {"6.17.0-23-generic", "6.17.0-29-generic"}


def test_parse_skips_non_nvidia():
    text = ("zfs/2.2.0, 6.5.0-26-generic, x86_64: installed\n"
            "nvidia/535.86.05, 6.5.0-26-generic, x86_64: installed\n")
    out = ds.parse_dkms_status(text)
    assert len(out) == 1
    assert out[0]["module"] == "nvidia"


def test_parse_empty():
    assert ds.parse_dkms_status("") == []


def test_parse_lowercase_state():
    text = "nvidia/535.86.05, 6.5.0, x86_64: BUILT\n"
    out = ds.parse_dkms_status(text)
    assert out[0]["state"] == "built"


# ── nvidia_ko_present ──────────────────────────────────────────────────


def test_ko_present_compressed(tmp_path):
    kdir = tmp_path / "6.5.0-26" / "updates" / "dkms"
    kdir.mkdir(parents=True)
    (kdir / "nvidia.ko.zst").touch()
    assert ds.nvidia_ko_present("6.5.0-26", mod_root=str(tmp_path)) is True


def test_ko_present_uncompressed(tmp_path):
    kdir = tmp_path / "6.5.0-26" / "updates" / "dkms"
    kdir.mkdir(parents=True)
    (kdir / "nvidia.ko").touch()
    assert ds.nvidia_ko_present("6.5.0-26", mod_root=str(tmp_path)) is True


def test_ko_present_missing(tmp_path):
    kdir = tmp_path / "6.5.0-26" / "updates" / "dkms"
    kdir.mkdir(parents=True)
    # No nvidia.ko
    assert ds.nvidia_ko_present("6.5.0-26", mod_root=str(tmp_path)) is False


def test_ko_present_dir_missing(tmp_path):
    assert ds.nvidia_ko_present("6.5.0-99", mod_root=str(tmp_path)) is False


# ── classify ───────────────────────────────────────────────────────────


def test_classify_no_entries_with_kernel():
    v = ds.classify(kernel="6.5.0-26", dkms_entries=[], ko_present=False)
    assert v["verdict"] == "no_nvidia_dkms"


def test_classify_no_kernel_no_entries():
    v = ds.classify(kernel="", dkms_entries=[], ko_present=False)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    entries = [{"module": "nvidia", "version": "535.86", "kernel": "6.5.0-26",
                 "arch": "x86_64", "state": "installed"}]
    v = ds.classify("6.5.0-26", entries, ko_present=True)
    assert v["verdict"] == "ok"


def test_classify_rebuild_needed_no_match_for_kernel():
    """DKMS has nvidia for an older kernel but not the running one."""
    entries = [{"module": "nvidia", "version": "535.86", "kernel": "6.4.0-1",
                 "arch": "x86_64", "state": "installed"}]
    v = ds.classify("6.5.0-26", entries, ko_present=False)
    assert v["verdict"] == "rebuild_needed"
    assert "dkms autoinstall" in v["recovery"]


def test_classify_rebuild_needed_state_not_installed():
    entries = [{"module": "nvidia", "version": "535.86", "kernel": "6.5.0-26",
                 "arch": "x86_64", "state": "added"}]
    v = ds.classify("6.5.0-26", entries, ko_present=True)
    assert v["verdict"] == "rebuild_needed"
    assert "added" in v["reason"]


def test_classify_rebuild_needed_ko_missing():
    """DKMS says installed but the .ko isn't on disk — out of sync."""
    entries = [{"module": "nvidia", "version": "535.86", "kernel": "6.5.0-26",
                 "arch": "x86_64", "state": "installed"}]
    v = ds.classify("6.5.0-26", entries, ko_present=False)
    assert v["verdict"] == "rebuild_needed"
    assert "dkms remove" in v["recovery"]


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_dkms():
    with patch.object(ds, "run_dkms_status", return_value=None):
        with patch.object(ds, "running_kernel", return_value="6.5.0-26"):
            s = ds.status()
    assert s["ok"] is False
    assert s["verdict"]["verdict"] == "dkms_missing"


def test_status_clean_installation():
    raw = "nvidia/535.86, 6.5.0-26-generic, x86_64: installed\n"
    with patch.object(ds, "run_dkms_status", return_value=raw):
        with patch.object(ds, "running_kernel",
                          return_value="6.5.0-26-generic"):
            with patch.object(ds, "nvidia_ko_present", return_value=True):
                s = ds.status()
    assert s["ok"] is True
    assert s["verdict"]["verdict"] == "ok"


def test_status_rebuild_after_kernel_upgrade():
    """User upgraded kernel ; DKMS has nvidia for the old one only."""
    raw = "nvidia/535.86, 6.4.0-1, x86_64: installed\n"
    with patch.object(ds, "run_dkms_status", return_value=raw):
        with patch.object(ds, "running_kernel",
                          return_value="6.5.0-26-generic"):
            with patch.object(ds, "nvidia_ko_present", return_value=False):
                s = ds.status()
    assert s["verdict"]["verdict"] == "rebuild_needed"
    assert "6.5.0-26-generic" in s["verdict"]["recovery"]
