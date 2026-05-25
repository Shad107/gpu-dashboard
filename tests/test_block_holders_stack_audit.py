"""Tests for modules/block_holders_stack_audit.py R&D #96.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import block_holders_stack_audit as mod


def _mk_dm(tmp_path, name, *, dm_name="myvg-mylv",
            suspended="0", holders=None):
    d = tmp_path / "block" / name
    d.mkdir(parents=True, exist_ok=True)
    dm = d / "dm"
    dm.mkdir()
    (dm / "name").write_text(dm_name + "\n")
    (dm / "suspended").write_text(suspended + "\n")
    h = d / "holders"
    h.mkdir()
    for hn in (holders or []):
        (h / hn).write_text("")


def _mk_md(tmp_path, name, *, degraded="0",
            sync_action="idle", array_state="clean"):
    d = tmp_path / "block" / name
    d.mkdir(parents=True, exist_ok=True)
    md = d / "md"
    md.mkdir()
    (md / "degraded").write_text(degraded + "\n")
    (md / "sync_action").write_text(sync_action + "\n")
    (md / "array_state").write_text(array_state + "\n")


def _mk_mounts(tmp_path, devs):
    p = tmp_path / "mounts"
    lines = []
    for d in devs:
        lines.append(f"{d} /mnt ext4 rw 0 0")
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def _mk_swaps(tmp_path, devs):
    p = tmp_path / "swaps"
    lines = ["Filename Type Size Used Priority"]
    for d in devs:
        lines.append(f"{d} partition 8388604 0 -2")
    p.write_text("\n".join(lines) + "\n")
    return str(p)


# --- parse_proc_mounts -----------------------------------------

def test_parse_mounts_empty():
    assert mod.parse_proc_mounts("") == set()


def test_parse_mounts_filters_dev():
    text = (
        "proc /proc proc rw 0 0\n"
        "/dev/sda1 / ext4 rw 0 0\n"
        "/dev/dm-1 /home ext4 rw 0 0\n"
        "tmpfs /tmp tmpfs rw 0 0\n")
    out = mod.parse_proc_mounts(text)
    assert out == {"/dev/sda1", "/dev/dm-1"}


# --- parse_proc_swaps ------------------------------------------

def test_parse_swaps_empty():
    assert mod.parse_proc_swaps("") == set()


def test_parse_swaps_skips_header():
    text = (
        "Filename Type Size Used Priority\n"
        "/dev/dm-2 partition 8388604 0 -2\n"
        "/swap.img file 8388604 0 -2\n")
    out = mod.parse_proc_swaps(text)
    # Only /dev/* entries
    assert out == {"/dev/dm-2"}


# --- _device_aliases -------------------------------------------

def test_aliases_dm():
    out = mod._device_aliases("dm-1", "myvg-mylv")
    assert "/dev/dm-1" in out
    assert "/dev/mapper/myvg-mylv" in out


def test_aliases_no_dm_name():
    out = mod._device_aliases("md0", "")
    assert out == ["/dev/md0"]


# --- walk_dm_md ------------------------------------------------

def test_walk_missing(tmp_path):
    out = mod.walk_dm_md(str(tmp_path / "nope"))
    assert out == {"dm": [], "md": []}


def test_walk_dm_and_md(tmp_path):
    _mk_dm(tmp_path, "dm-0", dm_name="vg-lv0")
    _mk_md(tmp_path, "md0", degraded="0")
    # Plus a non-dm/md to be ignored
    (tmp_path / "block" / "sda").mkdir(parents=True)
    out = mod.walk_dm_md(str(tmp_path / "block"))
    assert len(out["dm"]) == 1
    assert len(out["md"]) == 1
    assert out["dm"][0]["dm_name"] == "vg-lv0"


# --- classify --------------------------------------------------

def _dm(*, name="dm-0", dm_name="vg-lv", suspended=0,
        holders=None):
    return {"name": name, "dm_name": dm_name,
            "suspended": suspended,
            "holders": holders or []}


def _md(*, name="md0", degraded=0, sync_action="idle",
        array_state="clean"):
    return {"name": name, "degraded": degraded,
            "sync_action": sync_action,
            "array_state": array_state}


def test_classify_unknown_no_block():
    v = mod.classify(
        {"dm": [], "md": []}, set(), set(), False)
    assert v["verdict"] == "unknown"


def test_classify_sane_no_dm_md():
    v = mod.classify(
        {"dm": [], "md": []}, set(), set(), True)
    assert v["verdict"] == "block_stack_sane"


def test_classify_dm_suspended_mounted_err():
    v = mod.classify(
        {"dm": [_dm(suspended=1)], "md": []},
        {"/dev/dm-0"}, set(), True)
    assert v["verdict"] == "dm_suspended_with_mount"


def test_classify_dm_suspended_swap_err():
    v = mod.classify(
        {"dm": [_dm(suspended=1, dm_name="vg-swap")],
         "md": []},
        set(), {"/dev/mapper/vg-swap"}, True)
    assert v["verdict"] == "dm_suspended_with_mount"


def test_classify_dm_suspended_no_mount_is_ok():
    v = mod.classify(
        {"dm": [_dm(suspended=1, holders=["dm-1"])],
         "md": []},
        set(), set(), True)
    # not orphan (has holders), not mounted/swap → sane
    assert v["verdict"] == "block_stack_sane"


def test_classify_md_degraded_idle_warn():
    v = mod.classify(
        {"dm": [], "md": [_md(degraded=1,
                                sync_action="idle")]},
        set(), set(), True)
    assert v["verdict"] == "md_degraded_no_resync"


def test_classify_md_degraded_resyncing_is_ok():
    v = mod.classify(
        {"dm": [], "md": [_md(degraded=1,
                                sync_action="recover")]},
        set(), set(), True)
    assert v["verdict"] == "block_stack_sane"


def test_classify_orphan_dm_accent():
    v = mod.classify(
        {"dm": [_dm(name="dm-9", dm_name="leftover",
                    suspended=0, holders=[])],
         "md": []},
        set(), set(), True)
    assert v["verdict"] == "orphan_dm_device"


def test_classify_dm_with_holders_is_ok():
    v = mod.classify(
        {"dm": [_dm(name="dm-0",
                    suspended=0, holders=["dm-1"])],
         "md": []},
        set(), set(), True)
    assert v["verdict"] == "block_stack_sane"


# Priority : dm_suspended > md_degraded > orphan
def test_priority_dm_susp_over_md_degraded():
    v = mod.classify(
        {"dm": [_dm(name="dm-0", suspended=1)],
         "md": [_md(degraded=1, sync_action="idle")]},
        {"/dev/dm-0"}, set(), True)
    assert v["verdict"] == "dm_suspended_with_mount"


def test_priority_md_degraded_over_orphan():
    v = mod.classify(
        {"dm": [_dm(name="dm-9", suspended=0, holders=[])],
         "md": [_md(degraded=1, sync_action="idle")]},
        set(), set(), True)
    assert v["verdict"] == "md_degraded_no_resync"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope"),
                       str(tmp_path / "no_mounts"),
                       str(tmp_path / "no_swaps"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_sane_synthetic(tmp_path):
    # No dm/md at all
    (tmp_path / "block").mkdir()
    m = _mk_mounts(tmp_path, [])
    s = _mk_swaps(tmp_path, [])
    out = mod.status(None, str(tmp_path / "block"), m, s)
    assert out["verdict"]["verdict"] == "block_stack_sane"
    assert out["dm_count"] == 0


def test_status_dm_suspended_mounted_synthetic(tmp_path):
    _mk_dm(tmp_path, "dm-0", dm_name="myvg-myroot",
                suspended="1")
    m = _mk_mounts(tmp_path, ["/dev/mapper/myvg-myroot"])
    s = _mk_swaps(tmp_path, [])
    out = mod.status(None, str(tmp_path / "block"), m, s)
    assert (out["verdict"]["verdict"]
            == "dm_suspended_with_mount")
    assert out["ok"] is False
