"""Module typec_audit — USB-C alt-mode + PD-contract (R&D #51.4).

Walks /sys/class/typec/port*/ for USB Type-C port state, partner
detection, alt-mode (DisplayPort, Thunderbolt), and PD contract
parameters. Reads /sys/class/extcon/* for hotplug edge events
(charger / HDMI / TypeC plug detection state).

Per-port :
  power_role               source / sink / dual
  data_role                host / device / dual
  port_type                source / sink / dual
  power_operation_mode     default / 1.5A / 3.0A / usb_power_delivery
  preferred_role           source / sink
  pd_revision              "3.0"
  vconn_source             "yes" / "no"
  partner/                 subdir present when something is plugged
    accessory_mode         analog_audio / debug
    supports_usb_power_delivery
    type
  cable/                   subdir for the cable
    plug_type
    type

Per-extcon :
  state                    list of cables : SDP=0 CDP=0 USB_PD=1 ...

Verdicts (priority-ordered) :
  pd_no_contract           ≥1 typec port with a partner attached
                           but power_operation_mode != usb_power_
                           delivery → fell back to legacy USB,
                           limited to 1.5A / 5V instead of negotiated
                           PD profile.
  alt_mode_active          ≥1 port has an active alt-mode (DP, TBT)
                           → display / dock attached, surface info.
  no_typec                 /sys/class/typec absent or empty.
  unknown                  unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "typec_audit"


_SYS_TYPEC = "/sys/class/typec"
_SYS_EXTCON = "/sys/class/extcon"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def list_typec_ports(sys_typec: str = _SYS_TYPEC) -> list:
    if not os.path.isdir(sys_typec):
        return []
    out: list = []
    try:
        for name in sorted(os.listdir(sys_typec)):
            if not name.startswith("port"):
                continue
            # Skip partner / cable / alt-mode subnames like "port0-partner"
            if "-" in name:
                continue
            d = os.path.join(sys_typec, name)
            if not os.path.isdir(d):
                continue
            rec: dict = {"name": name}
            for attr in ("power_role", "data_role", "port_type",
                          "power_operation_mode", "preferred_role",
                          "pd_revision", "vconn_source",
                          "usb_typec_revision"):
                v = _read(os.path.join(d, attr))
                if v is not None:
                    rec[attr] = v.strip() or None
            # Partner detection : look for {port}-partner subdir.
            partner_dir = os.path.join(sys_typec, f"{name}-partner")
            rec["partner_attached"] = os.path.isdir(partner_dir)
            if rec["partner_attached"]:
                supports_pd = _read(os.path.join(
                    partner_dir, "supports_usb_power_delivery"))
                rec["partner_supports_pd"] = (
                    (supports_pd or "").strip() == "yes")
                rec["partner_type"] = (
                    (_read(os.path.join(partner_dir, "type"))
                     or "").strip() or None)
            # Alt-mode detection : enumerate {port}-partner.* / .M
            # subdirs (DP, TBT) via os.listdir(sys_typec).
            alt_modes: list = []
            try:
                for sub in os.listdir(sys_typec):
                    if sub.startswith(f"{name}-partner.")  \
                            and os.path.isdir(os.path.join(
                                sys_typec, sub)):
                        alt_modes.append(sub)
            except OSError:
                pass
            rec["alt_modes"] = alt_modes
            out.append(rec)
    except OSError:
        return []
    return out


def list_extcon(sys_extcon: str = _SYS_EXTCON) -> list:
    if not os.path.isdir(sys_extcon):
        return []
    out: list = []
    try:
        for name in sorted(os.listdir(sys_extcon)):
            d = os.path.join(sys_extcon, name)
            if not os.path.isdir(d):
                continue
            state = (_read(os.path.join(d, "state")) or "").strip()
            out.append({"name": name, "state": state})
    except OSError:
        return []
    return out


_RECIPE_PD_NO_CONTRACT = (
    "# A USB-C partner is plugged but the port fell back to legacy\n"
    "# USB power (1.5A / 5V) instead of negotiating a PD contract.\n"
    "# Common causes :\n"
    "#  - The cable doesn't support PD (older USB-C charging cables).\n"
    "#  - The charger / hub negotiates only a low-current profile.\n"
    "#  - Kernel typec driver doesn't speak the partner's PD revision.\n"
    "# Inspect via :\n"
    "for p in /sys/class/typec/port*; do\n"
    "  echo \"$p : op_mode=$(cat $p/power_operation_mode)\"\n"
    "done\n"
    "# Try a known-good PD-capable cable + verify with `lsusb -t`."
)


def classify(ports: list, extcons: list) -> dict:
    if not ports and not extcons:
        return {"verdict": "no_typec",
                "reason": ("/sys/class/typec absent or empty — "
                           "no USB-C ports surfaced (typical for "
                           "desktops + servers + VMs)."),
                "recommendation": ""}
    # 1) PD contract missing
    pd_missing: list = []
    for p in ports:
        if (p.get("partner_attached")
                and p.get("partner_supports_pd") is True
                and (p.get("power_operation_mode")
                       or "").lower() != "usb_power_delivery"):
            pd_missing.append(p)
    if pd_missing:
        names = ", ".join(
            f"{p['name']} (op={p.get('power_operation_mode')})"
            for p in pd_missing)
        return {"verdict": "pd_no_contract",
                "reason": (f"{len(pd_missing)} USB-C port(s) with "
                           f"PD-capable partner but no PD contract: "
                           f"{names}."),
                "recommendation": _RECIPE_PD_NO_CONTRACT}
    # 2) Alt-mode active (info)
    alt_active = [p for p in ports if p.get("alt_modes")]
    if alt_active:
        names = ", ".join(
            f"{p['name']} ({len(p['alt_modes'])} alt-mode(s))"
            for p in alt_active)
        return {"verdict": "alt_mode_active",
                "reason": (f"{len(alt_active)} USB-C port(s) with "
                           f"alt-mode (DP/TBT/...) active: {names}."),
                "recommendation": ""}
    if not ports:
        return {"verdict": "no_typec",
                "reason": ("/sys/class/typec empty (no USB-C ports), "
                           "but extcon present — partial typec "
                           "framework."),
                "recommendation": ""}
    return {"verdict": "ok",
            "reason": (f"{len(ports)} USB-C port(s), "
                       f"{len(extcons)} extcon device(s), no PD "
                       f"contract gap, no alt-mode active."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    ports = list_typec_ports(_SYS_TYPEC)
    extcons = list_extcon(_SYS_EXTCON)
    verdict = classify(ports, extcons)
    return {
        "ok": bool(ports) or bool(extcons),
        "port_count": len(ports),
        "ports": ports,
        "extcon_count": len(extcons),
        "extcons": extcons,
        "verdict": verdict,
    }
