"""R&D #21.5 — SD / ComfyUI cache janitor tests."""
import os
import time
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import sd_cache_janitor as sj


@pytest.fixture(autouse=True)
def _clear_cache():
    sj._SCAN_CACHE.clear()
    yield
    sj._SCAN_CACHE.clear()


# ── existing_targets ───────────────────────────────────────────────────


def test_existing_targets_filters_missing(tmp_path):
    real = tmp_path / "real"; real.mkdir()
    out = sj.existing_targets(targets=[
        str(real), str(tmp_path / "does_not_exist"),
    ])
    assert str(real) in out
    assert str(tmp_path / "does_not_exist") not in out


def test_existing_targets_expands_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "ComfyUI").mkdir(parents=True)
    (tmp_path / "ComfyUI" / "models").mkdir()
    out = sj.existing_targets()
    assert any("ComfyUI/models" in p for p in out)


# ── scan_dir ───────────────────────────────────────────────────────────


def test_scan_empty_dir(tmp_path):
    s = sj.scan_dir(str(tmp_path), now_ts=time.time())
    assert s["total_bytes"] == 0
    assert s["file_count"] == 0


def test_scan_counts_files(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 1024)
    (tmp_path / "b.bin").write_bytes(b"y" * 2048)
    s = sj.scan_dir(str(tmp_path), now_ts=time.time())
    assert s["file_count"] == 2
    assert s["total_bytes"] == 3072


def test_scan_recursive(tmp_path):
    sub = tmp_path / "sub"; sub.mkdir()
    (sub / "x.bin").write_bytes(b"z" * 100)
    s = sj.scan_dir(str(tmp_path), now_ts=time.time())
    assert s["file_count"] == 1
    assert s["total_bytes"] == 100


def test_scan_detects_cold_files(tmp_path):
    f = tmp_path / "cold.bin"
    f.write_bytes(b"a" * 1024)
    # Set mtime to 60 days ago
    sixty_days_ago = time.time() - 60 * 86400
    os.utime(str(f), (sixty_days_ago, sixty_days_ago))
    s = sj.scan_dir(str(tmp_path), cold_age_s=30 * 86400,
                     now_ts=time.time())
    assert s["cold_count"] == 1
    assert s["cold_bytes"] == 1024


def test_scan_hot_files_not_counted_cold(tmp_path):
    f = tmp_path / "hot.bin"
    f.write_bytes(b"a" * 1024)
    s = sj.scan_dir(str(tmp_path), cold_age_s=30 * 86400,
                     now_ts=time.time())
    assert s["cold_count"] == 0


def test_scan_samples_large_old_files(tmp_path):
    """sample_old_files should include files > 100 MiB."""
    # Build a sparse 200 MiB file
    f = tmp_path / "big.safetensors"
    with open(f, "wb") as fp:
        fp.seek(200 * 1024 * 1024 - 1)
        fp.write(b"\0")
    sixty_days_ago = time.time() - 60 * 86400
    os.utime(str(f), (sixty_days_ago, sixty_days_ago))
    s = sj.scan_dir(str(tmp_path), cold_age_s=30 * 86400,
                     now_ts=time.time())
    assert len(s["sample_old_files"]) == 1
    assert s["sample_old_files"][0]["age_days"] >= 60


def test_scan_skips_small_files_in_sample(tmp_path):
    """Even cold, files < 100 MiB don't pollute the sample list."""
    f = tmp_path / "small.bin"
    f.write_bytes(b"a" * 1024)
    sixty_days_ago = time.time() - 60 * 86400
    os.utime(str(f), (sixty_days_ago, sixty_days_ago))
    s = sj.scan_dir(str(tmp_path), cold_age_s=30 * 86400,
                     now_ts=time.time())
    assert s["sample_old_files"] == []
    # but still counted in cold_bytes
    assert s["cold_count"] == 1


def test_scan_caches_results(tmp_path):
    """A second call within TTL should return cached data without re-walking."""
    (tmp_path / "x.bin").write_bytes(b"x" * 100)
    now = time.time()
    s1 = sj.scan_dir(str(tmp_path), now_ts=now)
    # Add another file. The cache should hide it.
    (tmp_path / "y.bin").write_bytes(b"y" * 200)
    s2 = sj.scan_dir(str(tmp_path), now_ts=now + 10)
    assert s1["file_count"] == s2["file_count"] == 1


def test_scan_handles_missing_dir():
    s = sj.scan_dir("/nonexistent/foo/bar", now_ts=time.time())
    assert s["total_bytes"] == 0


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    s = sj.status()
    assert s["scanned_count"] == 0
    assert s["total_gib"] == 0.0


def test_status_aggregates_across_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    comfy = tmp_path / "ComfyUI" / "models"
    comfy.mkdir(parents=True)
    (comfy / "x.safetensors").write_bytes(b"x" * 10 * 1024 * 1024)
    out = tmp_path / "ComfyUI" / "output"
    out.mkdir(parents=True)
    (out / "y.png").write_bytes(b"y" * 1024)
    s = sj.status()
    assert s["scanned_count"] >= 2
    assert s["total_bytes"] >= 10 * 1024 * 1024 + 1024


def test_status_uses_cold_days_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    comfy = tmp_path / "ComfyUI" / "models"
    comfy.mkdir(parents=True)
    f = comfy / "old.safetensors"
    f.write_bytes(b"x" * 1024)
    seven_days_ago = time.time() - 7 * 86400
    os.utime(str(f), (seven_days_ago, seven_days_ago))
    # Default 30 days → not cold
    s_default = sj.status()
    assert s_default["cold_bytes"] == 0
    # Override to 3 days → cold
    sj._SCAN_CACHE.clear()
    s_short = sj.status(cfg={"SD_CACHE_COLD_DAYS": "3"})
    assert s_short["cold_bytes"] == 1024
