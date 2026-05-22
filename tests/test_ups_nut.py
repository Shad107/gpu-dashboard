"""R&D #7.5 — NUT UPS client tests."""
import socket
from unittest.mock import patch, MagicMock
from gpu_dashboard.modules import ups_nut


def test_parse_var_reply_simple():
    assert ups_nut.parse_var_reply('VAR ups battery.charge "85"') == "85"


def test_parse_var_reply_with_spaces():
    assert ups_nut.parse_var_reply('VAR ups ups.model "APC Smart-UPS 1500"') == "APC Smart-UPS 1500"


def test_parse_var_reply_invalid_returns_none():
    assert ups_nut.parse_var_reply("nonsense") is None
    assert ups_nut.parse_var_reply("VAR ups var no_quotes") is None


def test_parse_list_ups_extracts_names():
    text = "BEGIN LIST UPS\nUPS apc \"APC Smart-UPS\"\nUPS eaton \"Eaton 5P\"\nEND LIST UPS"
    assert ups_nut.parse_list_ups(text) == ["apc", "eaton"]


def test_parse_list_ups_empty():
    text = "BEGIN LIST UPS\nEND LIST UPS"
    assert ups_nut.parse_list_ups(text) == []


def test_query_connection_refused_returns_unavailable():
    """When NUT isn't running, returns available=false with reason."""
    with patch.object(socket, "create_connection", side_effect=ConnectionRefusedError("denied")):
        result = ups_nut.query()
    assert result["ok"] is True
    assert result["available"] is False
    assert "NUT unreachable" in result["reason"]


def test_query_no_ups_configured_returns_unavailable():
    """NUT runs but has no UPS configured → available=false."""
    fake_sock = MagicMock()
    fake_sock.__enter__ = lambda self: self
    fake_sock.__exit__ = lambda *a: None
    # First read returns 'END LIST UPS' immediately (no UPS lines)
    fake_sock.recv.return_value = b"END LIST UPS\n"
    with patch.object(socket, "create_connection", return_value=fake_sock):
        result = ups_nut.query()
    assert result["available"] is False
    assert "no UPS" in result["reason"]


def test_query_returns_status_and_charge_for_active_ups():
    """Mock a full NUT session : list UPS, query 5 vars, return."""
    fake_sock = MagicMock()
    fake_sock.__enter__ = lambda self: self
    fake_sock.__exit__ = lambda *a: None

    replies = [
        b'BEGIN LIST UPS\nUPS apc "APC Smart-UPS"\nEND LIST UPS\n',  # LIST UPS reply
        b'VAR apc ups.status "OB DISCHRG"\n',
        b'VAR apc battery.charge "65"\n',
        b'VAR apc battery.runtime "1234"\n',
        b'VAR apc input.voltage "0.0"\n',
        b'VAR apc battery.voltage "12.8"\n',
    ]
    fake_sock.recv.side_effect = replies
    with patch.object(socket, "create_connection", return_value=fake_sock):
        result = ups_nut.query()
    assert result["available"] is True
    assert result["ups"] == "apc"
    assert result["on_battery"] is True   # 'OB' present
    assert result["low_battery"] is False  # no 'LB'
    assert result["charge_pct"] == 65
    assert result["runtime_s"] == 1234
    assert "OB" in result["status"]


def test_query_low_battery_flag():
    fake_sock = MagicMock()
    fake_sock.__enter__ = lambda self: self
    fake_sock.__exit__ = lambda *a: None
    replies = [
        b'BEGIN LIST UPS\nUPS x "X"\nEND LIST UPS\n',
        b'VAR x ups.status "OB LB"\n',
        b'VAR x battery.charge "10"\n',
        b'VAR x battery.runtime "60"\n',
        b'VAR x input.voltage "0"\n',
        b'VAR x battery.voltage "11.0"\n',
    ]
    fake_sock.recv.side_effect = replies
    with patch.object(socket, "create_connection", return_value=fake_sock):
        result = ups_nut.query()
    assert result["low_battery"] is True
    assert result["charge_pct"] == 10


def test_query_with_explicit_ups_skips_discovery():
    """If ups_name passed, LIST UPS is NOT called."""
    fake_sock = MagicMock()
    fake_sock.__enter__ = lambda self: self
    fake_sock.__exit__ = lambda *a: None
    # 5 VAR replies, no LIST reply
    replies = [
        b'VAR myups ups.status "OL"\n',
        b'VAR myups battery.charge "100"\n',
        b'VAR myups battery.runtime "9999"\n',
        b'VAR myups input.voltage "230.0"\n',
        b'VAR myups battery.voltage "13.5"\n',
    ]
    fake_sock.recv.side_effect = replies
    with patch.object(socket, "create_connection", return_value=fake_sock):
        result = ups_nut.query(ups="myups")
    assert result["available"] is True
    assert result["ups"] == "myups"
    assert result["on_battery"] is False  # OL = online
    assert result["charge_pct"] == 100
