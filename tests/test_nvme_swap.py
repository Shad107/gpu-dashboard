"""R&D #18.1 — NVMe-as-VRAM-swap monitor tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import nvme_swap as ns


# ── _parse_kib ─────────────────────────────────────────────────────────


def test_parse_kib_kb():
    assert ns._parse_kib("12345 kB") == 12345 * 1024


def test_parse_kib_mb():
    assert ns._parse_kib("2 mB") == 2 * 1024 ** 2


def test_parse_kib_empty():
    assert ns._parse_kib("") == 0
    assert ns._parse_kib("not a number") == 0


def test_parse_kib_unit_missing():
    assert ns._parse_kib("999") == 999


# ── is_llm_process ─────────────────────────────────────────────────────


def test_is_llm_process_ollama():
    assert ns.is_llm_process("ollama", "/usr/bin/ollama serve") is True


def test_is_llm_process_llama_server():
    assert ns.is_llm_process("llama-server", "llama-server -m foo.gguf") is True


def test_is_llm_process_python_without_hint():
    assert ns.is_llm_process("python3", "python3 hello.py") is False


def test_is_llm_process_python_with_hint():
    assert ns.is_llm_process("python3",
                              "python3 -m vllm.entrypoints.openai.api_server") is True


def test_is_llm_process_random_app():
    assert ns.is_llm_process("firefox", "/usr/bin/firefox") is False


def test_is_llm_process_empty():
    assert ns.is_llm_process("", "") is False


# ── scan_llm_processes ─────────────────────────────────────────────────


def _mkproc(root, pid, comm, cmdline="", swap_kb=0, rss_kb=0):
    p = os.path.join(root, str(pid))
    os.makedirs(p)
    with open(os.path.join(p, "comm"), "w") as f:
        f.write(comm + "\n")
    with open(os.path.join(p, "cmdline"), "wb") as f:
        f.write(cmdline.replace(" ", "\x00").encode() + b"\x00")
    with open(os.path.join(p, "status"), "w") as f:
        f.write(f"Name:\t{comm}\nVmSwap:\t{swap_kb} kB\nVmRSS:\t{rss_kb} kB\n")


def test_scan_finds_llm_process(tmp_path):
    _mkproc(str(tmp_path), 1, "ollama", "ollama serve",
            swap_kb=2 * 1024 ** 2, rss_kb=512 * 1024)
    _mkproc(str(tmp_path), 2, "firefox", "/usr/bin/firefox")
    out = ns.scan_llm_processes(proc_root=str(tmp_path))
    assert len(out) == 1
    assert out[0]["comm"] == "ollama"
    assert out[0]["swap_bytes"] == 2 * 1024 ** 3
    assert out[0]["rss_bytes"] == 512 * 1024 * 1024


def test_scan_empty_when_no_llm(tmp_path):
    _mkproc(str(tmp_path), 10, "bash")
    assert ns.scan_llm_processes(proc_root=str(tmp_path)) == []


# ── read_block_stat ────────────────────────────────────────────────────


def test_read_block_stat_valid(tmp_path):
    d = tmp_path / "nvme0n1"; d.mkdir()
    (d / "stat").write_text("100 50 200 60 300 40 400 70 0 1000 2000\n")
    s = ns.read_block_stat("nvme0n1", sys_root=str(tmp_path))
    assert s is not None
    assert s["sectors_read"] == 200
    assert s["sectors_written"] == 400


def test_read_block_stat_missing(tmp_path):
    assert ns.read_block_stat("nvme9n9", sys_root=str(tmp_path)) is None


def test_read_block_stat_malformed(tmp_path):
    d = tmp_path / "nvme0n1"; d.mkdir()
    (d / "stat").write_text("garbage\n")
    assert ns.read_block_stat("nvme0n1", sys_root=str(tmp_path)) is None


# ── list_nvme_devices ──────────────────────────────────────────────────


def test_list_nvme_devices(tmp_path):
    (tmp_path / "nvme0n1").mkdir()
    (tmp_path / "nvme1n1").mkdir()
    (tmp_path / "nvme0n1p1").mkdir()  # partition — should be skipped
    (tmp_path / "sda").mkdir()         # not nvme
    devs = ns.list_nvme_devices(sys_root=str(tmp_path))
    assert sorted(devs) == ["nvme0n1", "nvme1n1"]


# ── data_units_to_tb ───────────────────────────────────────────────────


def test_data_units_to_tb_known_value():
    # 2 000 000 data units = 2 000 000 × 512 000 B = 1.024 TB
    assert ns.data_units_to_tb(2_000_000) == pytest.approx(1.024, rel=1e-3)


def test_data_units_to_tb_zero():
    assert ns.data_units_to_tb(0) == 0.0


# ── project_tbw_remaining ──────────────────────────────────────────────


def test_project_clean_drive():
    p = ns.project_tbw_remaining(duw=0, rated_tbw=600.0,
                                  write_rate_bps=None)
    assert p["used_tb"] == 0.0
    assert p["remaining_tb"] == 600.0
    assert p["days_remaining"] is None


def test_project_with_write_rate():
    # 1 TB used of 600 TB; writing at 1 MB/s steady → ~6.93 M days
    p = ns.project_tbw_remaining(duw=1_953_125,  # ≈ 1 TB
                                  rated_tbw=600.0,
                                  write_rate_bps=1024 ** 2)
    assert p["used_tb"] == pytest.approx(1.0, rel=1e-2)
    assert p["days_remaining"] is not None and p["days_remaining"] > 1000


def test_project_burning_through_endurance():
    """100 MB/s steady → days_remaining should drop into days."""
    p = ns.project_tbw_remaining(duw=0,
                                  rated_tbw=1.0,  # tiny rated TBW
                                  write_rate_bps=100 * 1024 ** 2)
    assert p["days_remaining"] is not None
    assert p["days_remaining"] < 1


# ── status diagnose ────────────────────────────────────────────────────


def test_diagnose_warns_on_big_swap():
    procs = [{"swap_bytes": 4 * 1024 ** 3}]
    w = ns._diagnose(procs, devices=[])
    assert w is not None and "swap" in w.lower()


def test_diagnose_no_warn_when_quiet():
    assert ns._diagnose([], []) is None


def test_diagnose_warns_on_short_endurance():
    devices = [{"device": "nvme0n1",
                "endurance": {"days_remaining": 100, "pct_used": 30}}]
    w = ns._diagnose([], devices)
    assert w is not None and "runs out" in w


def test_status_returns_basic_shape():
    with patch.object(ns, "scan_llm_processes", return_value=[]):
        with patch.object(ns, "list_nvme_devices", return_value=[]):
            s = ns.status()
    assert s["ok"] is True
    assert s["llm_total_swap_gib"] == 0
    assert s["nvme_devices"] == []
