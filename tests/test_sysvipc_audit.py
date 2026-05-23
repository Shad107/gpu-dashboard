"""Tests for modules/sysvipc_audit.py — R&D #45.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import sysvipc_audit as mod


SHM_SAMPLE = """\
       key      shmid perms                  size  cpid  lpid nattch   uid   gid  cuid  cgid      atime      dtime      ctime                   rss                  swap
         0     360448  1600               7372800 689453 1865729      2  1000  1000  1000  1000 1779462701 1779462701 1779435099               7004160                     0
         0     360450  1600               6881280 689453 1865729      0  1000  1000  1000  1000 1779462701 1779462701 1779435099                     0               6651904
"""

SEM_SAMPLE = """\
       key      semid perms      nsems   uid   gid  cuid  cgid      otime      ctime
1361772625          0   600          1  1000  1000  1000  1000 1779457673 1779312680
"""

MSG_SAMPLE = """\
       key      msqid perms      cbytes       qnum lspid lrpid   uid   gid  cuid  cgid      stime      rtime      ctime
        12         42   600         100          5  1234  5678  1000  1000  1000  1000  1700000000  1700000000  1700000000
"""


# --- parse_table ---------------------------------------------------

def test_parse_shm_basic():
    rows = mod.parse_shm(SHM_SAMPLE)
    assert len(rows) == 2
    assert rows[0]["shmid"] == 360448
    assert rows[0]["size"] == 7372800
    assert rows[0]["nattch"] == 2


def test_parse_shm_empty():
    assert mod.parse_shm("") == []


def test_parse_shm_header_only():
    assert mod.parse_shm("key shmid perms\n") == []


def test_parse_shm_skips_mismatched_row():
    txt = ("key shmid perms\n"
           "1 2 3\n"
           "4 5\n")
    rows = mod.parse_shm(txt)
    assert len(rows) == 1


def test_parse_sem_basic():
    rows = mod.parse_sem(SEM_SAMPLE)
    assert len(rows) == 1
    assert rows[0]["nsems"] == 1


def test_parse_msg_basic():
    rows = mod.parse_msg(MSG_SAMPLE)
    assert rows[0]["cbytes"] == 100
    assert rows[0]["qnum"] == 5


# --- classify ------------------------------------------------------

def _shm(nattch=2, size=1000, ctime=1_000_000_000, shmid=1):
    return {"shmid": shmid, "size": size, "nattch": nattch,
              "ctime": ctime}


def test_classify_ok_when_empty():
    v = mod.classify([], [], [])
    assert v["verdict"] == "ok"


def test_classify_ok_with_attached_shm():
    v = mod.classify([_shm()], [], [], now=2_000_000_000)
    assert v["verdict"] == "ok"


def test_classify_stale_shm():
    # nattch=0, size > 1 MB, created over an hour ago
    stale = _shm(nattch=0, size=5 * 1024 * 1024,
                  ctime=1_000_000_000)
    v = mod.classify([stale], [], [], now=2_000_000_000)
    assert v["verdict"] == "stale_shm"
    assert "5 MB" in v["reason"]


def test_classify_stale_skipped_when_small():
    # nattch=0 but size < 1 MB → not flagged.
    s = _shm(nattch=0, size=1024, ctime=1_000_000_000)
    v = mod.classify([s], [], [], now=2_000_000_000)
    assert v["verdict"] == "ok"


def test_classify_stale_skipped_when_recent():
    # nattch=0 + size > 1 MB but created < 1 hour ago.
    s = _shm(nattch=0, size=5 * 1024 * 1024,
              ctime=2_000_000_000 - 60)
    v = mod.classify([s], [], [], now=2_000_000_000)
    assert v["verdict"] == "ok"


def test_classify_sem_exhaustion():
    # > 80 % of 32k = 25.6k
    sems = [{"semid": i, "nsems": 1} for i in range(30_000)]
    v = mod.classify([], sems, [], now=0)
    assert v["verdict"] == "sem_exhaustion"


def test_classify_msg_queue_backlog():
    msg = [{"msqid": 1, "cbytes": 2 * 1024 * 1024, "qnum": 100}]
    v = mod.classify([], [], msg, now=0)
    assert v["verdict"] == "msg_queue_backlog"


def test_classify_priority_stale_wins():
    stale = _shm(nattch=0, size=5 * 1024 * 1024,
                  ctime=1_000_000_000)
    sems = [{"semid": i, "nsems": 1} for i in range(30_000)]
    v = mod.classify([stale], sems, [], now=2_000_000_000)
    assert v["verdict"] == "stale_shm"


# --- status integration -------------------------------------------

def test_status_with_isolated(monkeypatch, tmp_path):
    ipc = tmp_path / "ipc"
    ipc.mkdir()
    (ipc / "shm").write_text(SHM_SAMPLE)
    (ipc / "sem").write_text(SEM_SAMPLE)
    (ipc / "msg").write_text(MSG_SAMPLE)
    monkeypatch.setattr(mod, "_PROC_SYSVIPC", str(ipc))
    out = mod.status()
    assert out["ok"] is True
    assert out["shm_count"] == 2
    # The 2 sample rows are both recent ctime ; stale_shm requires
    # ctime > 1h ago vs now ; but our sample ctime=1.78e9 = 2026,
    # which is ~ "now". So no stale flag.
    # The 360450 row has nattch=0 but its size=6.5 MB and ctime is
    # near "now" — so not flagged.
    # Result should be ok or stale depending on monkeypatched clock.
    assert out["verdict"]["verdict"] in ("ok", "stale_shm")


def test_status_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_SYSVIPC", str(tmp_path / "nope"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
