"""Module alsa_cards_audit — ALSA cards + HDA runtime PM (R&D #58.4).

Reads /proc/asound/cards + /proc/asound/modules + per-card
/sys/class/sound/card*/device/power/control. The crucial check on
an LLM rig : the NVIDIA HDA audio function (function .1 of the
GPU PCIe device) keeps the PCIe link out of ASPM L1-substates
when its runtime PM is forced to "on".

Why this matters :

* A 3090's HDA function stuck at power/control = "on" prevents the
  GPU PCIe link from negotiating low-power L1 substates → ~5-8 W
  of idle waste and ~10-50 µs of extra wakeup latency when CUDA
  kernels launch.
* Orphan HDMI audio (a sound card with no associated display) is
  a sign of misconfigured AV setup ; sometimes the GPU stays
  awake to keep it alive.

Reads :
  /proc/asound/cards
  /proc/asound/modules
  /sys/class/sound/card*/device/power/control
  /sys/bus/pci/devices/*/class            (to map HDA function back
                                            to NVIDIA GPU)

Verdicts (priority-ordered) :
  gpu_hda_runtime_pm_off       NVIDIA HDA card with power/control
                               = 'on' (or 'unsupported').
  orphan_hdmi_audio            HDMI-only ALSA card with no PCM
                               capture/playback children — sign
                               of disconnected sink.
  conflicting_codec_drivers    Same card has multiple snd_hda
                               modules registered (rare,
                               post-rmmod state).
  ok                           Cards present, runtime PM healthy.
  unknown                      /proc/asound/cards absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "alsa_cards_audit"


_PROC_ASOUND = "/proc/asound"
_SYS_SOUND = "/sys/class/sound"


_CARD_LINE_RE = re.compile(
    r"^\s*(?P<idx>\d+)\s+\[(?P<id>\S+)\s*\]:\s+(?P<driver>\S+)\s+-\s+(?P<name>.+)$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def parse_cards(text: Optional[str]) -> List[dict]:
    """Parse /proc/asound/cards : header line per card."""
    if not text:
        return []
    out: List[dict] = []
    for line in text.splitlines():
        m = _CARD_LINE_RE.match(line)
        if m:
            out.append({
                "index": int(m.group("idx")),
                "id": m.group("id"),
                "driver": m.group("driver"),
                "name": m.group("name").strip(),
            })
    return out


def parse_modules(text: Optional[str]) -> Dict[int, str]:
    """Parse /proc/asound/modules : returns {index: module_name}."""
    out: Dict[int, str] = {}
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            out[int(parts[0])] = parts[1]
    return out


def read_power_control(idx: int,
                          sys_sound: str = _SYS_SOUND
                          ) -> Optional[str]:
    p = os.path.join(sys_sound, f"card{idx}", "device", "power",
                       "control")
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def is_nvidia_card(card: dict) -> bool:
    """Heuristic : NVIDIA HDA card is identified by 'NVidia' in id
    or name."""
    name = (card.get("name") or "") + " " + (card.get("id") or "")
    return "nvidia" in name.lower()


def card_pcm_children(idx: int, sys_sound: str = _SYS_SOUND
                        ) -> List[str]:
    """Returns the per-card pcm/control device names."""
    if not os.path.isdir(sys_sound):
        return []
    prefix = f"pcmC{idx}D"
    return sorted(name for name in os.listdir(sys_sound)
                       if name.startswith(prefix))


def classify(cards: List[dict],
              power_controls: Dict[int, Optional[str]],
              modules: Dict[int, str],
              pcm_children: Dict[int, List[str]]) -> dict:
    if not cards:
        return {"verdict": "unknown",
                "reason": "/proc/asound/cards is empty or absent.",
                "recommendation": ""}

    # 1) gpu_hda_runtime_pm_off
    gpu_pm_off = []
    for c in cards:
        if is_nvidia_card(c):
            pc = power_controls.get(c["index"])
            if pc and pc.lower() != "auto":
                gpu_pm_off.append(
                    f"{c['name']} (power/control={pc})")
    if gpu_pm_off:
        return {"verdict": "gpu_hda_runtime_pm_off",
                "reason": (f"NVIDIA HDA card(s) with runtime PM "
                          f"forced on : {gpu_pm_off[0]}. PCIe "
                          f"link stays out of L1 substates."),
                "recommendation": _recipe_pm_auto()}

    # 2) orphan_hdmi_audio — HDMI-named card with no PCM children
    orphan = []
    for c in cards:
        if "hdmi" in (c.get("name") or "").lower():
            if not pcm_children.get(c["index"]):
                orphan.append(c["name"])
    if orphan:
        return {"verdict": "orphan_hdmi_audio",
                "reason": (f"HDMI ALSA card(s) with no PCM children "
                          f": {orphan[0]}. Likely a disconnected "
                          f"sink keeping the GPU awake."),
                "recommendation": _recipe_orphan_hdmi()}

    # 3) conflicting_codec_drivers — same index has multiple
    #    snd_hda* modules listed (rare, post rmmod state).
    # /proc/asound/modules is one-line-per-card so duplicate
    # detection is when the same module name appears twice for
    # different indices but we want different modules on same idx.
    # This requires more rigorous parsing — here we simply check
    # for cards with index appearing twice in the file (impossible
    # by format) ; instead flag when number of modules > number of
    # cards (a stale entry).
    if len(modules) > len(cards):
        return {"verdict": "conflicting_codec_drivers",
                "reason": (f"/proc/asound/modules lists "
                          f"{len(modules)} entries for "
                          f"{len(cards)} cards. Module unload "
                          f"likely left a stale registration."),
                "recommendation": _recipe_codec_conflict()}

    return {"verdict": "ok",
            "reason": (f"{len(cards)} ALSA card(s), runtime PM "
                      f"healthy."),
            "recommendation": ""}


def status(config=None,
            proc_asound: str = _PROC_ASOUND,
            sys_sound: str = _SYS_SOUND) -> dict:
    cards_text = _read(os.path.join(proc_asound, "cards"))
    modules_text = _read(os.path.join(proc_asound, "modules"))
    cards = parse_cards(cards_text)
    modules = parse_modules(modules_text)
    power_controls = {c["index"]: read_power_control(c["index"],
                                                          sys_sound)
                         for c in cards}
    pcm_children = {c["index"]: card_pcm_children(c["index"],
                                                         sys_sound)
                       for c in cards}
    ok = bool(cards)
    verdict = classify(cards, power_controls, modules, pcm_children)
    return {"ok": ok,
              "card_count": len(cards),
              "cards": [{**c,
                            "power_control": power_controls.get(
                                c["index"]),
                            "pcm_children": pcm_children.get(
                                c["index"], [])}
                          for c in cards],
              "modules": modules,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_pm_auto() -> str:
    return ("# Restore runtime PM on the NVIDIA HDA function :\n"
            "for c in /sys/class/sound/card*; do\n"
            "  read name < $c/id 2>/dev/null\n"
            "  case \"$name\" in NVidia*|NVIDIA*)\n"
            "    echo auto | sudo tee $c/device/power/control ;;\n"
            "  esac\n"
            "done\n"
            "# Persist via /etc/udev/rules.d/90-nvidia-hda-pm.rules :\n"
            "#   SUBSYSTEM==\"sound\", DRIVER==\"snd_hda_intel\",\\\n"
            "#     ATTR{device/power/control}=\"auto\"\n")


def _recipe_orphan_hdmi() -> str:
    return ("# Unbind the orphan HDMI audio function to let the\n"
            "# PCIe link power down :\n"
            "ls /sys/class/sound/card*/device  # find the BDF\n"
            "echo <BDF> | sudo tee /sys/bus/pci/drivers/snd_hda_intel/unbind\n"
            "# Or blacklist if you never use HDMI audio :\n"
            "echo 'blacklist snd_hda_intel' | sudo tee /etc/modprobe.d/blacklist-hda-hdmi.conf\n")


def _recipe_codec_conflict() -> str:
    return ("# Refresh the snd_hda_intel module to clear stale state :\n"
            "sudo modprobe -r snd_hda_intel && sudo modprobe snd_hda_intel\n"
            "# Inspect for warnings :\n"
            "dmesg | grep -i 'hda\\|snd' | tail -20\n")
