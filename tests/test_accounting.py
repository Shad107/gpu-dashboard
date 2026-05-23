"""R&D #24.1 — NVML accounting harvester tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import accounting as ac


def _with_log(td):
    return patch.object(ac, "log_path",
                        lambda: os.path.join(td, "acc.json"))


# ── parse_accounted_csv ────────────────────────────────────────────────


def test_parse_basic():
    csv = ("GPU-abc, 1234, 80, 30, 5000, 60000\n"
            "GPU-abc, 5678, 50, 20, 3000, 120000\n")
    out = ac.parse_accounted_csv(csv)
    assert len(out) == 2
    assert out[0]["pid"] == 1234
    assert out[0]["gpu_util_pct"] == 80
    assert out[0]["max_memory_mib"] == 5000
    assert out[1]["wall_time_ms"] == 120000


def test_parse_empty():
    assert ac.parse_accounted_csv("") == []


def test_parse_handles_na():
    csv = "GPU-abc, 1, N/A, N/A, N/A, 1000\n"
    out = ac.parse_accounted_csv(csv)
    assert out[0]["gpu_util_pct"] is None
    assert out[0]["wall_time_ms"] == 1000


def test_parse_skips_invalid_rows():
    csv = "garbage line\nGPU-x, 1, 50, 20, 1000, 5000\n"
    out = ac.parse_accounted_csv(csv)
    assert len(out) == 1


# ── load_log / save_log ────────────────────────────────────────────────


def test_load_empty_when_missing(tmp_path):
    with _with_log(str(tmp_path)):
        assert ac.load_log() == []


def test_save_and_reload(tmp_path):
    with _with_log(str(tmp_path)):
        ac.save_log([{"pid": 1}, {"pid": 2}])
        assert ac.load_log() == [{"pid": 1}, {"pid": 2}]


def test_save_caps_at_max(tmp_path):
    with _with_log(str(tmp_path)):
        big = [{"pid": i} for i in range(ac._MAX_RECORDS + 50)]
        ac.save_log(big)
        loaded = ac.load_log()
    assert len(loaded) == ac._MAX_RECORDS
    # Newest retained
    assert loaded[-1]["pid"] == ac._MAX_RECORDS + 49


def test_load_handles_malformed(tmp_path):
    with _with_log(str(tmp_path)):
        with open(ac.log_path(), "w") as f:
            f.write("{not json")
        assert ac.load_log() == []


# ── merge_into_log ─────────────────────────────────────────────────────


def test_merge_adds_new():
    existing = []
    new = [{"gpu_uuid": "GPU-A", "pid": 1, "gpu_util_pct": 50}]
    out = ac.merge_into_log(existing, new, now_ts=1000.0)
    assert len(out) == 1
    assert out[0]["observed_at"] == 1000
    assert out[0]["first_seen_at"] == 1000


def test_merge_updates_existing_keeps_first_seen():
    existing = [{"gpu_uuid": "GPU-A", "pid": 1, "gpu_util_pct": 30,
                  "observed_at": 500, "first_seen_at": 500}]
    new = [{"gpu_uuid": "GPU-A", "pid": 1, "gpu_util_pct": 80}]
    out = ac.merge_into_log(existing, new, now_ts=2000.0)
    assert out[0]["first_seen_at"] == 500
    assert out[0]["observed_at"] == 2000
    assert out[0]["gpu_util_pct"] == 80


def test_merge_distinct_pids():
    existing = [{"gpu_uuid": "A", "pid": 1, "first_seen_at": 100,
                  "observed_at": 100}]
    new = [{"gpu_uuid": "A", "pid": 2}]
    out = ac.merge_into_log(existing, new, now_ts=200.0)
    assert len(out) == 2


# ── aggregate_by_command ───────────────────────────────────────────────


def test_aggregate_groups_by_comm(tmp_path):
    proc = tmp_path
    p1 = proc / "100"; p1.mkdir(); (p1 / "comm").write_text("ollama")
    p2 = proc / "200"; p2.mkdir(); (p2 / "comm").write_text("ollama")
    p3 = proc / "300"; p3.mkdir(); (p3 / "comm").write_text("blender")
    records = [
        {"pid": 100, "gpu_util_pct": 50, "max_memory_mib": 5000,
         "wall_time_ms": 1000},
        {"pid": 200, "gpu_util_pct": 70, "max_memory_mib": 8000,
         "wall_time_ms": 5000},
        {"pid": 300, "gpu_util_pct": 90, "max_memory_mib": 12000,
         "wall_time_ms": 2000},
    ]
    out = ac.aggregate_by_command(records, proc_root=str(proc))
    ol = next(e for e in out if e["comm"] == "ollama")
    assert ol["count"] == 2
    assert ol["max_memory_mib"] == 8000
    assert ol["total_wall_ms"] == 6000
    assert ol["mean_gpu_util_pct"] == 60.0


def test_aggregate_unknown_comm(tmp_path):
    records = [{"pid": 99999, "gpu_util_pct": 10, "max_memory_mib": 100,
                 "wall_time_ms": 50}]
    out = ac.aggregate_by_command(records, proc_root=str(tmp_path))
    assert out[0]["comm"] == "?"
    assert out[0]["count"] == 1


def test_aggregate_empty():
    assert ac.aggregate_by_command([], proc_root="/proc") == []


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_smi(tmp_path):
    with _with_log(str(tmp_path)):
        with patch.object(ac, "query_accounting_mode", return_value=None):
            s = ac.status()
    assert s["ok"] is False


def test_status_accounting_disabled(tmp_path):
    with _with_log(str(tmp_path)):
        with patch.object(ac, "query_accounting_mode",
                          return_value="Disabled"):
            s = ac.status()
    assert s["ok"] is True
    assert s["accounting_mode"] == "Disabled"
    assert "accounting-mode=1" in s["enable_command"]


def test_status_with_records(tmp_path):
    with _with_log(str(tmp_path)):
        with patch.object(ac, "query_accounting_mode",
                          return_value="Enabled"):
            with patch.object(ac, "query_accounted_apps",
                              return_value=[
                                  {"gpu_uuid": "GPU-X", "pid": 1,
                                   "gpu_util_pct": 70,
                                   "mem_util_pct": 20,
                                   "max_memory_mib": 8000,
                                   "wall_time_ms": 60000},
                              ]):
                s = ac.status()
    assert s["accounting_mode"] == "Enabled"
    assert s["record_count"] == 1
    assert len(s["records"]) == 1
