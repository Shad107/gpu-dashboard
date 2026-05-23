"""Tests for modules/hugepages_audit.py — R&D #54.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import hugepages_audit as mod


def _mk_pool(root, size_kb, *, nr=0, free=0, surplus=0, resv=0,
              nr_overcommit=0):
    d = root / f"hugepages-{size_kb}kB"
    d.mkdir(parents=True, exist_ok=True)
    (d / "nr_hugepages").write_text(f"{nr}\n")
    (d / "free_hugepages").write_text(f"{free}\n")
    (d / "surplus_hugepages").write_text(f"{surplus}\n")
    (d / "resv_hugepages").write_text(f"{resv}\n")
    (d / "nr_overcommit_hugepages").write_text(f"{nr_overcommit}\n")
    return d


def _mk_node_pool(root, node, size_kb, *, nr=0):
    d = root / f"node{node}" / "hugepages" / f"hugepages-{size_kb}kB"
    d.mkdir(parents=True, exist_ok=True)
    (d / "nr_hugepages").write_text(f"{nr}\n")
    return d


# --- list_pool_sizes --------------------------------------------

def test_list_pool_sizes_missing(tmp_path):
    assert mod.list_pool_sizes(str(tmp_path / "nope")) == []


def test_list_pool_sizes(tmp_path):
    _mk_pool(tmp_path, 2048, nr=512, free=256)
    _mk_pool(tmp_path, 1048576, nr=2, free=2)
    out = mod.list_pool_sizes(str(tmp_path))
    assert len(out) == 2
    sizes = sorted(p["size_kb"] for p in out)
    assert sizes == [2048, 1048576]


def test_list_pool_sizes_ignores_other(tmp_path):
    _mk_pool(tmp_path, 2048)
    (tmp_path / "other").mkdir()
    out = mod.list_pool_sizes(str(tmp_path))
    assert len(out) == 1


# --- list_per_node ----------------------------------------------

def test_list_per_node_missing(tmp_path):
    assert mod.list_per_node(str(tmp_path / "nope")) == {}


def test_list_per_node_two_nodes(tmp_path):
    _mk_node_pool(tmp_path, 0, 2048, nr=400)
    _mk_node_pool(tmp_path, 1, 2048, nr=100)
    out = mod.list_per_node(str(tmp_path))
    assert out == {0: {2048: 400}, 1: {2048: 100}}


# --- read_meminfo_hp --------------------------------------------

def test_read_meminfo_hp(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text(
        "MemTotal: 1\n"
        "HugePages_Total:       8\n"
        "HugePages_Free:        4\n"
        "HugePages_Rsvd:        0\n"
        "HugePages_Surp:        0\n"
        "Hugepagesize:       2048 kB\n"
        "Hugetlb:           16384 kB\n")
    out = mod.read_meminfo_hp(str(p))
    assert out["hugepages_total"] == 8
    assert out["hugepages_free"] == 4
    assert out["hugepagesize"] == 2048


# --- classify ---------------------------------------------------

def _pool(size=2048, nr=0, free=0, surplus=0, resv=0,
            nr_overcommit=0):
    return {"size_kb": size, "nr": nr, "free": free,
              "surplus": surplus, "resv": resv,
              "nr_overcommit": nr_overcommit}


def test_classify_unknown():
    v = mod.classify([], {})
    assert v["verdict"] == "unknown"


def test_classify_ok_empty():
    # No pages reserved at all → ok
    v = mod.classify([_pool(nr=0, free=0)], {})
    assert v["verdict"] == "ok"


def test_classify_reserved_unused():
    v = mod.classify([_pool(nr=512, free=512, nr_overcommit=256)], {})
    assert v["verdict"] == "reserved_unused"


def test_classify_exhausted():
    v = mod.classify([_pool(nr=512, free=4, nr_overcommit=256)], {})
    assert v["verdict"] == "exhausted"


def test_classify_numa_imbalance():
    pools = [_pool(size=2048, nr=512, free=250,
                       nr_overcommit=256)]
    per_node = {0: {2048: 480}, 1: {2048: 32}}  # 480/512 = 93 %
    v = mod.classify(pools, per_node)
    assert v["verdict"] == "numa_imbalance"


def test_classify_overcommit_disabled():
    v = mod.classify([_pool(nr=512, free=250, nr_overcommit=0)], {})
    assert v["verdict"] == "overcommit_disabled"


def test_classify_priority_unused_wins():
    # All reserved, all free, AND overcommit=0 → unused wins.
    v = mod.classify([_pool(nr=512, free=512, nr_overcommit=0)], {})
    assert v["verdict"] == "reserved_unused"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nohp"),
                       str(tmp_path / "nonode"),
                       str(tmp_path / "nomem"),
                       str(tmp_path / "novm"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_idle(tmp_path):
    hp = tmp_path / "hp"
    _mk_pool(hp, 2048, nr=0, free=0)
    _mk_pool(hp, 1048576, nr=0, free=0)
    node = tmp_path / "node"
    node.mkdir()
    mem = tmp_path / "meminfo"
    mem.write_text(
        "HugePages_Total: 0\nHugePages_Free: 0\n"
        "Hugepagesize: 2048 kB\n")
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "nr_hugepages").write_text("0\n")
    (vm / "nr_overcommit_hugepages").write_text("0\n")
    out = mod.status(None, str(hp), str(node), str(mem), str(vm))
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "ok"
    assert out["vm_nr_hugepages"] == 0
