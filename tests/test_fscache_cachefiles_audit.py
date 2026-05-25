"""Tests for modules/fscache_cachefiles_audit.py R&D #101.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import fscache_cachefiles_audit as mod


# --- parse_caches ----------------------------------------------

def test_parse_caches_empty():
    assert mod.parse_caches("") == []
    assert mod.parse_caches(None) == []


def test_parse_caches_skips_header():
    text = (
        "CACHE   STATE     NAME\n"
        "SSD     ACTIVE    default\n"
        "HDD     CULLING   bulk\n")
    out = mod.parse_caches(text)
    assert len(out) == 2
    assert out[0]["state"] == "ACTIVE"
    assert out[0]["name"] == "default"
    assert out[1]["state"] == "CULLING"


# --- parse_nfs_fsc_mounts --------------------------------------

def test_parse_nfs_empty():
    assert mod.parse_nfs_fsc_mounts("") == []


def test_parse_nfs_fsc():
    text = (
        "server:/data /mnt/data nfs4 rw,fsc,relatime 0 0\n"
        "server:/other /mnt/other nfs4 rw,relatime 0 0\n"
        "/dev/sda1 / ext4 rw 0 0\n")
    out = mod.parse_nfs_fsc_mounts(text)
    assert out == ["/mnt/data"]


# --- classify --------------------------------------------------

def _c(name, state):
    return {"name": name, "state": state}


def test_classify_unknown_module_absent():
    v = mod.classify(False, False, False, [], [], False)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify(True, True, False, [], [], False)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True, True, True,
                          [_c("default", "ACTIVE")],
                          ["/mnt/data"], True)
    assert v["verdict"] == "ok"


def test_classify_culling_err():
    v = mod.classify(True, True, True,
                          [_c("default", "CULLING")],
                          [], True)
    assert v["verdict"] == "fscache_caches_culling"


def test_classify_exhausted_err():
    v = mod.classify(True, True, True,
                          [_c("default", "EXHAUSTED")],
                          [], True)
    assert v["verdict"] == "fscache_caches_culling"


def test_classify_nfs_fsc_no_backend_warn():
    v = mod.classify(True, True, True,
                          [_c("default", "ACTIVE")],
                          ["/mnt/data"], False)
    assert v["verdict"] == "nfs_fsc_without_backend"


def test_classify_loaded_no_caches_accent():
    v = mod.classify(True, True, True,
                          [], [], True)
    assert v["verdict"] == "fscache_loaded_no_caches"


# Priority : culling > nfs_fsc_no_backend > no_caches
def test_priority_culling_over_nfs():
    v = mod.classify(True, True, True,
                          [_c("default", "CULLING")],
                          ["/mnt/data"], False)
    assert v["verdict"] == "fscache_caches_culling"


def test_priority_nfs_over_no_caches():
    # caches is empty AND nfs_fsc + no backend
    # → nfs_fsc_no_backend (nfs check evaluated first
    # in classify with caches present), but with empty caches
    # the no_caches check would also fire.
    # Implementation evaluates nfs_fsc check before no_caches
    v = mod.classify(True, True, True,
                          [], ["/mnt/data"], False)
    assert v["verdict"] == "nfs_fsc_without_backend"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "no_proc"),
                       str(tmp_path / "no_sysfs"),
                       str(tmp_path / "no_mod"),
                       str(tmp_path / "no_mounts"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_loaded_no_caches(tmp_path):
    mod_dir = tmp_path / "module"
    mod_dir.mkdir()
    proc = tmp_path / "fscache"
    proc.mkdir()
    (proc / "caches").write_text("CACHE STATE NAME\n")
    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/sda1 / ext4 rw 0 0\n")
    out = mod.status(None, str(proc),
                       str(tmp_path / "no_cachefiles"),
                       str(mod_dir), str(mounts))
    assert (out["verdict"]["verdict"]
            == "fscache_loaded_no_caches")
