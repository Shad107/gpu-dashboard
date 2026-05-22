"""R&D #18.3 — CUDA_VISIBLE_DEVICES UUID drift detector tests."""
import os
import tempfile
from unittest.mock import patch
import pytest
from gpu_dashboard.modules import cuda_advisor as ca


GPUS = [
    {"index": 0, "uuid": "GPU-aaaa1111-2222-3333-4444-555555555555",
     "name": "NVIDIA GeForce RTX 3090"},
    {"index": 1, "uuid": "GPU-bbbb1111-2222-3333-4444-555555555555",
     "name": "NVIDIA GeForce RTX 4090"},
]


# ── parse_cuda_env ─────────────────────────────────────────────────────


def test_parse_empty():
    assert ca.parse_cuda_env("") == []


def test_parse_single_index():
    assert ca.parse_cuda_env("0") == ["0"]


def test_parse_multi_index():
    assert ca.parse_cuda_env("0,1,2") == ["0", "1", "2"]


def test_parse_uuid():
    assert ca.parse_cuda_env("GPU-aaaa1111-2222-3333-4444-555555555555") == [
        "GPU-aaaa1111-2222-3333-4444-555555555555"
    ]


def test_parse_strips_whitespace():
    assert ca.parse_cuda_env(" 0 , 1 , ") == ["0", "1"]


# ── resolve_entry ──────────────────────────────────────────────────────


def test_resolve_index_match():
    g = ca.resolve_entry("0", GPUS)
    assert g is not None
    assert g["index"] == 0


def test_resolve_index_out_of_range():
    assert ca.resolve_entry("5", GPUS) is None


def test_resolve_uuid_match():
    g = ca.resolve_entry("GPU-aaaa1111-2222-3333-4444-555555555555", GPUS)
    assert g is not None and g["index"] == 0


def test_resolve_uuid_short_prefix():
    """GPU-xxxx short form also matches by prefix."""
    g = ca.resolve_entry("GPU-aaaa1111", GPUS)
    assert g is not None and g["index"] == 0


def test_resolve_uuid_unknown():
    assert ca.resolve_entry("GPU-ffff9999-0000-0000-0000-000000000000", GPUS) is None


# ── read_proc_env ──────────────────────────────────────────────────────


def test_read_proc_env_finds_var():
    with tempfile.TemporaryDirectory() as td:
        proc_dir = os.path.join(td, "1234")
        os.makedirs(proc_dir)
        with open(os.path.join(proc_dir, "environ"), "wb") as f:
            f.write(b"PATH=/usr/bin\x00CUDA_VISIBLE_DEVICES=0,1\x00HOME=/root\x00")
        with patch.object(ca, "_iter_pids", return_value=[1234]):
            # Patch the path resolution by monkeypatching read_proc_env target
            orig = open
            def fake_open(p, *a, **k):
                if p == "/proc/1234/environ":
                    return orig(os.path.join(proc_dir, "environ"), *a, **k)
                if p == "/proc/1234/comm":
                    return orig("/dev/null", *a, **k)
                return orig(p, *a, **k)
            with patch("builtins.open", side_effect=fake_open):
                val = ca.read_proc_env(1234)
        assert val == "0,1"


def test_read_proc_env_missing_var():
    with tempfile.TemporaryDirectory() as td:
        proc_dir = os.path.join(td, "9999")
        os.makedirs(proc_dir)
        with open(os.path.join(proc_dir, "environ"), "wb") as f:
            f.write(b"PATH=/usr/bin\x00HOME=/root\x00")
        orig = open
        def fake_open(p, *a, **k):
            if p == "/proc/9999/environ":
                return orig(os.path.join(proc_dir, "environ"), *a, **k)
            return orig(p, *a, **k)
        with patch("builtins.open", side_effect=fake_open):
            val = ca.read_proc_env(9999)
        assert val is None


def test_read_proc_env_at_start_of_buffer():
    """CUDA_VISIBLE_DEVICES is the FIRST variable."""
    with tempfile.TemporaryDirectory() as td:
        proc_dir = os.path.join(td, "111")
        os.makedirs(proc_dir)
        with open(os.path.join(proc_dir, "environ"), "wb") as f:
            f.write(b"CUDA_VISIBLE_DEVICES=GPU-abc\x00OTHER=x\x00")
        orig = open
        def fake_open(p, *a, **k):
            if p == "/proc/111/environ":
                return orig(os.path.join(proc_dir, "environ"), *a, **k)
            return orig(p, *a, **k)
        with patch("builtins.open", side_effect=fake_open):
            val = ca.read_proc_env(111)
        assert val == "GPU-abc"


def test_read_proc_env_unreadable_returns_none():
    assert ca.read_proc_env(99999999) is None  # very unlikely real pid


# ── scan_processes ─────────────────────────────────────────────────────


def test_scan_processes_skips_pids_without_env(tmp_path):
    p1 = tmp_path / "100"; p1.mkdir()
    (p1 / "environ").write_bytes(b"PATH=/x\x00")
    (p1 / "comm").write_text("noenv")
    procs = ca.scan_processes(gpus=GPUS, proc_root=str(tmp_path))
    assert procs == []


def test_scan_processes_flags_drift(tmp_path):
    p1 = tmp_path / "200"; p1.mkdir()
    (p1 / "environ").write_bytes(b"CUDA_VISIBLE_DEVICES=5\x00")
    (p1 / "comm").write_text("bad_proc")
    procs = ca.scan_processes(gpus=GPUS, proc_root=str(tmp_path))
    assert len(procs) == 1
    assert procs[0]["pid"] == 200
    assert procs[0]["has_drift"] is True
    assert procs[0]["resolved"][0]["drift"] is True
    assert "out of range" in procs[0]["resolved"][0]["reason"]


def test_scan_processes_clean_no_drift(tmp_path):
    p1 = tmp_path / "300"; p1.mkdir()
    (p1 / "environ").write_bytes(b"CUDA_VISIBLE_DEVICES=0\x00")
    (p1 / "comm").write_text("good_proc")
    procs = ca.scan_processes(gpus=GPUS, proc_root=str(tmp_path))
    assert len(procs) == 1
    assert procs[0]["has_drift"] is False


def test_scan_processes_multi_with_one_drift(tmp_path):
    p1 = tmp_path / "400"; p1.mkdir()
    (p1 / "environ").write_bytes(b"CUDA_VISIBLE_DEVICES=0,9\x00")
    (p1 / "comm").write_text("mixed")
    procs = ca.scan_processes(gpus=GPUS, proc_root=str(tmp_path))
    assert procs[0]["has_drift"] is True
    drifts = [r for r in procs[0]["resolved"] if r["drift"]]
    assert len(drifts) == 1
    assert drifts[0]["entry"] == "9"


# ── status ────────────────────────────────────────────────────────────


def test_status_no_nvidia_smi():
    with patch.object(ca, "list_gpu_uuids", return_value=[]):
        with patch.object(ca, "scan_processes", return_value=[]):
            s = ca.status()
    assert s["gpu_count"] == 0
    assert "unreachable" in s["recommendation"]


def test_status_recommendation_when_drift():
    with patch.object(ca, "list_gpu_uuids", return_value=GPUS):
        with patch.object(ca, "scan_processes", return_value=[
            {"pid": 1, "comm": "x", "raw": "9", "entries": ["9"],
             "resolved": [{"entry": "9", "gpu": None, "drift": True,
                            "reason": "index out of range"}],
             "has_drift": True},
        ]):
            s = ca.status()
    assert s["drift_count"] == 1
    assert "Restart" in s["recommendation"]


def test_status_clean_recommendation():
    with patch.object(ca, "list_gpu_uuids", return_value=GPUS):
        with patch.object(ca, "scan_processes", return_value=[
            {"pid": 1, "comm": "x", "raw": "0", "entries": ["0"],
             "resolved": [{"entry": "0", "gpu": GPUS[0], "drift": False,
                            "reason": "ok"}],
             "has_drift": False},
        ]):
            s = ca.status()
    assert s["drift_count"] == 0
    assert "resolve cleanly" in s["recommendation"]
