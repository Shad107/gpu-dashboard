"""Tests for /api/processes — per-process VRAM via nvidia-smi."""
from __future__ import annotations

import subprocess

import pytest

from gpu_dashboard import api


class FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestHandleProcesses:
    def test_no_nvidia_smi(self, monkeypatch):
        def fake_run(*a, **kw): raise FileNotFoundError()
        monkeypatch.setattr(subprocess, "run", fake_run)
        code, body = api.handle_processes({"config": _cfg()})
        assert code == 200
        assert body["available"] is False

    def test_no_processes(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: FakeRun(stdout="", returncode=0))
        code, body = api.handle_processes({"config": _cfg()})
        assert code == 200
        assert body["available"] is True
        assert body["processes"] == []

    def test_parses_processes(self, monkeypatch):
        stdout = (
            "12345, python, 18432 MiB\n"
            "67890, llama-server, 5120 MiB\n"
        )
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: FakeRun(stdout=stdout, returncode=0))
        code, body = api.handle_processes({"config": _cfg()})
        assert code == 200
        assert len(body["processes"]) == 2
        p0 = body["processes"][0]
        assert p0["pid"] == 12345
        assert p0["name"] == "python"
        assert p0["vram_mib"] == 18432

    def test_sorted_by_vram_desc(self, monkeypatch):
        stdout = (
            "1, small, 100 MiB\n"
            "2, big, 5000 MiB\n"
            "3, mid, 1000 MiB\n"
        )
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: FakeRun(stdout=stdout, returncode=0))
        _, body = api.handle_processes({"config": _cfg()})
        vrams = [p["vram_mib"] for p in body["processes"]]
        assert vrams == sorted(vrams, reverse=True)

    def test_passes_gpu_index(self, monkeypatch):
        captured = []
        def fake_run(cmd, *a, **kw):
            captured.append(cmd)
            return FakeRun(stdout="", returncode=0)
        monkeypatch.setattr(subprocess, "run", fake_run)
        api.handle_processes({"config": _cfg(gpu_index=2)})
        assert "-i" in captured[0]
        idx = captured[0].index("-i")
        assert captured[0][idx + 1] == "2"


def _cfg(gpu_index=0):
    class C:
        def get_int(self, k, default=0): return gpu_index if k == "GPU_INDEX" else default
    return C()
