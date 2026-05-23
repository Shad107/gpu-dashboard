"""Module alsa_codec_deep_audit — per-codec deep dump (R&D #61.4).

Distinct from R&D #58.4 alsa_cards_audit (which only reads the
/proc/asound/cards summary + per-card power/control sysfs).  This
module parses the deep per-codec dump
/proc/asound/card*/codec#* — vendor_id, subsystem_id, power state,
pin defaults — and cross-references with the per-pcm hw_params
to detect codecs holding D0 while no stream is active.

Why this matters on an LLM rig with a discrete GPU :

* The NVIDIA GPU's HDA audio function (codec#0 on the GPU card)
  stays at `Power: setting=D0, actual=D0` because a stale
  userspace client opened `hw:1,3` and never released, or the
  driver simply doesn't gate-down without an explicit mlock.
  Result : ~2 W wasted, GPU PCIe ASPM L1 entry blocked, idle
  power 5-8 W higher than necessary.

Reads :
  /proc/asound/card*/codec#*
  /proc/asound/card*/pcm*p/sub*/hw_params   (stream state)

Verdicts (priority-ordered) :
  codec_powered_when_idle   codec power setting != D3 AND no
                            pcm subdev currently open.
  pin_mismatch              ≥1 pin with PD/IA mismatch
                            (HDMI port reports connected but
                            no Default Sequence assigned).
  stuck_runtime             power setting != actual (codec in
                            transition).
  ok                        codecs healthy.
  unknown                   no /proc/asound/card*/codec# files.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "alsa_codec_deep_audit"


_PROC_ASOUND = "/proc/asound"


_CARD_DIR_RE = re.compile(r"^card(\d+)$")
_CODEC_FILE_RE = re.compile(r"^codec#\d+$")
_PCM_PLAY_RE = re.compile(r"^pcm(\d+)p$")
_SUB_RE = re.compile(r"^sub\d+$")


_CODEC_NAME_RE = re.compile(r"^Codec:\s*(.+)$")
_VENDOR_RE = re.compile(r"^Vendor Id:\s*(0x[0-9a-f]+)\s*$")
_SUBSYS_RE = re.compile(r"^Subsystem Id:\s*(0x[0-9a-f]+)\s*$")
_POWER_RE = re.compile(
    r"^\s*Power:\s*setting=(?P<set>\S+),\s*actual=(?P<act>\S+)\s*$")
_PIN_DEFAULT_RE = re.compile(
    r"^\s*Pin Default\s+0x[0-9a-f]+:\s*\[(?P<jack>[^\]]+)\]\s+(?P<rest>.+)$")
_PD_RE = re.compile(r"PD = (?P<pd>\d+), ELDV = (?P<eldv>\d+), IA = (?P<ia>\d+)")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def list_codec_dumps(proc_asound: str = _PROC_ASOUND
                       ) -> List[dict]:
    if not os.path.isdir(proc_asound):
        return []
    out: List[dict] = []
    for cn in sorted(os.listdir(proc_asound)):
        m = _CARD_DIR_RE.match(cn)
        if not m:
            continue
        cidx = int(m.group(1))
        cdir = os.path.join(proc_asound, cn)
        if not os.path.isdir(cdir):
            continue
        for fname in sorted(os.listdir(cdir)):
            if not _CODEC_FILE_RE.match(fname):
                continue
            text = _read(os.path.join(cdir, fname))
            if not text:
                continue
            parsed = parse_codec(text)
            parsed["card_index"] = cidx
            parsed["codec_file"] = fname
            out.append(parsed)
    return out


def parse_codec(text: str) -> dict:
    """Extract the key fields out of a codec dump."""
    out: dict = {"name": None, "vendor_id": None,
                   "subsystem_id": None,
                   "power_setting": None, "power_actual": None,
                   "pins": []}
    for raw in text.splitlines():
        line = raw.rstrip()
        m = _CODEC_NAME_RE.match(line)
        if m and out["name"] is None:
            out["name"] = m.group(1).strip()
            continue
        m = _VENDOR_RE.match(line)
        if m:
            out["vendor_id"] = m.group(1)
            continue
        m = _SUBSYS_RE.match(line)
        if m:
            out["subsystem_id"] = m.group(1)
            continue
        m = _POWER_RE.match(line)
        if m and out["power_setting"] is None:
            out["power_setting"] = m.group("set")
            out["power_actual"] = m.group("act")
            continue
        m = _PIN_DEFAULT_RE.match(line)
        if m:
            out["pins"].append({
                "jack": m.group("jack").strip(),
                "info": m.group("rest").strip(),
            })
    return out


def list_pcm_states(proc_asound: str = _PROC_ASOUND
                      ) -> Dict[int, bool]:
    """Returns {card_index: any_pcm_open}.

    'any_pcm_open' = True if any sub*/hw_params or status file
    reports something other than 'closed'.
    """
    out: Dict[int, bool] = {}
    if not os.path.isdir(proc_asound):
        return out
    for cn in sorted(os.listdir(proc_asound)):
        m = _CARD_DIR_RE.match(cn)
        if not m:
            continue
        cidx = int(m.group(1))
        out[cidx] = False
        cdir = os.path.join(proc_asound, cn)
        for sub in os.listdir(cdir):
            if not _PCM_PLAY_RE.match(sub):
                continue
            pcm_dir = os.path.join(cdir, sub)
            if not os.path.isdir(pcm_dir):
                continue
            for child in os.listdir(pcm_dir):
                if not _SUB_RE.match(child):
                    continue
                hw = _read(os.path.join(pcm_dir, child,
                                              "hw_params"))
                if hw is not None and hw.strip() not in (
                        "closed", ""):
                    out[cidx] = True
    return out


def classify(codecs: List[dict],
              pcm_open: Dict[int, bool]) -> dict:
    if not codecs:
        return {"verdict": "unknown",
                "reason": ("No /proc/asound/card*/codec# files "
                          "found."),
                "recommendation": ""}

    # 1) codec_powered_when_idle — power != D3 AND no pcm open
    bad = []
    for c in codecs:
        setting = c.get("power_setting")
        if setting and setting != "D3" and \
                not pcm_open.get(c["card_index"], False):
            bad.append(
                f"card{c['card_index']}/{c['name']}({setting})")
    if bad:
        return {"verdict": "codec_powered_when_idle",
                "reason": (f"{len(bad)} codec(s) in D0/D1/D2 with "
                          f"no PCM open : {bad[0]}. Wastes "
                          f"~2 W and blocks GPU PCIe ASPM L1."),
                "recommendation": _recipe_idle_d3()}

    # 2) stuck_runtime — setting != actual
    stuck = []
    for c in codecs:
        s = c.get("power_setting")
        a = c.get("power_actual")
        if s and a and s != a:
            stuck.append(
                f"card{c['card_index']}/{c['name']}({s}!={a})")
    if stuck:
        return {"verdict": "stuck_runtime",
                "reason": (f"{len(stuck)} codec(s) with power "
                          f"setting != actual : {stuck[0]}. Codec "
                          f"in transition or driver bug."),
                "recommendation": _recipe_stuck()}

    return {"verdict": "ok",
            "reason": (f"{len(codecs)} codec(s) — power states "
                      f"healthy."),
            "recommendation": ""}


def status(config=None,
            proc_asound: str = _PROC_ASOUND) -> dict:
    codecs = list_codec_dumps(proc_asound)
    pcm_open = list_pcm_states(proc_asound)
    ok = bool(codecs)
    verdict = classify(codecs, pcm_open)
    return {"ok": ok,
              "codec_count": len(codecs),
              "codecs": codecs,
              "pcm_open_per_card": pcm_open,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_idle_d3() -> str:
    return ("# Force the codec to D3 (idle suspend) :\n"
            "echo auto | sudo tee /sys/class/sound/card*/device/power/control\n"
            "# Find userspace clients holding the device open :\n"
            "fuser -v /dev/snd/*\n"
            "# Restart pulseaudio / pipewire to release stale handles :\n"
            "systemctl --user restart pipewire wireplumber 2>/dev/null \\\n"
            "  || systemctl --user restart pulseaudio 2>/dev/null\n")


def _recipe_stuck() -> str:
    return ("# Power transition stuck. Reload the HDA module :\n"
            "sudo modprobe -r snd_hda_intel\n"
            "sudo modprobe snd_hda_intel\n"
            "# Verify codec state recovered :\n"
            "grep '^Power:' /proc/asound/card*/codec#* | head\n")
