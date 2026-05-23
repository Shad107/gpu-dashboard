"""Tests for modules/alsa_codec_deep_audit.py — R&D #61.4."""
from __future__ import annotations

import pytest

from gpu_dashboard.modules import alsa_codec_deep_audit as mod


CODEC_NVIDIA_HDMI_D0 = """\
Codec: Nvidia GPU 9a HDMI/DP
Address: 0
AFG Function Id: 0x1 (unsol 1)
Vendor Id: 0x10de009a
Subsystem Id: 0x19da1613
Revision Id: 0x100100
No Modem Function Group found
State of AFG node 0x01:
  Power states:  D0 D1 D2 D3 CLKSTOP EPSS
  Power: setting=D0, actual=D0
Node 0x04 [Pin Complex] wcaps 0x407381: 8-Channels Digital CP
  Pin Default 0x185600f0: [Jack] Digital Out at Int HDMI
"""


CODEC_INTEL_D3 = """\
Codec: Realtek ALC256
Vendor Id: 0x10ec0256
Subsystem Id: 0x103c8753
Revision Id: 0x100000
State of AFG node 0x01:
  Power: setting=D3, actual=D3
Node 0x14 [Pin Complex] wcaps 0x40058d: Stereo Amp-Out
  Pin Default 0x90170110: [Fixed] Speaker at Int N/A
"""

CODEC_STUCK = """\
Codec: Stuck Test
Vendor Id: 0x1234abcd
Subsystem Id: 0xdeadbeef
State of AFG node 0x01:
  Power: setting=D3, actual=D0
"""


# --- parse_codec ------------------------------------------------

def test_parse_codec_nvidia():
    out = mod.parse_codec(CODEC_NVIDIA_HDMI_D0)
    assert out["name"] == "Nvidia GPU 9a HDMI/DP"
    assert out["vendor_id"] == "0x10de009a"
    assert out["subsystem_id"] == "0x19da1613"
    assert out["power_setting"] == "D0"
    assert out["power_actual"] == "D0"
    assert any("HDMI" in p["info"] for p in out["pins"])


def test_parse_codec_realtek():
    out = mod.parse_codec(CODEC_INTEL_D3)
    assert out["name"] == "Realtek ALC256"
    assert out["power_setting"] == "D3"


def test_parse_codec_stuck():
    out = mod.parse_codec(CODEC_STUCK)
    assert out["power_setting"] == "D3"
    assert out["power_actual"] == "D0"


# --- list_codec_dumps + list_pcm_states -------------------------

def _mk_codec_file(asound, card_idx, codec_idx, content):
    cd = asound / f"card{card_idx}"
    cd.mkdir(parents=True, exist_ok=True)
    (cd / f"codec#{codec_idx}").write_text(content)


def _mk_pcm(asound, card_idx, pcm_idx, sub_idx, hw_params="closed"):
    d = asound / f"card{card_idx}" / f"pcm{pcm_idx}p" / f"sub{sub_idx}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "hw_params").write_text(hw_params)


def test_list_codec_dumps(tmp_path):
    _mk_codec_file(tmp_path, 1, 0, CODEC_NVIDIA_HDMI_D0)
    out = mod.list_codec_dumps(str(tmp_path))
    assert len(out) == 1
    assert out[0]["card_index"] == 1
    assert out[0]["power_setting"] == "D0"


def test_list_pcm_states_closed(tmp_path):
    _mk_pcm(tmp_path, 1, 3, 0, hw_params="closed")
    out = mod.list_pcm_states(str(tmp_path))
    assert out[1] is False


def test_list_pcm_states_open(tmp_path):
    _mk_pcm(tmp_path, 1, 3, 0,
              hw_params="access: RW_INTERLEAVED\nformat: S16_LE\n")
    out = mod.list_pcm_states(str(tmp_path))
    assert out[1] is True


# --- classify ---------------------------------------------------

def _codec_nvidia_d0(card_idx=1):
    return {**mod.parse_codec(CODEC_NVIDIA_HDMI_D0),
              "card_index": card_idx,
              "codec_file": "codec#0"}


def _codec_d3(card_idx=0):
    return {**mod.parse_codec(CODEC_INTEL_D3),
              "card_index": card_idx,
              "codec_file": "codec#0"}


def _codec_stuck(card_idx=1):
    return {**mod.parse_codec(CODEC_STUCK),
              "card_index": card_idx,
              "codec_file": "codec#0"}


def test_classify_unknown():
    v = mod.classify([], {})
    assert v["verdict"] == "unknown"


def test_classify_ok_all_d3():
    v = mod.classify([_codec_d3()], {0: False})
    assert v["verdict"] == "ok"


def test_classify_idle_d0():
    v = mod.classify([_codec_nvidia_d0()], {1: False})
    assert v["verdict"] == "codec_powered_when_idle"


def test_classify_d0_with_pcm_open_ok():
    # Codec in D0 but a PCM is open → expected, not flagged
    v = mod.classify([_codec_nvidia_d0()], {1: True})
    assert v["verdict"] == "ok"


def test_classify_stuck_runtime():
    v = mod.classify([_codec_stuck()], {1: True})
    assert v["verdict"] == "stuck_runtime"


def test_classify_priority_idle_wins():
    v = mod.classify(
        [_codec_nvidia_d0(), _codec_stuck(card_idx=2)],
        {1: False, 2: True})
    assert v["verdict"] == "codec_powered_when_idle"


# --- status integration -----------------------------------------

def test_status_unknown(tmp_path):
    out = mod.status(None, str(tmp_path / "nope"))
    assert out["ok"] is False
    assert out["verdict"]["verdict"] == "unknown"


def test_status_live_like(tmp_path):
    _mk_codec_file(tmp_path, 1, 0, CODEC_NVIDIA_HDMI_D0)
    _mk_pcm(tmp_path, 1, 3, 0, hw_params="closed")
    out = mod.status(None, str(tmp_path))
    assert out["ok"] is True
    assert out["codec_count"] == 1
    assert out["verdict"]["verdict"] == "codec_powered_when_idle"
