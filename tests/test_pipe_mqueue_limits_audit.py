"""Tests for modules/pipe_mqueue_limits_audit.py — R&D #93.1."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import pipe_mqueue_limits_audit as mod


def _mk_proc_sys_fs(tmp_path, *,
                      pipe_max_size="1048576",
                      pipe_user_pages_soft="16384",
                      pipe_user_pages_hard="0",
                      queues_max="256",
                      msg_max="10",
                      msgsize_max="8192",
                      epoll_max_user_watches="1048576"):
    d = tmp_path / "fs"
    d.mkdir(parents=True, exist_ok=True)
    if pipe_max_size is not None:
        (d / "pipe-max-size").write_text(
            pipe_max_size + "\n")
    if pipe_user_pages_soft is not None:
        (d / "pipe-user-pages-soft").write_text(
            pipe_user_pages_soft + "\n")
    if pipe_user_pages_hard is not None:
        (d / "pipe-user-pages-hard").write_text(
            pipe_user_pages_hard + "\n")
    mq = d / "mqueue"
    mq.mkdir(exist_ok=True)
    if queues_max is not None:
        (mq / "queues_max").write_text(queues_max + "\n")
    if msg_max is not None:
        (mq / "msg_max").write_text(msg_max + "\n")
    if msgsize_max is not None:
        (mq / "msgsize_max").write_text(msgsize_max + "\n")
    ep = d / "epoll"
    ep.mkdir(exist_ok=True)
    if epoll_max_user_watches is not None:
        (ep / "max_user_watches").write_text(
            epoll_max_user_watches + "\n")
    return str(d)


def _mk_meminfo(tmp_path, mem_kib=32 * 2**20):
    p = tmp_path / "meminfo"
    p.write_text(f"MemTotal: {mem_kib} kB\n")
    return str(p)


# --- read_limits -----------------------------------------------

def test_read_limits_missing(tmp_path):
    out = mod.read_limits(str(tmp_path / "nope"))
    assert out["pipe_max_size"] is None


def test_read_limits_full(tmp_path):
    r = _mk_proc_sys_fs(tmp_path)
    out = mod.read_limits(r)
    assert out["pipe_max_size"] == 1048576
    assert out["queues_max"] == 256
    assert out["epoll_max_user_watches"] == 1048576


# --- classify --------------------------------------------------

_BIG_RAM = 32 * 2**30
_SMALL_RAM = 4 * 2**30


def _limits(**overrides):
    base = {
        "pipe_max_size": 1048576,
        "pipe_user_pages_soft": 16384,
        "pipe_user_pages_hard": 0,
        "queues_max": 256,
        "msg_max": 10,
        "msgsize_max": 8192,
        "epoll_max_user_watches": 4_000_000,
    }
    base.update(overrides)
    return base


def test_classify_unknown():
    v = mod.classify({"pipe_max_size": None}, None)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(_limits(), _BIG_RAM)
    assert v["verdict"] == "ok"


def test_classify_mqueue_exhausted_zero_queues():
    v = mod.classify(_limits(queues_max=0), _BIG_RAM)
    assert v["verdict"] == "mqueue_exhausted"


def test_classify_mqueue_exhausted_small_msgsize():
    v = mod.classify(_limits(msgsize_max=1024), _BIG_RAM)
    assert v["verdict"] == "mqueue_exhausted"


def test_classify_pipe_pages_low():
    v = mod.classify(_limits(pipe_user_pages_soft=100),
                          _BIG_RAM)
    assert v["verdict"] == "pipe_user_pages_low"


def test_classify_pipe_pages_zero_is_ok():
    # 0 means "unlimited" — not a fault
    v = mod.classify(_limits(pipe_user_pages_soft=0),
                          _BIG_RAM)
    assert v["verdict"] == "ok"


def test_classify_epoll_low_on_big_box():
    v = mod.classify(_limits(
        epoll_max_user_watches=8192), _BIG_RAM)
    assert v["verdict"] == "epoll_watches_low"


def test_classify_epoll_low_on_small_box_is_ok():
    # < threshold but < 16 GiB → not a fault
    v = mod.classify(_limits(
        epoll_max_user_watches=8192), _SMALL_RAM)
    assert v["verdict"] == "ok"


def test_classify_non_default_pipe_max_accent():
    v = mod.classify(_limits(pipe_max_size=4 * 1048576),
                          _BIG_RAM)
    assert v["verdict"] == "non_default_pipe_max"


# Priority : mqueue > pipe_pages > epoll > non_default_pipe
def test_priority_mqueue_over_pipe_pages():
    v = mod.classify(_limits(
        queues_max=0,
        pipe_user_pages_soft=10), _BIG_RAM)
    assert v["verdict"] == "mqueue_exhausted"


def test_priority_pipe_over_epoll():
    v = mod.classify(_limits(
        pipe_user_pages_soft=10,
        epoll_max_user_watches=1000), _BIG_RAM)
    assert v["verdict"] == "pipe_user_pages_low"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope"),
                       str(tmp_path / "no_mem"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    r = _mk_proc_sys_fs(tmp_path)
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, m)
    assert out["verdict"]["verdict"] == "ok"


def test_status_mqueue_exhausted_synthetic(tmp_path):
    r = _mk_proc_sys_fs(tmp_path, queues_max="0")
    m = _mk_meminfo(tmp_path)
    out = mod.status(None, r, m)
    assert out["verdict"]["verdict"] == "mqueue_exhausted"
    assert out["ok"] is False
