"""Tests for modules/fdinfo_kinds_audit.py — R&D #67.2."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import fdinfo_kinds_audit as mod


def _mk_pid(root, pid, *, uid=1000, fds=None):
    """fds : dict {fd_name: link_target} ; for fdinfo, also pass
    `fdinfo` dict keyed same way."""
    pdir = root / str(pid)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "status").write_text(
        f"Name:\ttest\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
    fd = pdir / "fd"; fd.mkdir()
    fi = pdir / "fdinfo"; fi.mkdir()
    for name, target in (fds or {}).items():
        link = fd / name
        os.symlink(target, str(link))
    return pdir


def _write_fdinfo(pdir, fd_name, text):
    (pdir / "fdinfo" / fd_name).write_text(text)


# --- kind_of ----------------------------------------------------

def test_kind_of_real():
    assert mod.kind_of("anon_inode:[io_uring]") == "[io_uring]"
    assert mod.kind_of("anon_inode:inotify") == "inotify"
    assert mod.kind_of("/dev/null") is None


# --- epoll_watch_count -----------------------------------------

def test_epoll_watch_count_zero():
    assert mod.epoll_watch_count("pos: 0\nflags: 0\n") == 0


def test_epoll_watch_count_two():
    txt = "pos: 0\ntfd:        8 events:1 data:0\ntfd: 4 events:1 data:0\n"
    assert mod.epoll_watch_count(txt) == 2


# --- scan_pid ---------------------------------------------------

def test_scan_pid_empty(tmp_path):
    _mk_pid(tmp_path, 100, fds={})
    s = mod.scan_pid("100", str(tmp_path))
    assert s["kinds_count"] == {}


def test_scan_pid_mixed(tmp_path):
    pdir = _mk_pid(tmp_path, 100, uid=1000, fds={
        "0": "/dev/null",
        "3": "anon_inode:[eventfd]",
        "4": "anon_inode:[eventfd]",
        "5": "anon_inode:[io_uring]",
        "6": "anon_inode:[eventpoll]",
    })
    _write_fdinfo(pdir, "6",
                       "pos: 0\ntfd: 8 events:1 data:0\n"
                       "tfd: 9 events:1 data:0\n")
    _write_fdinfo(pdir, "5", "pos:0\nSqMask:0xff\n")
    s = mod.scan_pid("100", str(tmp_path))
    assert s["kinds_count"]["[eventfd]"] == 2
    assert s["io_uring_count"] == 1
    assert s["eventfd_count"] == 2
    assert s["eventpoll_max_watch"] == 2
    assert s["uid"] == 1000


# --- aggregate --------------------------------------------------

def test_aggregate_empty():
    a = mod.aggregate({})
    assert a["all_kinds"] == {}
    assert a["iouring_in_nonroot"] == []


def test_aggregate_iouring_nonroot():
    scans = {
        "1": {"kinds_count": {"[io_uring]": 1},
                "fdinfo_readable": 1, "eventpoll_max_watch": 0,
                "io_uring_count": 1, "eventfd_count": 0,
                "uid": 0},
        "100": {"kinds_count": {"[io_uring]": 1},
                  "fdinfo_readable": 1,
                  "eventpoll_max_watch": 0,
                  "io_uring_count": 1, "eventfd_count": 0,
                  "uid": 1000},
    }
    a = mod.aggregate(scans)
    assert a["iouring_in_nonroot"] == ["100"]


def test_aggregate_epoll_offender():
    scans = {
        "1": {"kinds_count": {"[eventpoll]": 1},
                "fdinfo_readable": 1,
                "eventpoll_max_watch": 6000,
                "io_uring_count": 0, "eventfd_count": 0,
                "uid": 1000},
    }
    a = mod.aggregate(scans)
    assert a["epoll_offenders"] == [{"pid": "1", "watches": 6000}]


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify({}, False)
    assert v["verdict"] == "unknown"


def test_classify_iouring_nonroot():
    a = {"all_kinds": {"[io_uring]": 1},
            "pid_count": 5, "pids_with_anon": 1,
            "fdinfo_readable": 5,
            "iouring_in_nonroot": ["100"],
            "eventfd_offenders": [],
            "epoll_offenders": []}
    v = mod.classify(a, True)
    assert v["verdict"] == "io_uring_in_unprivileged_proc"


def test_classify_epoll_runaway():
    a = {"all_kinds": {"[eventpoll]": 1},
            "pid_count": 5, "pids_with_anon": 1,
            "fdinfo_readable": 5,
            "iouring_in_nonroot": [],
            "eventfd_offenders": [],
            "epoll_offenders": [{"pid": "1", "watches": 5000}]}
    v = mod.classify(a, True)
    assert v["verdict"] == "epoll_watch_runaway"


def test_classify_eventfd_leak():
    a = {"all_kinds": {"[eventfd]": 150},
            "pid_count": 5, "pids_with_anon": 1,
            "fdinfo_readable": 5,
            "iouring_in_nonroot": [],
            "eventfd_offenders": [{"pid": "1", "count": 150}],
            "epoll_offenders": []}
    v = mod.classify(a, True)
    assert v["verdict"] == "eventfd_leak"


def test_classify_requires_root():
    a = {"all_kinds": {},
            "pid_count": 100, "pids_with_anon": 0,
            "fdinfo_readable": 2,
            "iouring_in_nonroot": [],
            "eventfd_offenders": [],
            "epoll_offenders": []}
    v = mod.classify(a, True)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    a = {"all_kinds": {"[eventfd]": 50},
            "pid_count": 50, "pids_with_anon": 5,
            "fdinfo_readable": 50,
            "iouring_in_nonroot": [],
            "eventfd_offenders": [],
            "epoll_offenders": []}
    v = mod.classify(a, True)
    assert v["verdict"] == "ok"


# Priority : iouring > epoll > eventfd
def test_priority_iouring_over_epoll():
    a = {"all_kinds": {"[io_uring]": 1, "[eventpoll]": 1},
            "pid_count": 5, "pids_with_anon": 2,
            "fdinfo_readable": 5,
            "iouring_in_nonroot": ["100"],
            "eventfd_offenders": [{"pid": "100", "count": 200}],
            "epoll_offenders": [{"pid": "100", "watches": 5000}]}
    v = mod.classify(a, True)
    assert v["verdict"] == "io_uring_in_unprivileged_proc"


# --- status integration -----------------------------------------

def test_status_synthetic_ok(tmp_path):
    pdir = _mk_pid(tmp_path, 100, uid=1000, fds={
        "0": "/dev/null",
        "3": "anon_inode:[eventfd]",
    })
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["all_kinds"].get("[eventfd]") == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_synthetic_iouring(tmp_path):
    pdir = _mk_pid(tmp_path, 100, uid=1000, fds={
        "3": "anon_inode:[io_uring]",
    })
    _write_fdinfo(pdir, "3", "pos:0\nSqMask:0\n")
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "io_uring_in_unprivileged_proc"


def test_status_live_smoke():
    out = mod.status(None)
    assert out["ok"] is True
    assert out["pid_count"] > 0
    assert out["verdict"]["verdict"] in (
        "ok", "io_uring_in_unprivileged_proc",
        "epoll_watch_runaway", "eventfd_leak",
        "requires_root", "unknown")
