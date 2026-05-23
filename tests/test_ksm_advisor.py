"""Tests for modules/ksm_advisor.py — R&D #40.2 KSM advisor."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import ksm_advisor


def _mk_ksm(root: Path, **fields):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in fields.items():
        (root / k).write_text(str(v) + "\n")


# --- read_ksm_state -------------------------------------------------

def test_read_ksm_state_missing(tmp_path):
    s = ksm_advisor.read_ksm_state(str(tmp_path / "nope"))
    assert s == {"available": False}


def test_read_ksm_state_basic(tmp_path):
    root = tmp_path / "ksm"
    _mk_ksm(root, run=1, pages_shared=42, pages_sharing=100,
            pages_to_scan=100, sleep_millisecs=20,
            merge_across_nodes=1, general_profit=-1234,
            full_scans=7, pages_scanned=1000, pages_skipped=10,
            pages_unshared=5, pages_volatile=3, max_page_sharing=256,
            stable_node_chains=0, stable_node_dups=0,
            use_zero_pages=1, ksm_zero_pages=8, smart_scan=1)
    s = ksm_advisor.read_ksm_state(str(root))
    assert s["available"] is True
    assert s["run"] == 1
    assert s["pages_shared"] == 42
    assert s["pages_sharing"] == 100
    assert s["merge_across_nodes"] == 1
    assert s["general_profit"] == -1234


def test_read_ksm_state_skips_unparseable(tmp_path):
    root = tmp_path / "ksm"
    _mk_ksm(root, run=1, pages_shared="not-an-int")
    s = ksm_advisor.read_ksm_state(str(root))
    assert s["available"] is True
    assert s["run"] == 1
    assert "pages_shared" not in s


def test_read_ksm_state_advisor_mode(tmp_path):
    root = tmp_path / "ksm"
    _mk_ksm(root, run=1, advisor_mode="scan-time")
    s = ksm_advisor.read_ksm_state(str(root))
    assert s["advisor_mode"] == "scan-time"


# --- parse_ksm_stat -------------------------------------------------

def test_parse_ksm_stat_modern():
    txt = ("ksm_rmap_items 12\n"
           "ksm_zero_pages 0\n"
           "ksm_merging_pages 5\n"
           "ksm_process_profit -34\n"
           "ksm_merge_any: no\n"
           "ksm_mergeable: yes\n")
    s = ksm_advisor.parse_ksm_stat(txt)
    assert s["ksm_rmap_items"] == 12
    assert s["ksm_merging_pages"] == 5
    assert s["ksm_process_profit"] == -34
    assert s["ksm_merge_any"] is False
    assert s["ksm_mergeable"] is True


def test_parse_ksm_stat_empty():
    assert ksm_advisor.parse_ksm_stat("") == {}


def test_parse_ksm_stat_garbage_line_skipped():
    s = ksm_advisor.parse_ksm_stat("ksm_rmap_items 7\nrandom\n")
    assert s == {"ksm_rmap_items": 7}


# --- is_llm_proc -----------------------------------------------------

def test_is_llm_proc_comm():
    assert ksm_advisor.is_llm_proc("ollama", "") is True
    assert ksm_advisor.is_llm_proc("llama-server", "") is True
    assert ksm_advisor.is_llm_proc("comfyui", "") is True


def test_is_llm_proc_python_with_hint():
    assert ksm_advisor.is_llm_proc(
        "python3", "/usr/bin/python3 -m vllm.entrypoints.openai.api_server") is True
    assert ksm_advisor.is_llm_proc("uvicorn", "ollama serve --port 11434") is True


def test_is_llm_proc_negative():
    assert ksm_advisor.is_llm_proc("bash", "") is False
    assert ksm_advisor.is_llm_proc("python3", "manage.py runserver") is False


# --- scan_llm_procs --------------------------------------------------

def _mk_pid(proc_root: Path, pid: int, comm: str, cmdline: str = "",
              ksm_stat: str = "", ksm_merging_legacy: str = None):
    d = proc_root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n")
    (d / "cmdline").write_bytes(cmdline.replace(" ", "\0").encode() + b"\0")
    if ksm_stat is not None:
        (d / "ksm_stat").write_text(ksm_stat)
    if ksm_merging_legacy is not None:
        (d / "ksm_merging_pages").write_text(ksm_merging_legacy)


def test_scan_llm_procs_finds_ollama(tmp_path):
    proc_root = tmp_path / "proc"
    _mk_pid(proc_root, 1234, "ollama",
              ksm_stat="ksm_rmap_items 0\nksm_merging_pages 0\n")
    _mk_pid(proc_root, 1235, "bash")
    found = ksm_advisor.scan_llm_procs(str(proc_root))
    assert len(found) == 1
    assert found[0]["pid"] == 1234
    assert found[0]["comm"] == "ollama"
    assert found[0]["ksm_merging_pages"] == 0


def test_scan_llm_procs_uses_legacy_when_no_ksm_stat(tmp_path):
    proc_root = tmp_path / "proc"
    _mk_pid(proc_root, 1234, "llama-server",
              ksm_stat=None, ksm_merging_legacy="42\n")
    found = ksm_advisor.scan_llm_procs(str(proc_root))
    assert found[0]["ksm_merging_pages"] == 42


def test_scan_llm_procs_empty_proc(tmp_path):
    assert ksm_advisor.scan_llm_procs(str(tmp_path / "noproc")) == []


# --- classify --------------------------------------------------------

def _state(**overrides):
    base = {"available": True, "run": 1, "pages_shared": 0,
            "pages_sharing": 0, "merge_across_nodes": 1}
    base.update(overrides)
    return base


def test_classify_unknown_when_unavailable():
    v = ksm_advisor.classify({"available": False}, [])
    assert v["verdict"] == "unknown"


def test_classify_not_running_when_run_0():
    v = ksm_advisor.classify(_state(run=0), [])
    assert v["verdict"] == "not_running"
    assert v["recommendation"] == ""


def test_classify_not_running_when_run_2():
    # run=2 means "unmerged and stopped"
    v = ksm_advisor.classify(_state(run=2), [])
    assert v["verdict"] == "not_running"


def test_classify_hurting_inference_when_llm_has_merged_pages():
    procs = [{"pid": 1234, "comm": "ollama", "ksm_merging_pages": 17}]
    v = ksm_advisor.classify(_state(run=1, pages_sharing=20), procs)
    assert v["verdict"] == "hurting_inference"
    assert "ollama" in v["reason"]
    assert "echo 2" in v["recommendation"]


def test_classify_running_no_dedup_when_pages_sharing_zero():
    v = ksm_advisor.classify(_state(run=1, pages_sharing=0,
                                       pages_shared=0), [])
    assert v["verdict"] == "running_no_dedup"
    assert "ksmtuned" in v["recommendation"]


def test_classify_justified_on_kvm_host():
    v = ksm_advisor.classify(_state(run=1, pages_sharing=1000,
                                       pages_shared=200,
                                       merge_across_nodes=1),
                              [], host_form_factor="kvm_host")
    assert v["verdict"] == "justified_on_kvm_host"
    # merge_across_nodes=1 + KVM host → recommend NUMA fence
    assert "merge_across_nodes" in v["recommendation"]


def test_classify_justified_no_numa_recipe_when_fence_already_set():
    v = ksm_advisor.classify(_state(run=1, pages_sharing=1000,
                                       pages_shared=200,
                                       merge_across_nodes=0),
                              [], host_form_factor="server")
    assert v["verdict"] == "justified_on_kvm_host"
    assert v["recommendation"] == ""


def test_classify_running_dedup_on_desktop_is_still_net_cost():
    v = ksm_advisor.classify(_state(run=1, pages_sharing=50,
                                       pages_shared=10),
                              [], host_form_factor="desktop")
    assert v["verdict"] == "running_no_dedup"


def test_classify_hurting_wins_over_running_no_dedup():
    procs = [{"pid": 1, "comm": "llama-server", "ksm_merging_pages": 3}]
    v = ksm_advisor.classify(_state(run=1, pages_sharing=0), procs)
    assert v["verdict"] == "hurting_inference"


# --- status (integration with isolated roots) -----------------------

def test_status_unknown_when_no_ksm(monkeypatch, tmp_path):
    monkeypatch.setattr(ksm_advisor, "_SYS_KSM", str(tmp_path / "nope"))
    monkeypatch.setattr(ksm_advisor, "_PROC", str(tmp_path / "proc-empty"))
    out = ksm_advisor.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_runs_with_disabled_ksm(monkeypatch, tmp_path):
    ksm_root = tmp_path / "ksm"
    _mk_ksm(ksm_root, run=0, pages_shared=0, pages_sharing=0)
    monkeypatch.setattr(ksm_advisor, "_SYS_KSM", str(ksm_root))
    monkeypatch.setattr(ksm_advisor, "_PROC", str(tmp_path / "proc-empty"))
    out = ksm_advisor.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "not_running"
    assert out["state"]["run"] == 0


def test_status_picks_up_merging_llm_pid(monkeypatch, tmp_path):
    ksm_root = tmp_path / "ksm"
    _mk_ksm(ksm_root, run=1, pages_shared=10, pages_sharing=50,
            merge_across_nodes=1)
    proc_root = tmp_path / "proc"
    _mk_pid(proc_root, 9999, "ollama",
              ksm_stat="ksm_rmap_items 5\nksm_merging_pages 7\n")
    monkeypatch.setattr(ksm_advisor, "_SYS_KSM", str(ksm_root))
    monkeypatch.setattr(ksm_advisor, "_PROC", str(proc_root))
    out = ksm_advisor.status()
    assert out["ok"] is True
    assert out["verdict"]["verdict"] == "hurting_inference"
    assert out["process_count"] == 1
