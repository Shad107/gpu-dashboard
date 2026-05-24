"""Tests for modules/interrupt_skew_audit.py — R&D #87.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import interrupt_skew_audit as mod


_HEADER = (
    "           CPU0       CPU1       CPU2       CPU3\n")


def _mk_interrupts(tmp_path, irqs):
    p = tmp_path / "interrupts"
    text = _HEADER
    for irq in irqs:
        text += (f"{irq:>4}:        100        200        300"
                 "        400   IR-PCI-MSI  enp0s1\n")
    text += " NMI:          0          0          0          0\n"
    text += " LOC:        500        500        500        500\n"
    p.write_text(text)
    return str(p)


def _mk_irq_dir(tmp_path, irq, *, hint=None, smp=None):
    d = tmp_path / "irq" / str(irq)
    d.mkdir(parents=True, exist_ok=True)
    if hint is not None:
        (d / "affinity_hint").write_text(hint + "\n")
    if smp is not None:
        (d / "smp_affinity_list").write_text(smp + "\n")
    return d


# --- parse_irq_numbers -----------------------------------------

def test_parse_empty():
    assert mod.parse_irq_numbers("") == []


def test_parse_skips_header_and_symbolic():
    text = (_HEADER
            + " 16:  1  2  3  4 IR-PCI-MSI nvme0q0\n"
            + " 17:  1  2  3  4 IR-PCI-MSI ahci\n"
            + " NMI: 0 0 0 0\n"
            + " LOC: 9 9 9 9\n")
    assert mod.parse_irq_numbers(text) == [16, 17]


def test_parse_large_irq_numbers():
    text = (_HEADER
            + " 256:  1 2 3 4 IR-PCI-MSI dev\n"
            + "1024:  1 2 3 4 IR-PCI-MSI dev\n")
    assert mod.parse_irq_numbers(text) == [256, 1024]


# --- parse_hex_mask --------------------------------------------

def test_parse_hex_mask_empty():
    assert mod.parse_hex_mask("") == set()
    assert mod.parse_hex_mask("00000000") == set()


def test_parse_hex_mask_simple():
    # 0xff = CPUs 0..7
    assert mod.parse_hex_mask("ff") == set(range(8))


def test_parse_hex_mask_with_commas():
    # 64-bit mask: 'ffffffff,ffffffff' = CPUs 0..63
    assert mod.parse_hex_mask(
        "ffffffff,ffffffff") == set(range(64))


def test_parse_hex_mask_high_bit():
    # 0x10000 = CPU 16 only
    assert mod.parse_hex_mask("00010000") == {16}


def test_parse_hex_mask_garbage():
    assert mod.parse_hex_mask("zzzzz") == set()


# --- parse_cpu_list --------------------------------------------

def test_parse_cpu_list_empty():
    assert mod.parse_cpu_list("") == set()


def test_parse_cpu_list_range_and_single():
    assert mod.parse_cpu_list("0-3,8,10-11") == {
        0, 1, 2, 3, 8, 10, 11}


def test_parse_cpu_list_garbage_token_skipped():
    assert mod.parse_cpu_list("0-3,zz,5") == {0, 1, 2, 3, 5}


# --- read_irq_pair ---------------------------------------------

def test_read_irq_pair_missing(tmp_path):
    pair = mod.read_irq_pair(str(tmp_path / "nope"), 16)
    assert pair == {"hint": set(), "smp": set()}


def test_read_irq_pair_full(tmp_path):
    irq_root = tmp_path / "irq"
    _mk_irq_dir(tmp_path, 16, hint="00000003", smp="0-1")
    pair = mod.read_irq_pair(str(irq_root), 16)
    assert pair["hint"] == {0, 1}
    assert pair["smp"] == {0, 1}


# --- find_mismatches -------------------------------------------

def test_find_mismatches_none(tmp_path):
    irq_root = tmp_path / "irq"
    _mk_irq_dir(tmp_path, 16, hint="00000003", smp="0-1")
    _mk_irq_dir(tmp_path, 17, hint="0000000c", smp="2-3")
    mismatches = mod.find_mismatches(str(irq_root), [16, 17])
    assert mismatches == []


def test_find_mismatches_hint_disjoint(tmp_path):
    irq_root = tmp_path / "irq"
    # IRQ 16: hint=CPU0 but smp=2-3 → disjoint
    _mk_irq_dir(tmp_path, 16, hint="00000001", smp="2-3")
    # IRQ 17: hint=CPU2 and smp=2-3 → overlap, no mismatch
    _mk_irq_dir(tmp_path, 17, hint="00000004", smp="2-3")
    # IRQ 18: no hint → skipped
    _mk_irq_dir(tmp_path, 18, hint="00000000", smp="0-3")
    mismatches = mod.find_mismatches(
        str(irq_root), [16, 17, 18])
    irqs = sorted(m[0] for m in mismatches)
    assert irqs == [16]


# --- classify --------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], [])
    assert v["verdict"] == "unknown"


def test_classify_ok_no_mismatches():
    v = mod.classify([16, 17, 18], [])
    assert v["verdict"] == "ok"


def test_classify_mismatch_small():
    mismatches = [(16, {0}, {2, 3}), (17, {1}, {2, 3})]
    v = mod.classify([16, 17, 18], mismatches)
    assert v["verdict"] == "affinity_hint_mismatch"
    assert v["mismatch_count"] == 2


def test_classify_mismatch_widely_overridden():
    mismatches = [(16 + i, {i}, {7}) for i in range(5)]
    v = mod.classify(list(range(16, 21)), mismatches)
    assert v["verdict"] == "affinity_hint_widely_overridden"
    assert v["mismatch_count"] == 5


# Priority : widely_overridden > mismatch > ok > unknown
def test_priority_widely_overridden_over_mismatch():
    mismatches = [(20 + i, {i}, {0}) for i in range(6)]
    v = mod.classify(list(range(20, 30)), mismatches)
    assert v["verdict"] == "affinity_hint_widely_overridden"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                     str(tmp_path / "nope_interrupts"),
                     str(tmp_path / "nope_irq"))
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    interrupts = _mk_interrupts(tmp_path, [16, 17])
    _mk_irq_dir(tmp_path, 16, hint="00000003", smp="0-1")
    _mk_irq_dir(tmp_path, 17, hint="0000000c", smp="2-3")
    out = mod.status(None, interrupts, str(tmp_path / "irq"))
    assert out["irq_count"] == 2
    assert out["mismatch_count"] == 0
    assert out["verdict"]["verdict"] == "ok"


def test_status_mismatch_synthetic(tmp_path):
    interrupts = _mk_interrupts(tmp_path, [16, 17, 18])
    _mk_irq_dir(tmp_path, 16, hint="00000001", smp="2-3")
    _mk_irq_dir(tmp_path, 17, hint="00000004", smp="2-3")
    _mk_irq_dir(tmp_path, 18, hint="00000000", smp="0-3")
    out = mod.status(None, interrupts, str(tmp_path / "irq"))
    assert out["mismatch_count"] == 1
    assert (out["verdict"]["verdict"]
            == "affinity_hint_mismatch")


def test_status_widely_overridden_synthetic(tmp_path):
    irqs = list(range(16, 22))
    interrupts = _mk_interrupts(tmp_path, irqs)
    for i, irq in enumerate(irqs):
        hint_mask = f"{(1 << i):08x}"
        _mk_irq_dir(tmp_path, irq, hint=hint_mask, smp="7")
    out = mod.status(None, interrupts, str(tmp_path / "irq"))
    assert out["mismatch_count"] >= 5
    assert (out["verdict"]["verdict"]
            == "affinity_hint_widely_overridden")
    assert out["ok"] is False
