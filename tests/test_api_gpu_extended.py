"""Tests for the extended fields in _gpu_card_snapshot:
- mem_temp (memory junction temperature)
- vbios_version
"""
from __future__ import annotations

import subprocess

import pytest

from gpu_dashboard import api


class FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout; self.stderr = stderr; self.returncode = returncode


class TestExtendedFields:
    def test_includes_mem_temp_and_vbios(self, monkeypatch):
        # 10 fields: name, temp, fan, power, plimit, util, mem_used, mem_total, mem_temp, vbios
        stdout = "RTX 3090, 55, 40, 230.0, 350.0, 75, 12000, 24576, 78, 94.02.42.80.10\n"
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: FakeRun(stdout=stdout, returncode=0))
        g = api._gpu_card_snapshot(gpu_index=0)
        assert g["alive"] is True
        assert g["mem_temp"] == 78
        assert g["vbios_version"] == "94.02.42.80.10"

    def test_old_driver_no_mem_temp(self, monkeypatch):
        # 8 fields only (old driver, no extended support)
        stdout = "RTX 3090, 55, 40, 230.0, 350.0, 75, 12000, 24576\n"
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: FakeRun(stdout=stdout, returncode=0))
        g = api._gpu_card_snapshot(gpu_index=0)
        assert g["alive"] is True
        # Should be None when not provided
        assert g.get("mem_temp") is None
        assert g.get("vbios_version") is None

    def test_partial_mem_temp_unsupported(self, monkeypatch):
        # Some cards return "[N/A]" for mem_temp — should become None
        stdout = "RTX 3090, 55, 40, 230.0, 350.0, 75, 12000, 24576, [N/A], 94.02.42.80.10\n"
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **kw: FakeRun(stdout=stdout, returncode=0))
        g = api._gpu_card_snapshot(gpu_index=0)
        assert g["mem_temp"] is None
        assert g["vbios_version"] == "94.02.42.80.10"
