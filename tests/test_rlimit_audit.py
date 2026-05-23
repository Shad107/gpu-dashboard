"""R&D #29.8 — rlimit auditor for LLM daemons tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import rlimit_audit as ra


def _mkproc(tmp, pid, comm, cmdline="", limits_text="", vm_lck_kb=0):
    p = tmp / str(pid); p.mkdir()
    (p / "comm").write_text(comm + "\n")
    (p / "cmdline").write_bytes(cmdline.replace(" ", "\x00").encode() + b"\x00")
    (p / "limits").write_text(limits_text or "")
    status = f"Name:\t{comm}\nVmLck:\t{vm_lck_kb} kB\n"
    (p / "status").write_text(status)


_REAL_LIMITS_64K = """\
Limit                     Soft Limit           Hard Limit           Units
Max cpu time              unlimited            unlimited            seconds
Max file size             unlimited            unlimited            bytes
Max data size             unlimited            unlimited            bytes
Max stack size            8388608              unlimited            bytes
Max core file size        0                    unlimited            bytes
Max resident set          unlimited            unlimited            bytes
Max processes             127108               127108               processes
Max open files            1024                 524288               files
Max locked memory         65536                65536                bytes
Max address space         unlimited            unlimited            bytes
"""

_REAL_LIMITS_UNLIMITED = _REAL_LIMITS_64K.replace(
    "Max locked memory         65536                65536",
    "Max locked memory         unlimited            unlimited")


# ── is_llm_proc ────────────────────────────────────────────────────────


def test_is_llm_ollama():
    assert ra.is_llm_proc("ollama", "ollama serve") is True


def test_is_llm_llama_server():
    assert ra.is_llm_proc("llama-server", "llama-server -m foo.gguf") is True


def test_is_llm_python_with_vllm_hint():
    assert ra.is_llm_proc(
        "python3", "python -m vllm.entrypoints.openai") is True


def test_is_llm_random_python_no():
    assert ra.is_llm_proc("python3", "python hello.py") is False


def test_is_llm_random_app_no():
    assert ra.is_llm_proc("firefox", "/usr/bin/firefox") is False


# ── read_memlock_rlimit ────────────────────────────────────────────────


def test_read_memlock_64k(tmp_path):
    _mkproc(tmp_path, 1, "ollama", limits_text=_REAL_LIMITS_64K)
    out = ra.read_memlock_rlimit(1, proc_root=str(tmp_path))
    assert out == 65536


def test_read_memlock_unlimited(tmp_path):
    _mkproc(tmp_path, 1, "ollama", limits_text=_REAL_LIMITS_UNLIMITED)
    out = ra.read_memlock_rlimit(1, proc_root=str(tmp_path))
    assert out == 2 ** 63 - 1


def test_read_memlock_missing(tmp_path):
    """No /proc/<pid>/limits."""
    assert ra.read_memlock_rlimit(99999, proc_root=str(tmp_path)) is None


def test_read_memlock_garbage(tmp_path):
    _mkproc(tmp_path, 1, "ollama",
              limits_text="Max locked memory  garbage  garbage  bytes\n")
    assert ra.read_memlock_rlimit(1, proc_root=str(tmp_path)) is None


# ── read_vm_lck_bytes ──────────────────────────────────────────────────


def test_read_vm_lck(tmp_path):
    _mkproc(tmp_path, 1, "ollama", vm_lck_kb=8192)
    assert ra.read_vm_lck_bytes(1, proc_root=str(tmp_path)) == 8192 * 1024


def test_read_vm_lck_zero(tmp_path):
    _mkproc(tmp_path, 1, "ollama", vm_lck_kb=0)
    assert ra.read_vm_lck_bytes(1, proc_root=str(tmp_path)) == 0


# ── classify ───────────────────────────────────────────────────────────


def test_classify_ok_unlimited():
    v = ra.classify({"memlock_bytes": 2 ** 63 - 1, "vm_lck_bytes": 0})
    assert v["verdict"] == "ok"


def test_classify_ok_4_gib():
    v = ra.classify({"memlock_bytes": 4 * 1024 ** 3, "vm_lck_bytes": 0})
    assert v["verdict"] == "ok"


def test_classify_low_limit_default_64k():
    """Stock systemd inheritance = 64 KiB MEMLOCK ; warn even without mlock yet."""
    v = ra.classify({"memlock_bytes": 64 * 1024, "vm_lck_bytes": 0})
    assert v["verdict"] == "low_limit"


def test_classify_severely_low_with_active_mlock():
    """64 KiB MEMLOCK BUT VmLck > 0 → mlock() is failing silently."""
    v = ra.classify({"memlock_bytes": 64 * 1024,
                       "vm_lck_bytes": 100 * 1024 ** 2})
    assert v["verdict"] == "severely_low"
    assert "paging out" in v["reason"] or "failing" in v["reason"]


def test_classify_unknown():
    v = ra.classify({"memlock_bytes": None, "vm_lck_bytes": 0})
    assert v["verdict"] == "unknown"


# ── systemd_dropin_recipe ──────────────────────────────────────────────


def test_dropin_recipe_basic():
    r = ra.systemd_dropin_recipe("ollama")
    assert "LimitMEMLOCK=infinity" in r
    assert "ollama.service" in r
    assert "daemon-reload" in r


def test_dropin_recipe_sanitizes_comm():
    """A comm with shell metacharacters falls back to placeholder."""
    r = ra.systemd_dropin_recipe("a/../b")
    assert "your-service.service" in r


# ── scan_llm_procs ─────────────────────────────────────────────────────


def test_scan_finds_llm_only(tmp_path):
    _mkproc(tmp_path, 100, "ollama", "ollama serve", _REAL_LIMITS_UNLIMITED)
    _mkproc(tmp_path, 200, "firefox", "/usr/bin/firefox")
    out = ra.scan_llm_procs(proc_root=str(tmp_path))
    assert len(out) == 1
    assert out[0]["comm"] == "ollama"


def test_scan_extracts_memlock(tmp_path):
    _mkproc(tmp_path, 100, "ollama", "ollama serve",
              _REAL_LIMITS_64K, vm_lck_kb=5)
    out = ra.scan_llm_procs(proc_root=str(tmp_path))
    assert out[0]["memlock_bytes"] == 65536
    assert out[0]["vm_lck_bytes"] == 5120


# ── status ─────────────────────────────────────────────────────────────


def test_status_no_llm_procs(tmp_path):
    with patch.object(ra, "scan_llm_procs", return_value=[]):
        s = ra.status()
    assert s["worst_verdict"] == "no_llm_procs"


def test_status_severely_low_flag():
    fake = [{"pid": 100, "comm": "ollama", "cmdline_short": "ollama serve",
             "memlock_bytes": 65536, "vm_lck_bytes": 100 * 1024 ** 2}]
    with patch.object(ra, "scan_llm_procs", return_value=fake):
        s = ra.status()
    assert s["worst_verdict"] == "severely_low"
    assert "LimitMEMLOCK=infinity" in s["processes"][0]["recipe"]


def test_status_ok_when_unlimited():
    fake = [{"pid": 100, "comm": "ollama", "cmdline_short": "ollama serve",
             "memlock_bytes": 2 ** 63 - 1, "vm_lck_bytes": 8 * 1024 ** 3}]
    with patch.object(ra, "scan_llm_procs", return_value=fake):
        s = ra.status()
    assert s["worst_verdict"] == "ok"
    assert s["processes"][0]["recipe"] == ""
