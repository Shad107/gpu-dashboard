"""Tests for modules/inotify_audit.py — R&D #41.4."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import inotify_audit as mod


# --- read_limits ---------------------------------------------------

def test_read_limits_missing(tmp_path):
    assert mod.read_limits(str(tmp_path / "nope")) == {}


def test_read_limits_basic(tmp_path):
    root = tmp_path / "in"
    root.mkdir()
    (root / "max_user_watches").write_text("524288\n")
    (root / "max_user_instances").write_text("128\n")
    (root / "max_queued_events").write_text("16384\n")
    l = mod.read_limits(str(root))
    assert l == {"max_user_watches": 524288,
                  "max_user_instances": 128,
                  "max_queued_events": 16384}


# --- count_inotify_fd ----------------------------------------------

def test_count_inotify_fd_inotify():
    txt = ("pos:\t0\nflags:\t02000000\nmnt_id:\t18\n"
           "inotify wd:1 ino:aaa\n"
           "inotify wd:2 ino:bbb\n"
           "inotify wd:3 ino:ccc\n")
    r = mod.count_inotify_fd(txt)
    assert r == {"kind": "inotify", "watches": 3}


def test_count_inotify_fd_fanotify():
    txt = ("pos:\t0\n"
           "fanotify ino:aaa mnt_id:18 sdev:42\n"
           "fanotify ino:bbb mnt_id:18 sdev:42\n")
    r = mod.count_inotify_fd(txt)
    assert r == {"kind": "fanotify", "watches": 2}


def test_count_inotify_fd_neither():
    assert mod.count_inotify_fd("pos:\t0\nflags:\t0\n") == {
        "kind": None, "watches": 0}


def test_count_inotify_fd_empty():
    assert mod.count_inotify_fd("") == {"kind": None, "watches": 0}


# --- scan_processes (with isolated /proc) -------------------------

def _mk_pid(proc_root: Path, pid: int, comm: str, uid: int,
              fdinfos: dict):
    """fdinfos = {<fd-number>: <fdinfo-text>}"""
    d = proc_root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n")
    (d / "status").write_text(f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    fdinfo = d / "fdinfo"
    fdinfo.mkdir(exist_ok=True)
    for fd, text in fdinfos.items():
        (fdinfo / str(fd)).write_text(text)


def test_scan_processes_picks_up_watchers(tmp_path):
    proc_root = tmp_path / "proc"
    _mk_pid(proc_root, 1234, "node", uid=1000, fdinfos={
        16: ("pos:\t0\ninotify wd:1 ino:a\ninotify wd:2 ino:b\n"),
        17: ("pos:\t0\nflags:\t0\n"),
    })
    _mk_pid(proc_root, 1235, "bash", uid=1000, fdinfos={
        0: "pos:\t0\nflags:\t0\n",
    })
    out = mod.scan_processes(str(proc_root))
    assert len(out) == 1
    assert out[0]["pid"] == 1234
    assert out[0]["inotify_instances"] == 1
    assert out[0]["inotify_watches"] == 2
    assert out[0]["uid"] == 1000


def test_scan_processes_handles_fanotify(tmp_path):
    proc_root = tmp_path / "proc"
    _mk_pid(proc_root, 7777, "fanotifier", uid=0, fdinfos={
        5: "pos:\t0\nfanotify ino:a\nfanotify ino:b\n",
    })
    out = mod.scan_processes(str(proc_root))
    assert out[0]["fanotify_instances"] == 1
    assert out[0]["fanotify_watches"] == 2


def test_scan_processes_skips_unreadable_fdinfo(tmp_path):
    proc_root = tmp_path / "proc"
    # PID with comm but no fdinfo dir at all
    d = proc_root / "9999"
    d.mkdir(parents=True)
    (d / "comm").write_text("ghost\n")
    (d / "status").write_text("Uid:\t0\t0\t0\t0\n")
    out = mod.scan_processes(str(proc_root))
    assert out == []


def test_scan_processes_empty_proc(tmp_path):
    assert mod.scan_processes(str(tmp_path / "noproc")) == []


# --- aggregate_by_uid ----------------------------------------------

def test_aggregate_by_uid_sums():
    procs = [
        {"pid": 1, "comm": "node", "uid": 1000,
         "inotify_instances": 2, "inotify_watches": 100,
         "fanotify_instances": 0, "fanotify_watches": 0},
        {"pid": 2, "comm": "dolphin", "uid": 1000,
         "inotify_instances": 3, "inotify_watches": 250,
         "fanotify_instances": 0, "fanotify_watches": 0},
        {"pid": 3, "comm": "systemd", "uid": 0,
         "inotify_instances": 1, "inotify_watches": 5,
         "fanotify_instances": 0, "fanotify_watches": 0},
    ]
    agg = mod.aggregate_by_uid(procs)
    assert agg[1000]["watches"] == 350
    assert agg[1000]["instances"] == 5
    assert agg[1000]["procs"] == 2
    assert agg[0]["watches"] == 5


# --- classify ------------------------------------------------------

def _proc(pid, uid, watches, instances=1, fan_w=0, fan_i=0):
    return {"pid": pid, "comm": "x", "uid": uid,
              "inotify_instances": instances,
              "inotify_watches": watches,
              "fanotify_instances": fan_i,
              "fanotify_watches": fan_w}


def test_classify_unknown_when_no_limits():
    v = mod.classify({}, [])
    assert v["verdict"] == "unknown"


def test_classify_no_watches_in_use():
    v = mod.classify({"max_user_watches": 1000,
                       "max_user_instances": 128}, [])
    assert v["verdict"] == "no_watches_in_use"


def test_classify_ok():
    procs = [_proc(1, 1000, 50, instances=2)]
    v = mod.classify({"max_user_watches": 1000,
                       "max_user_instances": 128}, procs)
    assert v["verdict"] == "ok"


def test_classify_approaching_max_watches():
    procs = [_proc(1, 1000, 900, instances=2)]
    v = mod.classify({"max_user_watches": 1000,
                       "max_user_instances": 128}, procs)
    assert v["verdict"] == "approaching_max_watches"
    assert "1000" in v["reason"]
    assert "fs.inotify.max_user_watches" in v["recommendation"]


def test_classify_instance_per_pid_high():
    procs = [_proc(1, 1000, 50, instances=110)]
    v = mod.classify({"max_user_watches": 100000,
                       "max_user_instances": 128}, procs)
    assert v["verdict"] == "instance_per_pid_high"
    assert "max_user_instances" in v["recommendation"]


def test_classify_watches_wins_over_instances():
    # Both exceeded — watches priority is higher.
    procs = [_proc(1, 1000, 900, instances=110)]
    v = mod.classify({"max_user_watches": 1000,
                       "max_user_instances": 128}, procs)
    assert v["verdict"] == "approaching_max_watches"


# --- status integration -------------------------------------------

def test_status_integration(monkeypatch, tmp_path):
    sys_in = tmp_path / "in"
    sys_in.mkdir()
    (sys_in / "max_user_watches").write_text("1000\n")
    (sys_in / "max_user_instances").write_text("128\n")
    proc_root = tmp_path / "proc"
    _mk_pid(proc_root, 4242, "syncthing", uid=1000, fdinfos={
        3: ("pos:\t0\n" + "\n".join(f"inotify wd:{i} ino:a"
                                       for i in range(900))),
    })
    monkeypatch.setattr(mod, "_PROC_SYS_INOTIFY", str(sys_in))
    monkeypatch.setattr(mod, "_PROC", str(proc_root))
    out = mod.status()
    assert out["ok"] is True
    assert out["limits"]["max_user_watches"] == 1000
    assert out["process_count"] == 1
    assert out["verdict"]["verdict"] == "approaching_max_watches"
    assert out["top_processes"][0]["pid"] == 4242
    assert out["top_processes"][0]["inotify_watches"] == 900


def test_status_unknown_when_no_proc_sys(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_SYS_INOTIFY",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_PROC", str(tmp_path / "noproc"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"
