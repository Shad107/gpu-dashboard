"""Tests for /api/export/year shortcut (cycle 108)."""
import datetime
import time

import pytest

from gpu_dashboard import api
from gpu_dashboard.storage import Storage


@pytest.fixture
def ctx(tmp_path):
    s = Storage(str(tmp_path / "metrics.db"))
    yield {"storage": s}
    s.close()


def test_year_export_no_storage_returns_503():
    code, body = api.handle_export_year({}, {})
    assert code == 503


def test_year_export_returns_csv_string(ctx):
    """Default empty DB still returns a valid CSV with header."""
    code, body = api.handle_export_year(ctx, {})
    assert code == 200
    assert isinstance(body, str)
    # First line should be CSV header (ts column)
    assert body.startswith("ts,") or "ts" in body.split("\n")[0]


def test_year_export_excludes_pre_january(ctx):
    """Samples from last calendar year should NOT appear in /export/year."""
    now = int(time.time())
    year_start = int(datetime.datetime.fromtimestamp(now).replace(
        month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())

    # Sample BEFORE this year's Jan 1 (sneaky : use ts = year_start - 1000)
    ctx["storage"].record_sample({"ts": year_start - 1000, "power": 100, "temp": 50})
    # Sample AFTER Jan 1 — should appear
    if year_start < now:  # only if we're past Jan 1 already (always true except 00:00:01 Jan 1)
        ctx["storage"].record_sample({"ts": year_start + 100, "power": 200, "temp": 60})

    code, body = api.handle_export_year(ctx, {})
    lines = body.strip().split("\n")
    data_lines = lines[1:]  # skip CSV header
    # Pre-Jan-1 sample (ts=year_start-1000) must NOT appear
    assert not any(str(year_start - 1000) in line for line in data_lines)


def test_year_export_includes_this_year(ctx):
    """Samples after Jan 1 should appear in the CSV."""
    now = int(time.time())
    year_start = int(datetime.datetime.fromtimestamp(now).replace(
        month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    # Plant a sample 10 minutes ago — definitely this year
    ctx["storage"].record_sample({"ts": now - 600, "power": 250, "temp": 75})

    code, body = api.handle_export_year(ctx, {})
    # The sample's ts should appear somewhere in the CSV
    assert str(now - 600) in body
