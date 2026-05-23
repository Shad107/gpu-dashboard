"""Tests for modules/file_locks_audit.py — R&D #42.1."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import file_locks_audit as mod


# --- parse_proc_locks ----------------------------------------------

def test_parse_proc_locks_basic():
    txt = (
        "1: POSIX  ADVISORY  READ  1234 08:02:7512759 128 128\n"
        "2: FLOCK  ADVISORY  WRITE 5678 08:02:42 0 EOF\n"
        "3: OFDLCK ADVISORY  WRITE 9012 fd:00:99 1073741826 1073742335\n"
    )
    out = mod.parse_proc_locks(txt)
    assert len(out) == 3
    assert out[0]["type"] == "POSIX"
    assert out[0]["pid"] == 1234
    assert out[0]["major"] == 8
    assert out[0]["minor"] == 2
    assert out[0]["inode"] == 7512759
    assert out[1]["access"] == "WRITE"
    assert out[2]["major"] == 0xfd
    assert out[2]["minor"] == 0
    assert out[2]["inode"] == 99
    assert out[2]["end"] == "1073742335"


def test_parse_proc_locks_empty():
    assert mod.parse_proc_locks("") == []


def test_parse_proc_locks_garbage_lines_skipped():
    txt = "garbage\n1: POSIX  ADVISORY  READ 1 00:00:1 0 EOF\n"
    out = mod.parse_proc_locks(txt)
    assert len(out) == 1
    assert out[0]["pid"] == 1


def test_parse_proc_locks_negative_pid():
    # /proc/locks can emit pid=-1 for some kinds — should still parse.
    txt = "1: FLOCK  ADVISORY  WRITE -1 00:00:1 0 EOF\n"
    out = mod.parse_proc_locks(txt)
    assert out[0]["pid"] == -1


# --- is_llm_path ---------------------------------------------------

def test_is_llm_path_gguf():
    assert mod.is_llm_path("/home/u/models/llama-3-70b-Q4.gguf") is True


def test_is_llm_path_safetensors():
    assert mod.is_llm_path("/srv/model.safetensors") is True


def test_is_llm_path_ollama():
    assert mod.is_llm_path(
        "/home/u/.ollama/models/blobs/sha256-abc...") is True


def test_is_llm_path_other():
    assert mod.is_llm_path("/var/log/syslog") is False
    assert mod.is_llm_path(None) is False
    assert mod.is_llm_path("") is False


# --- enrich / resolve_inode_to_path -------------------------------

def _mk_pid_with_fd(proc_root: Path, pid: int, comm: str,
                       fd_target_path: Path):
    d = proc_root / str(pid)
    (d / "fd").mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n")
    # Create an fd symlink → real file
    (d / "fd" / "3").symlink_to(fd_target_path)


def test_resolve_inode_to_path_finds_match(tmp_path):
    target = tmp_path / "model.gguf"
    target.write_text("blob")
    proc_root = tmp_path / "proc"
    _mk_pid_with_fd(proc_root, 1234, "ollama", target)
    st = target.stat()
    import os as _os
    found = mod.resolve_inode_to_path(
        1234, _os.major(st.st_dev), _os.minor(st.st_dev),
        st.st_ino, str(proc_root))
    assert found is not None
    assert "model.gguf" in found


def test_resolve_inode_to_path_no_fd_dir(tmp_path):
    found = mod.resolve_inode_to_path(9999, 0, 0, 1,
                                          str(tmp_path / "noproc"))
    assert found is None


def test_resolve_inode_to_path_inode_not_matched(tmp_path):
    target = tmp_path / "f"
    target.write_text("a")
    proc_root = tmp_path / "proc"
    _mk_pid_with_fd(proc_root, 1, "x", target)
    # Wrong inode = won't match
    assert mod.resolve_inode_to_path(1, 0, 0, 999999999,
                                          str(proc_root)) is None


def test_enrich_flags_alive_and_llm(tmp_path):
    target = tmp_path / "weights.safetensors"
    target.write_text("blob")
    proc_root = tmp_path / "proc"
    _mk_pid_with_fd(proc_root, 1234, "vllm", target)
    st = target.stat()
    import os as _os
    locks = [{
        "type": "POSIX", "kind": "ADVISORY", "access": "WRITE",
        "pid": 1234,
        "major": _os.major(st.st_dev),
        "minor": _os.minor(st.st_dev),
        "inode": st.st_ino,
        "start": "0", "end": "EOF",
    }]
    e = mod.enrich(locks, str(proc_root))
    assert e[0]["comm"] == "vllm"
    assert e[0]["pid_alive"] is True
    assert e[0]["is_llm"] is True


def test_enrich_dead_pid(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    locks = [{"type": "POSIX", "kind": "ADVISORY", "access": "WRITE",
                "pid": 99999, "major": 0, "minor": 0, "inode": 1,
                "start": "0", "end": "EOF"}]
    e = mod.enrich(locks, str(proc_root))
    assert e[0]["pid_alive"] is False


# --- detect_contention --------------------------------------------

def _entry(pid, access, inode=42, major=8, minor=2,
             path=None, is_llm=False):
    return {"pid": pid, "access": access, "inode": inode,
              "major": major, "minor": minor, "path": path,
              "is_llm": is_llm, "type": "FLOCK",
              "kind": "ADVISORY"}


def test_detect_contention_two_writers():
    locks = [
        _entry(1, "WRITE", inode=42,
                path="/m/x.gguf", is_llm=True),
        _entry(2, "WRITE", inode=42,
                path="/m/x.gguf", is_llm=True),
    ]
    c = mod.detect_contention(locks)
    assert len(c) == 1
    assert c[0]["is_llm"] is True
    assert "/m/x.gguf" in c[0]["paths"]


def test_detect_contention_same_pid_no_conflict():
    locks = [_entry(1, "WRITE", inode=42),
               _entry(1, "WRITE", inode=42)]
    assert mod.detect_contention(locks) == []


def test_detect_contention_one_writer_one_reader():
    # We only flag WRITE-vs-WRITE — READ + WRITE coexists fine.
    locks = [_entry(1, "WRITE", inode=42),
               _entry(2, "READ", inode=42)]
    assert mod.detect_contention(locks) == []


# --- classify ------------------------------------------------------

def test_classify_ok_when_no_conflicts():
    locks = [_entry(1, "READ", inode=1)]
    v = mod.classify(locks, [], [])
    assert v["verdict"] == "ok"


def test_classify_contention_on_model():
    conflicts = [{"inode_key": [8, 2, 42],
                    "writers": [_entry(1, "WRITE"),
                                  _entry(2, "WRITE")],
                    "all_entries": [],
                    "paths": ["/m/x.gguf"], "is_llm": True}]
    v = mod.classify([], conflicts, [])
    assert v["verdict"] == "contention_on_model"
    assert "x.gguf" in v["reason"]


def test_classify_contention_general():
    conflicts = [{"inode_key": [8, 2, 42],
                    "writers": [_entry(1, "WRITE"),
                                  _entry(2, "WRITE")],
                    "all_entries": [],
                    "paths": ["/var/log/foo"], "is_llm": False}]
    v = mod.classify([], conflicts, [])
    assert v["verdict"] == "contention_general"


def test_classify_orphan_lock():
    orphans = [_entry(99999, "WRITE")]
    v = mod.classify([], [], orphans)
    assert v["verdict"] == "orphan_lock"


def test_classify_contention_wins_over_orphan():
    conflicts = [{"inode_key": [0, 0, 1],
                    "writers": [_entry(1, "WRITE"),
                                  _entry(2, "WRITE")],
                    "all_entries": [], "paths": [], "is_llm": False}]
    v = mod.classify([], conflicts, [_entry(99999, "WRITE")])
    assert v["verdict"] == "contention_general"


# --- status integration -------------------------------------------

def test_status_unknown_when_no_proc_locks(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_PROC_LOCKS",
                        str(tmp_path / "nope"))
    monkeypatch.setattr(mod, "_PROC", str(tmp_path / "noproc"))
    out = mod.status()
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_with_empty_proc_locks(monkeypatch, tmp_path):
    (tmp_path / "locks").write_text("")
    (tmp_path / "proc").mkdir()
    monkeypatch.setattr(mod, "_PROC_LOCKS", str(tmp_path / "locks"))
    monkeypatch.setattr(mod, "_PROC", str(tmp_path / "proc"))
    out = mod.status()
    assert out["ok"] is True
    assert out["lock_count"] == 0
    assert out["verdict"]["verdict"] == "ok"
