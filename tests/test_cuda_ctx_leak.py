"""R&D #26.2 — zombie CUDA-FD detector tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import cuda_ctx_leak as cl


# ── scan_proc_for_cuda_fds ─────────────────────────────────────────────


def _make_pid(root, pid, fd_targets: dict):
    """Build /proc/<pid>/fd/<n> symlinks pointing at targets."""
    pdir = root / str(pid); pdir.mkdir()
    fd_dir = pdir / "fd"; fd_dir.mkdir()
    for fd, target in fd_targets.items():
        os.symlink(target, fd_dir / str(fd))


def test_scan_finds_dev_nvidia(tmp_path):
    _make_pid(tmp_path, 100, {0: "/dev/nvidia0", 1: "/dev/null"})
    out = cl.scan_proc_for_cuda_fds(proc_root=str(tmp_path))
    assert 100 in out
    assert "/dev/nvidia0" in out[100]


def test_scan_skips_ctl_only(tmp_path):
    """Process holding only /dev/nvidiactl is NOT pinning VRAM."""
    _make_pid(tmp_path, 200, {0: "/dev/nvidiactl"})
    out = cl.scan_proc_for_cuda_fds(proc_root=str(tmp_path))
    assert 200 not in out


def test_scan_dedupes_same_device(tmp_path):
    _make_pid(tmp_path, 300, {0: "/dev/nvidia0", 1: "/dev/nvidia0",
                                2: "/dev/nvidia1"})
    out = cl.scan_proc_for_cuda_fds(proc_root=str(tmp_path))
    assert sorted(out[300]) == ["/dev/nvidia0", "/dev/nvidia1"]


def test_scan_skips_non_numeric(tmp_path):
    """Non-PID dirs (kthreads, etc.) are skipped."""
    (tmp_path / "kthreadd").mkdir()
    _make_pid(tmp_path, 400, {0: "/dev/nvidia0"})
    out = cl.scan_proc_for_cuda_fds(proc_root=str(tmp_path))
    assert 400 in out
    assert len(out) == 1


def test_scan_no_proc_dir(tmp_path):
    assert cl.scan_proc_for_cuda_fds(proc_root=str(tmp_path / "nope")) == {}


def test_scan_handles_permission_denied(tmp_path, monkeypatch):
    """Other-user PIDs are silently skipped."""
    _make_pid(tmp_path, 500, {0: "/dev/nvidia0"})
    real_listdir = os.listdir
    def fake_listdir(p):
        if p.endswith("/500/fd"):
            raise PermissionError("not yours")
        return real_listdir(p)
    monkeypatch.setattr(os, "listdir", fake_listdir)
    out = cl.scan_proc_for_cuda_fds(proc_root=str(tmp_path))
    assert 500 not in out


# ── find_leaks ─────────────────────────────────────────────────────────


def test_find_leaks_none(tmp_path):
    _make_pid(tmp_path, 100, {0: "/dev/nvidia0"})
    (tmp_path / "100" / "comm").write_text("ollama\n")
    (tmp_path / "100" / "cmdline").write_bytes(b"ollama\x00serve\x00")
    fd_holders = {100: ["/dev/nvidia0"]}
    compute_pids = {100}
    out = cl.find_leaks(fd_holders, compute_pids, proc_root=str(tmp_path))
    assert out == []


def test_find_leaks_zombie(tmp_path):
    _make_pid(tmp_path, 200, {0: "/dev/nvidia0"})
    (tmp_path / "200" / "comm").write_text("python\n")
    (tmp_path / "200" / "cmdline").write_bytes(b"python\x00notebook.py\x00")
    fd_holders = {200: ["/dev/nvidia0"]}
    compute_pids: set = set()  # process not in compute-app list
    out = cl.find_leaks(fd_holders, compute_pids, proc_root=str(tmp_path))
    assert len(out) == 1
    assert out[0]["pid"] == 200
    assert out[0]["comm"] == "python"
    assert out[0]["kill_cmd"] == "kill -TERM 200"


def test_find_leaks_skips_legit_holders(tmp_path):
    """Mix: 100 is in compute_apps, 200 isn't → only 200 in leaks."""
    for pid, comm in ((100, "ollama"), (200, "stale")):
        _make_pid(tmp_path, pid, {0: f"/dev/nvidia0"})
        (tmp_path / str(pid) / "comm").write_text(comm + "\n")
        (tmp_path / str(pid) / "cmdline").write_bytes(b"")
    out = cl.find_leaks({100: ["/dev/nvidia0"], 200: ["/dev/nvidia0"]},
                          {100}, proc_root=str(tmp_path))
    assert len(out) == 1
    assert out[0]["pid"] == 200


# ── classify ───────────────────────────────────────────────────────────


def test_classify_no_fds():
    v = cl.classify({}, set(), [])
    assert v["verdict"] == "no_fds"


def test_classify_ok_all_in_compute():
    v = cl.classify({100: ["/dev/nvidia0"]}, {100}, [])
    assert v["verdict"] == "ok"


def test_classify_leaks_detected():
    v = cl.classify({100: ["/dev/nvidia0"]}, set(),
                     leaks=[{"pid": 100, "devices": ["/dev/nvidia0"]}])
    assert v["verdict"] == "leaks_detected"
    assert "VRAM stays pinned" in v["reason"]


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_holders():
    with patch.object(cl, "scan_proc_for_cuda_fds", return_value={}):
        with patch.object(cl, "list_compute_pids", return_value=set()):
            s = cl.status()
    assert s["verdict"]["verdict"] == "no_fds"
    assert s["leak_count"] == 0


def test_status_clean_match(tmp_path):
    """Holder PID == compute PID → ok."""
    with patch.object(cl, "scan_proc_for_cuda_fds",
                      return_value={100: ["/dev/nvidia0"]}):
        with patch.object(cl, "list_compute_pids", return_value={100}):
            s = cl.status()
    assert s["verdict"]["verdict"] == "ok"


def test_status_flag_leak():
    with patch.object(cl, "scan_proc_for_cuda_fds",
                      return_value={200: ["/dev/nvidia0"]}):
        with patch.object(cl, "list_compute_pids", return_value=set()):
            with patch.object(cl, "read_comm", return_value="stale"):
                with patch.object(cl, "read_cmdline_short",
                                  return_value="python notebook.py"):
                    s = cl.status()
    assert s["leak_count"] == 1
    assert s["verdict"]["verdict"] == "leaks_detected"
