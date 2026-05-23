"""Tests for modules/scsi_transport_audit.py — R&D #58.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import scsi_transport_audit as mod


def _mk_disk(root, id_, *, cache_type="write back", fua=0,
               protection_type=0, manage_start_stop=1,
               allow_restart=1):
    d = root / id_
    d.mkdir(parents=True, exist_ok=True)
    (d / "cache_type").write_text(cache_type + "\n")
    (d / "FUA").write_text(f"{fua}\n")
    (d / "protection_type").write_text(f"{protection_type}\n")
    (d / "manage_start_stop").write_text(f"{manage_start_stop}\n")
    (d / "allow_restart").write_text(f"{allow_restart}\n")
    return d


def _mk_device(root, id_, *, queue_depth=128, state="running",
                 type_=0, timeout=30, eh_timeout=10):
    d = root / id_ / "device"
    d.mkdir(parents=True, exist_ok=True)
    (d / "queue_depth").write_text(f"{queue_depth}\n")
    (d / "state").write_text(state + "\n")
    (d / "type").write_text(f"{type_}\n")
    (d / "timeout").write_text(f"{timeout}\n")
    (d / "eh_timeout").write_text(f"{eh_timeout}\n")
    return d


def _mk_host(root, idx, *, use_blk_mq=1, can_queue=32,
               cmd_per_lun=32):
    d = root / f"host{idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "use_blk_mq").write_text(f"{use_blk_mq}\n")
    (d / "can_queue").write_text(f"{can_queue}\n")
    (d / "cmd_per_lun").write_text(f"{cmd_per_lun}\n")


# --- list helpers -----------------------------------------------

def test_list_scsi_disks(tmp_path):
    _mk_disk(tmp_path, "0:0:0:0", cache_type="write back")
    _mk_disk(tmp_path, "0:0:1:0", cache_type="write through")
    out = mod.list_scsi_disks(str(tmp_path))
    assert len(out) == 2
    assert out[0]["cache_type"] == "write back"
    assert out[1]["cache_type"] == "write through"


def test_list_scsi_devices(tmp_path):
    _mk_device(tmp_path, "0:0:0:0", queue_depth=128)
    _mk_device(tmp_path, "2:0:0:0", queue_depth=1, type_=5)
    out = mod.list_scsi_devices(str(tmp_path))
    assert len(out) == 2
    cdrom = next(d for d in out if d["id"] == "2:0:0:0")
    assert cdrom["type"] == 5


def test_list_scsi_hosts(tmp_path):
    _mk_host(tmp_path, 0)
    _mk_host(tmp_path, 1, can_queue=256)
    (tmp_path / "platform").mkdir()
    out = mod.list_scsi_hosts(str(tmp_path))
    assert len(out) == 2


# --- classify ---------------------------------------------------

def _disk(id_="0:0:0:0", cache_type="write back"):
    return {"id": id_, "cache_type": cache_type, "FUA": 0,
              "protection_type": 0, "manage_start_stop": 1,
              "allow_restart": 1}


def _dev(id_="0:0:0:0", queue_depth=128, state="running",
          type_=0):
    return {"id": id_, "queue_depth": queue_depth,
              "state": state, "type": type_,
              "timeout": 30, "eh_timeout": 10}


def test_classify_unknown():
    v = mod.classify([], [], [])
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify([_disk()], [_dev()],
                       [{"id": "host0", "use_blk_mq": 1,
                          "can_queue": 32, "cmd_per_lun": 32}])
    assert v["verdict"] == "ok"


def test_classify_write_cache_disabled():
    v = mod.classify(
        [_disk(cache_type="write through")], [_dev()], [])
    assert v["verdict"] == "write_cache_disabled"


def test_classify_qd_starved_disk():
    v = mod.classify([_disk()],
                       [_dev(queue_depth=1, type_=0)], [])
    assert v["verdict"] == "queue_depth_starved"


def test_classify_qd_one_cdrom_ok():
    # cdrom (type 5) with queue_depth=1 is normal
    v = mod.classify([_disk()],
                       [_dev(queue_depth=1, type_=5)], [])
    assert v["verdict"] == "ok"


def test_classify_offline():
    v = mod.classify([_disk()],
                       [_dev(state="offline")], [])
    assert v["verdict"] == "device_offline"


def test_classify_priority_cache_wins():
    v = mod.classify(
        [_disk(cache_type="write through")],
        [_dev(queue_depth=1, state="offline")], [])
    assert v["verdict"] == "write_cache_disabled"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope1"),
                       str(tmp_path / "nope2"),
                       str(tmp_path / "nope3"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    sd = tmp_path / "scsi_disk"
    _mk_disk(sd, "0:0:0:0")
    sv = tmp_path / "scsi_device"
    _mk_device(sv, "0:0:0:0", queue_depth=128, type_=0)
    _mk_device(sv, "2:0:0:0", queue_depth=1, type_=5)
    sh = tmp_path / "scsi_host"
    _mk_host(sh, 0)
    out = mod.status(None, str(sd), str(sv), str(sh))
    assert out["ok"] is True
    assert out["disk_count"] == 1
    assert out["device_count"] == 2
    assert out["verdict"]["verdict"] == "ok"
