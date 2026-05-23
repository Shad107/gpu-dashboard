"""Module ata_port_sata_audit — ATA / SATA link audit
(R&D #71.1).

The kernel ATA layer exposes per-port / per-link / per-device
state under :

  /sys/class/ata_port/ata<N>/{port_no, idle_irq}
  /sys/class/ata_link/link<N>/{sata_spd, sata_spd_limit,
                                hw_sata_spd_limit}
  /sys/class/ata_device/dev<N>.<M>/{class, dma_mode, xfer_mode,
                                      spdn_cnt}

`spdn_cnt` is a monotonic counter the libata core bumps when it
auto-renegotiates a link down (Gen3 → Gen2 → Gen1) due to error
rate. The first downgrade is invisible to most monitoring
because everything still "works", just slower. This audit
surfaces it.

Likewise comparing `sata_spd_limit` against `hw_sata_spd_limit`
catches the case where a user (or distro) capped the link by
kernel cmdline (`libata.force=ata1:1.5G`) and forgot.

Why on a homelab :

* SATA Gen1 negotiation locks a 6 Gb/s drive to 1.5 Gb/s — a
  4× throughput cliff that's easy to miss.
* `spdn_cnt > 0` on a Samsung 870 EVO is almost always the
  power-supply / SATA-cable degradation cascade.

Verdicts (priority order) :
  link_renegotiated_down   ≥1 ata_device with spdn_cnt > 0.
  excessive_errors         ≥1 ata_link with hw_sata_spd_limit
                             present but sata_spd != hw_limit
                             AND sata_spd is a known low gen.
  spd_limit_capped         ≥1 ata_link has sata_spd_limit set
                             to a lower value than
                             hw_sata_spd_limit (user/distro
                             cap).
  requires_root            /sys/class/ata_* unreadable (rare —
                             usually world-readable).
  ok                       all links at native speed.
  unknown                  /sys/class/ata_port absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "ata_port_sata_audit"


_SYS_ATA_PORT = "/sys/class/ata_port"
_SYS_ATA_LINK = "/sys/class/ata_link"
_SYS_ATA_DEVICE = "/sys/class/ata_device"


# SATA speed strings : "3.0 Gbps", "1.5 Gbps", "6.0 Gbps"
_SATA_GBPS_RE = re.compile(r"([\d.]+)\s*Gbps?")


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def parse_gbps(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    if "unknown" in text.lower():
        return None
    m = _SATA_GBPS_RE.search(text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def list_ata_ports(sys_path: str = _SYS_ATA_PORT) -> List[dict]:
    if not os.path.isdir(sys_path):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    for n in names:
        d = os.path.join(sys_path, n)
        if not os.path.isdir(d):
            continue
        out.append({"id": n,
                       "port_no": _read_int(os.path.join(
                           d, "port_no"))})
    return out


def list_ata_links(sys_path: str = _SYS_ATA_LINK) -> List[dict]:
    if not os.path.isdir(sys_path):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    for n in names:
        d = os.path.join(sys_path, n)
        if not os.path.isdir(d):
            continue
        sata_spd_text = _read(os.path.join(d, "sata_spd"))
        spd_limit_text = _read(os.path.join(
            d, "sata_spd_limit"))
        hw_limit_text = _read(os.path.join(
            d, "hw_sata_spd_limit"))
        out.append({
            "id": n,
            "sata_spd_text": sata_spd_text,
            "sata_spd": parse_gbps(sata_spd_text),
            "sata_spd_limit_text": spd_limit_text,
            "sata_spd_limit": parse_gbps(spd_limit_text),
            "hw_sata_spd_limit_text": hw_limit_text,
            "hw_sata_spd_limit": parse_gbps(hw_limit_text),
        })
    return out


def list_ata_devices(sys_path: str = _SYS_ATA_DEVICE
                          ) -> List[dict]:
    if not os.path.isdir(sys_path):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(sys_path))
    except OSError:
        return []
    for n in names:
        d = os.path.join(sys_path, n)
        if not os.path.isdir(d):
            continue
        out.append({
            "id": n,
            "class": _read(os.path.join(d, "class")),
            "dma_mode": _read(os.path.join(d, "dma_mode")),
            "xfer_mode": _read(os.path.join(d, "xfer_mode")),
            "spdn_cnt": _read_int(os.path.join(d, "spdn_cnt")),
        })
    return out


def classify(ports: List[dict], links: List[dict],
              devices: List[dict],
              ata_present: bool) -> dict:
    if not ata_present:
        return {"verdict": "unknown",
                "reason": ("/sys/class/ata_port absent — host "
                          "has no ATA / SATA controllers (NVMe-"
                          "only system or no storage)."),
                "recommendation": ""}

    # 1) link_renegotiated_down
    spdn = [d for d in devices
              if (d.get("spdn_cnt") or 0) > 0]
    if spdn:
        sample = ", ".join(
            f"{d['id']} spdn={d['spdn_cnt']}"
                for d in spdn[:3])
        return {"verdict": "link_renegotiated_down",
                "reason": (f"{len(spdn)} ATA device(s) report "
                          f"spdn_cnt > 0 (link renegotiated "
                          f"down) : {sample}. Check cable / "
                          f"power supply."),
                "recommendation": _recipe_spdn()}

    # 2) excessive_errors — sata_spd below hw_sata_spd_limit
    #    AND no user cap explains it (sata_spd_limit >= hw_limit).
    err = []
    for l in links:
        spd = l.get("sata_spd")
        hw = l.get("hw_sata_spd_limit")
        limit = l.get("sata_spd_limit")
        if (spd is not None and hw is not None
                and spd < hw
                and (limit is None or limit >= hw)):
            err.append(l)
    if err:
        sample = ", ".join(
            f"{l['id']} @{l['sata_spd']}Gbps<hw{l['hw_sata_spd_limit']}"
                for l in err[:3])
        return {"verdict": "excessive_errors",
                "reason": (f"{len(err)} ATA link(s) operating "
                          f"below hardware limit (errors most "
                          f"likely) : {sample}."),
                "recommendation": _recipe_errors()}

    # 3) spd_limit_capped
    capped = []
    for l in links:
        limit = l.get("sata_spd_limit")
        hw = l.get("hw_sata_spd_limit")
        if limit is not None and hw is not None and limit < hw:
            capped.append(l)
    if capped:
        sample = ", ".join(
            f"{l['id']} cap@{l['sata_spd_limit']}<hw{l['hw_sata_spd_limit']}"
                for l in capped[:3])
        return {"verdict": "spd_limit_capped",
                "reason": (f"{len(capped)} ATA link(s) have a "
                          f"user/distro-imposed cap below "
                          f"hardware limit : {sample}."),
                "recommendation": _recipe_capped()}

    return {"verdict": "ok",
            "reason": (f"{len(ports)} ports, {len(links)} links, "
                      f"{len(devices)} devices — all at native "
                      f"speed, no spdn."),
            "recommendation": ""}


def status(config=None,
            sys_ata_port: str = _SYS_ATA_PORT,
            sys_ata_link: str = _SYS_ATA_LINK,
            sys_ata_device: str = _SYS_ATA_DEVICE) -> dict:
    ata_present = os.path.isdir(sys_ata_port)
    ports = list_ata_ports(sys_ata_port)
    links = list_ata_links(sys_ata_link)
    devices = list_ata_devices(sys_ata_device)
    verdict = classify(ports, links, devices, ata_present)
    return {"ok": ata_present,
              "ata_present": ata_present,
              "port_count": len(ports),
              "link_count": len(links),
              "device_count": len(devices),
              "ports": ports,
              "links": links,
              "devices": devices,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_spdn() -> str:
    return ("# spdn_cnt > 0 = libata auto-renegotiated the link.\n"
            "for d in /sys/class/ata_device/dev*; do\n"
            "  echo \"$(basename $d) spdn=$(cat $d/spdn_cnt) "
            "xfer=$(cat $d/xfer_mode)\"\n"
            "done\n"
            "# Common causes :\n"
            "#   - SATA cable seated badly / damaged\n"
            "#   - PSU 12 V rail dipping under load\n"
            "#   - Backplane port near end-of-life\n"
            "# Force-clear the counter (warm reboot only) :\n"
            "echo 1 | sudo tee /sys/block/sda/device/rescan\n")


def _recipe_errors() -> str:
    return ("# Link operating below hardware capability without\n"
            "# user override = sustained errors. Inspect ering :\n"
            "for d in /sys/class/ata_device/dev*; do\n"
            "  echo \"-- $d\"; sudo cat \"$d/ering\"\n"
            "done | head -100\n")


def _recipe_capped() -> str:
    return ("# Someone wrote to sata_spd_limit. Check kernel\n"
            "# cmdline for libata.force= entries :\n"
            "cat /proc/cmdline | tr ' ' '\\n' | grep -i libata\n"
            "# Restore hw limit at runtime :\n"
            "echo 0 | sudo tee /sys/class/ata_link/<id>/sata_spd_limit\n")
