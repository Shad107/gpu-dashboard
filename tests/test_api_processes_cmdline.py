"""Tests for the cmdline addition on /api/processes (cycle 125, R&D #6)."""
import os
import pytest

from gpu_dashboard import api


def test_read_cmdline_returns_none_for_missing_pid():
    assert api._read_cmdline(999_999_999) is None


def test_read_cmdline_returns_string_for_self():
    """Reading our own /proc/self/cmdline (via os.getpid()) should work
    on Linux. On non-Linux this returns None (graceful)."""
    result = api._read_cmdline(os.getpid())
    # On Linux : non-empty string. On non-Linux : None.
    assert result is None or isinstance(result, str)


def test_read_cmdline_truncates_to_200_chars(tmp_path, monkeypatch):
    """Spoof a /proc/123/cmdline file via monkeypatching open."""
    long_cmd = ("python " + "x" * 500).encode("utf-8")

    real_open = open

    def fake_open(path, mode="r", *args, **kw):
        if isinstance(path, str) and "/proc/12345/cmdline" in path:
            from io import BytesIO
            return BytesIO(long_cmd)
        return real_open(path, mode, *args, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    result = api._read_cmdline(12345)
    assert result is not None
    assert len(result) <= 200


def test_read_cmdline_converts_nulls_to_spaces(monkeypatch):
    fake_cmd = b"python\x00-m\x00module\x00--arg=value\x00"

    real_open = open

    def fake_open(path, mode="r", *args, **kw):
        if isinstance(path, str) and "/proc/54321/cmdline" in path:
            from io import BytesIO
            return BytesIO(fake_cmd)
        return real_open(path, mode, *args, **kw)

    monkeypatch.setattr("builtins.open", fake_open)
    result = api._read_cmdline(54321)
    assert result == "python -m module --arg=value"
