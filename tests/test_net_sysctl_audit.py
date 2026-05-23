"""Tests for modules/net_sysctl_audit.py — R&D #35.2 LAN socket-buffer."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import net_sysctl_audit


def _mk_sysctl(root: Path, **values):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in values.items():
        # Translate "core_rmem_max" → core/rmem_max
        if "_" in k and k.startswith(("core_", "ipv4_")):
            family, name = k.split("_", 1)
            d = root / family
        else:
            d = root
            name = k
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(str(v) + "\n")


# --- field reader -------------------------------------------------

def test_read_sysctl_int(tmp_path):
    _mk_sysctl(tmp_path, core_rmem_max=212992)
    assert net_sysctl_audit.read_sysctl(str(tmp_path),
                                            "core/rmem_max") == 212992


def test_read_sysctl_missing_returns_none(tmp_path):
    assert net_sysctl_audit.read_sysctl(str(tmp_path),
                                            "core/nonsense") is None


def test_read_sysctl_garbage(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "weird").write_text("not_a_number\n")
    assert net_sysctl_audit.read_sysctl(str(tmp_path),
                                            "core/weird") is None


# --- read_tcp_rmem (3 integers) ----------------------------------

def test_read_tcp_rmem_returns_triple(tmp_path):
    _mk_sysctl(tmp_path, ipv4_tcp_rmem="4096\t131072\t33554432")
    triple = net_sysctl_audit.read_tcp_triple(str(tmp_path),
                                                 "ipv4/tcp_rmem")
    assert triple == (4096, 131072, 33554432)


def test_read_tcp_rmem_missing_returns_none(tmp_path):
    assert net_sysctl_audit.read_tcp_triple(str(tmp_path),
                                                "ipv4/tcp_rmem") is None


# --- classify_one ------------------------------------------------

def test_classify_rmem_max_default_is_warn():
    # Linux default 208 KiB → flag for LAN-served LLM
    v = net_sysctl_audit.classify_one("core/rmem_max", 212992)
    assert v["severity"] == "warn"
    assert "208" in v["reason"] or "rmem_max" in v["reason"]
    assert v["recommended"] >= 16 * 1024 * 1024


def test_classify_rmem_max_tuned_is_ok():
    v = net_sysctl_audit.classify_one("core/rmem_max", 16 * 1024 * 1024)
    assert v["severity"] == "ok"


def test_classify_wmem_max_default_is_warn():
    v = net_sysctl_audit.classify_one("core/wmem_max", 212992)
    assert v["severity"] == "warn"


def test_classify_somaxconn_default_is_warn():
    # Pre-5.4 default was 128 → warn
    v = net_sysctl_audit.classify_one("core/somaxconn", 128)
    assert v["severity"] == "warn"


def test_classify_somaxconn_high_is_ok():
    v = net_sysctl_audit.classify_one("core/somaxconn", 4096)
    assert v["severity"] == "ok"


def test_classify_netdev_max_backlog_default_is_warn():
    v = net_sysctl_audit.classify_one("core/netdev_max_backlog", 1000)
    assert v["severity"] == "warn"


def test_classify_netdev_max_backlog_tuned_is_ok():
    v = net_sysctl_audit.classify_one("core/netdev_max_backlog", 5000)
    assert v["severity"] == "ok"


def test_classify_unknown_key():
    v = net_sysctl_audit.classify_one("core/never_heard", 42)
    assert v["severity"] == "unknown"


def test_classify_none_value():
    v = net_sysctl_audit.classify_one("core/rmem_max", None)
    assert v["severity"] == "unknown"


# --- aggregate ---------------------------------------------------

def test_aggregate_all_ok():
    rows = [{"severity": "ok"}, {"severity": "ok"}]
    assert net_sysctl_audit.aggregate(rows) == "ok"


def test_aggregate_one_warn():
    rows = [{"severity": "ok"}, {"severity": "warn"}, {"severity": "ok"}]
    assert net_sysctl_audit.aggregate(rows) == "warn"


def test_aggregate_empty():
    assert net_sysctl_audit.aggregate([]) == "unknown"


# --- recipe ------------------------------------------------------

def test_make_recipe_includes_sysctld_path():
    flagged = [
        {"name": "core/rmem_max", "value": 212992,
         "recommended": 16777216},
    ]
    r = net_sysctl_audit.make_recipe(flagged)
    assert "/etc/sysctl.d/" in r
    assert "net.core.rmem_max=16777216" in r
    assert "sysctl --system" in r or "sysctl -p" in r


def test_make_recipe_empty_when_nothing_flagged():
    assert net_sysctl_audit.make_recipe([]) == ""


def test_make_recipe_multi_flagged():
    flagged = [
        {"name": "core/rmem_max", "value": 212992,
         "recommended": 16777216},
        {"name": "core/wmem_max", "value": 212992,
         "recommended": 16777216},
    ]
    r = net_sysctl_audit.make_recipe(flagged)
    assert "rmem_max" in r
    assert "wmem_max" in r


# --- status ------------------------------------------------------

def test_status_no_proc_net(tmp_path, monkeypatch):
    monkeypatch.setattr(net_sysctl_audit, "_SYSCTL_ROOT",
                          str(tmp_path / "absent"))
    s = net_sysctl_audit.status()
    assert s["ok"] is False
    assert s["error"] == "net_sysctl_unavailable"


def test_status_live_default_kernel(tmp_path, monkeypatch):
    # The live-rig case
    _mk_sysctl(tmp_path,
               core_rmem_max=212992,
               core_wmem_max=212992,
               core_somaxconn=4096,
               core_netdev_max_backlog=1000)
    monkeypatch.setattr(net_sysctl_audit, "_SYSCTL_ROOT", str(tmp_path))
    s = net_sysctl_audit.status()
    assert s["ok"] is True
    assert s["worst_severity"] == "warn"
    # rmem_max, wmem_max, netdev_max_backlog flagged ; somaxconn ok
    names = {r["name"]: r["severity"] for r in s["rows"]}
    assert names["core/rmem_max"] == "warn"
    assert names["core/wmem_max"] == "warn"
    assert names["core/somaxconn"] == "ok"
    assert names["core/netdev_max_backlog"] == "warn"
    # Recipe targets the bumped values
    assert "net.core.rmem_max=" in s["recipe"]


def test_status_tuned_kernel_ok(tmp_path, monkeypatch):
    _mk_sysctl(tmp_path,
               core_rmem_max=16777216,
               core_wmem_max=16777216,
               core_somaxconn=4096,
               core_netdev_max_backlog=5000)
    monkeypatch.setattr(net_sysctl_audit, "_SYSCTL_ROOT", str(tmp_path))
    s = net_sysctl_audit.status()
    assert s["worst_severity"] == "ok"
    assert s["recipe"] == ""


def test_status_partial_sysctl_omits_absent(tmp_path, monkeypatch):
    # Some kernels omit netdev_max_backlog on non-network namespaces
    _mk_sysctl(tmp_path, core_rmem_max=16777216,
               core_wmem_max=16777216, core_somaxconn=4096)
    monkeypatch.setattr(net_sysctl_audit, "_SYSCTL_ROOT", str(tmp_path))
    s = net_sysctl_audit.status()
    names = {r["name"] for r in s["rows"]}
    assert "core/netdev_max_backlog" not in names
