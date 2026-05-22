"""R&D #7.4 — InfluxDB line protocol tests."""
from unittest.mock import patch, MagicMock
import urllib.error
from gpu_dashboard.modules import influxdb_push as ip


def test_escape_tag_handles_specials():
    assert ip._escape_tag("simple") == "simple"
    assert ip._escape_tag("with,comma") == "with\\,comma"
    assert ip._escape_tag("with space") == "with\\ space"
    assert ip._escape_tag("k=v") == "k\\=v"


def test_format_line_basic():
    line = ip.format_line(
        "gpu_metrics",
        {"host": "rig1", "gpu": "0"},
        {"temp": 72, "util": 98, "power": 180.5},
        ts_ns=1234567890,
    )
    assert line.startswith("gpu_metrics,gpu=0,host=rig1 ")  # tags sorted alphabetically
    assert "temp=72i" in line  # int → 'i' suffix
    assert "util=98i" in line
    assert "power=180.5" in line  # float no suffix
    assert line.endswith(" 1234567890")


def test_format_line_skips_none_fields():
    line = ip.format_line("m", {"h": "x"}, {"a": 1, "b": None, "c": 2})
    assert "a=1i" in line
    assert "b=" not in line
    assert "c=2i" in line


def test_format_line_empty_fields_returns_empty():
    """No valid fields → empty string (don't post empty lines)."""
    assert ip.format_line("m", {"h": "x"}, {}) == ""
    assert ip.format_line("m", {"h": "x"}, {"a": None}) == ""


def test_format_line_string_field_escapes_quotes():
    line = ip.format_line("m", {"h": "x"}, {"note": 'has "quote"'})
    assert 'note="has \\"quote\\""' in line


def test_format_line_bool_field():
    line = ip.format_line("m", {"h": "x"}, {"flag": True, "off": False})
    assert "flag=true" in line
    assert "off=false" in line


# ── endpoint URL builder ─────────────────────────────────────────────────


def test_build_endpoint_v2_with_org_and_bucket():
    url = ip.build_endpoint("https://x:8086/", org="my-org", bucket="my-bucket")
    assert url == "https://x:8086/api/v2/write?org=my-org&bucket=my-bucket&precision=ns"


def test_build_endpoint_v1_with_database():
    url = ip.build_endpoint("https://x:8086", database="mydb")
    assert url == "https://x:8086/write?db=mydb&precision=ns"


def test_build_endpoint_v2_without_org_uses_bucket_only():
    url = ip.build_endpoint("https://x:8086", bucket="b")
    assert "api/v2/write" in url
    assert "bucket=b" in url


def test_build_endpoint_default_falls_back_to_v1():
    url = ip.build_endpoint("https://x:8086")
    assert "/write?db=gpu-dashboard" in url


# ── push() HTTP behavior ─────────────────────────────────────────────────


def test_push_empty_lines_returns_ok_no_data():
    ok, msg = ip.push([], "http://x")
    assert ok is True
    assert "no data" in msg


def test_push_success_returns_ok():
    fake_response = MagicMock()
    fake_response.status = 204
    fake_response.__enter__ = lambda self: self
    fake_response.__exit__ = lambda *a: None
    with patch("urllib.request.urlopen", return_value=fake_response):
        ok, msg = ip.push(["m h=1i"], "http://x")
    assert ok is True
    assert "204" in msg


def test_push_http_error_returns_failure():
    err = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)
    with patch("urllib.request.urlopen", side_effect=err):
        ok, msg = ip.push(["m h=1i"], "http://x")
    assert ok is False
    assert "401" in msg


def test_push_network_error_returns_failure():
    err = urllib.error.URLError("connection refused")
    with patch("urllib.request.urlopen", side_effect=err):
        ok, msg = ip.push(["m h=1i"], "http://x")
    assert ok is False
    assert "net:" in msg


def test_push_with_token_sets_authorization_header():
    captured = {}
    def fake_open(req, timeout=5):
        captured["headers"] = dict(req.header_items())
        fake_response = MagicMock()
        fake_response.status = 204
        fake_response.__enter__ = lambda self: self
        fake_response.__exit__ = lambda *a: None
        return fake_response
    with patch("urllib.request.urlopen", side_effect=fake_open):
        ok, _ = ip.push(["m h=1i"], "http://x", token="mytoken")
    assert ok is True
    # Header keys are case-canonical
    auth = captured["headers"].get("Authorization", "")
    assert "Token mytoken" in auth
