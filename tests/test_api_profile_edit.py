"""Tests for /api/profile/save — write a profile override."""
from __future__ import annotations

import json
import os

import pytest

from gpu_dashboard import api


@pytest.fixture
def ctx(tmp_path):
    # Set up a fake profiles_dir with schema
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    schema = {
        "type": "object",
        "required": ["model", "match", "power"],
        "properties": {
            "model": {"type": "string"},
            "match": {"type": "array", "items": {"type": "string"}},
            "power": {
                "type": "object",
                "required": ["min", "max", "stock", "perf_curve"],
                "properties": {
                    "min": {"type": "integer"},
                    "max": {"type": "integer"},
                    "stock": {"type": "integer"},
                    "perf_curve": {"type": "array"},
                },
            },
        },
    }
    (profiles_dir / "schema.json").write_text(json.dumps(schema))
    overrides_dir = tmp_path / "overrides"
    return {
        "profiles_dir": str(profiles_dir),
        "overrides_dir": str(overrides_dir),
    }


def _valid_profile():
    return {
        "model": "Test 3090",
        "match": ["Test 3090"],
        "power": {"min": 100, "max": 350, "stock": 350,
                  "perf_curve": [[100, 30], [350, 100]]},
    }


class TestHandleProfileSave:
    def test_writes_override_file(self, ctx):
        code, body = api.handle_profile_save(ctx, _valid_profile())
        assert code == 200
        assert body["ok"] is True
        assert os.path.isfile(body["path"])
        with open(body["path"]) as f:
            saved = json.load(f)
        assert saved["model"] == "Test 3090"

    def test_creates_parent_dir(self, ctx):
        # overrides_dir doesn't exist yet
        assert not os.path.isdir(ctx["overrides_dir"])
        api.handle_profile_save(ctx, _valid_profile())
        assert os.path.isdir(ctx["overrides_dir"])

    def test_invalid_profile_returns_400(self, ctx):
        bad = {"model": "X"}  # missing required fields
        code, body = api.handle_profile_save(ctx, bad)
        assert code == 400
        assert body["ok"] is False

    def test_filename_uses_safe_model_name(self, ctx):
        p = _valid_profile()
        p["model"] = "RTX 4090 / Special !!"
        code, body = api.handle_profile_save(ctx, p)
        assert code == 200
        # Filename should be safe (no special chars)
        fname = os.path.basename(body["path"])
        assert ".." not in fname
        assert "/" not in fname
        assert "!" not in fname
