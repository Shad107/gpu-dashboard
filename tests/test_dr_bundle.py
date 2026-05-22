"""R&D #16.8 — 1-click DR bundle tests."""
import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import dr_bundle as dr


# ── _sha256_file ─────────────────────────────────────────────────────────


def test_sha256_known_value():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "x.txt")
        with open(p, "wb") as f:
            f.write(b"hello world")
        h = dr._sha256_file(p)
    assert h == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_sha256_missing_returns_none():
    assert dr._sha256_file("/nope") is None


# ── _vacuum_into ─────────────────────────────────────────────────────────


def test_vacuum_into_creates_consistent_snapshot():
    """SQLite VACUUM INTO copies the DB even while it's open."""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "live.db")
        conn = sqlite3.connect(src)
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1), (2), (3)")
        conn.commit()
        # Don't close — VACUUM INTO should work on a live DB
        dst = os.path.join(td, "snap.db")
        assert dr._vacuum_into(src, dst) is True
        # Snap should be readable + have the same row count
        snap = sqlite3.connect(dst)
        rows = snap.execute("SELECT COUNT(*) FROM t").fetchone()
        assert rows[0] == 3
        conn.close()
        snap.close()


def test_vacuum_into_missing_db_returns_false():
    with tempfile.TemporaryDirectory() as td:
        assert dr._vacuum_into("/nope.db", os.path.join(td, "snap.db")) is False


# ── _restore_script ──────────────────────────────────────────────────────


def test_restore_script_includes_basename_and_host():
    s = dr._restore_script("greenwatts-dr-x-20260101", "myhost")
    assert "myhost" in s
    assert "greenwatts-dr-x-20260101" in s
    assert s.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in s


def test_restore_script_prompts_before_overwrite():
    s = dr._restore_script("x", "y")
    assert "Overwrite?" in s


# ── build_manifest ───────────────────────────────────────────────────────


def test_build_manifest_skips_missing_files():
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "real.txt")
        with open(p1, "w") as f:
            f.write("x")
        files = [p1, "/does/not/exist"]
        m = dr.build_manifest(files, version="0.1", host="testhost")
    assert m["file_count"] == 1
    assert m["host"] == "testhost"
    assert m["files"][0]["sha256"]


def test_build_manifest_records_size_and_mtime():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "x.bin")
        with open(p, "wb") as f:
            f.write(b"hello")
        m = dr.build_manifest([p])
    assert m["files"][0]["size_bytes"] == 5
    assert m["files"][0]["mtime"] > 0


# ── build_bundle integration ─────────────────────────────────────────────


def test_build_bundle_produces_artifact_and_includes_files():
    """End-to-end : sample config + DB → bundle artifact exists + contains both."""
    with tempfile.TemporaryDirectory() as td:
        # Set up fake config dir
        cfg = os.path.join(td, ".config", "gpu-dashboard")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "config.env"), "w") as f:
            f.write("DASHBOARD_PORT=9999\n")
        with open(os.path.join(cfg, "rules.json"), "w") as f:
            f.write('{"rules": []}')
        # Set up a fake DB
        db = os.path.join(td, "live.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE samples (ts INTEGER)")
        conn.commit()
        conn.close()
        # Override path helpers
        out = os.path.join(td, "bundles")
        with patch.object(dr, "_config_root", return_value=cfg), \
             patch.object(dr, "_bundles_dir", return_value=out):
            result = dr.build_bundle(history_db_path=db, version="0.3.0")
        assert result["ok"] is True
        assert os.path.exists(result["path"])
        assert result["snapshot_db_included"] is True
        # Tarball contains both config + snapshot
        # (decompress + list to verify)
        artifact = result["path"]
        if artifact.endswith(".zst"):
            tar_extracted = artifact[:-4]  # drop .zst
            subprocess.run(["zstd", "-d", "-q", "-f", artifact, "-o", tar_extracted],
                            check=True, capture_output=True)
            with tarfile.open(tar_extracted) as tar:
                names = tar.getnames()
        elif artifact.endswith(".tar"):
            with tarfile.open(artifact) as tar:
                names = tar.getnames()
        else:
            names = []
        # Should contain manifest.json + restore.sh + config/* + snapshot.db
        flat = " ".join(names)
        assert "manifest.json" in flat
        assert "restore.sh" in flat
        assert "snapshot.db" in flat
        assert "config.env" in flat


def test_build_bundle_handles_missing_db():
    """If history.db doesn't exist, bundle still succeeds (no snapshot)."""
    with tempfile.TemporaryDirectory() as td:
        cfg = os.path.join(td, ".config", "gpu-dashboard")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "config.env"), "w") as f:
            f.write("x=1")
        out = os.path.join(td, "bundles")
        with patch.object(dr, "_config_root", return_value=cfg), \
             patch.object(dr, "_bundles_dir", return_value=out):
            result = dr.build_bundle(history_db_path="/nope.db", version="0.3.0")
    assert result["ok"] is True
    assert result["snapshot_db_included"] is False


# ── list / delete bundles ───────────────────────────────────────────────


def test_list_bundles_empty_dir():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(dr, "_bundles_dir", return_value=os.path.join(td, "nope")):
            assert dr.list_bundles() == []


def test_list_bundles_newest_first():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(dr, "_bundles_dir", return_value=td):
            # Create 3 files with different mtimes
            for i, name in enumerate(["a.tar.zst", "b.tar.zst", "c.tar.zst"]):
                p = os.path.join(td, name)
                with open(p, "wb") as f:
                    f.write(b"x")
            out = dr.list_bundles()
    assert len(out) == 3
    # Sorted by filename reverse (newest = lexically last)
    assert out[0]["name"] == "c.tar.zst"


def test_delete_bundle_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(dr, "_bundles_dir", return_value=td):
            # Path-traversal attempts must be refused
            assert dr.delete_bundle("../etc/passwd") is False
            assert dr.delete_bundle("a/b") is False


def test_delete_bundle_removes_file():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(dr, "_bundles_dir", return_value=td):
            p = os.path.join(td, "victim.tar.zst")
            with open(p, "wb") as f:
                f.write(b"x")
            assert dr.delete_bundle("victim.tar.zst") is True
            assert not os.path.exists(p)


def test_delete_bundle_missing_returns_false():
    with tempfile.TemporaryDirectory() as td:
        with patch.object(dr, "_bundles_dir", return_value=td):
            assert dr.delete_bundle("nope.tar.zst") is False
