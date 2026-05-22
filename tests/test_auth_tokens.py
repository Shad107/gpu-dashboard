"""R&D #9.3 — Auth tokens + share-links tests."""
import os
import time
import tempfile
from unittest.mock import patch
from gpu_dashboard.modules import auth_tokens as at


def _with_tmp_paths(td):
    return patch.multiple(
        at,
        tokens_path=lambda: os.path.join(td, "tokens.json"),
        server_secret_path=lambda: os.path.join(td, ".server_secret"),
    )


# ── token store ──────────────────────────────────────────────────────────


def test_load_tokens_empty_file():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        assert at.load_tokens() == []


def test_create_returns_raw_secret_once():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        rec, raw = at.create_token(name="test", scope="read")
    assert isinstance(raw, str)
    assert len(raw) > 20  # token_urlsafe(24) is ~32 chars
    assert rec["id"]
    assert rec["secret_hash"] != raw  # never persist the raw secret


def test_create_unknown_scope_raises():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        import pytest
        with pytest.raises(ValueError):
            at.create_token("x", scope="superuser")


def test_verify_valid_token():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        rec, raw = at.create_token(name="mine", scope="write")
        verified = at.verify_token(raw)
    assert verified is not None
    assert verified["id"] == rec["id"]
    assert verified["scope"] == "write"


def test_verify_wrong_secret_returns_none():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        at.create_token(name="mine", scope="read")
        assert at.verify_token("wrong-secret") is None
        assert at.verify_token("") is None


def test_verify_expired_token_returns_none():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        rec, raw = at.create_token(name="x", scope="read", ttl_s=1)
        time.sleep(1.1)
        assert at.verify_token(raw) is None


def test_delete_token():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        rec, _ = at.create_token(name="x")
        assert at.delete_token(rec["id"]) is True
        assert at.load_tokens() == []
        # Deleting again is a no-op (returns False)
        assert at.delete_token(rec["id"]) is False


# ── scope_allows ─────────────────────────────────────────────────────────


def test_scope_admin_includes_write_and_read():
    assert at.scope_allows("admin", "read") is True
    assert at.scope_allows("admin", "write") is True
    assert at.scope_allows("admin", "admin") is True


def test_scope_write_includes_read_only():
    assert at.scope_allows("write", "read") is True
    assert at.scope_allows("write", "write") is True
    assert at.scope_allows("write", "admin") is False


def test_scope_read_only_allows_read():
    assert at.scope_allows("read", "read") is True
    assert at.scope_allows("read", "write") is False


# ── share-links ──────────────────────────────────────────────────────────


def test_share_link_roundtrip():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        link = at.make_share_link(scope="read", ttl_s=3600, sub="alice")
        payload = at.verify_share_link(link)
    assert payload is not None
    assert payload["scope"] == "read"
    assert payload["sub"] == "alice"
    assert payload["exp"] > int(time.time())


def test_share_link_tampered_sig_rejected():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        link = at.make_share_link(scope="read", ttl_s=3600)
        body, sig = link.rsplit(".", 1)
        # Flip a byte in the sig
        bad = body + "." + (sig[:-1] + ("A" if sig[-1] != "A" else "B"))
        assert at.verify_share_link(bad) is None


def test_share_link_expired_rejected():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        link = at.make_share_link(scope="read", ttl_s=1)
        time.sleep(1.1)
        assert at.verify_share_link(link) is None


def test_share_link_bad_format_rejected():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        assert at.verify_share_link("") is None
        assert at.verify_share_link("nosignature") is None
        assert at.verify_share_link("bad.b64.payload") is None


def test_share_link_unknown_scope_rejected_at_creation():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        import pytest
        with pytest.raises(ValueError):
            at.make_share_link(scope="root")


def test_server_secret_persists_across_calls():
    with tempfile.TemporaryDirectory() as td, _with_tmp_paths(td):
        s1 = at.get_or_create_server_secret()
        s2 = at.get_or_create_server_secret()
    assert s1 == s2
    assert len(s1) == 32  # 256 bits
