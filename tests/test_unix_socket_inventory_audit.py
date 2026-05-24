"""Tests for modules/unix_socket_inventory_audit.py — R&D #85.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import unix_socket_inventory_audit as mod


HEADER = ("Num       RefCount Protocol Flags    Type St Inode Path\n")


def _line(path=None, state=3, inode=1000):
    """Generate a /proc/net/unix line. state=3 = connected,
    state=1 = unconnected/listening."""
    base = (f"0000000000000000: 00000003 00000000 00000000 "
            f"0001 {state:02x} {inode}")
    return base + (f" {path}" if path else "") + "\n"


# --- parse_unix ------------------------------------------------

def test_parse_empty():
    out = mod.parse_unix(HEADER)
    assert out["total"] == 0


def test_parse_named():
    text = HEADER + _line(path="/run/systemd/notify")
    out = mod.parse_unix(text)
    assert out["total"] == 1
    assert out["named"] == 1
    assert out["abstract"] == 0


def test_parse_abstract():
    text = HEADER + _line(path="@/tmp/.X11-unix/X0")
    out = mod.parse_unix(text)
    assert out["abstract"] == 1
    assert out["named"] == 0


def test_parse_unnamed():
    text = HEADER + _line()  # no path
    out = mod.parse_unix(text)
    assert out["unnamed"] == 1


def test_parse_listening():
    text = HEADER + _line(path="/run/foo", state=1)
    out = mod.parse_unix(text)
    assert out["listening"] == 1


def test_parse_mixed():
    text = (HEADER
              + _line(path="/run/a")
              + _line(path="@abstract1")
              + _line(path="@abstract2")
              + _line()  # unnamed
              + _line(path="/run/b", state=1))  # listening
    out = mod.parse_unix(text)
    assert out["total"] == 5
    assert out["abstract"] == 2
    assert out["named"] == 2
    assert out["unnamed"] == 1
    assert out["listening"] == 1


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify(None)
    assert v["verdict"] == "unknown"


def test_classify_ok_few():
    v = mod.classify({"total": 50, "abstract": 5,
                          "named": 30, "unnamed": 15,
                          "listening": 10})
    assert v["verdict"] == "ok"


def test_classify_busy_total():
    v = mod.classify({"total": 1000, "abstract": 50,
                          "named": 500, "unnamed": 450,
                          "listening": 100})
    assert v["verdict"] == "many_unix_sockets"


def test_classify_growth():
    # Moderate total but high abstract count
    v = mod.classify({"total": 800, "abstract": 300,
                          "named": 300, "unnamed": 200,
                          "listening": 50})
    assert v["verdict"] == "unix_socket_growth"


def test_classify_leak():
    v = mod.classify({"total": 3000, "abstract": 600,
                          "named": 1000, "unnamed": 1400,
                          "listening": 100})
    assert v["verdict"] == "unix_socket_leak"


def test_classify_high_total_low_abstract_busy():
    # Total > 2000 but abstract < 500 → still busy, not leak
    v = mod.classify({"total": 2500, "abstract": 100,
                          "named": 1500, "unnamed": 900,
                          "listening": 50})
    assert v["verdict"] == "many_unix_sockets"


# Priority : leak > growth > busy
def test_priority_leak_over_growth():
    v = mod.classify({"total": 3000, "abstract": 600,
                          "named": 1000, "unnamed": 1400,
                          "listening": 100})
    assert v["verdict"] == "unix_socket_leak"


def test_priority_growth_over_busy():
    # Both growth-triggering AND busy → growth wins
    v = mod.classify({"total": 1500, "abstract": 300,
                          "named": 800, "unnamed": 400,
                          "listening": 50})
    assert v["verdict"] == "unix_socket_growth"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    p = tmp_path / "unix"
    p.write_text(HEADER + _line(path="/run/foo") * 10)
    out = mod.status(None, str(p))
    assert out["ok"] is True
    assert out["total"] == 10
    assert out["verdict"]["verdict"] == "ok"


def test_status_leak_synthetic(tmp_path):
    p = tmp_path / "unix"
    body = (HEADER
              + _line(path="@x") * 600  # 600 abstract
              + _line(path="/r") * 1500
              + _line(path="/q") * 500)
    p.write_text(body)
    out = mod.status(None, str(p))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unix_socket_leak"
