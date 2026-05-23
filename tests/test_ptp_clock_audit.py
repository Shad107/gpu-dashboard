"""Tests for modules/ptp_clock_audit.py — R&D #63.2."""
from __future__ import annotations

import os
import pytest

from gpu_dashboard.modules import ptp_clock_audit as mod


def _mk_phc(root, idx, *, clock_name="ptp_kvm",
              max_adjustment=1000000, n_alarm=0, n_ext_ts=0,
              n_per_out=0, n_pins=0, pps_available=0):
    d = root / f"ptp{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "clock_name").write_text(clock_name + "\n")
    (d / "max_adjustment").write_text(f"{max_adjustment}\n")
    (d / "n_alarm").write_text(f"{n_alarm}\n")
    (d / "n_ext_ts").write_text(f"{n_ext_ts}\n")
    (d / "n_per_out").write_text(f"{n_per_out}\n")
    (d / "n_pins").write_text(f"{n_pins}\n")
    (d / "pps_available").write_text(f"{pps_available}\n")


def _mk_dev(root, name, mode=0o660):
    p = root / name
    p.write_text("")
    os.chmod(p, mode)


# --- list_phcs --------------------------------------------------

def test_list_phcs_missing(tmp_path):
    assert mod.list_phcs(str(tmp_path / "nope")) == []


def test_list_phcs(tmp_path):
    _mk_phc(tmp_path, 0)
    _mk_phc(tmp_path, 1, clock_name="ice")
    out = mod.list_phcs(str(tmp_path))
    assert len(out) == 2


# --- list_dev_perms ---------------------------------------------

def test_list_dev_perms(tmp_path):
    _mk_dev(tmp_path, "ptp0", mode=0o660)
    _mk_dev(tmp_path, "ptp1", mode=0o600)
    _mk_dev(tmp_path, "sda", mode=0o660)
    out = mod.list_dev_perms(str(tmp_path))
    assert len(out) == 2


# --- classify ---------------------------------------------------

def _phc(id_="ptp0", clock_name="ice", max_adjustment=1000000):
    return {"id": id_, "clock_name": clock_name,
              "max_adjustment": max_adjustment,
              "n_alarm": 0, "n_ext_ts": 0, "n_per_out": 0,
              "n_pins": 0, "pps_available": 0}


def _dev(name="ptp0", mode=0o660):
    return {"name": name, "mode": mode, "uid": 0, "gid": 0}


def test_classify_unknown():
    v = mod.classify([], [], sys_ptp_present=False)
    assert v["verdict"] == "unknown"


def test_classify_sw_timestamping_only():
    v = mod.classify([], [], sys_ptp_present=True)
    assert v["verdict"] == "sw_timestamping_only"


def test_classify_ok():
    v = mod.classify([_phc()], [_dev()],
                       sys_ptp_present=True)
    assert v["verdict"] == "ok"


def test_classify_max_adj_zero():
    v = mod.classify([_phc(max_adjustment=0)], [_dev()],
                       sys_ptp_present=True)
    assert v["verdict"] == "max_adjustment_zero"


def test_classify_phc_unused_root_only():
    v = mod.classify([_phc()], [_dev(mode=0o600)],
                       sys_ptp_present=True)
    assert v["verdict"] == "phc_unused"


def test_classify_priority_max_adj_wins():
    v = mod.classify(
        [_phc(max_adjustment=0)], [_dev(mode=0o600)],
        sys_ptp_present=True)
    assert v["verdict"] == "max_adjustment_zero"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nophc"),
                       str(tmp_path / "nodev"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    sp = tmp_path / "ptp"
    _mk_phc(sp, 0)
    dv = tmp_path / "dev"
    dv.mkdir()
    _mk_dev(dv, "ptp0", mode=0o660)
    out = mod.status(None, str(sp), str(dv))
    assert out["ok"] is True
    assert out["phc_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
