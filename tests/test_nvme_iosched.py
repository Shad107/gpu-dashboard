"""Tests for modules/nvme_iosched.py — R&D #30.3 NVMe I/O scheduler tuner."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from gpu_dashboard.modules import nvme_iosched


def _mk_queue(root: Path, dev: str, **attrs):
    q = root / dev / "queue"
    q.mkdir(parents=True)
    for k, v in attrs.items():
        (q / k).write_text(v)


def test_parse_scheduler_picks_bracketed_value():
    assert nvme_iosched.parse_scheduler("[none] mq-deadline kyber") == "none"
    assert nvme_iosched.parse_scheduler("none [mq-deadline] kyber") == "mq-deadline"


def test_parse_scheduler_single_available():
    assert nvme_iosched.parse_scheduler("[none]") == "none"


def test_parse_scheduler_no_brackets_returns_none():
    # Should not happen on modern kernels but be defensive
    assert nvme_iosched.parse_scheduler("none mq-deadline kyber") is None


def test_parse_scheduler_empty_returns_none():
    assert nvme_iosched.parse_scheduler("") is None
    assert nvme_iosched.parse_scheduler(None) is None


def test_list_nvme_devices_filters(tmp_path):
    (tmp_path / "nvme0n1").mkdir()
    (tmp_path / "nvme1n1").mkdir()
    (tmp_path / "sda").mkdir()
    (tmp_path / "sr0").mkdir()
    (tmp_path / "loop3").mkdir()
    devs = nvme_iosched.list_nvme_devices(str(tmp_path))
    assert devs == ["nvme0n1", "nvme1n1"]


def test_list_nvme_devices_no_dir_returns_empty(tmp_path):
    assert nvme_iosched.list_nvme_devices(str(tmp_path / "missing")) == []


def test_read_attr_strips(tmp_path):
    _mk_queue(tmp_path, "nvme0n1", scheduler="[none]\n")
    assert nvme_iosched.read_attr(str(tmp_path), "nvme0n1", "scheduler") == "[none]"


def test_read_attr_missing_returns_none(tmp_path):
    (tmp_path / "nvme0n1" / "queue").mkdir(parents=True)
    assert nvme_iosched.read_attr(str(tmp_path), "nvme0n1", "scheduler") is None


def test_classify_optimal():
    v = nvme_iosched.classify({
        "scheduler": "none",
        "read_ahead_kb": 4096,
        "nr_requests": 1024,
    })
    assert v["verdict"] == "optimal"
    assert v["recommendation"] == ""


def test_classify_optimal_with_higher_readahead():
    v = nvme_iosched.classify({
        "scheduler": "none",
        "read_ahead_kb": 8192,
        "nr_requests": 1024,
    })
    assert v["verdict"] == "optimal"


def test_classify_suboptimal_scheduler():
    # Ubuntu default mq-deadline on NVMe — the headline catch.
    v = nvme_iosched.classify({
        "scheduler": "mq-deadline",
        "read_ahead_kb": 4096,
        "nr_requests": 256,
    })
    assert v["verdict"] == "suboptimal_scheduler"
    assert "mq-deadline" in v["reason"]
    assert "scheduler" in v["recommendation"]
    assert "none" in v["recommendation"]


def test_classify_low_readahead():
    v = nvme_iosched.classify({
        "scheduler": "none",
        "read_ahead_kb": 128,
        "nr_requests": 256,
    })
    assert v["verdict"] == "low_readahead"
    assert "128" in v["reason"]
    assert "read_ahead_kb" in v["recommendation"]


def test_classify_both_bad_picks_worst():
    # Both wrong → scheduler is the bigger lever, but recommendation
    # should cover BOTH knobs.
    v = nvme_iosched.classify({
        "scheduler": "mq-deadline",
        "read_ahead_kb": 128,
        "nr_requests": 256,
    })
    assert v["verdict"] == "both_bad"
    assert "scheduler" in v["recommendation"]
    assert "read_ahead_kb" in v["recommendation"]


def test_classify_kyber_is_suboptimal():
    v = nvme_iosched.classify({
        "scheduler": "kyber",
        "read_ahead_kb": 4096,
        "nr_requests": 256,
    })
    assert v["verdict"] == "suboptimal_scheduler"


def test_classify_unknown_on_missing():
    v = nvme_iosched.classify({
        "scheduler": None,
        "read_ahead_kb": None,
        "nr_requests": None,
    })
    assert v["verdict"] == "unknown"


def test_status_no_nvme_devices(tmp_path, monkeypatch):
    monkeypatch.setattr(nvme_iosched, "_SYS_BLOCK", str(tmp_path))
    s = nvme_iosched.status()
    assert s["ok"] is True
    assert s["device_count"] == 0
    assert s["devices"] == []
    assert s["worst_verdict"] == "no_nvme"


def test_status_full_payload(tmp_path, monkeypatch):
    _mk_queue(tmp_path, "nvme0n1",
              scheduler="none [mq-deadline] kyber",
              read_ahead_kb="128",
              nr_requests="256")
    monkeypatch.setattr(nvme_iosched, "_SYS_BLOCK", str(tmp_path))
    s = nvme_iosched.status()
    assert s["ok"] is True
    assert s["device_count"] == 1
    d = s["devices"][0]
    assert d["device"] == "nvme0n1"
    assert d["scheduler"] == "mq-deadline"
    assert d["read_ahead_kb"] == 128
    assert d["nr_requests"] == 256
    assert d["verdict"]["verdict"] == "both_bad"
    assert s["worst_verdict"] == "both_bad"


def test_status_picks_worst_across_devices(tmp_path, monkeypatch):
    _mk_queue(tmp_path, "nvme0n1",
              scheduler="[none] mq-deadline",
              read_ahead_kb="4096",
              nr_requests="1024")
    _mk_queue(tmp_path, "nvme1n1",
              scheduler="none [mq-deadline]",
              read_ahead_kb="128",
              nr_requests="256")
    monkeypatch.setattr(nvme_iosched, "_SYS_BLOCK", str(tmp_path))
    s = nvme_iosched.status()
    assert s["device_count"] == 2
    assert s["worst_verdict"] == "both_bad"


def test_status_handles_partial_attrs(tmp_path, monkeypatch):
    # nr_requests file missing, scheduler readable
    _mk_queue(tmp_path, "nvme0n1",
              scheduler="[none]",
              read_ahead_kb="4096")
    monkeypatch.setattr(nvme_iosched, "_SYS_BLOCK", str(tmp_path))
    s = nvme_iosched.status()
    assert s["ok"] is True
    assert s["devices"][0]["nr_requests"] is None
    assert s["devices"][0]["scheduler"] == "none"


def test_recommendation_is_persistent_udev(tmp_path, monkeypatch):
    _mk_queue(tmp_path, "nvme0n1",
              scheduler="none [mq-deadline]",
              read_ahead_kb="128",
              nr_requests="256")
    monkeypatch.setattr(nvme_iosched, "_SYS_BLOCK", str(tmp_path))
    s = nvme_iosched.status()
    rec = s["devices"][0]["verdict"]["recommendation"]
    # Single-line echo for now, udev recipe for permanence
    assert "echo" in rec
    assert "udev" in rec.lower() or "/etc/udev" in rec


def test_status_no_brackets_scheduler_unknown(tmp_path, monkeypatch):
    # File exists but no bracketed value
    _mk_queue(tmp_path, "nvme0n1",
              scheduler="weirdtext",
              read_ahead_kb="4096",
              nr_requests="1024")
    monkeypatch.setattr(nvme_iosched, "_SYS_BLOCK", str(tmp_path))
    s = nvme_iosched.status()
    assert s["devices"][0]["scheduler"] is None
    assert s["devices"][0]["verdict"]["verdict"] == "unknown"
