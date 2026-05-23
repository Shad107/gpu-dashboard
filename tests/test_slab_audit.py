"""Tests for modules/slab_audit.py — R&D #44.2."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import slab_audit as mod


SLABINFO_SAMPLE = """\
slabinfo - version: 2.1
# name            <active_objs> <num_objs> <objsize> <objperslab> <pagesperslab> : tunables ... : slabdata ...
dentry           150000 200000 192 21 1 : tunables 0 0 0 : slabdata 9524 9524 0
inode_cache      80000 100000 624 13 2 : tunables 0 0 0 : slabdata 7692 7692 0
kmalloc-1024     1000  1024  1024 16 4 : tunables 0 0 0 : slabdata 64 64 0
"""


# --- parse_slabinfo ------------------------------------------------

def test_parse_slabinfo_skips_header():
    rows = mod.parse_slabinfo(SLABINFO_SAMPLE)
    names = [r["name"] for r in rows]
    assert names == ["dentry", "inode_cache", "kmalloc-1024"]


def test_parse_slabinfo_resident_kb():
    rows = mod.parse_slabinfo(SLABINFO_SAMPLE)
    dentry = rows[0]
    assert dentry["num_objs"] == 200000
    assert dentry["object_size"] == 192
    assert dentry["resident_kb"] == (200000 * 192) // 1024


def test_parse_slabinfo_empty():
    assert mod.parse_slabinfo("") == []


def test_parse_slabinfo_skips_malformed():
    txt = ("slabinfo - version: 2.1\n"
           "garbage\n"
           "ok 100 100 64 16 1 : tunables 0 0 0 : slabdata 0 0 0\n")
    rows = mod.parse_slabinfo(txt)
    assert len(rows) == 1
    assert rows[0]["name"] == "ok"


# --- read_sysfs_slab ----------------------------------------------

def _mk_slab(root: Path, name: str, *, objects: int = 1000,
              object_size: int = 64, slabs: int = 50,
              partial: int = 10, cpu_slabs: int = 12,
              objs_per_slab: int = 16):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "objects").write_text(str(objects) + "\n")
    (d / "object_size").write_text(str(object_size) + "\n")
    (d / "slabs").write_text(str(slabs) + "\n")
    (d / "partial").write_text(str(partial) + "\n")
    (d / "cpu_slabs").write_text(str(cpu_slabs) + "\n")
    (d / "objs_per_slab").write_text(str(objs_per_slab) + "\n")


def test_read_sysfs_slab_basic(tmp_path):
    _mk_slab(tmp_path, "dentry", objects=200000, object_size=192,
              slabs=9524, partial=2000)
    _mk_slab(tmp_path, "inode_cache", objects=80000, object_size=624)
    out = mod.read_sysfs_slab(str(tmp_path))
    names = sorted(d["name"] for d in out)
    assert names == ["dentry", "inode_cache"]
    dentry = next(d for d in out if d["name"] == "dentry")
    assert dentry["resident_kb"] == (200000 * 192) // 1024


def test_read_sysfs_slab_missing(tmp_path):
    assert mod.read_sysfs_slab(str(tmp_path / "nope")) == []


# --- _frag_ratio --------------------------------------------------

def test_frag_ratio_basic():
    assert mod._frag_ratio({"slabs": 100, "partial": 30}) == 0.30


def test_frag_ratio_zero_slabs():
    assert mod._frag_ratio({"slabs": 0, "partial": 10}) == 0.0


# --- classify ------------------------------------------------------

def _cache(name="dentry", objects=10000, object_size=192,
            slabs=50, partial=10, resident_kb=None):
    if resident_kb is None:
        resident_kb = (objects * object_size) // 1024
    return {"name": name, "objects": objects,
              "object_size": object_size, "slabs": slabs,
              "partial": partial, "cpu_slabs": 12,
              "objs_per_slab": 16, "resident_kb": resident_kb}


def test_classify_requires_root_when_no_caches_and_probe():
    v = mod.classify([], requires_root_probe=True)
    assert v["verdict"] == "requires_root"
    assert "CAP_DAC_READ_SEARCH" in v["recommendation"]


def test_classify_no_slab_data():
    v = mod.classify([], requires_root_probe=False)
    assert v["verdict"] == "no_slab_data"


def test_classify_ok():
    v = mod.classify([_cache(slabs=100, partial=10)],
                       requires_root_probe=False)
    assert v["verdict"] == "ok"


def test_classify_fragmented():
    # 60 MB cache, 40 % partial.
    v = mod.classify([
        _cache(name="dentry", objects=500000, object_size=192,
                slabs=100, partial=40,
                resident_kb=60 * 1024)
    ], requires_root_probe=False)
    assert v["verdict"] == "fragmented"


def test_classify_fragmented_skipped_below_size():
    # Only 10 MB, 40 % partial — below 50 MB threshold.
    v = mod.classify([
        _cache(slabs=100, partial=40, resident_kb=10 * 1024)
    ], requires_root_probe=False)
    assert v["verdict"] == "ok"


def test_classify_leak_suspect():
    # > 80 % partial + > 10k objects + > 10 MB resident.
    v = mod.classify([
        _cache(name="task_struct", objects=20000, object_size=2048,
                slabs=100, partial=90,
                resident_kb=40 * 1024)
    ], requires_root_probe=False)
    assert v["verdict"] == "leak_suspect"


def test_classify_leak_wins_over_fragmented():
    leak = _cache(name="leaker", objects=20000, object_size=2048,
                    slabs=100, partial=90,
                    resident_kb=40 * 1024)
    frag = _cache(name="dentry", objects=500000, object_size=192,
                    slabs=100, partial=40,
                    resident_kb=60 * 1024)
    v = mod.classify([leak, frag], requires_root_probe=False)
    assert v["verdict"] == "leak_suspect"


# --- status integration -------------------------------------------

def test_status_requires_root(monkeypatch, tmp_path):
    # Create the sysfs dir with one child that's mode 0700 + child
    # files mode 0000 — simulate "permission denied" by patching the
    # _read helper.
    sk = tmp_path / "slab"
    sk.mkdir()
    (sk / ":0000016").mkdir()
    monkeypatch.setattr(mod, "_PROC_SLABINFO",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SYS_KERNEL_SLAB", str(sk))
    monkeypatch.setattr(mod, "_has_permission_error", lambda p: True)
    out = mod.status()
    assert out["ok"] is True
    assert out["requires_root"] is True
    assert out["verdict"]["verdict"] == "requires_root"


def test_status_with_slabinfo(monkeypatch, tmp_path):
    (tmp_path / "slabinfo").write_text(SLABINFO_SAMPLE)
    (tmp_path / "slab").mkdir()
    monkeypatch.setattr(mod, "_PROC_SLABINFO",
                        str(tmp_path / "slabinfo"))
    monkeypatch.setattr(mod, "_SYS_KERNEL_SLAB",
                        str(tmp_path / "slab"))
    out = mod.status()
    assert out["ok"] is True
    assert out["cache_count"] == 3
    # Top cache by resident_kb should be inode_cache (80k * 624 B ≈ 49 MB)
    # vs dentry (200k * 192 B ≈ 38 MB) — actually dentry is smaller.
    # Sorted descending by resident_kb : inode_cache first.
    assert out["top_caches"][0]["name"] == "inode_cache"
