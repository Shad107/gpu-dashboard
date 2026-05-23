"""Tests for modules/sata_link_pm_audit.py — R&D #56.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import sata_link_pm_audit as mod


def _mk_host(root, idx, *, policy=None):
    d = root / f"host{idx}"
    d.mkdir(parents=True, exist_ok=True)
    if policy is not None:
        (d / "link_power_management_policy").write_text(
            policy + "\n")
    return d


def _mk_link(root, idx, *, spd=None, spd_limit=None):
    d = root / f"link{idx}"
    d.mkdir(parents=True, exist_ok=True)
    if spd is not None:
        (d / "sata_spd").write_text(spd + "\n")
    if spd_limit is not None:
        (d / "sata_spd_limit").write_text(spd_limit + "\n")
    return d


# --- list_host_policies -----------------------------------------

def test_list_host_policies_missing(tmp_path):
    assert mod.list_host_policies(str(tmp_path / "nope")) == []


def test_list_host_policies(tmp_path):
    _mk_host(tmp_path, 0)  # no policy file → non-SATA
    _mk_host(tmp_path, 1, policy="max_performance")
    _mk_host(tmp_path, 2, policy="min_power")
    out = mod.list_host_policies(str(tmp_path))
    assert len(out) == 3
    assert out[0]["policy"] is None
    assert out[1]["policy"] == "max_performance"
    assert out[2]["policy"] == "min_power"


# --- list_link_speeds -------------------------------------------

def test_list_link_speeds_missing(tmp_path):
    assert mod.list_link_speeds(str(tmp_path / "nope")) == []


def test_list_link_speeds(tmp_path):
    _mk_link(tmp_path, 1, spd="6.0 Gbps", spd_limit="6.0 Gbps")
    out = mod.list_link_speeds(str(tmp_path))
    assert len(out) == 1
    assert out[0]["sata_spd"] == "6.0 Gbps"


# --- classify ---------------------------------------------------

def _hosts(*policies):
    return [{"id": f"host{i}", "policy": p}
              for i, p in enumerate(policies)]


def test_classify_unknown():
    v = mod.classify([])
    assert v["verdict"] == "unknown"


def test_classify_no_policy_files():
    # Hosts present but no link_power_management_policy file
    # → non-SATA transports (USB/NVMe). Verdict = ok.
    v = mod.classify(_hosts(None, None))
    assert v["verdict"] == "ok"


def test_classify_all_max_performance():
    v = mod.classify(_hosts(None,
                                "max_performance",
                                "max_performance"))
    assert v["verdict"] == "ok"


def test_classify_min_power():
    v = mod.classify(_hosts("max_performance", "min_power"))
    assert v["verdict"] == "min_power"


def test_classify_med_power_with_dipm():
    v = mod.classify(_hosts("max_performance",
                                "med_power_with_dipm"))
    assert v["verdict"] == "med_power_with_dipm"


def test_classify_medium_power():
    v = mod.classify(_hosts("max_performance", "medium_power"))
    assert v["verdict"] == "medium_power"


def test_classify_priority_min_wins():
    v = mod.classify(_hosts("min_power",
                                "med_power_with_dipm",
                                "medium_power"))
    assert v["verdict"] == "min_power"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope1"),
                       str(tmp_path / "nope2"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    sh = tmp_path / "scsi_host"
    _mk_host(sh, 0)
    for i in range(1, 7):
        _mk_host(sh, i, policy="max_performance")
    al = tmp_path / "ata_link"
    _mk_link(al, 1, spd="6.0 Gbps")
    out = mod.status(None, str(sh), str(al))
    assert out["ok"] is True
    assert out["host_count"] == 7
    assert out["link_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
