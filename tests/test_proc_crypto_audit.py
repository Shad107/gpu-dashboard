"""Tests for modules/proc_crypto_audit.py — R&D #56.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import proc_crypto_audit as mod


CRYPTO_AESNI = """\
name         : aes
driver       : aes-aesni
module       : aesni_intel
priority     : 300
refcnt       : 1
selftest     : passed
internal     : no
type         : cipher

name         : aes
driver       : aes-generic
module       : kernel
priority     : 100
refcnt       : 1
selftest     : passed
internal     : no
type         : cipher
"""

CRYPTO_GENERIC_ONLY = """\
name         : aes
driver       : aes-generic
module       : kernel
priority     : 100
refcnt       : 1
selftest     : passed
internal     : no
type         : cipher

name         : sha256
driver       : sha256-generic
module       : kernel
priority     : 100
refcnt       : 1
selftest     : passed
internal     : no
type         : shash
"""

CRYPTO_FAIL = """\
name         : aes
driver       : aes-aesni
module       : aesni_intel
priority     : 300
refcnt       : 1
selftest     : failed
internal     : no
type         : cipher
"""


# --- parse_crypto -----------------------------------------------

def test_parse_crypto_empty():
    assert mod.parse_crypto("") == []
    assert mod.parse_crypto(None) == []


def test_parse_crypto_aesni():
    out = mod.parse_crypto(CRYPTO_AESNI)
    assert len(out) == 2
    assert out[0]["name"] == "aes"
    assert out[0]["driver"] == "aes-aesni"
    assert out[1]["driver"] == "aes-generic"


def test_parse_crypto_generic_only():
    out = mod.parse_crypto(CRYPTO_GENERIC_ONLY)
    assert any(e.get("name") == "aes" for e in out)


def test_parse_crypto_failed():
    out = mod.parse_crypto(CRYPTO_FAIL)
    assert out[0]["selftest"] == "failed"


# --- has_aesni_cpu ----------------------------------------------

def test_has_aesni_cpu_yes(tmp_path):
    p = tmp_path / "cpuinfo"
    p.write_text(
        "processor       : 0\n"
        "flags           : fpu vme aes pclmulqdq sse4_2\n")
    assert mod.has_aesni_cpu(str(p)) is True


def test_has_aesni_cpu_no(tmp_path):
    p = tmp_path / "cpuinfo"
    p.write_text(
        "processor       : 0\n"
        "flags           : fpu vme sse4_2\n")
    assert mod.has_aesni_cpu(str(p)) is False


def test_has_aesni_cpu_arm(tmp_path):
    p = tmp_path / "cpuinfo"
    p.write_text("Features        : aes pmull\n")
    assert mod.has_aesni_cpu(str(p)) is True


def test_has_aesni_cpu_missing(tmp_path):
    assert mod.has_aesni_cpu(str(tmp_path / "nope")) is False


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify([], None, False)
    assert v["verdict"] == "unknown"


def test_classify_ok_aesni():
    entries = mod.parse_crypto(CRYPTO_AESNI)
    v = mod.classify(entries, 0, True)
    assert v["verdict"] == "ok"


def test_classify_fips_on():
    entries = mod.parse_crypto(CRYPTO_AESNI)
    v = mod.classify(entries, 1, True)
    assert v["verdict"] == "fips_mode_on"


def test_classify_selftest_failed():
    entries = mod.parse_crypto(CRYPTO_FAIL)
    v = mod.classify(entries, 0, True)
    assert v["verdict"] == "selftest_failed_entry"


def test_classify_aesni_missing_but_aes_used():
    entries = mod.parse_crypto(CRYPTO_GENERIC_ONLY)
    v = mod.classify(entries, 0, True)  # CPU has AES-NI
    assert v["verdict"] == "aesni_missing_but_aes_used"


def test_classify_generic_only_no_cpu():
    entries = mod.parse_crypto(CRYPTO_GENERIC_ONLY)
    v = mod.classify(entries, 0, False)  # CPU has no AES-NI
    assert v["verdict"] == "generic_only_aes"


def test_classify_priority_fips_wins():
    entries = mod.parse_crypto(CRYPTO_FAIL)
    v = mod.classify(entries, 1, True)
    assert v["verdict"] == "fips_mode_on"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope1"),
                       str(tmp_path / "nope2"),
                       str(tmp_path / "nope3"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    pc = tmp_path / "crypto"
    pc.write_text(CRYPTO_AESNI)
    pf = tmp_path / "fips"
    pf.write_text("0\n")
    ci = tmp_path / "cpuinfo"
    ci.write_text(
        "processor : 0\n"
        "flags : aes pclmulqdq sse4_2\n")
    out = mod.status(None, str(pc), str(pf), str(ci))
    assert out["ok"] is True
    assert out["entry_count"] == 2
    assert out["fips_enabled"] == 0
    assert out["cpu_has_aes_flag"] is True
    assert out["verdict"]["verdict"] == "ok"
