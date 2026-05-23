"""R&D #22.5 — CUDA toolkit inventory + collision detector tests."""
import json
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import cuda_inventory as ci


# ── _version_from_toolkit_root ─────────────────────────────────────────


def test_version_from_version_json(tmp_path):
    (tmp_path / "version.json").write_text(json.dumps({
        "cuda": {"version": "12.4.1", "name": "CUDA SDK"}
    }))
    assert ci._version_from_toolkit_root(str(tmp_path)) == "12.4.1"


def test_version_from_version_txt(tmp_path):
    (tmp_path / "version.txt").write_text("CUDA Version 11.8.0\n")
    assert ci._version_from_toolkit_root(str(tmp_path)) == "11.8.0"


def test_version_missing(tmp_path):
    assert ci._version_from_toolkit_root(str(tmp_path)) is None


def test_version_malformed_json(tmp_path):
    (tmp_path / "version.json").write_text("{bad")
    # Falls through to version.txt — also missing → None
    assert ci._version_from_toolkit_root(str(tmp_path)) is None


# ── _version_from_name ─────────────────────────────────────────────────


def test_version_from_name_dashed():
    assert ci._version_from_name("cuda-12.4") == "12.4"


def test_version_from_name_without_dash():
    assert ci._version_from_name("cuda12") == "12"


def test_version_from_name_garbage():
    assert ci._version_from_name("cudaX") is None


# ── find_cuda_toolkits ─────────────────────────────────────────────────


def test_find_toolkits_in_root(tmp_path):
    (tmp_path / "cuda-12.4").mkdir()
    (tmp_path / "cuda-12.4" / "version.json").write_text(
        json.dumps({"cuda": {"version": "12.4.1"}}))
    (tmp_path / "cuda-11.8").mkdir()
    (tmp_path / "cuda-11.8" / "version.txt").write_text("CUDA Version 11.8.0\n")
    out = ci.find_cuda_toolkits(roots=[str(tmp_path)])
    assert len(out) == 2
    versions = sorted([t["version"] for t in out])
    assert versions == ["11.8.0", "12.4.1"]


def test_find_toolkits_empty_root(tmp_path):
    out = ci.find_cuda_toolkits(roots=[str(tmp_path)])
    assert out == []


def test_find_toolkits_ignores_non_cuda_dirs(tmp_path):
    (tmp_path / "something_else").mkdir()
    out = ci.find_cuda_toolkits(roots=[str(tmp_path)])
    assert out == []


def test_find_toolkits_missing_root_returns_empty():
    out = ci.find_cuda_toolkits(roots=["/nonexistent/path"])
    assert out == []


# ── _find_cudart_in_dir ────────────────────────────────────────────────


def test_find_cudart_versioned(tmp_path):
    (tmp_path / "libcudart.so.12.4.1").touch()
    (tmp_path / "libcudart.so.11.8").touch()
    versions = ci._find_cudart_in_dir(str(tmp_path))
    assert "11.8" in versions
    assert "12.4.1" in versions


def test_find_cudart_skips_major_only(tmp_path):
    """libcudart.so.12 alone (no minor) is not version-specific enough."""
    (tmp_path / "libcudart.so.12").touch()
    assert ci._find_cudart_in_dir(str(tmp_path)) == []


def test_find_cudart_empty_dir(tmp_path):
    assert ci._find_cudart_in_dir(str(tmp_path)) == []


# ── find_conda_cuda ────────────────────────────────────────────────────


def test_find_conda_cuda(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    env_root = tmp_path / "anaconda3" / "envs" / "myenv" / "lib"
    env_root.mkdir(parents=True)
    (env_root / "libcudart.so.12.4.1").touch()
    out = ci.find_conda_cuda()
    assert len(out) == 1
    assert out[0]["source"] == "conda-env:myenv"
    assert out[0]["version"] == "12.4.1"


def test_find_conda_cuda_no_envs(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ci.find_conda_cuda() == []


# ── parse_ld_library_path ──────────────────────────────────────────────


def test_parse_ld_empty(monkeypatch):
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    assert ci.parse_ld_library_path() == []


def test_parse_ld_cuda_dir(tmp_path, monkeypatch):
    cuda_lib = tmp_path / "cuda-12.4" / "lib64"
    cuda_lib.mkdir(parents=True)
    (cuda_lib / "libcudart.so.12.4.1").touch()
    monkeypatch.setenv("LD_LIBRARY_PATH", str(cuda_lib))
    out = ci.parse_ld_library_path()
    assert len(out) == 1
    assert "12.4.1" in out[0]["versions"]


def test_parse_ld_skips_missing_dirs(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/nope:/also-nope")
    assert ci.parse_ld_library_path() == []


# ── detect_collisions ──────────────────────────────────────────────────


def test_collisions_clean():
    out = ci.detect_collisions([
        {"path": "/usr/local/cuda-12.4", "version": "12.4.1"},
    ])
    assert out == []


def test_collisions_same_major():
    """Two 12.x installs → multiple_same_major flag."""
    out = ci.detect_collisions([
        {"path": "/usr/local/cuda-12.4", "version": "12.4.1"},
        {"path": "/home/u/.conda/envs/x", "version": "12.1.0"},
    ])
    assert any(c["kind"] == "multiple_same_major" for c in out)


def test_collisions_multiple_majors():
    """11.x AND 12.x → multiple_majors_present."""
    out = ci.detect_collisions([
        {"path": "/usr/local/cuda-11.8", "version": "11.8.0"},
        {"path": "/usr/local/cuda-12.4", "version": "12.4.1"},
    ])
    assert any(c["kind"] == "multiple_majors_present" for c in out)


def test_collisions_ignores_unversioned():
    out = ci.detect_collisions([
        {"path": "/a", "version": None},
        {"path": "/b", "version": None},
    ])
    assert out == []


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_installs():
    with patch.object(ci, "find_cuda_toolkits", return_value=[]):
        with patch.object(ci, "find_conda_cuda", return_value=[]):
            with patch.object(ci, "parse_ld_library_path", return_value=[]):
                s = ci.status()
    assert s["install_count"] == 0
    assert s["verdict"]["verdict"] == "none"


def test_status_clean():
    with patch.object(ci, "find_cuda_toolkits", return_value=[
        {"path": "/usr/local/cuda-12.4", "version": "12.4.1",
         "source": "toolkit"},
    ]):
        with patch.object(ci, "find_conda_cuda", return_value=[]):
            with patch.object(ci, "parse_ld_library_path", return_value=[]):
                s = ci.status()
    assert s["install_count"] == 1
    assert s["verdict"]["verdict"] == "clean"


def test_status_version_conflict():
    with patch.object(ci, "find_cuda_toolkits", return_value=[
        {"path": "/usr/local/cuda-11.8", "version": "11.8.0",
         "source": "toolkit"},
        {"path": "/usr/local/cuda-12.4", "version": "12.4.1",
         "source": "toolkit"},
    ]):
        with patch.object(ci, "find_conda_cuda", return_value=[]):
            with patch.object(ci, "parse_ld_library_path", return_value=[]):
                s = ci.status()
    assert s["verdict"]["verdict"] == "version_conflict"
