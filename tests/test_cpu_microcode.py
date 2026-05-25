"""Tests for modules/cpu_microcode.py — R&D #36.1 microcode audit."""
from __future__ import annotations

from pathlib import Path

import pytest

from gpu_dashboard.modules import cpu_microcode


_LIVE_CPUINFO = """\
processor\t: 0
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 154
model name\t: 12th Gen Intel(R) Core(TM) i9-12900H
microcode\t: 0x426
cpu MHz\t\t: 2918.397

processor\t: 1
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 154
microcode\t: 0x426

processor\t: 2
vendor_id\t: GenuineIntel
microcode\t: 0x426
"""


_DRIFT_CPUINFO = """\
processor\t: 0
vendor_id\t: GenuineIntel
microcode\t: 0x426

processor\t: 1
vendor_id\t: GenuineIntel
microcode\t: 0x420
"""


_NO_MICROCODE = """\
processor\t: 0
vendor_id\t: GenuineIntel
cpu family\t: 6
"""


def _mk_cpuinfo(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# --- parse_cpuinfo ----------------------------------------------

def test_parse_cpuinfo_extracts_microcodes():
    info = cpu_microcode.parse_cpuinfo(_LIVE_CPUINFO)
    assert info["microcodes"] == ["0x426", "0x426", "0x426"]


def test_parse_cpuinfo_extracts_vendor_family_model():
    info = cpu_microcode.parse_cpuinfo(_LIVE_CPUINFO)
    assert info["vendor_id"] == "GenuineIntel"
    assert info["cpu_family"] == "6"
    assert info["model"] == "154"
    assert "i9-12900H" in info["model_name"]


def test_parse_cpuinfo_drift():
    info = cpu_microcode.parse_cpuinfo(_DRIFT_CPUINFO)
    assert info["microcodes"] == ["0x426", "0x420"]


def test_parse_cpuinfo_no_microcode_field():
    info = cpu_microcode.parse_cpuinfo(_NO_MICROCODE)
    assert info["microcodes"] == []
    assert info["vendor_id"] == "GenuineIntel"


def test_parse_cpuinfo_empty():
    info = cpu_microcode.parse_cpuinfo("")
    assert info["microcodes"] == []
    assert info.get("vendor_id") is None


# --- classify --------------------------------------------------

def test_classify_synced_all_same():
    v = cpu_microcode.classify(microcodes=["0x426", "0x426", "0x426"],
                                   vendor="GenuineIntel")
    assert v["verdict"] == "synced"
    assert "0x426" in v["reason"]


def test_classify_drift_when_mixed():
    v = cpu_microcode.classify(microcodes=["0x426", "0x420", "0x426"],
                                   vendor="GenuineIntel")
    assert v["verdict"] == "drift"
    assert "0x426" in v["reason"]
    assert "0x420" in v["reason"]
    assert "initramfs" in v["recommendation"].lower() or "microcode" in v["recommendation"].lower()


def test_classify_missing_when_empty():
    v = cpu_microcode.classify(microcodes=[], vendor="GenuineIntel")
    assert v["verdict"] == "missing"


def test_classify_recommendation_intel_package():
    # Intel vendor → recommend intel-microcode
    v = cpu_microcode.classify(microcodes=["0x420", "0x426"],
                                   vendor="GenuineIntel")
    assert "intel-microcode" in v["recommendation"]


def test_classify_recommendation_amd_package():
    # AMD vendor → recommend amd64-microcode
    v = cpu_microcode.classify(microcodes=["0x800", "0x801"],
                                   vendor="AuthenticAMD")
    assert "amd64-microcode" in v["recommendation"]


# --- status ---------------------------------------------------

def test_status_live_synced(tmp_path, monkeypatch):
    bi = tmp_path / "cpuinfo"
    _mk_cpuinfo(bi, _LIVE_CPUINFO)
    monkeypatch.setattr(cpu_microcode, "_CPUINFO", str(bi))
    monkeypatch.setattr(cpu_microcode, "_SYS_MICROCODE",
                          str(tmp_path / "missing"))
    s = cpu_microcode.status()
    assert s["ok"] is True
    assert s["distinct_microcodes"] == ["0x426"]
    assert s["cpu_count"] == 3
    assert s["vendor_id"] == "GenuineIntel"
    assert s["verdict"]["verdict"] == "synced"


def test_status_drift_warns(tmp_path, monkeypatch):
    bi = tmp_path / "cpuinfo"
    _mk_cpuinfo(bi, _DRIFT_CPUINFO)
    monkeypatch.setattr(cpu_microcode, "_CPUINFO", str(bi))
    monkeypatch.setattr(cpu_microcode, "_SYS_MICROCODE",
                          str(tmp_path / "missing"))
    s = cpu_microcode.status()
    assert s["verdict"]["verdict"] == "drift"
    assert len(s["distinct_microcodes"]) == 2


def test_status_no_cpuinfo(tmp_path, monkeypatch):
    monkeypatch.setattr(cpu_microcode, "_CPUINFO",
                          str(tmp_path / "absent"))
    monkeypatch.setattr(cpu_microcode, "_SYS_MICROCODE",
                          str(tmp_path / "missing"))
    s = cpu_microcode.status()
    assert s["ok"] is False
    assert s["error"] == "cpuinfo_unavailable"


def test_status_uses_sys_microcode_version_when_present(tmp_path, monkeypatch):
    bi = tmp_path / "cpuinfo"
    _mk_cpuinfo(bi, _LIVE_CPUINFO)
    sys_dir = tmp_path / "sys_microcode"
    sys_dir.mkdir()
    (sys_dir / "version").write_text("0x426\n")
    monkeypatch.setattr(cpu_microcode, "_CPUINFO", str(bi))
    monkeypatch.setattr(cpu_microcode, "_SYS_MICROCODE", str(sys_dir))
    s = cpu_microcode.status()
    assert s["sys_microcode_version"] == "0x426"


def test_status_extracts_processor_flags(tmp_path, monkeypatch):
    bi = tmp_path / "cpuinfo"
    _mk_cpuinfo(bi, _LIVE_CPUINFO)
    sys_dir = tmp_path / "sys_microcode"
    sys_dir.mkdir()
    (sys_dir / "version").write_text("0x426\n")
    (sys_dir / "processor_flags").write_text("0x5\n")
    monkeypatch.setattr(cpu_microcode, "_CPUINFO", str(bi))
    monkeypatch.setattr(cpu_microcode, "_SYS_MICROCODE", str(sys_dir))
    s = cpu_microcode.status()
    assert s["sys_processor_flags"] == "0x5"
    assert s["sys_microcode_dir_present"] is True


def test_status_processor_flags_absent_on_vm(tmp_path, monkeypatch):
    bi = tmp_path / "cpuinfo"
    _mk_cpuinfo(bi, _LIVE_CPUINFO)
    monkeypatch.setattr(cpu_microcode, "_CPUINFO", str(bi))
    monkeypatch.setattr(cpu_microcode, "_SYS_MICROCODE",
                          str(tmp_path / "absent_dir"))
    s = cpu_microcode.status()
    assert s["sys_processor_flags"] is None
    assert s["sys_microcode_dir_present"] is False


def test_status_no_microcode_field_returns_missing(tmp_path, monkeypatch):
    bi = tmp_path / "cpuinfo"
    _mk_cpuinfo(bi, _NO_MICROCODE)
    monkeypatch.setattr(cpu_microcode, "_CPUINFO", str(bi))
    monkeypatch.setattr(cpu_microcode, "_SYS_MICROCODE",
                          str(tmp_path / "missing"))
    s = cpu_microcode.status()
    assert s["verdict"]["verdict"] == "missing"


def test_status_includes_cpu_family_and_model(tmp_path, monkeypatch):
    bi = tmp_path / "cpuinfo"
    _mk_cpuinfo(bi, _LIVE_CPUINFO)
    monkeypatch.setattr(cpu_microcode, "_CPUINFO", str(bi))
    monkeypatch.setattr(cpu_microcode, "_SYS_MICROCODE",
                          str(tmp_path / "missing"))
    s = cpu_microcode.status()
    assert s["cpu_family"] == "6"
    assert s["model"] == "154"
    assert "i9-12900H" in s["model_name"]
