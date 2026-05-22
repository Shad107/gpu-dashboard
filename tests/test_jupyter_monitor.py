"""R&D #8.7 — Jupyter kernel monitor tests."""
import json
import os
import tempfile
import subprocess
from unittest.mock import patch
from gpu_dashboard.modules import jupyter_monitor as jm


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def test_find_kernel_files_empty_dir():
    with tempfile.TemporaryDirectory() as td:
        assert jm.find_kernel_files(td) == []


def test_find_kernel_files_returns_only_kernel_json():
    with tempfile.TemporaryDirectory() as td:
        for name in ["kernel-abc.json", "kernel-xyz.json", "other.json", "kernel-foo.txt"]:
            with open(os.path.join(td, name), "w") as f:
                f.write("{}")
        files = jm.find_kernel_files(td)
        assert len(files) == 2
        assert all(f.endswith(".json") and "kernel-" in os.path.basename(f) for f in files)


def test_parse_kernel_file_modern_jupyter_with_pid():
    """jupyter_client 8.x writes 'pid' into the kernel file."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "kernel-abc123.json")
        with open(path, "w") as f:
            json.dump({
                "ip": "127.0.0.1", "shell_port": 51234,
                "kernel_name": "python3", "pid": 99999,
            }, f)
        info = jm.parse_kernel_file(path)
    assert info is not None
    assert info["kernel_id"] == "abc123"
    assert info["pid"] == 99999
    assert info["shell_port"] == 51234


def test_parse_kernel_file_legacy_no_pid():
    """Older kernel files don't include pid — accept None."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "kernel-old.json")
        with open(path, "w") as f:
            json.dump({"ip": "127.0.0.1", "shell_port": 50000}, f)
        info = jm.parse_kernel_file(path)
    assert info["pid"] is None
    assert info["kernel_name"] == "python3"  # default


def test_parse_kernel_file_corrupt_returns_none():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "kernel-bad.json")
        with open(path, "w") as f:
            f.write("{ not json")
        assert jm.parse_kernel_file(path) is None


def test_get_pmon_by_pid_no_nvidia_smi_returns_empty():
    with patch.object(subprocess, "run", side_effect=FileNotFoundError):
        assert jm.get_pmon_by_pid() == {}


def test_get_pmon_by_pid_parses_pids_and_vram():
    out = "\n".join([
        "# header line",
        "# more header",
        "    0     1785     C   25    0    0    0    0    0  23584      0    llama-server",
        "    0   165012     G    5    0    0    0    0    0     85      0    Xorg",
    ])
    with patch.object(subprocess, "run", return_value=FakeProc(stdout=out)):
        result = jm.get_pmon_by_pid()
    assert 1785 in result
    assert result[1785]["sm_pct"] == 25.0
    assert result[1785]["vram_mib"] == 23584
    assert result[1785]["command"] == "llama-server"
    assert 165012 in result


def test_list_kernels_no_files_returns_empty():
    with tempfile.TemporaryDirectory() as td:
        assert jm.list_kernels(td) == []


def test_list_kernels_attributes_gpu_to_pid():
    """Kernel file has pid=1785, pmon attributes 23 GiB → kernel shows up with that."""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "kernel-nb1.json")
        with open(path, "w") as f:
            json.dump({"pid": 1785, "shell_port": 50000, "kernel_name": "python3"}, f)
        fake_pmon = {
            1785: {"sm_pct": 50.0, "vram_mib": 16384, "command": "ipykernel"},
        }
        with patch.object(jm, "get_pmon_by_pid", return_value=fake_pmon), \
             patch.object(jm, "get_notebook_path_for_pid", return_value="python -m ipykernel"):
            kernels = jm.list_kernels(td)
    assert len(kernels) == 1
    k = kernels[0]
    assert k["pid"] == 1785
    assert k["sm_pct"] == 50.0
    assert k["vram_mib"] == 16384
    assert k["on_gpu"] is True
    assert "ipykernel" in k["cmdline"]


def test_list_kernels_sorted_by_vram_desc():
    with tempfile.TemporaryDirectory() as td:
        # 2 kernels : pid 1=24 GiB, pid 2=2 GiB
        for pid, port in [(1, 5001), (2, 5002)]:
            with open(os.path.join(td, f"kernel-k{pid}.json"), "w") as f:
                json.dump({"pid": pid, "shell_port": port}, f)
        fake_pmon = {
            1: {"sm_pct": 80.0, "vram_mib": 24000, "command": "k1"},
            2: {"sm_pct": 5.0, "vram_mib": 2000, "command": "k2"},
        }
        with patch.object(jm, "get_pmon_by_pid", return_value=fake_pmon), \
             patch.object(jm, "get_notebook_path_for_pid", return_value=""):
            kernels = jm.list_kernels(td)
    assert len(kernels) == 2
    assert kernels[0]["pid"] == 1   # top consumer
    assert kernels[1]["pid"] == 2


def test_kernel_not_on_gpu_marked_false():
    """A kernel that's not using the GPU at all should have on_gpu=False."""
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "kernel-idle.json"), "w") as f:
            json.dump({"pid": 9999, "shell_port": 5000}, f)
        with patch.object(jm, "get_pmon_by_pid", return_value={}), \
             patch.object(jm, "get_notebook_path_for_pid", return_value=""):
            kernels = jm.list_kernels(td)
    assert kernels[0]["on_gpu"] is False
    assert kernels[0]["vram_mib"] == 0
