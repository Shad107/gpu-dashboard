"""Tests for modules/proc_maps_anomaly_audit.py — R&D #78.4."""
from __future__ import annotations

import os

import pytest

from gpu_dashboard.modules import proc_maps_anomaly_audit as mod


def _mk_pid(root, pid, comm, maps_lines, *, perms_404=False):
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n")
    if not perms_404:
        (d / "maps").write_text("\n".join(maps_lines) + "\n")


def _maps(perms, path=""):
    """Generate a /proc/<pid>/maps line with given perms/path."""
    base = (f"7f0000000000-7f0000001000 {perms} 00000000 "
            f"08:02 12345")
    if path:
        return f"{base}                    {path}"
    return base


# --- _list_pids ------------------------------------------------

def test_list_pids_missing(tmp_path):
    assert mod._list_pids(str(tmp_path / "nope")) == []


def test_list_pids(tmp_path):
    _mk_pid(tmp_path, 1, "init", [_maps("r--p", "/bin/init")])
    _mk_pid(tmp_path, 100, "test",
            [_maps("r--p", "/bin/test")])
    (tmp_path / "self").mkdir()  # non-numeric
    assert mod._list_pids(str(tmp_path)) == [1, 100]


# --- _classify_path --------------------------------------------

def test_classify_no_exec():
    assert mod._classify_path("r--p", "/bin/foo") is None


def test_classify_rwx():
    assert mod._classify_path("rwxp", "") == "rwx"
    assert mod._classify_path("rwxp", "/bin/foo") == "rwx"


def test_classify_anon_exec():
    assert mod._classify_path("r-xp", "") == "anon_exec"


def test_classify_memfd_exec():
    assert (mod._classify_path("r-xp", "/memfd:test")
            == "memfd_exec")


def test_classify_deleted_exec_real():
    assert (mod._classify_path("r-xp",
                                "/usr/bin/oldbin (deleted)")
            == "deleted_exec")


def test_classify_deleted_exec_nvidia_skipped():
    assert (mod._classify_path("r-xp",
                                "/usr/lib/x86_64-linux-gnu/"
                                "libnvidia-glcore.so.535.86 "
                                "(deleted)")
            is None)


def test_classify_deleted_exec_libcuda_skipped():
    assert (mod._classify_path("r-xp",
                                "/usr/lib/libcuda.so.1 "
                                "(deleted)")
            is None)


def test_classify_deleted_exec_snap_skipped():
    assert (mod._classify_path("r-xp",
                                "/snap/code/123/bin "
                                "(deleted)")
            is None)


def test_classify_normal_file():
    assert mod._classify_path(
        "r-xp", "/usr/bin/normal") is None


def test_classify_jsjit_code_label_suppressed():
    # Node.js / V8 labels JIT pages as [anon:JSJITCode]
    assert mod._classify_path(
        "rwxp", "[anon:JSJITCode]") is None


def test_classify_unlabeled_anon_label_still_flagged():
    # [anon:foo] with unknown label is still suspicious
    assert mod._classify_path(
        "r-xp", "[anon:custom_thing]") == "anon_exec"


def test_classify_memfd_jit_suppressed():
    # Qt-QML writes JIT code to a memfd labeled JITCode
    assert mod._classify_path(
        "r-xp", "/memfd:JITCode:QtQml (deleted)") is None


def test_classify_memfd_unknown_still_flagged():
    assert mod._classify_path(
        "r-xp", "/memfd:malware_payload") == "memfd_exec"


# --- scan_pid --------------------------------------------------

def test_scan_pid_clean(tmp_path):
    _mk_pid(tmp_path, 1, "init",
            [_maps("r-xp", "/bin/init"),
             _maps("r--p", "/bin/init")])
    out = mod.scan_pid(str(tmp_path), 1)
    assert out is not None
    assert out["pid"] == 1
    assert out["rwx"] == []
    assert out["anon_exec"] == []


def test_scan_pid_rwx_flagged(tmp_path):
    _mk_pid(tmp_path, 1, "bad_proc",
            [_maps("rwxp", "/some/path")])
    out = mod.scan_pid(str(tmp_path), 1)
    assert out["rwx"] == ["/some/path"]


def test_scan_pid_rwx_jit_suppressed(tmp_path):
    _mk_pid(tmp_path, 1, "node",
            [_maps("rwxp", "/some/jit/region")])
    out = mod.scan_pid(str(tmp_path), 1)
    assert out["rwx"] == []  # JIT process — suppressed


def test_scan_pid_anon_exec_flagged(tmp_path):
    _mk_pid(tmp_path, 1, "weird",
            [_maps("r-xp", "")])
    out = mod.scan_pid(str(tmp_path), 1)
    assert out["anon_exec"] == ["<anon>"]


def test_scan_pid_anon_exec_jit_suppressed(tmp_path):
    _mk_pid(tmp_path, 1, "java",
            [_maps("r-xp", "")])
    out = mod.scan_pid(str(tmp_path), 1)
    assert out["anon_exec"] == []


def test_scan_pid_qt_jit_suppressed_by_lib(tmp_path):
    # comm not in whitelist, but libQt6Qml present → JIT
    _mk_pid(tmp_path, 1, "plasmashell",
            [_maps("r--p",
                   "/usr/lib/x86_64-linux-gnu/libQt6Qml.so.6"),
             _maps("rwxp", "/some/jit/region")])
    out = mod.scan_pid(str(tmp_path), 1)
    assert out["rwx"] == []


def test_scan_pid_jvm_suppressed_by_lib(tmp_path):
    _mk_pid(tmp_path, 1, "someapp",
            [_maps("r--p", "/usr/lib/jvm/.../libjvm.so"),
             _maps("rwxp", "/jvm/codecache")])
    out = mod.scan_pid(str(tmp_path), 1)
    assert out["rwx"] == []


def test_scan_pid_memfd_exec(tmp_path):
    _mk_pid(tmp_path, 1, "anyproc",
            [_maps("r-xp", "/memfd:something")])
    out = mod.scan_pid(str(tmp_path), 1)
    assert "/memfd:something" in out["memfd_exec"]


def test_scan_pid_memfd_not_suppressed_jit(tmp_path):
    # Even JIT processes get memfd flagged — it's distinct
    _mk_pid(tmp_path, 1, "node",
            [_maps("r-xp", "/memfd:weird")])
    out = mod.scan_pid(str(tmp_path), 1)
    assert "/memfd:weird" in out["memfd_exec"]


def test_scan_pid_deleted_exec_flagged(tmp_path):
    _mk_pid(tmp_path, 1, "any",
            [_maps("r-xp", "/usr/bin/old (deleted)")])
    out = mod.scan_pid(str(tmp_path), 1)
    assert "/usr/bin/old (deleted)" in out["deleted_exec"]


def test_scan_pid_unreadable(tmp_path):
    # Don't create maps file → unreadable
    d = tmp_path / "1"
    d.mkdir()
    (d / "comm").write_text("test\n")
    assert mod.scan_pid(str(tmp_path), 1) is None


# --- classify --------------------------------------------------

def _empty_scan(pid, comm):
    return {"pid": pid, "comm": comm, "rwx": [],
            "anon_exec": [], "memfd_exec": [],
            "deleted_exec": []}


def test_classify_unknown_no_proc():
    v = mod.classify(False, [], 0)
    assert v["verdict"] == "unknown"


def test_classify_unknown_no_pids():
    v = mod.classify(True, [], 0)
    assert v["verdict"] == "unknown"


def test_classify_requires_root():
    # /proc present, PIDs there, but none scanned
    v = mod.classify(True, [], 366)
    assert v["verdict"] == "requires_root"


def test_classify_ok():
    v = mod.classify(True,
                       [_empty_scan(1, "init"),
                        _empty_scan(100, "shell")],
                       2)
    assert v["verdict"] == "ok"


def test_classify_rwx():
    s = _empty_scan(1, "weird")
    s["rwx"] = ["/some/path"]
    v = mod.classify(True, [s], 1)
    assert v["verdict"] == "rwx_mapping_found"


def test_classify_anon():
    s = _empty_scan(1, "weird")
    s["anon_exec"] = ["<anon>"]
    v = mod.classify(True, [s], 1)
    assert v["verdict"] == "anon_exec_segment"


def test_classify_deleted():
    s = _empty_scan(1, "any")
    s["deleted_exec"] = ["/usr/bin/old (deleted)"]
    v = mod.classify(True, [s], 1)
    assert v["verdict"] == "deleted_exec_backing"


def test_classify_memfd():
    s = _empty_scan(1, "any")
    s["memfd_exec"] = ["/memfd:foo"]
    v = mod.classify(True, [s], 1)
    assert v["verdict"] == "memfd_exec_present"


# Priority : rwx > anon > deleted > memfd
def test_priority_rwx_over_anon():
    s = _empty_scan(1, "x")
    s["rwx"] = ["/a"]
    s["anon_exec"] = ["<anon>"]
    v = mod.classify(True, [s], 1)
    assert v["verdict"] == "rwx_mapping_found"


def test_priority_anon_over_deleted():
    s1 = _empty_scan(1, "x")
    s1["anon_exec"] = ["<anon>"]
    s2 = _empty_scan(2, "y")
    s2["deleted_exec"] = ["/old (deleted)"]
    v = mod.classify(True, [s1, s2], 2)
    assert v["verdict"] == "anon_exec_segment"


def test_priority_deleted_over_memfd():
    s1 = _empty_scan(1, "x")
    s1["deleted_exec"] = ["/old (deleted)"]
    s2 = _empty_scan(2, "y")
    s2["memfd_exec"] = ["/memfd:foo"]
    v = mod.classify(True, [s1, s2], 2)
    assert v["verdict"] == "deleted_exec_backing"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    _mk_pid(tmp_path, 1, "init",
            [_maps("r--p", "/bin/init"),
             _maps("r-xp", "/bin/init")])
    _mk_pid(tmp_path, 100, "shell",
            [_maps("r-xp", "/usr/bin/bash")])
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["pid_count_total"] == 2
    assert out["pid_count_scanned"] == 2
    assert out["verdict"]["verdict"] == "ok"


def test_status_rwx_synthetic(tmp_path):
    _mk_pid(tmp_path, 1, "evil",
            [_maps("rwxp", "/tmp/shellcode")])
    out = mod.status(None, str(tmp_path))
    assert out["verdict"]["verdict"] == "rwx_mapping_found"
