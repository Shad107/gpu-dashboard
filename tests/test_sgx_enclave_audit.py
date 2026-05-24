"""Tests for modules/sgx_enclave_audit.py — R&D #75.3."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import sgx_enclave_audit as mod


# --- parse_cpu_flags -------------------------------------------

def test_parse_empty():
    assert mod.parse_cpu_flags("") == set()


def test_parse_with_sgx():
    text = ("processor\t: 0\n"
              "vendor_id\t: GenuineIntel\n"
              "flags\t: fpu vme sgx sgx_lc avx\n")
    flags = mod.parse_cpu_flags(text)
    assert "sgx" in flags
    assert "sgx_lc" in flags


def test_parse_without_sgx():
    text = ("processor\t: 0\n"
              "flags\t: fpu vme avx\n")
    flags = mod.parse_cpu_flags(text)
    assert "sgx" not in flags


# --- list_dev_nodes --------------------------------------------

def test_list_dev_nodes_absent(tmp_path):
    out = mod.list_dev_nodes(str(tmp_path))
    assert all(not d["present"] for d in out)
    assert {d["name"] for d in out} == {
        "sgx_enclave", "sgx_provision", "sgx_vepc"}


def test_list_dev_nodes_present(tmp_path):
    for name in ("sgx_enclave", "sgx_provision"):
        node = tmp_path / name
        node.write_text("")
        os.chmod(str(node), 0o660)
    out = mod.list_dev_nodes(str(tmp_path))
    by_name = {d["name"]: d for d in out}
    assert by_name["sgx_enclave"]["present"] is True
    assert by_name["sgx_enclave"]["mode"] == 0o660
    assert by_name["sgx_vepc"]["present"] is False


# --- classify ---------------------------------------------------

def _no_dev():
    return [{"name": n, "present": False, "mode": None}
              for n in ("sgx_enclave", "sgx_provision",
                            "sgx_vepc")]


def _with_dev(mode_enclave=0o660,
                mode_provision=0o660):
    return [{"name": "sgx_enclave", "present": True,
                "mode": mode_enclave},
              {"name": "sgx_provision", "present": True,
                "mode": mode_provision},
              {"name": "sgx_vepc", "present": False,
                "mode": None}]


def test_classify_unknown_no_cpuinfo():
    v = mod.classify(set(), [], _no_dev(), False)
    assert v["verdict"] == "unknown"


def test_classify_sgx_unavailable():
    v = mod.classify(set(["fpu", "avx"]), [], _no_dev(), True)
    assert v["verdict"] == "sgx_unavailable"


def test_classify_sgx_disabled_in_bios():
    v = mod.classify({"sgx", "sgx_lc"}, [], _no_dev(), True)
    assert v["verdict"] == "sgx_disabled_in_bios"


def test_classify_provision_world_writable():
    v = mod.classify({"sgx", "sgx_lc"}, [],
                          _with_dev(mode_provision=0o666),
                          True)
    assert v["verdict"] == "provision_node_world_writable"


def test_classify_flc_missing():
    # sgx flag but no sgx_lc
    v = mod.classify({"sgx"}, [], _with_dev(), True)
    assert v["verdict"] == "flc_missing"


def test_classify_ok():
    v = mod.classify({"sgx", "sgx_lc"}, [], _with_dev(), True)
    assert v["verdict"] == "ok"


# Priority : disabled_bios > unavailable > provision_ww > flc
def test_priority_disabled_over_provision_ww():
    # CPU advertises sgx but no enclave node → disabled_in_bios
    # wins even if provision dev is hypothetically world-writable
    # (impossible scenario but tests priority).
    bad = [{"name": "sgx_enclave", "present": False,
              "mode": None},
              {"name": "sgx_provision", "present": True,
                "mode": 0o666},
              {"name": "sgx_vepc", "present": False,
                "mode": None}]
    v = mod.classify({"sgx", "sgx_lc"}, [], bad, True)
    assert v["verdict"] == "sgx_disabled_in_bios"


def test_priority_provision_ww_over_flc():
    v = mod.classify({"sgx"}, [],
                          _with_dev(mode_provision=0o666),
                          True)
    assert v["verdict"] == "provision_node_world_writable"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                          str(tmp_path / "no_cpuinfo"),
                          str(tmp_path / "no_old"),
                          str(tmp_path / "no_new"),
                          str(tmp_path / "no_dev"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_unavailable_synthetic(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("flags\t: fpu avx\n")
    out = mod.status(None, str(cpuinfo),
                          str(tmp_path / "no_old"),
                          str(tmp_path / "no_new"),
                          str(tmp_path / "dev"))
    assert out["cpu_has_sgx"] is False
    assert out["verdict"]["verdict"] == "sgx_unavailable"


def test_status_ok_synthetic(tmp_path):
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("flags\t: fpu sgx sgx_lc avx\n")
    dev = tmp_path / "dev"; dev.mkdir()
    (dev / "sgx_enclave").write_text("")
    os.chmod(str(dev / "sgx_enclave"), 0o660)
    (dev / "sgx_provision").write_text("")
    os.chmod(str(dev / "sgx_provision"), 0o660)
    out = mod.status(None, str(cpuinfo),
                          str(tmp_path / "no_old"),
                          str(tmp_path / "no_new"),
                          str(dev))
    assert out["verdict"]["verdict"] == "ok"
