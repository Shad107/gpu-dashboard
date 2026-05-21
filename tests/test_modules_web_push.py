"""Tests for the web_push module — VAPID key management."""
from __future__ import annotations

import json
import os
import stat

import pytest

from gpu_dashboard.modules import web_push


class TestCanEnable:
    def test_openssl_available(self):
        ok, reason = web_push.can_enable()
        # On a Linux dev machine openssl is always available
        assert ok is True
        assert "openssl" in reason.lower()


class TestEnsureVapidKeys:
    def test_generates_on_first_call(self, tmp_path):
        config_dir = str(tmp_path)
        data = web_push.ensure_vapid_keys(config_dir)
        assert "public_key" in data
        assert "private_key" in data
        # base64url, no padding
        assert "=" not in data["public_key"]
        assert "=" not in data["private_key"]

    def test_persists_to_file_with_0600_perm(self, tmp_path):
        config_dir = str(tmp_path)
        web_push.ensure_vapid_keys(config_dir)
        path = tmp_path / "vapid.json"
        assert path.exists()
        # Owner-readable, owner-writable, no group/world
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_idempotent_does_not_regen(self, tmp_path):
        config_dir = str(tmp_path)
        d1 = web_push.ensure_vapid_keys(config_dir)
        d2 = web_push.ensure_vapid_keys(config_dir)
        assert d1 == d2

    def test_corrupted_file_regenerates(self, tmp_path):
        config_dir = str(tmp_path)
        path = tmp_path / "vapid.json"
        path.write_text("{this is not valid json")
        # Should not raise — silently regenerate
        d = web_push.ensure_vapid_keys(config_dir)
        assert "public_key" in d

    def test_missing_fields_regenerates(self, tmp_path):
        config_dir = str(tmp_path)
        path = tmp_path / "vapid.json"
        path.write_text(json.dumps({"public_key": "only-public"}))
        d = web_push.ensure_vapid_keys(config_dir)
        assert "private_key" in d

    def test_keys_are_distinct(self, tmp_path):
        """Two fresh configs should produce different keys."""
        a = web_push.ensure_vapid_keys(str(tmp_path / "a"))
        b = web_push.ensure_vapid_keys(str(tmp_path / "b"))
        assert a["public_key"] != b["public_key"]
        assert a["private_key"] != b["private_key"]


class TestB64UrlRoundtrip:
    def test_roundtrip(self):
        for payload in [b"", b"\x00", b"hello", b"\xff" * 32, b"a" * 65]:
            encoded = web_push._b64url_encode(payload)
            decoded = web_push._b64url_decode(encoded)
            assert decoded == payload
            assert "=" not in encoded  # no padding
