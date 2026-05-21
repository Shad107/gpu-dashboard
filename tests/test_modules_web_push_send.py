"""Tests for VAPID JWT signing + send_push() request shape."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from gpu_dashboard.modules import web_push


@pytest.fixture
def vapid(tmp_path):
    """Real keypair generated in tmp_path."""
    return web_push.ensure_vapid_keys(str(tmp_path))


def test_jwt_structure(vapid):
    """JWT = header.payload.signature, all base64url no-padding."""
    jwt = web_push._vapid_jwt("https://fcm.googleapis.com/fcm/send/abc", vapid["pem_path"])
    parts = jwt.split(".")
    assert len(parts) == 3
    for p in parts:
        assert "=" not in p  # base64url no padding


def test_jwt_payload_has_audience(vapid):
    jwt = web_push._vapid_jwt("https://fcm.googleapis.com/foo/bar", vapid["pem_path"])
    _, payload_b64, _ = jwt.split(".")
    payload = json.loads(web_push._b64url_decode(payload_b64))
    assert payload["aud"] == "https://fcm.googleapis.com"
    assert payload["exp"] > 0
    assert payload["sub"].startswith("mailto:")


def test_der_to_jose_basic():
    """Synthetic ASN.1 DER signature → 64-byte raw."""
    # DER : 0x30 0x44 0x02 0x20 <32 r bytes> 0x02 0x20 <32 s bytes>
    r = b"\x01" * 32
    s = b"\x02" * 32
    der = b"\x30\x44\x02\x20" + r + b"\x02\x20" + s
    jose = web_push._der_to_jose(der)
    assert len(jose) == 64
    assert jose[:32] == r
    assert jose[32:] == s


def test_der_to_jose_pads_short_components():
    """If r is < 32 bytes (no leading 0x00 in DER), JOSE pads with 0x00."""
    r_short = b"\xfe"  # 1 byte
    s = b"\x02" * 32
    der = b"\x30\x25\x02\x01" + r_short + b"\x02\x20" + s
    jose = web_push._der_to_jose(der)
    assert len(jose) == 64
    assert jose[:32] == b"\x00" * 31 + r_short


def test_send_push_calls_urlopen(vapid):
    """Verify send_push() POSTs to endpoint with VAPID Authorization."""
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/xxx", "p256dh": "pub", "auth": "auth"}
    mock_resp = MagicMock()
    mock_resp.status = 201
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = lambda *a: None
    with patch("urllib.request.urlopen", return_value=mock_resp) as m:
        ok, msg = web_push.send_push(sub, vapid)
        assert ok is True
        assert "201" in msg
        req = m.call_args[0][0]
        assert req.get_method() == "POST"
        assert "vapid t=" in req.headers["Authorization"]
        assert req.headers["Ttl"] == "60"
        assert req.full_url == "https://fcm.googleapis.com/fcm/send/xxx"


def test_send_push_returns_failure_on_http_error(vapid):
    sub = {"endpoint": "https://example.com/expired", "p256dh": "p", "auth": "a"}
    import urllib.error
    err = urllib.error.HTTPError(sub["endpoint"], 410, "Gone", {}, None)
    err.read = lambda: b"Gone"
    with patch("urllib.request.urlopen", side_effect=err):
        ok, msg = web_push.send_push(sub, vapid)
        assert ok is False
        assert "410" in msg
