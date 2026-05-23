"""Tests for modules/efi_runtime_map_audit.py — R&D #65.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import efi_runtime_map_audit as mod


def _mk_entry(root, idx, *, type_=3, num_pages=10,
                attribute=0x800000000000000F,
                phys_addr="0x100000000", virt_addr="0x100000000"):
    d = root / str(idx)
    d.mkdir(parents=True, exist_ok=True)
    (d / "type").write_text(f"{type_}\n")
    (d / "num_pages").write_text(f"{num_pages}\n")
    (d / "attribute").write_text(f"{attribute}\n")
    (d / "phys_addr").write_text(phys_addr + "\n")
    (d / "virt_addr").write_text(virt_addr + "\n")


# --- list_entries -----------------------------------------------

def test_list_entries_missing(tmp_path):
    assert mod.list_entries(str(tmp_path / "nope")) == []


def test_list_entries_empty_dir(tmp_path):
    assert mod.list_entries(str(tmp_path)) == []


def test_list_entries(tmp_path):
    _mk_entry(tmp_path, 0, num_pages=10)
    _mk_entry(tmp_path, 1, num_pages=20)
    out = mod.list_entries(str(tmp_path))
    assert len(out) == 2
    assert out[0]["num_pages"] == 10
    assert out[1]["num_pages"] == 20


# --- classify ---------------------------------------------------

def _e(id_="0", type_=3, num_pages=10, attribute=15):
    return {"id": id_, "type": type_, "num_pages": num_pages,
              "attribute": attribute,
              "phys_addr": "0x100000000",
              "virt_addr": "0x100000000"}


def test_classify_no_efi():
    v = mod.classify([], efi_present=False,
                       runtime_map_present=False,
                       perm_denied=False)
    assert v["verdict"] == "unknown"


def test_classify_runtime_map_absent():
    v = mod.classify([], efi_present=True,
                       runtime_map_present=False,
                       perm_denied=False)
    assert v["verdict"] == "runtime_map_absent"


def test_classify_kexec_no_efi():
    v = mod.classify([], efi_present=True,
                       runtime_map_present=True,
                       perm_denied=False)
    assert v["verdict"] == "kexec_no_efi_rt"


def test_classify_ok():
    v = mod.classify([_e(num_pages=10), _e(id_="1", num_pages=20)],
                       efi_present=True,
                       runtime_map_present=True,
                       perm_denied=False)
    assert v["verdict"] == "ok"


def test_classify_pinned_large():
    # 5000 pages * 4 KiB = 20 MiB > 16 MiB threshold
    v = mod.classify([_e(num_pages=5000)],
                       efi_present=True,
                       runtime_map_present=True,
                       perm_denied=False)
    assert v["verdict"] == "runtime_pinned_large"


def test_classify_requires_root():
    # entries enumerated but num_pages all None → perm_denied
    e = _e(num_pages=None)
    v = mod.classify([e],
                       efi_present=True,
                       runtime_map_present=True,
                       perm_denied=True)
    assert v["verdict"] == "requires_root"


# --- status integration -----------------------------------------

def test_status_no_efi(tmp_path):
    out = mod.status(None, str(tmp_path / "no_efi"),
                       str(tmp_path / "no_rt"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like_requires_root(tmp_path):
    efi = tmp_path / "efi"
    efi.mkdir()
    rt = efi / "runtime-map"
    rt.mkdir()
    # Make entries without readable contents
    for i in range(4):
        d = rt / str(i)
        d.mkdir()
        # don't write num_pages → read returns None
    out = mod.status(None, str(efi), str(rt))
    assert out["ok"] is True
    assert out["entry_count"] == 4
    assert out["permission_denied"] is True
    assert out["verdict"]["verdict"] == "requires_root"
