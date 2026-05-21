"""Tests for /api/update/check and /api/update/pull — git-based update checker."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from gpu_dashboard import api


@pytest.fixture
def git_repo(tmp_path):
    """Initialize a fake git repo for testing without hitting real remote."""
    import subprocess as sp
    sp.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test"], check=True)
    sp.run(["git", "-C", str(tmp_path), "config", "user.name", "test"], check=True)
    f = tmp_path / "x.txt"
    f.write_text("hello")
    sp.run(["git", "-C", str(tmp_path), "add", "x.txt"], check=True)
    sp.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)
    return tmp_path


@pytest.fixture
def ctx(git_repo):
    return {"repo_path": str(git_repo)}


class TestUpdateCheck:
    def test_no_repo_returns_error(self):
        code, body = api.handle_update_check({"repo_path": "/nonexistent"})
        assert code == 400
        assert body["ok"] is False

    def test_returns_current_sha(self, ctx):
        code, body = api.handle_update_check(ctx)
        # Fetch may fail (no remote), but current_sha should still be there
        assert "current_sha" in body
        assert len(body["current_sha"]) >= 7  # short or long sha

    def test_no_remote_returns_unknown_behind(self, ctx):
        # No remote configured → behind should be None/unknown
        code, body = api.handle_update_check(ctx)
        # Without a remote, we can't know behind count, so it should be None
        assert body.get("behind") in (None, 0)


class TestUpdatePull:
    def test_no_repo_returns_error(self):
        code, body = api.handle_update_pull({"repo_path": "/nonexistent"})
        assert code == 400
        assert body["ok"] is False

    def test_dirty_tree_refuses(self, ctx, git_repo):
        # Make the tree dirty (uncommitted changes)
        (git_repo / "x.txt").write_text("modified")
        code, body = api.handle_update_pull(ctx)
        assert code == 409  # conflict
        assert body["ok"] is False
        assert "dirty" in body.get("error", "").lower() or "uncommitted" in body.get("error", "").lower()
