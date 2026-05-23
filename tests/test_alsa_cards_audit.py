"""Tests for modules/alsa_cards_audit.py — R&D #58.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import alsa_cards_audit as mod


CARDS_TEXT = """\
 0 [Intel          ]: HDA-Intel - HDA Intel
                      HDA Intel at 0x82880000 irq 76
 1 [NVidia         ]: HDA-Intel - HDA NVidia
                      HDA NVidia at 0x81e00000 irq 23
"""

MODULES_TEXT = """\
 0 snd_hda_intel
 1 snd_hda_intel
"""


# --- parse_cards ------------------------------------------------

def test_parse_cards_basic():
    out = mod.parse_cards(CARDS_TEXT)
    assert len(out) == 2
    assert out[0]["id"] == "Intel"
    assert out[1]["id"] == "NVidia"


def test_parse_cards_empty():
    assert mod.parse_cards("") == []
    assert mod.parse_cards(None) == []


# --- parse_modules ----------------------------------------------

def test_parse_modules():
    out = mod.parse_modules(MODULES_TEXT)
    assert out == {0: "snd_hda_intel", 1: "snd_hda_intel"}


# --- is_nvidia_card ---------------------------------------------

def test_is_nvidia_card():
    assert mod.is_nvidia_card({"name": "HDA NVidia",
                                  "id": "NVidia"}) is True
    assert mod.is_nvidia_card({"name": "HDA Intel",
                                  "id": "Intel"}) is False


# --- classify ---------------------------------------------------

def _card(idx=0, id_="NVidia", driver="HDA-Intel",
            name="HDA NVidia"):
    return {"index": idx, "id": id_, "driver": driver,
              "name": name}


def test_classify_unknown():
    v = mod.classify([], {}, {}, {})
    assert v["verdict"] == "unknown"


def test_classify_ok_nvidia_auto():
    v = mod.classify([_card(0, "NVidia", name="HDA NVidia")],
                       {0: "auto"}, {0: "snd_hda_intel"},
                       {0: ["pcmC0D0p"]})
    assert v["verdict"] == "ok"


def test_classify_gpu_pm_off():
    v = mod.classify([_card(0, "NVidia", name="HDA NVidia")],
                       {0: "on"}, {0: "snd_hda_intel"},
                       {0: ["pcmC0D0p"]})
    assert v["verdict"] == "gpu_hda_runtime_pm_off"


def test_classify_intel_on_doesnt_fire():
    v = mod.classify([_card(0, "Intel", name="HDA Intel")],
                       {0: "on"}, {0: "snd_hda_intel"},
                       {0: ["pcmC0D0p"]})
    assert v["verdict"] == "ok"


def test_classify_orphan_hdmi():
    v = mod.classify(
        [_card(0, "HDMI", name="HDMI Audio")],
        {0: "auto"}, {0: "snd_hda_intel"},
        {0: []})  # no PCM children
    assert v["verdict"] == "orphan_hdmi_audio"


def test_classify_conflicting_drivers():
    v = mod.classify([_card(0, "NVidia", name="HDA NVidia")],
                       {0: "auto"},
                       {0: "snd_hda_intel",
                          1: "snd_hda_intel"},  # > # of cards
                       {0: ["pcmC0D0p"]})
    assert v["verdict"] == "conflicting_codec_drivers"


def test_classify_priority_gpu_pm_wins():
    v = mod.classify(
        [_card(0, "NVidia", name="HDA NVidia HDMI")],
        {0: "on"},
        {0: "snd_hda_intel"},
        {0: []})
    assert v["verdict"] == "gpu_hda_runtime_pm_off"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope1"),
                       str(tmp_path / "nope2"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    pa = tmp_path / "asound"
    pa.mkdir()
    (pa / "cards").write_text(CARDS_TEXT)
    (pa / "modules").write_text(MODULES_TEXT)
    ss = tmp_path / "sound"
    for i in (0, 1):
        d = ss / f"card{i}" / "device" / "power"
        d.mkdir(parents=True, exist_ok=True)
        # Intel card stuck on, NVIDIA on auto
        pc = "on" if i == 0 else "auto"
        (d / "control").write_text(pc + "\n")
    out = mod.status(None, str(pa), str(ss))
    assert out["ok"] is True
    assert out["card_count"] == 2
    # NVIDIA on auto → ok ; Intel "on" doesn't trigger the
    # GPU-specific verdict.
    assert out["verdict"]["verdict"] == "ok"
