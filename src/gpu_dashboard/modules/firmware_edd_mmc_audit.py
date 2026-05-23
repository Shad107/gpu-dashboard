"""Module firmware_edd_mmc_audit — EDD + MMC/eMMC wear (R&D #64.4).

Reads :
  /sys/firmware/edd/int13_dev*/{mbr_signature, host_bus,
                                   interface, legacy_max_head}
  /sys/bus/mmc/devices/*/{type, name, manfid, oemid, serial,
                             life_time}
  /sys/class/mmc_host/*/clock

Why this matters on embedded / NUC / SBC LLM rigs :

* eMMC `life_time` byte encodes Type A / Type B percentile
  estimates of NAND wear (per JEDEC eMMC 5.x). ≥ 0x0A (= 80-90 %)
  is wear-out imminent ; the embedded boot drive will fail in
  weeks-to-months.
* EDD MBR signature mismatch vs the active boot disk → BIOS
  boot-order drift after a disk swap or live-cloned image.
* `mmc clock` capped at HS legacy 26 MHz despite HS200 / HS400
  capability → 5-10× slower model loads from an eMMC store.

Verdicts (priority-ordered) :
  emmc_wear_imminent           ≥1 MMC device with life_time ≥
                               0x0A (≥ 80 % wear).
  edd_mbr_drift                ≥1 EDD entry with mbr_signature
                               not matching any current MBR
                               (we just surface the values for
                               manual cross-check).
  mmc_clock_legacy             ≥1 mmc_host clocked < 50 MHz
                               (HS legacy) with a JEDEC-reported
                               eMMC device.
  ok                           Counters present and healthy.
  unknown                      /sys/firmware/edd + /sys/bus/mmc
                               both absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "firmware_edd_mmc_audit"


_SYS_FW_EDD = "/sys/firmware/edd"
_SYS_BUS_MMC = "/sys/bus/mmc/devices"
_SYS_CLASS_MMC_HOST = "/sys/class/mmc_host"

_EDD_DIR_RE = re.compile(r"^int13_dev[0-9a-f]+$")


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
        return int(t, 0)
    except ValueError:
        return None


def list_edd_entries(sys_edd: str = _SYS_FW_EDD) -> List[dict]:
    if not os.path.isdir(sys_edd):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_edd)):
        if not _EDD_DIR_RE.match(name):
            continue
        d = os.path.join(sys_edd, name)
        out.append({
            "id": name,
            "mbr_signature": _read(
                os.path.join(d, "mbr_signature")),
            "host_bus": _read(os.path.join(d, "host_bus")),
            "interface": _read(os.path.join(d, "interface")),
        })
    return out


def list_mmc_devices(sys_bus_mmc: str = _SYS_BUS_MMC
                       ) -> List[dict]:
    if not os.path.isdir(sys_bus_mmc):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_bus_mmc)):
        d = os.path.join(sys_bus_mmc, name)
        if not os.path.isdir(d):
            continue
        life_time_raw = _read(os.path.join(d, "life_time"))
        out.append({
            "id": name,
            "type": _read(os.path.join(d, "type")),
            "name": _read(os.path.join(d, "name")),
            "manfid": _read(os.path.join(d, "manfid")),
            "oemid": _read(os.path.join(d, "oemid")),
            "serial": _read(os.path.join(d, "serial")),
            "life_time": life_time_raw,
        })
    return out


def list_mmc_hosts(sys_class_mmc_host: str = _SYS_CLASS_MMC_HOST
                     ) -> List[dict]:
    if not os.path.isdir(sys_class_mmc_host):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_class_mmc_host)):
        d = os.path.join(sys_class_mmc_host, name)
        out.append({
            "id": name,
            "clock": _read_int(os.path.join(d, "clock")),
        })
    return out


def _max_life_time_byte(life_time_raw: Optional[str]) -> Optional[int]:
    """life_time is reported by the kernel as 2 space-separated
    hex bytes (Type A / Type B). Return max() of them."""
    if not life_time_raw:
        return None
    parts = life_time_raw.split()
    max_v = 0
    found = False
    for p in parts:
        try:
            v = int(p, 16)
        except ValueError:
            continue
        if v > max_v:
            max_v = v
        found = True
    return max_v if found else None


def classify(edd: List[dict], mmcs: List[dict],
              hosts: List[dict]) -> dict:
    if not edd and not mmcs and not hosts:
        return {"verdict": "unknown",
                "reason": ("Neither /sys/firmware/edd nor "
                          "/sys/bus/mmc present — host has no "
                          "EDD/MMC subsystem."),
                "recommendation": ""}

    # 1) emmc_wear_imminent
    worn = []
    for m in mmcs:
        b = _max_life_time_byte(m.get("life_time"))
        if b is not None and b >= 0x0A:
            worn.append(f"{m['id']}({m['life_time']})")
    if worn:
        return {"verdict": "emmc_wear_imminent",
                "reason": (f"{len(worn)} MMC/eMMC device(s) with "
                          f"life_time ≥ 0x0A : {worn[0]}. NAND "
                          f"wear-out imminent."),
                "recommendation": _recipe_emmc_wear()}

    # 2) edd_mbr_drift — informational, surface the values
    # we can't reliably auto-detect drift without comparing against
    # the live partition table. Only fire if EDD says one thing
    # but no /dev/sda* exists.
    if edd and not os.path.isdir("/sys/block/sda") and \
            not os.path.isdir("/sys/block/vda"):
        return {"verdict": "edd_mbr_drift",
                "reason": (f"EDD reports {len(edd)} BIOS int13 "
                          f"device(s) but no /dev/sda or /dev/vda "
                          f"in /sys/block — possible boot disk "
                          f"swap / drift."),
                "recommendation": _recipe_edd_drift()}

    # 3) mmc_clock_legacy
    slow = [h for h in hosts
               if h.get("clock") is not None and
                  h["clock"] > 0 and h["clock"] < 50_000_000]
    if slow and mmcs:
        sample = ", ".join(
            f"{h['id']}({(h['clock'] or 0) / 1_000_000:.1f}MHz)"
            for h in slow[:3])
        return {"verdict": "mmc_clock_legacy",
                "reason": (f"{len(slow)} mmc_host(s) clocked < 50 "
                          f"MHz : {sample}. HS200/HS400 capability "
                          f"unused."),
                "recommendation": _recipe_mmc_clock()}

    return {"verdict": "ok",
            "reason": (f"{len(edd)} EDD entries, {len(mmcs)} MMC "
                      f"device(s), {len(hosts)} mmc_host(s) — "
                      f"healthy."),
            "recommendation": ""}


def status(config=None,
            sys_edd: str = _SYS_FW_EDD,
            sys_bus_mmc: str = _SYS_BUS_MMC,
            sys_class_mmc_host: str = _SYS_CLASS_MMC_HOST) -> dict:
    edd = list_edd_entries(sys_edd)
    mmcs = list_mmc_devices(sys_bus_mmc)
    hosts = list_mmc_hosts(sys_class_mmc_host)
    ok = bool(edd or mmcs or hosts)
    verdict = classify(edd, mmcs, hosts)
    return {"ok": ok,
              "edd_count": len(edd),
              "edd_entries": edd,
              "mmc_count": len(mmcs),
              "mmc_devices": mmcs,
              "mmc_host_count": len(hosts),
              "mmc_hosts": hosts,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_emmc_wear() -> str:
    return ("# Verify the wear estimate via mmc-utils :\n"
            "sudo mmc extcsd read /dev/mmcblk0 | grep -i life_time\n"
            "# Type A / Type B each percentile band : 0x00 unknown,\n"
            "# 0x01-0x09 = 0-90 %, 0x0A = 90-100 %, 0x0B = >100 %.\n"
            "# Plan replacement / image-clone to a healthier drive.\n")


def _recipe_edd_drift() -> str:
    return ("# Compare EDD-reported signature(s) to live MBR :\n"
            "cat /sys/firmware/edd/int13_dev*/mbr_signature\n"
            "for d in /sys/block/[sv]d?; do\n"
            "  echo \"$d: $(sudo blkid $(readlink -f $d | "
            "sed 's,.*block/,/dev/,'))\"\n"
            "done\n"
            "# If signatures don't line up, BIOS boot-order may\n"
            "# have shifted after a disk swap.\n")


def _recipe_mmc_clock() -> str:
    return ("# Inspect host capabilities :\n"
            "for h in /sys/class/mmc_host/*; do\n"
            "  echo \"$h : clock=$(cat $h/clock 2>/dev/null) \"\n"
            "  cat $h/ios 2>/dev/null | head -3\n"
            "done\n"
            "# Vendor BIOS option (eMMC speed mode) or DT overlay\n"
            "# usually controls HS200/HS400 enablement.\n")
