"""Tests for /api/snapshot — tar.gz bundle of config + secrets + DB."""
from __future__ import annotations

import io
import os
import tarfile

import pytest

from gpu_dashboard import api
from gpu_dashboard.config import Config
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    cfg_path = tmp_path / "config.env"
    cfg_path.write_text("MODULE_POWER_LIMIT=1\nDASHBOARD_PORT=9999\n")
    secrets_path = tmp_path / "secrets.env"
    secrets_path.write_text("TG_TOKEN=hidden\n")
    db_path = str(tmp_path / "metrics.db")
    storage = Storage(db_path)

    cfg = Config(defaults={
        "STORAGE_DB_PATH": db_path,
        "_CONFIG_PATH": str(cfg_path),       # used by snapshot
        "_SECRETS_PATH": str(secrets_path),  # used by snapshot
    })
    yield {
        "config": cfg,
        "config_path": str(cfg_path),
        "secrets_path": str(secrets_path),
        "storage": storage,
        "storage_path": db_path,
    }
    storage.close()


class TestHandleSnapshot:
    def test_returns_bytes_with_tar_gz(self, ctx):
        code, body = api.handle_snapshot(ctx)
        assert code == 200
        # Body is bytes (the tar.gz content)
        assert isinstance(body, bytes)
        # Magic bytes for gzip
        assert body[:2] == b"\x1f\x8b"

    def test_tar_contains_config_env(self, ctx):
        _, body = api.handle_snapshot(ctx)
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            names = tar.getnames()
        # At minimum the config.env should be there
        assert any("config.env" in n for n in names), f"got: {names}"

    def test_tar_contains_secrets_env(self, ctx):
        _, body = api.handle_snapshot(ctx)
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            names = tar.getnames()
        assert any("secrets.env" in n for n in names), f"got: {names}"

    def test_tar_contains_metrics_db(self, ctx):
        _, body = api.handle_snapshot(ctx)
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            names = tar.getnames()
        assert any("metrics.db" in n for n in names), f"got: {names}"

    def test_missing_files_skipped_gracefully(self, tmp_path):
        """If config/secrets don't exist, snapshot should still work."""
        db_path = str(tmp_path / "metrics.db")
        storage = Storage(db_path)
        ctx = {
            "config": Config(defaults={}),
            "config_path": str(tmp_path / "missing.env"),
            "secrets_path": str(tmp_path / "missing-secrets.env"),
            "storage": storage,
            "storage_path": db_path,
        }
        code, body = api.handle_snapshot(ctx)
        assert code == 200
        # Should have at least the DB
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            names = tar.getnames()
        assert any("metrics.db" in n for n in names)
        storage.close()
