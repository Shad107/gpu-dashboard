"""Tests for modules/_proc_fd_cache.py — Hardening #15."""
from __future__ import annotations

import os
import time

import pytest

from gpu_dashboard.modules import _proc_fd_cache as mod


def _mk_proc(root, pid, fds: dict):
    """Build a fake /proc/<pid>/{fd,fdinfo}/* tree.

    ``fds`` is a dict {fd_str: {"target": <readlink_target_or_None>,
                                   "fdinfo_text": <text_or_None>}}.
    """
    base = root / str(pid)
    fd_dir = base / "fd"
    fdinfo_dir = base / "fdinfo"
    fd_dir.mkdir(parents=True, exist_ok=True)
    fdinfo_dir.mkdir(parents=True, exist_ok=True)
    for fd, spec in fds.items():
        if spec.get("target"):
            try:
                os.symlink(spec["target"], fd_dir / fd)
            except FileExistsError:
                pass
        if spec.get("fdinfo_text") is not None:
            (fdinfo_dir / fd).write_text(spec["fdinfo_text"])


# --- scan_proc_fd basic ----------------------------------------

def test_scan_empty_proc(tmp_path):
    out = mod.scan_proc_fd(str(tmp_path))
    assert out == {}


def test_scan_skips_non_numeric(tmp_path):
    (tmp_path / "self").mkdir()  # /proc/self is a symlink; skip
    (tmp_path / "sys").mkdir()
    _mk_proc(tmp_path, 100, {"0": {"fdinfo_text": "pos: 0"}})
    out = mod.scan_proc_fd(str(tmp_path))
    assert list(out.keys()) == ["100"]


def test_scan_one_pid(tmp_path):
    _mk_proc(tmp_path, 42, {
        "0": {"target": "/dev/null",
              "fdinfo_text": "pos: 0\nflags: 02002\n"},
        "1": {"target": "socket:[12345]",
              "fdinfo_text": "pos: 0\n"},
    })
    out = mod.scan_proc_fd(str(tmp_path))
    assert "42" in out
    entry = out["42"]
    assert entry["pid"] == 42
    assert set(dict(entry["fd_links"]).keys()) == {"0", "1"}
    assert entry["fdinfo"]["0"].startswith("pos: 0")
    assert "socket" in dict(entry["fd_links"])["1"]


def test_scan_handles_unreadable_fdinfo(tmp_path):
    """An fd that has a symlink but missing fdinfo content
    should still produce an entry with text=None."""
    _mk_proc(tmp_path, 50, {"7": {"target": "/dev/null"}})
    out = mod.scan_proc_fd(str(tmp_path))
    entry = out["50"]
    # fd symlink present, fdinfo absent — fd shows up in
    # fd_links but not in fdinfo dict.
    assert dict(entry["fd_links"])["7"] == "/dev/null"
    assert entry["fdinfo"].get("7") is None


def test_scan_handles_missing_dirs(tmp_path):
    """A PID with no fd/fdinfo dirs at all should not crash —
    we just see empty containers."""
    (tmp_path / "999").mkdir()
    out = mod.scan_proc_fd(str(tmp_path))
    entry = out["999"]
    assert entry["fd_links"] == []
    assert entry["fdinfo"] == {}


# --- cache behavior --------------------------------------------

def test_non_default_proc_root_bypasses_cache(tmp_path):
    """A tmp_path proc_root must never hit the module-level
    cache — tests using tmp paths must be isolated."""
    _mk_proc(tmp_path, 1, {"0": {"fdinfo_text": "v1"}})
    out1 = mod.scan_proc_fd(str(tmp_path))
    # Mutate the live filesystem; verify the next scan picks it up.
    (tmp_path / "1" / "fdinfo" / "0").write_text("v2")
    out2 = mod.scan_proc_fd(str(tmp_path))
    assert out1["1"]["fdinfo"]["0"] == "v1"
    assert out2["1"]["fdinfo"]["0"] == "v2"


def test_invalidate_drops_cache(monkeypatch):
    """invalidate() forces a re-scan even within the TTL window."""
    calls = {"n": 0}
    def fake_do_scan(root):
        calls["n"] += 1
        return {"100": {"pid": 100, "fd_links": [],
                          "fdinfo": {}}}
    monkeypatch.setattr(mod, "_do_scan", fake_do_scan)
    mod.invalidate()
    mod.scan_proc_fd("/proc")
    mod.scan_proc_fd("/proc")  # within TTL — cached
    assert calls["n"] == 1
    mod.invalidate()
    mod.scan_proc_fd("/proc")
    assert calls["n"] == 2


def test_ttl_expires(monkeypatch):
    """After ttl_s elapses, the next call re-scans."""
    calls = {"n": 0}
    def fake_do_scan(root):
        calls["n"] += 1
        return {}
    monkeypatch.setattr(mod, "_do_scan", fake_do_scan)
    mod.invalidate()
    mod.scan_proc_fd("/proc", ttl_s=0.05)
    time.sleep(0.07)
    mod.scan_proc_fd("/proc", ttl_s=0.05)
    assert calls["n"] == 2


# --- live smoke ------------------------------------------------

def test_live_scan_proc_returns_dict():
    """End-to-end against the live /proc on the host."""
    mod.invalidate()
    out = mod.scan_proc_fd()
    assert isinstance(out, dict)
    # Self pid must be present; we just ran.
    self_pid = str(os.getpid())
    assert self_pid in out
    entry = out[self_pid]
    assert "fd_links" in entry
    assert "fdinfo" in entry
    # We have at least stdin/stdout/stderr.
    assert len(entry["fd_links"]) >= 3
