"""R&D #9.4 — HF janitor tests."""
import os
import tempfile
import time
from unittest.mock import patch
from gpu_dashboard.modules import hf_janitor as hj


def test_is_model_file_too_small():
    """File < 50 MiB → not a model."""
    assert hj.is_model_file("/tmp/model.gguf", 1024) is False


def test_is_model_file_known_extension():
    assert hj.is_model_file("/tmp/model.gguf", 100 * 1024 * 1024) is True
    assert hj.is_model_file("/tmp/model.safetensors", 100 * 1024 * 1024) is True


def test_is_model_file_unknown_ext_but_huge():
    """If file >= 1 GiB, treat as model regardless of extension."""
    assert hj.is_model_file("/tmp/random.bin", 2 * 1024 * 1024 * 1024) is True


def test_is_model_file_unknown_ext_below_1gib():
    """Random extension below 1 GiB → not a model."""
    assert hj.is_model_file("/tmp/weird.foo", 200 * 1024 * 1024) is False


def test_expand_dirs_only_existing():
    with tempfile.TemporaryDirectory() as td:
        sub1 = os.path.join(td, "exists")
        os.makedirs(sub1)
        # Provide one real + one fake, only real should come back
        result = hj.expand_dirs(extra=[sub1, "/nonexistent/path/zzz"])
        assert sub1 in result
        assert "/nonexistent/path/zzz" not in result


def test_scan_dir_finds_model_files(tmp_path):
    """Place a 60 MiB .gguf + a 1 KiB .txt — only the gguf should show."""
    big = tmp_path / "model.gguf"
    big.write_bytes(b"\0" * (60 * 1024 * 1024))
    small = tmp_path / "readme.txt"
    small.write_text("hi")
    result = hj.scan_dir(str(tmp_path))
    paths = {r["path"] for r in result}
    assert str(big) in paths
    assert str(small) not in paths


def test_scan_dir_records_age_days(tmp_path):
    big = tmp_path / "stale.gguf"
    big.write_bytes(b"\0" * (60 * 1024 * 1024))
    # Set atime to 90 days ago
    old_ts = time.time() - 90 * 86400
    os.utime(str(big), (old_ts, old_ts))
    result = hj.scan_dir(str(tmp_path))
    assert len(result) == 1
    assert 88 <= result[0]["age_days"] <= 92


def test_cold_score_larger_older_is_higher():
    """A 10 GiB file 100 days old should score higher than a 1 GiB 10-day file."""
    a = {"size_mib": 10000, "age_days": 100}
    b = {"size_mib": 1000, "age_days": 10}
    assert hj.cold_score(a) > hj.cold_score(b)


def test_audit_empty_when_no_dirs():
    """If none of the candidate dirs exist → available=false."""
    with patch.object(hj, "expand_dirs", return_value=[]):
        result = hj.audit()
    assert result["available"] is False
    assert "no model" in result["reason"].lower()


def test_audit_aggregates_and_sorts(tmp_path):
    big = tmp_path / "huge.gguf"
    big.write_bytes(b"\0" * (200 * 1024 * 1024))
    small = tmp_path / "med.gguf"
    small.write_bytes(b"\0" * (60 * 1024 * 1024))
    # Make 'huge' older so it ranks higher (colder)
    os.utime(str(big), (time.time() - 30 * 86400, time.time() - 30 * 86400))
    os.utime(str(small), (time.time() - 1 * 86400, time.time() - 1 * 86400))
    with patch.object(hj, "expand_dirs", return_value=[str(tmp_path)]), \
         patch.object(hj, "find_hot_paths", return_value=set()):
        result = hj.audit()
    assert result["available"] is True
    assert result["files_total"] == 2
    # 'huge' should be first (cold_score higher)
    assert result["top_cold"][0]["path"] == str(big)
    assert result["top_cold"][0]["is_hot"] is False


def test_audit_marks_hot_files(tmp_path):
    """When a file is in find_hot_paths() set, is_hot must be True."""
    f = tmp_path / "loaded.gguf"
    f.write_bytes(b"\0" * (100 * 1024 * 1024))
    with patch.object(hj, "expand_dirs", return_value=[str(tmp_path)]), \
         patch.object(hj, "find_hot_paths", return_value={str(f)}):
        result = hj.audit()
    assert result["hot_count"] == 1
    assert result["top_cold"][0]["is_hot"] is True
