"""Tests for modules/proc_static_kernel_registry_audit.py — R&D #69.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import (
    proc_static_kernel_registry_audit as mod
)


# --- parse_modules ----------------------------------------------

def test_parse_modules_empty():
    assert mod.parse_modules("") == []
    assert mod.parse_modules(None) == []


def test_parse_modules_clean():
    text = "xt_conntrack 12288 1 - Live 0x0000000000000000\n"
    out = mod.parse_modules(text)
    assert len(out) == 1
    assert out[0]["name"] == "xt_conntrack"
    assert out[0]["size"] == 12288
    assert out[0]["flags"] == ""


def test_parse_modules_oot():
    text = ("nvidia_uvm 2166784 4 - Live 0x0000000000000000 (OE)\n"
              "i915 1234 0 - Live 0x0000 (POE)\n")
    out = mod.parse_modules(text)
    assert out[0]["flags"] == "OE"
    assert out[1]["flags"] == "POE"


# --- parse_devices ----------------------------------------------

def test_parse_devices_basic():
    text = ("Character devices:\n"
              "  1 mem\n"
              "  4 tty\n"
              "\n"
              "Block devices:\n"
              "  8 sd\n"
              "259 blkext\n")
    out = mod.parse_devices(text)
    assert len(out["character"]) == 2
    assert out["character"][0]["major"] == 1
    assert out["character"][0]["name"] == "mem"
    assert len(out["block"]) == 2


def test_parse_devices_empty():
    assert mod.parse_devices(None) == {"character": [],
                                              "block": []}


# --- parse_filesystems ------------------------------------------

def test_parse_filesystems():
    text = ("nodev\tsysfs\n"
              "nodev\ttmpfs\n"
              "\text4\n")
    out = mod.parse_filesystems(text)
    assert set(out) == {"sysfs", "tmpfs", "ext4"}


# --- parse_consoles --------------------------------------------

def test_parse_consoles():
    text = ("tty0                 -WU (EC  p  )    4:2\n"
              "ttyS0                -W- (E   p  )    4:64\n")
    out = mod.parse_consoles(text)
    assert len(out) == 2
    # column-2 contains flags; 'E' implies enabled, '-' disabled
    assert out[0]["name"] == "tty0"
    # tty0 has '-WU' so no E in column 2 → disabled
    assert out[0]["enabled"] is False


def test_parse_consoles_enabled():
    text = "tty0                 EW  (EC  p  )    4:2\n"
    out = mod.parse_consoles(text)
    assert out[0]["enabled"] is True


# --- parse_misc -------------------------------------------------

def test_parse_misc():
    text = "232 kvm\n262 vsock\n"
    out = mod.parse_misc(text)
    assert out == [{"minor": 232, "name": "kvm"},
                       {"minor": 262, "name": "vsock"}]


# --- classify ---------------------------------------------------

def _readable():
    return {"modules": True, "devices": True, "misc": True,
              "filesystems": True, "consoles": True}


def test_classify_unknown_unreadable():
    r = _readable()
    r["modules"] = False
    v = mod.classify([], {"character": [], "block": []},
                          ["proc", "sysfs", "tmpfs", "devtmpfs",
                            "cgroup2"],
                          [{"name": "tty0", "flags": "EW",
                              "enabled": True}],
                          r)
    assert v["verdict"] == "unknown"


def test_classify_oot_module():
    v = mod.classify(
        [{"name": "nvidia", "flags": "OE", "state": "Live",
            "size": 14_000_000, "refcnt": 74}],
        {"character": [], "block": []},
        ["proc", "sysfs", "tmpfs", "devtmpfs", "cgroup2"],
        [{"name": "tty0", "flags": "EW", "enabled": True}],
        _readable())
    assert v["verdict"] == "out_of_tree_tainting_module_loaded"
    assert "nvidia" in v["reason"]


def test_classify_dup_major():
    v = mod.classify(
        [{"name": "xt_x", "flags": ""}],
        {"character": [{"major": 5, "name": "tty"},
                            {"major": 5, "name": "tty2"}],
          "block": []},
        ["proc", "sysfs", "tmpfs", "devtmpfs", "cgroup2"],
        [{"name": "tty0", "flags": "EW", "enabled": True}],
        _readable())
    assert v["verdict"] == "duplicate_or_orphan_major"


def test_classify_missing_required_fs():
    v = mod.classify(
        [{"name": "xt_x", "flags": ""}],
        {"character": [], "block": []},
        ["proc", "sysfs"],   # missing tmpfs, devtmpfs, cgroup2
        [{"name": "tty0", "flags": "EW", "enabled": True}],
        _readable())
    assert v["verdict"] == "missing_required_fs_for_gpu_stack"


def test_classify_console_misroute():
    v = mod.classify(
        [{"name": "xt_x", "flags": ""}],
        {"character": [], "block": []},
        ["proc", "sysfs", "tmpfs", "devtmpfs", "cgroup2"],
        [{"name": "tty0", "flags": "-WU", "enabled": False}],
        _readable())
    assert v["verdict"] == "stale_console_misroute"


def test_classify_ok():
    v = mod.classify(
        [{"name": "xt_x", "flags": ""}],
        {"character": [{"major": 4, "name": "tty"}],
          "block": [{"major": 8, "name": "sd"}]},
        ["proc", "sysfs", "tmpfs", "devtmpfs", "cgroup2"],
        [{"name": "tty0", "flags": "EW", "enabled": True}],
        _readable())
    assert v["verdict"] == "ok"


# Priority : oot > dup > missing fs > console
def test_priority_oot_over_dup():
    v = mod.classify(
        [{"name": "nvidia", "flags": "OE"}],
        {"character": [{"major": 5, "name": "tty"},
                            {"major": 5, "name": "tty2"}],
          "block": []},
        ["proc", "sysfs", "tmpfs", "devtmpfs", "cgroup2"],
        [{"name": "tty0", "flags": "EW", "enabled": True}],
        _readable())
    assert v["verdict"] == "out_of_tree_tainting_module_loaded"


# --- status integration -----------------------------------------

def test_status_synthetic_clean(tmp_path):
    files = {
        "modules": tmp_path / "modules",
        "devices": tmp_path / "devices",
        "misc": tmp_path / "misc",
        "filesystems": tmp_path / "filesystems",
        "consoles": tmp_path / "consoles",
    }
    files["modules"].write_text(
        "xt_x 1000 0 - Live 0x0000\n")
    files["devices"].write_text(
        "Character devices:\n  1 mem\n  4 tty\n"
        "Block devices:\n  8 sd\n")
    files["misc"].write_text("232 kvm\n")
    files["filesystems"].write_text(
        "nodev\tproc\nnodev\tsysfs\nnodev\ttmpfs\n"
        "nodev\tdevtmpfs\nnodev\tcgroup2\n")
    files["consoles"].write_text(
        "tty0                 EW  (E   p  )    4:2\n")
    out = mod.status(None,
                          str(files["modules"]),
                          str(files["devices"]),
                          str(files["misc"]),
                          str(files["filesystems"]),
                          str(files["consoles"]))
    assert out["ok"] is True
    assert out["module_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_synthetic_oot(tmp_path):
    f_mod = tmp_path / "modules"
    f_mod.write_text(
        "nvidia 14000000 74 - Live 0x0000 (OE)\n")
    f_dev = tmp_path / "devices"
    f_dev.write_text("Character devices:\n  1 mem\n"
                            "Block devices:\n  8 sd\n")
    f_misc = tmp_path / "misc"; f_misc.write_text("")
    f_fs = tmp_path / "filesystems"
    f_fs.write_text("nodev\tproc\nnodev\tsysfs\n"
                          "nodev\ttmpfs\nnodev\tdevtmpfs\n"
                          "nodev\tcgroup2\n")
    f_cons = tmp_path / "consoles"
    f_cons.write_text("tty0 EW (EC p) 4:2\n")
    out = mod.status(None, str(f_mod), str(f_dev),
                          str(f_misc), str(f_fs), str(f_cons))
    assert out["verdict"]["verdict"] == \
        "out_of_tree_tainting_module_loaded"
    assert out["tainting_module_count"] == 1
