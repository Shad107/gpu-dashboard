"""Tests for modules/dri_debugfs_audit.py — R&D #83.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import dri_debugfs_audit as mod


CLIENT_HEADER = (
    "command   tgid drm-minor master   uid    magic\n")


def _client_row(cmd, pid, master="n", uid=1000):
    return (f"     {cmd:18s} {pid:6d}         0      "
            f"{master}    {uid}  0\n")


def _mk_minor(tmp_path, idx, *, name="amdgpu",
                client_lines=None, gem_lines=None):
    d = tmp_path / "dri" / str(idx)
    d.mkdir(parents=True, exist_ok=True)
    (d / "name").write_text(name + "\n")
    body = CLIENT_HEADER + "".join(client_lines or [
        _client_row("gnome-shell", 1234, master="y")])
    (d / "clients").write_text(body)
    (d / "gem_names").write_text(
        "\n".join(gem_lines or [
            "100",
            "101",
            "102",
        ]) + "\n")


def _mk_proc(tmp_path, pids):
    """Make /proc/<pid> dirs that look 'alive'."""
    proc = tmp_path / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    for p in pids:
        (proc / str(p)).mkdir()
    return str(proc)


# --- parse_clients ---------------------------------------------

def test_parse_clients_empty():
    assert mod.parse_clients("") == []


def test_parse_clients_one():
    text = CLIENT_HEADER + _client_row(
        "gnome-shell", 1234, master="y")
    out = mod.parse_clients(text)
    assert len(out) == 1
    assert out[0]["command"] == "gnome-shell"
    assert out[0]["tgid"] == 1234
    assert out[0]["master"] is True


def test_parse_clients_skips_header():
    text = (CLIENT_HEADER
              + _client_row("a", 100)
              + _client_row("b", 200, master="y"))
    out = mod.parse_clients(text)
    assert len(out) == 2


# --- count_gem_handles -----------------------------------------

def test_count_gem_basic():
    text = "100\n101\n102\n"
    assert mod.count_gem_handles(text) == 3


def test_count_gem_skips_headers():
    text = "name\thandle\n-----\n100\n101\n"
    assert mod.count_gem_handles(text) == 2


def test_count_gem_skips_blank():
    text = "100\n\n101\n   \n102\n"
    assert mod.count_gem_handles(text) == 3


# --- read_dri_state --------------------------------------------

def test_read_state_missing(tmp_path):
    state = mod.read_dri_state(str(tmp_path / "nope"))
    assert state["read_state"] in ("unknown", "requires_root")


def test_read_state_one_minor(tmp_path):
    _mk_minor(tmp_path, 0)
    state = mod.read_dri_state(str(tmp_path / "dri"))
    assert state["read_state"] == "ok"
    assert "0" in state["minors"]
    assert state["minors"]["0"]["name"] == "amdgpu"


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify({"minors": {},
                          "read_state": "unknown"})
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    v = mod.classify({"minors": {},
                          "read_state": "requires_root"})
    assert v["verdict"] == "requires_root"


def test_classify_ok(tmp_path):
    proc = _mk_proc(tmp_path, [1234])
    state = {
        "read_state": "ok",
        "minors": {
            "0": {"name": "amdgpu",
                    "clients": [
                        {"command": "gnome-shell",
                            "tgid": 1234, "master": True}],
                    "gem_count": 500},
        }}
    v = mod.classify(state, proc)
    assert v["verdict"] == "ok"


def test_classify_orphan_gem(tmp_path):
    proc = _mk_proc(tmp_path, [1234])
    state = {
        "read_state": "ok",
        "minors": {
            "0": {"name": "amdgpu",
                    "clients": [
                        {"command": "renderer",
                            "tgid": 1234, "master": False}],
                    "gem_count": 5000},  # 5000:1 ratio
        }}
    v = mod.classify(state, proc)
    assert v["verdict"] == "orphaned_gem_handles"


def test_classify_zombie(tmp_path):
    proc = _mk_proc(tmp_path, [])   # no PIDs alive
    state = {
        "read_state": "ok",
        "minors": {
            "0": {"name": "amdgpu",
                    "clients": [
                        {"command": "dead",
                            "tgid": 9999, "master": False}],
                    "gem_count": 100},
        }}
    v = mod.classify(state, proc)
    assert v["verdict"] == "zombie_drm_clients"


def test_classify_multiple_masters(tmp_path):
    proc = _mk_proc(tmp_path, [100, 200])
    state = {
        "read_state": "ok",
        "minors": {
            "0": {"name": "amdgpu",
                    "clients": [
                        {"command": "compA",
                            "tgid": 100, "master": True},
                        {"command": "compB",
                            "tgid": 200, "master": True}],
                    "gem_count": 100},
        }}
    v = mod.classify(state, proc)
    assert v["verdict"] == "multiple_master_clients"


# Priority : orphan > zombie > masters
def test_priority_orphan_over_zombie(tmp_path):
    proc = _mk_proc(tmp_path, [])
    state = {
        "read_state": "ok",
        "minors": {
            "0": {"name": "amdgpu",
                    "clients": [
                        {"command": "dead",
                            "tgid": 9999, "master": False}],
                    "gem_count": 5000},
        }}
    v = mod.classify(state, proc)
    assert v["verdict"] == "orphaned_gem_handles"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "no_dri"),
                       str(tmp_path / "no_proc"))
    assert out["verdict"]["verdict"] in (
        "unknown", "requires_root")


def test_status_ok_synthetic(tmp_path):
    _mk_minor(tmp_path, 0)
    proc = _mk_proc(tmp_path, [1234])
    out = mod.status(None, str(tmp_path / "dri"), proc)
    assert out["read_state"] == "ok"
    assert out["minor_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_zombie_synthetic(tmp_path):
    _mk_minor(tmp_path, 0, client_lines=[
        _client_row("dead", 9999)])
    proc = _mk_proc(tmp_path, [])  # nothing alive
    out = mod.status(None, str(tmp_path / "dri"), proc)
    assert out["verdict"]["verdict"] == "zombie_drm_clients"
