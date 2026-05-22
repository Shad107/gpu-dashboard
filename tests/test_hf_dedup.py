"""R&D #15.3 — HF cache dedup tests."""
import os
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import hf_dedup as hd


# ── _scan_for_files ─────────────────────────────────────────────────────


def test_scan_finds_only_files_above_threshold():
    """Files < min_size are skipped."""
    with tempfile.TemporaryDirectory() as td:
        big = os.path.join(td, "big.bin")
        small = os.path.join(td, "small.txt")
        with open(big, "wb") as f:
            f.write(b"\0" * (60 * 1024 * 1024))
        with open(small, "w") as f:
            f.write("hi")
        files = hd._scan_for_files([td], min_size=50 * 1024 * 1024)
    paths = {f["path"] for f in files}
    assert big in paths
    assert small not in paths


def test_scan_returns_inode_and_device_metadata():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "x.bin")
        with open(p, "wb") as f:
            f.write(b"\0" * (60 * 1024 * 1024))
        files = hd._scan_for_files([td], min_size=50 * 1024 * 1024)
    assert files[0]["inode"] > 0
    assert files[0]["device"] > 0
    assert files[0]["nlinks"] == 1


# ── _hash_file ──────────────────────────────────────────────────────────


def test_hash_file_returns_sha256():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "x.bin")
        with open(p, "wb") as f:
            f.write(b"hello world")
        h = hd._hash_file(p)
    # Known SHA-256 of 'hello world'
    assert h == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_hash_file_missing_returns_none():
    assert hd._hash_file("/nonexistent/path/file.bin") is None


# ── build_plan ──────────────────────────────────────────────────────────


def test_build_plan_no_dirs():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(hd, "_expand_paths", return_value=[]):
            r = hd.build_plan()
    assert r["available"] is False


def test_build_plan_two_identical_files():
    """Two files with identical content → 1 dedup plan entry."""
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "models", "a", "blob1")
        p2 = os.path.join(td, "models", "b", "blob2")
        os.makedirs(os.path.dirname(p1))
        os.makedirs(os.path.dirname(p2))
        content = b"\0xDE\0xAD\0xBE\0xEF" * (16 * 1024 * 1024 // 8)  # ~50 MiB
        with open(p1, "wb") as f:
            f.write(content + b"\0" * (60 * 1024 * 1024 - len(content)))
        with open(p2, "wb") as f:
            f.write(content + b"\0" * (60 * 1024 * 1024 - len(content)))
        with patch.object(hd, "_expand_paths", return_value=[td]):
            r = hd.build_plan(min_size=50 * 1024 * 1024)
    assert r["available"] is True
    assert r["files_scanned"] == 2
    assert r["duplicate_groups"] == 1
    assert len(r["plan"]) == 1
    assert r["plan"][0]["keep"] != r["plan"][0]["replace"]
    assert r["reclaim_mib"] > 0


def test_build_plan_unique_files_no_plan():
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "a.bin")
        p2 = os.path.join(td, "b.bin")
        with open(p1, "wb") as f:
            f.write(b"\x01" * (60 * 1024 * 1024))
        with open(p2, "wb") as f:
            f.write(b"\x02" * (60 * 1024 * 1024))
        with patch.object(hd, "_expand_paths", return_value=[td]):
            r = hd.build_plan(min_size=50 * 1024 * 1024)
    assert r["plan"] == []


def test_build_plan_already_deduped_skipped():
    """If two paths point to the same inode (already hardlinked), no plan."""
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "a.bin")
        p2 = os.path.join(td, "b.bin")
        with open(p1, "wb") as f:
            f.write(b"\x55" * (60 * 1024 * 1024))
        os.link(p1, p2)  # hardlink
        with patch.object(hd, "_expand_paths", return_value=[td]):
            r = hd.build_plan(min_size=50 * 1024 * 1024)
    assert r["plan"] == []


# ── execute_plan ────────────────────────────────────────────────────────


def test_execute_plan_dry_run_no_changes():
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "keep.bin")
        p2 = os.path.join(td, "replace.bin")
        with open(p1, "wb") as f:
            f.write(b"original-1")
        with open(p2, "wb") as f:
            f.write(b"original-2")
        plan = [{"keep": p1, "replace": p2, "size": 9}]
        r = hd.execute_plan(plan, dry_run=True)
        # Asserts inside the with block so td survives
        assert r["dry_run"] is True
        assert r["applied"] == 1
        # No actual change : p2 still has 'original-2'
        with open(p2, "rb") as f:
            assert f.read() == b"original-2"


def test_execute_plan_live_replaces_with_hardlink():
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "keep.bin")
        p2 = os.path.join(td, "replace.bin")
        with open(p1, "wb") as f:
            f.write(b"canonical")
        with open(p2, "wb") as f:
            f.write(b"duplicate")
        plan = [{"keep": p1, "replace": p2, "size": 9}]
        r = hd.execute_plan(plan, dry_run=False)
        # Assert inside the with block so td still exists
        assert r["applied"] == 1
        assert not r["errors"]
        # After live execute, both paths should point to same inode
        assert os.stat(p1).st_ino == os.stat(p2).st_ino


def test_execute_plan_collects_errors():
    """Missing 'keep' file → step errored, others continue."""
    with tempfile.TemporaryDirectory() as td:
        good_keep = os.path.join(td, "good_keep.bin")
        good_replace = os.path.join(td, "good_replace.bin")
        with open(good_keep, "wb") as f:
            f.write(b"good")
        with open(good_replace, "wb") as f:
            f.write(b"dup")
        plan = [
            {"keep": "/does/not/exist", "replace": good_replace, "size": 4},
            {"keep": good_keep, "replace": good_replace, "size": 4},
        ]
        r = hd.execute_plan(plan, dry_run=False)
    assert r["errors"]   # first step errored
    assert r["applied"] == 1   # second one succeeded


# ── reports ─────────────────────────────────────────────────────────────


def test_save_and_list_reports():
    """Two reports saved with distinct mocked timestamps → both listed."""
    with tempfile.TemporaryDirectory() as td:
        with patch.object(hd, "reports_dir", return_value=td):
            with patch.object(hd.time, "time", return_value=1000):
                p1 = hd.save_report({"summary": "test1"})
            with patch.object(hd.time, "time", return_value=1001):
                p2 = hd.save_report({"summary": "test2"})
            recent = hd.list_reports(limit=10)
        assert os.path.basename(p1) in [os.path.basename(p) for p in recent]
        assert len(recent) == 2
