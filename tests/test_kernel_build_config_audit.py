"""Tests for modules/kernel_build_config_audit.py — R&D #58.2."""
from __future__ import annotations

import gzip
import pytest

from gpu_dashboard.modules import kernel_build_config_audit as mod


CONFIG_HEALTHY = """\
# comment line
CONFIG_PREEMPT_VOLUNTARY=y
CONFIG_PREEMPT_DYNAMIC=y
CONFIG_HZ=1000
CONFIG_NO_HZ_FULL=y
CONFIG_TRANSPARENT_HUGEPAGE=y
CONFIG_TRANSPARENT_HUGEPAGE_MADVISE=y
CONFIG_NUMA_BALANCING=y
CONFIG_RANDOMIZE_BASE=y
CONFIG_DEBUG_KERNEL=y
"""

CONFIG_PREEMPT_NONE = """\
CONFIG_PREEMPT_NONE=y
CONFIG_HZ=250
CONFIG_TRANSPARENT_HUGEPAGE=y
CONFIG_TRANSPARENT_HUGEPAGE_MADVISE=y
"""

CONFIG_DEBUG = """\
CONFIG_PREEMPT_VOLUNTARY=y
CONFIG_HZ=1000
CONFIG_DEBUG_PAGEALLOC=y
CONFIG_DEBUG_VM=y
"""

CONFIG_THP_ALWAYS = """\
CONFIG_PREEMPT_VOLUNTARY=y
CONFIG_HZ=1000
CONFIG_TRANSPARENT_HUGEPAGE=y
CONFIG_TRANSPARENT_HUGEPAGE_ALWAYS=y
"""


# --- parse_config -----------------------------------------------

def test_parse_config_empty():
    assert mod.parse_config("") == {}
    assert mod.parse_config(None) == {}


def test_parse_config_healthy():
    out = mod.parse_config(CONFIG_HEALTHY)
    assert out["CONFIG_PREEMPT_VOLUNTARY"] == "y"
    assert out["CONFIG_HZ"] == "1000"
    assert out["CONFIG_DEBUG_KERNEL"] == "y"
    # Comments + uninteresting keys aren't included
    assert "# comment line" not in out


# --- read_config ------------------------------------------------

def test_read_config_from_boot(tmp_path):
    cfg = tmp_path / "config-test"
    cfg.write_text(CONFIG_HEALTHY)
    text = mod.read_config(
        release="test",
        boot_config_fmt=str(tmp_path / "config-{release}"),
        proc_config_gz=str(tmp_path / "noproc.gz"))
    assert text == CONFIG_HEALTHY


def test_read_config_from_proc_gz(tmp_path):
    gz_path = tmp_path / "config.gz"
    with gzip.open(gz_path, "wt") as f:
        f.write(CONFIG_HEALTHY)
    text = mod.read_config(
        release="missing",
        boot_config_fmt=str(tmp_path / "noboot-{release}"),
        proc_config_gz=str(gz_path))
    assert text == CONFIG_HEALTHY


def test_read_config_neither(tmp_path):
    text = mod.read_config(
        release="missing",
        boot_config_fmt=str(tmp_path / "noboot-{release}"),
        proc_config_gz=str(tmp_path / "noproc.gz"))
    assert text is None


# --- classify ---------------------------------------------------

def test_classify_unknown():
    v = mod.classify({})
    assert v["verdict"] == "unknown"


def test_classify_ok():
    v = mod.classify(mod.parse_config(CONFIG_HEALTHY))
    assert v["verdict"] == "ok"


def test_classify_debug():
    v = mod.classify(mod.parse_config(CONFIG_DEBUG))
    assert v["verdict"] == "debug_kernel_in_use"


def test_classify_preempt_none():
    v = mod.classify(mod.parse_config(CONFIG_PREEMPT_NONE))
    assert v["verdict"] == "preempt_none_for_desktop"


def test_classify_thp_always():
    v = mod.classify(mod.parse_config(CONFIG_THP_ALWAYS))
    assert v["verdict"] == "thp_madvise_default_mismatch"


def test_classify_priority_debug_wins():
    cfg = mod.parse_config(CONFIG_DEBUG)
    cfg.update(mod.parse_config(CONFIG_PREEMPT_NONE))
    cfg.update(mod.parse_config(CONFIG_THP_ALWAYS))
    v = mod.classify(cfg)
    assert v["verdict"] == "debug_kernel_in_use"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, release="x",
                       boot_config_fmt=str(
                           tmp_path / "nope-{release}"),
                       proc_config_gz=str(tmp_path / "nope.gz"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    cfg = tmp_path / "config-test"
    cfg.write_text(CONFIG_HEALTHY)
    out = mod.status(None, release="test",
                       boot_config_fmt=str(
                           tmp_path / "config-{release}"),
                       proc_config_gz=str(tmp_path / "nope.gz"))
    assert out["ok"] is True
    assert out["release"] == "test"
    assert out["verdict"]["verdict"] == "ok"
