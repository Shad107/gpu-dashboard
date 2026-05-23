"""Tests for modules/mtd_flash_audit.py — R&D #66.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import mtd_flash_audit as mod


def _mk_mtd(root, idx, *, name="BIOS", type_="nor",
              size=0x400000, erasesize=0x1000,
              writesize=1, flags=0x401,
              numeraseregions=1, bad_blocks=0):
    d = root / f"mtd{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    (d / "type").write_text(type_ + "\n")
    (d / "size").write_text(f"{size}\n")
    (d / "erasesize").write_text(f"{erasesize}\n")
    (d / "writesize").write_text(f"{writesize}\n")
    (d / "flags").write_text(f"{flags}\n")
    (d / "numeraseregions").write_text(f"{numeraseregions}\n")
    (d / "bad_blocks").write_text(f"{bad_blocks}\n")


# --- parse_proc_mtd ---------------------------------------------

def test_parse_proc_mtd():
    text = ('dev:    size   erasesize  name\n'
              'mtd0: 00400000 00001000 "BIOS"\n'
              'mtd1: 00100000 00010000 "ME"\n')
    out = mod.parse_proc_mtd(text)
    assert len(out) == 2
    assert out[0]["name"] == "mtd0"
    assert out[0]["size"] == 0x400000
    assert out[0]["label"] == "BIOS"


def test_parse_proc_mtd_empty():
    assert mod.parse_proc_mtd("") == []
    assert mod.parse_proc_mtd(None) == []


# --- list_mtd_sysfs ---------------------------------------------

def test_list_mtd_sysfs_missing(tmp_path):
    assert mod.list_mtd_sysfs(str(tmp_path / "nope")) == []


def test_list_mtd_sysfs(tmp_path):
    _mk_mtd(tmp_path, 0, name="BIOS")
    _mk_mtd(tmp_path, 1, name="ME")
    out = mod.list_mtd_sysfs(str(tmp_path))
    assert len(out) == 2


# --- classify ---------------------------------------------------

def _m(id_="mtd0", name="rootfs", flags=0x1, bad_blocks=0):
    # default name avoids the BIOS write-protect heuristic
    return {"id": id_, "name": name, "type": "nor",
              "size": 0x400000, "erasesize": 0x1000,
              "writesize": 1, "flags": flags,
              "numeraseregions": 1, "bad_blocks": bad_blocks}


def test_classify_unknown():
    v = mod.classify([], [], False, False)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_m()], [], True, False)
    assert v["verdict"] == "ok"


def test_classify_bad_blocks():
    v = mod.classify([_m(bad_blocks=5)], [], True, False)
    assert v["verdict"] == "nor_bad_blocks"


def test_classify_write_protect_drift():
    # name=bios + flags has WRITEABLE bit (0x400) → drift
    v = mod.classify([_m(name="bios", flags=0xC00)],
                       [], True, False)
    assert v["verdict"] == "write_protect_drift"


def test_classify_unmapped():
    proc = [{"name": "mtd5", "size": 0x100,
              "erasesize": 0x100, "label": "extra"}]
    v = mod.classify([_m(id_="mtd0", name="rootfs",
                            flags=0x1)],
                       proc, True, True)
    assert v["verdict"] == "unmapped_partition"


def test_classify_priority_bad_blocks_wins():
    v = mod.classify(
        [_m(name="bios", flags=0xC00, bad_blocks=5)],
        [], True, False)
    assert v["verdict"] == "nor_bad_blocks"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nomtd"),
                       str(tmp_path / "noproc"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    sm = tmp_path / "mtd"
    _mk_mtd(sm, 0, name="rootfs", flags=0x1)
    pm = tmp_path / "proc_mtd"
    pm.write_text('dev: size erasesize name\n'
                     'mtd0: 00400000 00001000 "rootfs"\n')
    out = mod.status(None, str(sm), str(pm))
    assert out["ok"] is True
    assert out["sysfs_count"] == 1
    assert out["verdict"]["verdict"] == "ok"
