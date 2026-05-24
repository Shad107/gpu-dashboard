"""Tests for modules/fb_vtconsole_audit.py — R&D #79.3."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import fb_vtconsole_audit as mod


def _mk_proc_fb(tmp_path, entries):
    """entries: list of (id, name)"""
    p = tmp_path / "fb"
    p.write_text(
        "\n".join(f"{i} {n}" for i, n in entries) + "\n")
    return str(p)


def _mk_graphics(tmp_path, devs):
    """devs: {fb_name: driver_name}"""
    g = tmp_path / "graphics"
    g.mkdir(parents=True, exist_ok=True)
    for fb_name, drv in devs.items():
        d = g / fb_name
        d.mkdir(exist_ok=True)
        (d / "name").write_text(drv + "\n")
    return str(g)


def _mk_vtcons(tmp_path, vtcons):
    """vtcons: {node_name: (name, bind)}"""
    v = tmp_path / "vtconsole"
    v.mkdir(parents=True, exist_ok=True)
    for node, (name, bind) in vtcons.items():
        d = v / node
        d.mkdir(exist_ok=True)
        (d / "name").write_text(name + "\n")
        (d / "bind").write_text(f"{bind}\n")
    return str(v)


# --- read_proc_fb ----------------------------------------------

def test_read_proc_fb_missing(tmp_path):
    assert mod.read_proc_fb(str(tmp_path / "nope")) is None


def test_read_proc_fb_one(tmp_path):
    p = _mk_proc_fb(tmp_path, [(0, "nvidia-drmfb")])
    out = mod.read_proc_fb(p)
    assert out == [{"id": 0, "name": "nvidia-drmfb"}]


def test_read_proc_fb_multi(tmp_path):
    p = _mk_proc_fb(tmp_path, [(0, "efifb"),
                                  (1, "nvidia-drmfb")])
    out = mod.read_proc_fb(p)
    assert len(out) == 2
    assert out[1]["name"] == "nvidia-drmfb"


# --- read_graphics_devs ----------------------------------------

def test_read_graphics_missing(tmp_path):
    assert mod.read_graphics_devs(
        str(tmp_path / "nope")) == []


def test_read_graphics(tmp_path):
    g = _mk_graphics(tmp_path, {"fb0": "nvidia-drmfb",
                                    "fbcon": "fbcon"})
    out = mod.read_graphics_devs(g)
    assert len(out) == 2
    by_node = {d["node"]: d for d in out}
    assert by_node["fb0"]["name"] == "nvidia-drmfb"


# --- read_vtcons -----------------------------------------------

def test_read_vtcons_missing(tmp_path):
    assert mod.read_vtcons(str(tmp_path / "nope")) == []


def test_read_vtcons(tmp_path):
    v = _mk_vtcons(tmp_path, {
        "vtcon0": ("(S) dummy device", 0),
        "vtcon1": ("(M) frame buffer device", 1),
    })
    out = mod.read_vtcons(v)
    assert len(out) == 2
    assert out[1]["bind"] == 1
    assert "frame buffer" in out[1]["name"]


# --- classify --------------------------------------------------

def test_classify_unknown_no_proc_fb():
    v = mod.classify(None, [])
    assert v["verdict"] == "unknown"


def test_classify_headless():
    v = mod.classify([], [])
    assert v["verdict"] == "ok"


def test_classify_efifb_owns_console():
    v = mod.classify(
        [{"id": 0, "name": "efifb"},
         {"id": 1, "name": "nvidia-drmfb"}],
        [{"node": "vtcon1", "name": "(M) frame buffer device",
            "bind": 1}])
    assert v["verdict"] == "efifb_owns_console"


def test_classify_simpledrm_owns_console():
    v = mod.classify(
        [{"id": 0, "name": "simpledrm"},
         {"id": 1, "name": "amdgpudrmfb"}],
        [])
    assert v["verdict"] == "efifb_owns_console"


def test_classify_vesafb():
    v = mod.classify(
        [{"id": 0, "name": "vesafb"}], [])
    assert v["verdict"] == "vesafb_fallback"


def test_classify_only_firmware_fb():
    # efifb alone, no DRM → graphics stack incomplete
    v = mod.classify(
        [{"id": 0, "name": "efifb"}], [])
    assert v["verdict"] == "vesafb_fallback"


def test_classify_wrong_fb_bound():
    # 2 fbs, no fb vtcon bound
    v = mod.classify(
        [{"id": 0, "name": "nvidia-drmfb"},
         {"id": 1, "name": "i915drmfb"}],
        [{"node": "vtcon0", "name": "(S) dummy device",
            "bind": 0},
         {"node": "vtcon1", "name": "(M) frame buffer device",
            "bind": 0}])
    assert v["verdict"] == "wrong_fb_bound"


def test_classify_multi_drm_ok():
    # 2 GPU drm fbs and console properly bound
    v = mod.classify(
        [{"id": 0, "name": "nvidia-drmfb"},
         {"id": 1, "name": "i915drmfb"}],
        [{"node": "vtcon1", "name": "(M) frame buffer device",
            "bind": 1}])
    assert v["verdict"] == "multi_fb_ok"


def test_classify_ok_single_drm():
    v = mod.classify(
        [{"id": 0, "name": "nvidia-drmfb"}],
        [{"node": "vtcon0", "name": "(S) dummy device",
            "bind": 0},
         {"node": "vtcon1", "name": "(M) frame buffer device",
            "bind": 1}])
    assert v["verdict"] == "ok"
    assert v["fb_name"] == "nvidia-drmfb"


def test_classify_ok_virtio():
    v = mod.classify(
        [{"id": 0, "name": "virtio_gpudrmfb"}],
        [{"node": "vtcon1", "name": "(M) frame buffer device",
            "bind": 1}])
    assert v["verdict"] == "ok"


# Priority : efifb_owns > vesafb > wrong_bound > multi > ok
def test_priority_efifb_over_vesafb():
    # efifb + nvidia-drmfb → efifb_owns wins
    v = mod.classify(
        [{"id": 0, "name": "efifb"},
         {"id": 1, "name": "vesafb"},
         {"id": 2, "name": "nvidia-drmfb"}], [])
    assert v["verdict"] == "efifb_owns_console"


# --- status integration ----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None,
                       str(tmp_path / "nope_fb"),
                       str(tmp_path / "nope_g"),
                       str(tmp_path / "nope_v"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_ok_synthetic(tmp_path):
    pfb = _mk_proc_fb(tmp_path,
                        [(0, "nvidia-drmfb")])
    g = _mk_graphics(tmp_path, {"fb0": "nvidia-drmfb"})
    v = _mk_vtcons(tmp_path, {
        "vtcon1": ("(M) frame buffer device", 1)})
    out = mod.status(None, pfb, g, v)
    assert out["ok"] is True
    assert out["fb_count"] == 1
    assert out["verdict"]["verdict"] == "ok"


def test_status_efifb_owns(tmp_path):
    pfb = _mk_proc_fb(tmp_path, [
        (0, "efifb"), (1, "nvidia-drmfb")])
    g = _mk_graphics(tmp_path, {"fb0": "efifb"})
    v = _mk_vtcons(tmp_path, {})
    out = mod.status(None, pfb, g, v)
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "efifb_owns_console"
