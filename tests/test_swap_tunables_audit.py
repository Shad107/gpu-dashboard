"""Tests for modules/swap_tunables_audit.py — R&D #54.2."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import swap_tunables_audit as mod


def _mk_vm(root, **kv):
    root.mkdir(parents=True, exist_ok=True)
    for k, v in kv.items():
        # 'page-cluster' has a dash in the real sysctl name
        fname = k.replace("_DASH_", "-")
        (root / fname).write_text(str(v) + "\n")


def _mk_swaps(root, swap_text):
    p = root / "swaps"
    p.write_text(swap_text)
    return p


def _mk_block_dev(root, name, *, rotational=0,
                    discard_granularity=4096):
    q = root / name / "queue"
    q.mkdir(parents=True, exist_ok=True)
    (q / "rotational").write_text(str(rotational) + "\n")
    (q / "discard_granularity").write_text(
        str(discard_granularity) + "\n")
    return root / name


def _mk_zram(root, name, *, disksize=0):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "disksize").write_text(str(disksize) + "\n")
    return d


def _mk_pci_dev(root, bdf, vendor, klass):
    d = root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "class").write_text(klass + "\n")


# --- read_vm_knobs ----------------------------------------------

def test_read_vm_knobs_missing(tmp_path):
    out = mod.read_vm_knobs(str(tmp_path / "nope"))
    assert out == {"available": False}


def test_read_vm_knobs_present(tmp_path):
    _mk_vm(tmp_path, swappiness=60, min_free_kbytes=67584)
    # Use real dash path for page-cluster :
    (tmp_path / "page-cluster").write_text("3\n")
    out = mod.read_vm_knobs(str(tmp_path))
    assert out["available"] is True
    assert out["swappiness"] == 60
    assert out["page-cluster"] == 3


# --- detect_active_zram -----------------------------------------

def test_detect_active_zram_none(tmp_path):
    assert mod.detect_active_zram(str(tmp_path)) == []


def test_detect_active_zram_inactive(tmp_path):
    _mk_zram(tmp_path, "zram0", disksize=0)
    assert mod.detect_active_zram(str(tmp_path)) == []


def test_detect_active_zram_active(tmp_path):
    _mk_zram(tmp_path, "zram0", disksize=8388608000)
    _mk_zram(tmp_path, "zram1", disksize=0)
    assert mod.detect_active_zram(str(tmp_path)) == ["zram0"]


# --- has_nvidia_gpu ---------------------------------------------

def test_has_nvidia_gpu_no(tmp_path):
    _mk_pci_dev(tmp_path, "0000:00:00.0", "0x8086", "0x060000")
    assert mod.has_nvidia_gpu(str(tmp_path)) is False


def test_has_nvidia_gpu_yes(tmp_path):
    _mk_pci_dev(tmp_path, "0000:01:00.0", "0x10de", "0x030000")
    assert mod.has_nvidia_gpu(str(tmp_path)) is True


def test_has_nvidia_gpu_audio_only(tmp_path):
    # 0x040300 = NVIDIA audio device — not a display.
    _mk_pci_dev(tmp_path, "0000:01:00.1", "0x10de", "0x040300")
    assert mod.has_nvidia_gpu(str(tmp_path)) is False


# --- read_mem_total_kib -----------------------------------------

def test_read_mem_total_kib(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text("MemTotal:       98304000 kB\nMemFree: 1\n")
    assert mod.read_mem_total_kib(str(p)) == 98304000


def test_read_mem_total_kib_missing(tmp_path):
    assert mod.read_mem_total_kib(str(tmp_path / "nope")) is None


# --- read_swaps -------------------------------------------------

SWAPS_FILE = ("Filename\t\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n"
              "/dev/sda2                               partition\t"
              "8388604\t\t0\t\t-2\n")


def test_read_swaps(tmp_path):
    sb = tmp_path / "block"
    _mk_block_dev(sb, "sda", rotational=0)
    p = tmp_path / "swaps"
    p.write_text(SWAPS_FILE)
    out = mod.read_swaps(str(p), str(sb))
    assert len(out) == 1
    assert out[0]["device"] == "sda"
    assert out[0]["rotational"] == 0


def test_read_swaps_file_backed_fallback(tmp_path):
    sb = tmp_path / "block"
    _mk_block_dev(sb, "sda", rotational=0)
    txt = ("Filename\t\t\tType\tSize\tUsed\tPriority\n"
              "/swap.img\tfile\t8388604\t0\t-2\n")
    p = tmp_path / "swaps"
    p.write_text(txt)
    out = mod.read_swaps(str(p), str(sb))
    assert len(out) == 1
    assert out[0]["path"] == "/swap.img"
    # file-backed swap fallbacks to first non-loop/zram block dev
    assert out[0]["device"] == "sda"


# --- classify ---------------------------------------------------

def _vm(swappiness=60, page_cluster=3, min_free=67584):
    return {"available": True,
              "swappiness": swappiness,
              "page-cluster": page_cluster,
              "watermark_scale_factor": 10,
              "watermark_boost_factor": 15000,
              "min_free_kbytes": min_free,
              "extfrag_threshold": 500}


def _swap_sda():
    return {"path": "/dev/sda2", "type": "partition",
              "size_kib": 8388604, "used_kib": 0,
              "device": "sda", "rotational": 0}


def _swap_hdd():
    return {"path": "/dev/sdb1", "type": "partition",
              "size_kib": 8388604, "used_kib": 100,
              "device": "sdb", "rotational": 1}


def test_classify_unknown():
    v = mod.classify({"available": False}, {"available": False},
                       [], [], False, None)
    assert v["verdict"] == "unknown"


def test_classify_ok():
    # min_free 1 GiB / 32 GiB MemTotal = 3.1 % → above 0.5 %
    v = mod.classify(_vm(swappiness=10, min_free=1024 * 1024),
                       {"available": True},
                       [_swap_sda()], [], False, 32 * 1024 * 1024)
    assert v["verdict"] == "ok"


def test_classify_swap_on_hdd():
    v = mod.classify(_vm(), {"available": True}, [_swap_hdd()],
                       [], False, 32 * 1024 * 1024)
    assert v["verdict"] == "swap_on_hdd"


def test_classify_high_swappiness_with_gpu():
    v = mod.classify(_vm(swappiness=60), {"available": True},
                       [_swap_sda()], [], True,
                       32 * 1024 * 1024)
    assert v["verdict"] == "high_swappiness_with_gpu"


def test_classify_tiny_min_free():
    # 32 KiB out of 32 GiB → way under 0.5 %
    v = mod.classify(_vm(swappiness=10, min_free=32),
                       {"available": True}, [_swap_sda()],
                       [], False, 32 * 1024 * 1024)
    assert v["verdict"] == "tiny_min_free"


def test_classify_page_cluster_zram():
    v = mod.classify(_vm(swappiness=10, page_cluster=3,
                            min_free=1024 * 1024),
                       {"available": True}, [_swap_sda()],
                       ["zram0"], False, 32 * 1024 * 1024)
    assert v["verdict"] == "page_cluster_default_on_zram"


def test_classify_priority_hdd_wins():
    v = mod.classify(_vm(swappiness=60, page_cluster=3),
                       {"available": True}, [_swap_hdd()],
                       ["zram0"], True, 32 * 1024 * 1024)
    assert v["verdict"] == "swap_on_hdd"


# --- status integration -----------------------------------------

def test_status_unknown_vm(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "novm"),
                       str(tmp_path / "noswap"),
                       str(tmp_path / "noswaps"),
                       str(tmp_path / "noblock"),
                       str(tmp_path / "nomem"),
                       str(tmp_path / "nopci"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like_zram(tmp_path):
    vm = tmp_path / "vm"
    _mk_vm(vm, swappiness=10, min_free_kbytes=1048576)
    (vm / "page-cluster").write_text("3\n")
    mm = tmp_path / "mm"
    mm.mkdir()
    (mm / "vma_ra_enabled").write_text("true\n")
    swaps = tmp_path / "swaps"
    swaps.write_text(SWAPS_FILE)
    block = tmp_path / "block"
    _mk_block_dev(block, "sda", rotational=0)
    _mk_zram(block, "zram0", disksize=8000000000)
    mem = tmp_path / "meminfo"
    mem.write_text("MemTotal: 96000000 kB\n")
    pci = tmp_path / "pci"
    pci.mkdir()
    out = mod.status(None, str(vm), str(mm), str(swaps),
                       str(block), str(mem), str(pci))
    assert out["ok"] is True
    assert out["zram_active"] == ["zram0"]
    assert out["verdict"]["verdict"] == "page_cluster_default_on_zram"
