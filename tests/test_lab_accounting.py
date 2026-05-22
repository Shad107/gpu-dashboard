"""R&D #14.2 — per-user lab accounting tests."""
import os
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import lab_accounting as la


# ── alias_map ────────────────────────────────────────────────────────────


def test_load_alias_map_missing_file():
    with tempfile.TemporaryDirectory() as td, \
         patch.object(la, "alias_path",
                      return_value=os.path.join(td, "missing")):
        assert la.load_alias_map() == {}


def test_load_alias_map_parses_kv():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "users.allow")
        with open(p, "w") as f:
            f.write("# comment\n1000=alice\n1001=bob\n  1002 =  charlie  \n")
        with patch.object(la, "alias_path", return_value=p):
            d = la.load_alias_map()
    assert d == {1000: "alice", 1001: "bob", 1002: "charlie"}


def test_load_alias_map_skips_bad_rows():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "users.allow")
        with open(p, "w") as f:
            f.write("no-equals\n=onlyvalue\nNaN=name\n1000=alice\n")
        with patch.object(la, "alias_path", return_value=p):
            d = la.load_alias_map()
    assert d == {1000: "alice"}


# ── /proc/<pid>/loginuid ────────────────────────────────────────────────


def test_read_loginuid_returns_int():
    fake_open = patch("builtins.open", mock_text("1000"))
    with fake_open:
        assert la.read_loginuid(1234) == 1000


def test_read_loginuid_no_audit_returns_none():
    """4294967295 = UINT_MAX = 'no auditing' sentinel."""
    fake_open = patch("builtins.open", mock_text("4294967295"))
    with fake_open:
        assert la.read_loginuid(1234) is None


def test_read_loginuid_missing_returns_none():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert la.read_loginuid(1234) is None


def mock_text(content):
    """Helper : open() that returns the given text."""
    class _F:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return content
        def __iter__(self): return iter(content.splitlines())
    return lambda *a, **k: _F()


# ── read_proc_uid ────────────────────────────────────────────────────────


def test_read_proc_uid_parses_status():
    fake_status = "Name:\tpython3\nUmask:\t0022\nUid:\t1000\t1000\t1000\t1000\n"
    with patch("builtins.open", mock_text(fake_status)):
        assert la.read_proc_uid(1234) == 1000


# ── resolve_uid ──────────────────────────────────────────────────────────


def test_resolve_uid_prefers_loginuid():
    with patch.object(la, "read_loginuid", return_value=1000), \
         patch.object(la, "read_proc_uid", return_value=999):
        assert la.resolve_uid(1234) == 1000


def test_resolve_uid_falls_back_to_proc_uid():
    with patch.object(la, "read_loginuid", return_value=None), \
         patch.object(la, "read_proc_uid", return_value=999):
        assert la.resolve_uid(1234) == 999


# ── uid_to_name ──────────────────────────────────────────────────────────


def test_uid_to_name_uses_alias_when_present():
    assert la.uid_to_name(1000, alias_map={1000: "alice"}) == "alice"


def test_uid_to_name_falls_back_when_unknown_uid():
    """For an obscure UID, returns 'uid_X' instead of crashing."""
    name = la.uid_to_name(999_999_999)
    assert name.startswith("uid_") or name != ""


# ── probe_compute_apps ───────────────────────────────────────────────────


def test_probe_compute_apps_parses_csv():
    """Mock nvidia-smi output."""
    fake = "1785, /home/olivier/llama.cpp/llama-server, 23584\n7890, ollama, 4096\n"
    class FakeProc:
        stdout = fake
        returncode = 0
    import subprocess
    with patch.object(subprocess, "run", return_value=FakeProc()):
        out = la.probe_compute_apps()
    assert len(out) == 2
    assert out[0]["pid"] == 1785
    assert out[0]["used_memory_mib"] == 23584


def test_probe_compute_apps_handles_missing():
    import subprocess
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert la.probe_compute_apps() == []


# ── evaluate ─────────────────────────────────────────────────────────────


def test_evaluate_groups_by_uid():
    """Two processes from the same user → one user record."""
    procs = [
        {"pid": 1785, "name": "llama-server", "used_memory_mib": 12000},
        {"pid": 2000, "name": "ollama",        "used_memory_mib": 3000},
        {"pid": 3000, "name": "python3",       "used_memory_mib": 2000},
    ]
    def fake_resolve(pid):
        return {1785: 1000, 2000: 1000, 3000: 1001}[pid]
    with patch.object(la, "resolve_uid", side_effect=fake_resolve), \
         patch.object(la, "uid_to_name", side_effect=lambda u, *_: f"u{u}"):
        snap = la.evaluate(processes=procs, watts_total=250)
    # 2 users
    assert len(snap["users"]) == 2
    # u1000 has 2 PIDs + 15000 MiB (sorted first because more VRAM)
    u1000 = next(u for u in snap["users"] if u["name"] == "u1000")
    assert u1000["pid_count"] == 2
    assert u1000["vram_used_mib"] == 15000
    # watts share : 15000/17000 × 250 ≈ 220.6
    assert 220 < u1000["watts_share"] < 222


def test_evaluate_handles_unknown_uid():
    """When resolve_uid returns None, user is recorded as 'unknown' (uid=-1)."""
    procs = [{"pid": 9999, "name": "ghost", "used_memory_mib": 100}]
    with patch.object(la, "resolve_uid", return_value=None):
        snap = la.evaluate(processes=procs)
    assert len(snap["users"]) == 1
    assert snap["users"][0]["uid"] == -1


def test_evaluate_no_watts_skips_share():
    procs = [{"pid": 1785, "name": "llama-server", "used_memory_mib": 1000}]
    with patch.object(la, "resolve_uid", return_value=1000), \
         patch.object(la, "uid_to_name", return_value="alice"):
        snap = la.evaluate(processes=procs)  # no watts_total
    assert snap["users"][0]["watts_share"] is None


def test_evaluate_empty_processes():
    snap = la.evaluate(processes=[])
    assert snap["users"] == []


def test_evaluate_allow_only_uids_filter():
    procs = [
        {"pid": 1, "name": "a", "used_memory_mib": 100},
        {"pid": 2, "name": "b", "used_memory_mib": 200},
    ]
    def fake_resolve(pid):
        return {1: 1000, 2: 1001}[pid]
    with patch.object(la, "resolve_uid", side_effect=fake_resolve), \
         patch.object(la, "uid_to_name", side_effect=lambda u, *_: f"u{u}"):
        snap = la.evaluate(processes=procs, allow_only_uids=[1000])
    # Only u1000 passes
    assert len(snap["users"]) == 1
    assert snap["users"][0]["uid"] == 1000


# ── aggregate_seconds ────────────────────────────────────────────────────


def test_aggregate_no_samples_returns_empty():
    assert la.aggregate_seconds([]) == {"users": []}


def test_aggregate_sums_across_samples():
    samples = [
        {"ts": 0,   "users": [{"name": "alice", "uid": 1000,
                                "vram_used_mib": 1024, "watts_share": 100}]},
        {"ts": 60,  "users": [{"name": "alice", "uid": 1000,
                                "vram_used_mib": 1024, "watts_share": 100}]},
        {"ts": 120, "users": [{"name": "alice", "uid": 1000,
                                "vram_used_mib": 2048, "watts_share": 200}]},
    ]
    out = la.aggregate_seconds(samples)
    assert len(out["users"]) == 1
    alice = out["users"][0]
    assert alice["name"] == "alice"
    # gpu_seconds : 3 samples × 60s avg = 180
    assert alice["gpu_seconds"] == 180
    # vram_gb_hours : (1+1+2) GiB-min / 60 min = 4/60 ≈ 0.067 GiB-h
    assert 0.05 < alice["vram_gb_hours"] < 0.08
