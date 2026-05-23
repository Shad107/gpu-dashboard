"""Tests for modules/mdraid_health.py — R&D #45.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import mdraid_health as mod


MDSTAT_OK = """\
Personalities : [raid1] [raid5] [raid6] [linear]
md0 : active raid1 sda1[0] sdb1[1]
      1048576 blocks super 1.2 [2/2] [UU]

md1 : active raid5 sdc1[0] sdd1[1] sde1[2]
      2097152 blocks super 1.2 level 5, 64k chunk, algorithm 2 [3/3] [UUU]

unused devices: <none>
"""

MDSTAT_DEGRADED = """\
Personalities : [raid1]
md0 : active raid1 sda1[0] sdb1[1](F)
      1048576 blocks super 1.2 [2/1] [U_]

unused devices: <none>
"""

MDSTAT_RESYNCING = """\
Personalities : [raid5]
md0 : active raid5 sda1[0] sdb1[1] sdc1[2]
      2097152 blocks super 1.2 level 5, 64k chunk
      [============>........]  resync = 60.0% (629760/1048576) finish=2.0min speed=10000K/sec

unused devices: <none>
"""

MDSTAT_EMPTY = """\
Personalities : [raid1] [raid5] [raid6]
unused devices: <none>
"""


# --- parse_mdstat --------------------------------------------------

def test_parse_mdstat_ok():
    p = mod.parse_mdstat(MDSTAT_OK)
    assert p["personalities"] == ["raid1", "raid5", "raid6", "linear"]
    assert len(p["arrays"]) == 2
    md0 = p["arrays"][0]
    assert md0["name"] == "md0"
    assert md0["level"] == "raid1"
    assert md0["marker"] == "UU"


def test_parse_mdstat_degraded_marker():
    p = mod.parse_mdstat(MDSTAT_DEGRADED)
    md0 = p["arrays"][0]
    assert md0["marker"] == "U_"


def test_parse_mdstat_resync_line():
    p = mod.parse_mdstat(MDSTAT_RESYNCING)
    md0 = p["arrays"][0]
    assert md0["resync"] is not None
    assert "60.0%" in md0["resync"]


def test_parse_mdstat_empty_arrays():
    p = mod.parse_mdstat(MDSTAT_EMPTY)
    assert p["personalities"] == ["raid1", "raid5", "raid6"]
    assert p["arrays"] == []


def test_parse_mdstat_blank():
    assert mod.parse_mdstat("") == {"personalities": [], "arrays": []}


# --- classify ------------------------------------------------------

def _array(name="md0", marker="UU", sysfs=None, resync=None,
             level="raid1"):
    return {"name": name, "state": "active", "level": level,
              "members": [], "marker": marker, "resync": resync,
              "sysfs": sysfs or {}}


def test_classify_no_arrays():
    v = mod.classify([])
    assert v["verdict"] == "no_arrays"


def test_classify_ok():
    v = mod.classify([_array(marker="UU",
                                sysfs={"degraded": 0,
                                        "mismatch_cnt": 0,
                                        "sync_action": "idle"})])
    assert v["verdict"] == "ok"


def test_classify_degraded_via_marker():
    v = mod.classify([_array(marker="U_",
                                sysfs={"degraded": 1})])
    assert v["verdict"] == "degraded"


def test_classify_degraded_via_sysfs():
    v = mod.classify([_array(marker="UU",
                                sysfs={"degraded": 1})])
    assert v["verdict"] == "degraded"


def test_classify_mismatch_present():
    v = mod.classify([_array(marker="UU",
                                sysfs={"degraded": 0,
                                        "mismatch_cnt": 12})])
    assert v["verdict"] == "mismatch_present"
    assert "12" in v["reason"]


def test_classify_resyncing_via_resync_line():
    v = mod.classify([_array(marker="UU", resync="resync = 60%",
                                sysfs={"degraded": 0,
                                        "sync_action": "resync"})])
    assert v["verdict"] == "resyncing"


def test_classify_priority_degraded_over_mismatch():
    v = mod.classify([_array(marker="U_",
                                sysfs={"degraded": 1,
                                        "mismatch_cnt": 5})])
    assert v["verdict"] == "degraded"


def test_classify_priority_mismatch_over_resyncing():
    v = mod.classify([_array(marker="UU",
                                sysfs={"degraded": 0,
                                        "mismatch_cnt": 5,
                                        "sync_action": "resync"})])
    assert v["verdict"] == "mismatch_present"


# --- status integration -------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    (tmp_path / "mdstat").write_text(MDSTAT_OK)
    sys_block = tmp_path / "block"
    sys_block.mkdir()
    for n in ["md0", "md1"]:
        d = sys_block / n / "md"
        d.mkdir(parents=True)
        (d / "array_state").write_text("clean\n")
        (d / "sync_action").write_text("idle\n")
        (d / "sync_speed").write_text("0\n")
        (d / "mismatch_cnt").write_text("0\n")
        (d / "degraded").write_text("0\n")
    monkeypatch.setattr(mod, "_PROC_MDSTAT", str(tmp_path / "mdstat"))
    monkeypatch.setattr(mod, "_SYS_BLOCK", str(sys_block))
    out = mod.status()
    assert out["ok"] is True
    assert out["array_count"] == 2
    assert out["verdict"]["verdict"] == "ok"


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_MDSTAT", str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_SYS_BLOCK", str(tmp_path / "block"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_no_arrays(monkeypatch, tmp_path):
    (tmp_path / "mdstat").write_text(MDSTAT_EMPTY)
    monkeypatch.setattr(mod, "_PROC_MDSTAT", str(tmp_path / "mdstat"))
    monkeypatch.setattr(mod, "_SYS_BLOCK", str(tmp_path / "noblock"))
    out = mod.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "no_arrays"
