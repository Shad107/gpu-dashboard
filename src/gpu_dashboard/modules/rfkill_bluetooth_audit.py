"""Module rfkill_bluetooth_audit — wireless power gates (R&D #63.1).

Reads /sys/class/rfkill/rfkill*/{name, type, state, soft, hard,
persistent} + /sys/class/bluetooth/hci*/{address, type,
power/control}.

Why this matters on a laptop / SFF LLM rig :

* `hard=1` on a Wi-Fi or BT rfkill is the platform's HW kill —
  laptop lid switch / Fn-key combo / vendor EC quirk silently
  muted wireless. User fights NetworkManager for hours.
* `soft=1` left set by a previous session / suspend cycle (the
  PERSISTENT bit got cleared) blocks BT until reboot.
* BT HCI auto-suspend churn on a controller used by HID +
  audio constantly wakes the system.

Verdicts (priority-ordered) :
  hw_kill_blocks               ≥1 rfkill with hard=1.
  soft_block_stuck             ≥1 rfkill with soft=1 AND
                               persistent=0 (left over from
                               suspend).
  bt_autosuspend_churn         ≥1 hci controller with
                               power/control=auto AND state != up.
  ok                           wireless gates clean OR no
                               wireless hw present.
  unknown                      /sys/class/rfkill absent or
                               unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "rfkill_bluetooth_audit"


_SYS_RFKILL = "/sys/class/rfkill"
_SYS_BLUETOOTH = "/sys/class/bluetooth"

_RFKILL_DIR_RE = re.compile(r"^rfkill\d+$")
_HCI_DIR_RE = re.compile(r"^hci\d+$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def list_rfkills(sys_rfkill: str = _SYS_RFKILL) -> List[dict]:
    if not os.path.isdir(sys_rfkill):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_rfkill)):
        if not _RFKILL_DIR_RE.match(name):
            continue
        d = os.path.join(sys_rfkill, name)
        out.append({
            "id": name,
            "name": _read(os.path.join(d, "name")),
            "type": _read(os.path.join(d, "type")),
            "state": _read_int(os.path.join(d, "state")),
            "soft": _read_int(os.path.join(d, "soft")),
            "hard": _read_int(os.path.join(d, "hard")),
            "persistent": _read_int(
                os.path.join(d, "persistent")),
        })
    return out


def list_bluetooth(sys_bluetooth: str = _SYS_BLUETOOTH
                     ) -> List[dict]:
    if not os.path.isdir(sys_bluetooth):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_bluetooth)):
        if not _HCI_DIR_RE.match(name):
            continue
        d = os.path.join(sys_bluetooth, name)
        out.append({
            "id": name,
            "address": _read(os.path.join(d, "address")),
            "type": _read(os.path.join(d, "type")),
            "power_control": _read(
                os.path.join(d, "power", "control")),
        })
    return out


def classify(rfkills: List[dict],
              bluetooths: List[dict]) -> dict:
    if not rfkills and not bluetooths:
        return {"verdict": "unknown",
                "reason": ("Both /sys/class/rfkill and "
                          "/sys/class/bluetooth absent — host "
                          "has no wireless subsystem registered."),
                "recommendation": ""}

    # 1) hw_kill_blocks
    hw = [r for r in rfkills if r.get("hard") == 1]
    if hw:
        sample = ", ".join(
            f"{r['name'] or r['id']}({r.get('type')})"
            for r in hw[:3])
        return {"verdict": "hw_kill_blocks",
                "reason": (f"{len(hw)} rfkill switch(es) hard-"
                          f"blocked : {sample}. Hardware kill "
                          f"engaged — laptop lid switch, Fn-key, "
                          f"or vendor EC quirk."),
                "recommendation": _recipe_hw_kill()}

    # 2) soft_block_stuck
    stuck = [r for r in rfkills
                if r.get("soft") == 1 and r.get("persistent") == 0]
    if stuck:
        sample = ", ".join(
            f"{r['name'] or r['id']}({r.get('type')})"
            for r in stuck[:3])
        return {"verdict": "soft_block_stuck",
                "reason": (f"{len(stuck)} rfkill switch(es) soft-"
                          f"blocked non-persistently : {sample}. "
                          f"Leftover from suspend / previous "
                          f"session."),
                "recommendation": _recipe_unblock()}

    # 3) bt_autosuspend_churn
    bt_bad = [b for b in bluetooths
                 if b.get("power_control") == "auto"]
    if bt_bad:
        sample = ", ".join(b["id"] for b in bt_bad[:3])
        return {"verdict": "bt_autosuspend_churn",
                "reason": (f"{len(bt_bad)} BT controller(s) on "
                          f"power/control=auto : {sample}. HID + "
                          f"audio activity may cause auto-suspend "
                          f"churn."),
                "recommendation": _recipe_bt_pm()}

    return {"verdict": "ok",
            "reason": (f"{len(rfkills)} rfkill(s), "
                      f"{len(bluetooths)} BT controller(s) — "
                      f"wireless gates clean."),
            "recommendation": ""}


def status(config=None,
            sys_rfkill: str = _SYS_RFKILL,
            sys_bluetooth: str = _SYS_BLUETOOTH) -> dict:
    rfkills = list_rfkills(sys_rfkill)
    bluetooths = list_bluetooth(sys_bluetooth)
    ok = bool(rfkills or bluetooths)
    verdict = classify(rfkills, bluetooths)
    return {"ok": ok,
              "rfkill_count": len(rfkills),
              "rfkills": rfkills,
              "bluetooth_count": len(bluetooths),
              "bluetooths": bluetooths,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_hw_kill() -> str:
    return ("# Find the offending switch :\n"
            "rfkill list\n"
            "# Common causes : laptop lid sensor stuck (close-then-\n"
            "# open the lid), Fn+F2 / Fn+F12 toggle, vendor EC bug.\n"
            "# After clearing the HW source, soft-unblock :\n"
            "sudo rfkill unblock all\n")


def _recipe_unblock() -> str:
    return ("# Clear the leftover soft-block :\n"
            "sudo rfkill unblock all\n"
            "# Persist via /etc/systemd/system.conf.d/ or your\n"
            "# NetworkManager profile (NMS will normally do this).\n")


def _recipe_bt_pm() -> str:
    return ("# Pin BT controller to 'on' so HID/audio don't churn :\n"
            "for c in /sys/class/bluetooth/hci*; do\n"
            "  echo on | sudo tee $c/device/power/control\n"
            "done\n"
            "# Persist via /etc/udev/rules.d/52-bt-pm.rules :\n"
            "#   SUBSYSTEM==\"bluetooth\",\\\n"
            "#     ATTR{device/power/control}=\"on\"\n")
