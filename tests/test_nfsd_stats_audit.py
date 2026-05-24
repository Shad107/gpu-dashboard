"""Tests for modules/nfsd_stats_audit.py — R&D #83.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import nfsd_stats_audit as mod


POOL_HEADER = ("# pool packets-arrived sockets-enqueued "
                  "threads-woken threads-timedout\n")


def _mk_nfsd(tmp_path, *, threads=8, pool_lines=None,
              drc_lines=None):
    d = tmp_path / "nfsd"
    d.mkdir(parents=True, exist_ok=True)
    (d / "threads").write_text(f"{threads}\n")
    body = POOL_HEADER + "\n".join(pool_lines or [
        "0 1234 50 50 0"]) + "\n"
    (d / "pool_stats").write_text(body)
    drc = drc_lines or [
        "max entries:           65536",
        "num entries:           1024",
        "hash buckets:          256",
        "mem usage:             1048576 bytes",
        "cache hits:            900",
        "cache misses:          100",
        "not cached:            5",
        "payload misses:        0",
        "longest chain len:     2",
        "chains above avg len:  0",
    ]
    (d / "reply_cache_stats").write_text(
        "\n".join(drc) + "\n")
    return str(d)


def _mk_cpuinfo(tmp_path, cpus=12):
    p = tmp_path / "cpuinfo"
    p.write_text("\n".join(
        f"processor\t: {i}" for i in range(cpus)) + "\n")
    return str(p)


# --- is_nfsd_present -------------------------------------------

def test_is_nfsd_present_no(tmp_path):
    assert mod.is_nfsd_present(
        str(tmp_path / "nope")) is False


def test_is_nfsd_present_yes(tmp_path):
    _mk_nfsd(tmp_path)
    assert mod.is_nfsd_present(str(tmp_path / "nfsd")) is True


# --- parse_pool_stats ------------------------------------------

def test_parse_pool_empty():
    assert mod.parse_pool_stats("") == []


def test_parse_pool_with_header():
    text = (POOL_HEADER
              + "0 100 50 40 1\n"
              + "1 200 60 55 0\n")
    out = mod.parse_pool_stats(text)
    assert len(out) == 2
    assert out[1]["pool"] == 1
    assert out[1]["threads_woken"] == 55


def test_parse_pool_skips_garbage():
    text = POOL_HEADER + "garbage line\n0 100 50 40 1\n"
    out = mod.parse_pool_stats(text)
    assert len(out) == 1


# --- parse_reply_cache -----------------------------------------

def test_parse_drc():
    text = ("max entries:    65536\n"
              "cache hits:     900\n"
              "cache misses:   100\n"
              "not cached:     5\n"
              "mem usage:      1048576 bytes\n")
    out = mod.parse_reply_cache(text)
    assert out["max_entries"] == 65536
    assert out["cache_hits"] == 900
    assert out["not_cached"] == 5
    assert out["mem_usage"] == 1048576


# --- count_cpus ------------------------------------------------

def test_count_cpus():
    text = ("processor\t: 0\nfoo\nprocessor\t: 1\n"
              "processor\t: 2\n")
    assert mod.count_cpus(text) == 3


# --- classify --------------------------------------------------

def test_classify_na():
    v = mod.classify({"nfsd_present": False})
    assert v["verdict"] == "n/a"


def _ok_state():
    return {
        "nfsd_present": True,
        "threads": 12,
        "pools": [
            {"pool": 0, "packets": 1000,
                "sockets_enqueued": 50, "threads_woken": 30,
                "threads_timedout": 0}],
        "reply_cache": {"cache_hits": 900,
                          "cache_misses": 100,
                          "not_cached": 5},
        "cpu_count": 12,
    }


def test_classify_ok():
    v = mod.classify(_ok_state())
    assert v["verdict"] == "ok"


def test_classify_drc_overflow():
    s = _ok_state()
    s["reply_cache"] = {"cache_hits": 100,
                          "cache_misses": 100,
                          "not_cached": 50}  # 20% not_cached
    v = mod.classify(s)
    assert v["verdict"] == "reply_cache_overflow"


def test_classify_drc_below_threshold_ok():
    s = _ok_state()
    s["reply_cache"] = {"cache_hits": 1000,
                          "cache_misses": 100,
                          "not_cached": 50}  # 4% not_cached
    v = mod.classify(s)
    assert v["verdict"] == "ok"


def test_classify_threads_starved():
    s = _ok_state()
    s["pools"] = [{"pool": 0, "packets": 5000,
                     "sockets_enqueued": 5000,
                     "threads_woken": 4900,  # 98%
                     "threads_timedout": 0}]
    v = mod.classify(s)
    assert v["verdict"] == "threads_starved"


def test_classify_starvation_below_floor_ok():
    # high ratio but absolute count below threshold floor
    s = _ok_state()
    s["pools"] = [{"pool": 0, "packets": 100,
                     "sockets_enqueued": 100,
                     "threads_woken": 99,
                     "threads_timedout": 0}]
    v = mod.classify(s)
    assert v["verdict"] == "ok"


def test_classify_thread_count_low():
    s = _ok_state()
    s["threads"] = 4   # below cpu_count/2 = 6
    s["cpu_count"] = 12
    v = mod.classify(s)
    assert v["verdict"] == "thread_count_low"


def test_classify_thread_count_low_cpu_below_floor_ok():
    # cpu_count < 8 → don't fire even if threads is small
    s = _ok_state()
    s["threads"] = 2
    s["cpu_count"] = 4
    v = mod.classify(s)
    assert v["verdict"] == "ok"


# Priority : overflow > starvation > low_thread
def test_priority_overflow_over_starvation():
    s = _ok_state()
    s["reply_cache"] = {"cache_hits": 50,
                          "cache_misses": 50, "not_cached": 50}
    s["pools"] = [{"pool": 0, "packets": 5000,
                     "sockets_enqueued": 5000,
                     "threads_woken": 4900,
                     "threads_timedout": 0}]
    v = mod.classify(s)
    assert v["verdict"] == "reply_cache_overflow"


# --- status integration ----------------------------------------

def test_status_na(tmp_path):
    out = mod.status(None, str(tmp_path / "no_nfsd"),
                       str(tmp_path / "no_cpu"))
    assert out["verdict"]["verdict"] == "n/a"


def test_status_ok_synthetic(tmp_path):
    nfsd = _mk_nfsd(tmp_path, threads=12)
    cpuinfo = _mk_cpuinfo(tmp_path, cpus=12)
    out = mod.status(None, nfsd, cpuinfo)
    assert out["nfsd_present"] is True
    assert out["threads"] == 12
    assert out["cpu_count"] == 12
    assert out["verdict"]["verdict"] == "ok"


def test_status_thread_count_low_synthetic(tmp_path):
    nfsd = _mk_nfsd(tmp_path, threads=4)
    cpuinfo = _mk_cpuinfo(tmp_path, cpus=12)
    out = mod.status(None, nfsd, cpuinfo)
    assert out["verdict"]["verdict"] == "thread_count_low"
