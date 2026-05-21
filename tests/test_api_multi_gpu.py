"""Tests for multi-GPU support — list available GPUs + snapshot specific index."""
from __future__ import annotations

import subprocess

import pytest

from gpu_dashboard import api


class FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestGpusAvailable:
    def test_no_nvidia_smi_returns_empty(self, monkeypatch):
        def fake_run(*a, **kw):
            raise FileNotFoundError("nvidia-smi")
        monkeypatch.setattr(subprocess, "run", fake_run)
        assert api._gpus_available() == []

    def test_single_gpu(self, monkeypatch):
        stdout = "0, NVIDIA GeForce RTX 3090, 00000000:01:00.0, 24576 MiB\n"
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: FakeRun(stdout=stdout, returncode=0))
        gpus = api._gpus_available()
        assert len(gpus) == 1
        assert gpus[0]["index"] == 0
        assert gpus[0]["name"] == "NVIDIA GeForce RTX 3090"
        assert gpus[0]["vram_mib"] == 24576

    def test_multi_gpu(self, monkeypatch):
        stdout = (
            "0, NVIDIA GeForce RTX 3090, 00000000:01:00.0, 24576 MiB\n"
            "1, NVIDIA GeForce RTX 4090, 00000000:02:00.0, 24576 MiB\n"
        )
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: FakeRun(stdout=stdout, returncode=0))
        gpus = api._gpus_available()
        assert len(gpus) == 2
        assert gpus[1]["name"] == "NVIDIA GeForce RTX 4090"


class TestGpuCardSnapshotIndex:
    def test_passes_index_to_nvidia_smi(self, monkeypatch):
        captured = []
        def fake_run(cmd, *a, **kw):
            captured.append(cmd)
            return FakeRun(
                stdout="GPU 1, 42, 30, 200, 350, 50, 8000, 24576\n",
                returncode=0,
            )
        monkeypatch.setattr(subprocess, "run", fake_run)
        result = api._gpu_card_snapshot(gpu_index=1)
        assert "-i" in captured[0]
        idx = captured[0].index("-i")
        assert captured[0][idx + 1] == "1"
        assert result["index"] == 1
        assert result["alive"] is True

    def test_default_index_zero(self, monkeypatch):
        captured = []
        def fake_run(cmd, *a, **kw):
            captured.append(cmd)
            return FakeRun(stdout="", returncode=0)
        monkeypatch.setattr(subprocess, "run", fake_run)
        api._gpu_card_snapshot()  # no arg
        idx = captured[0].index("-i")
        assert captured[0][idx + 1] == "0"
