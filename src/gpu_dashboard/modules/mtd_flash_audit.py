"""Module mtd_flash_audit — /sys/class/mtd NOR/NAND (R&D #66.1).

Reads /sys/class/mtd/mtd*/{name, type, size, erasesize,
writesize, flags, numeraseregions, bad_blocks} + /proc/mtd
partitions.

Why this matters on BMC / NUC / SBC LLM rigs :

* The platform's UEFI bootblock + BMC firmware live on a SPI-NOR
  exposed as MTD. Bad-block growth on NAND-backed eMMC partitions
  signals a chip approaching end of life.
* A partition marked writeable that *should* be read-only (BIOS
  region) is a security and a "user accidentally fwupd'd" risk.
* Unmapped partition (in /proc/mtd but no /sys/class/mtd entry)
  suggests driver / udev mismatch.

Reads :
  /sys/class/mtd/mtd*/{name, type, size, erasesize, writesize,
                          flags, numeraseregions, bad_blocks}
  /proc/mtd                              (header + per-line list)

Verdicts (priority-ordered) :
  nor_bad_blocks                ≥1 MTD with bad_blocks > 0.
  write_protect_drift           ≥1 partition whose flags lack
                                NO_ERASE / WRITEABLE_ZONE bits
                                while name suggests BIOS region.
  unmapped_partition            /proc/mtd lists entries that
                                don't have a matching
                                /sys/class/mtd/mtd<N>/ dir.
  ok                            All MTDs healthy.
  unknown                       /sys/class/mtd absent AND
                                /proc/mtd absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple


NAME = "mtd_flash_audit"


_SYS_MTD = "/sys/class/mtd"
_PROC_MTD = "/proc/mtd"


_MTD_DIR_RE = re.compile(r"^mtd\d+$")
_PROC_MTD_LINE_RE = re.compile(
    r"^(?P<name>mtd\d+):\s+(?P<size>[0-9a-f]+)\s+"
    r"(?P<erasesize>[0-9a-f]+)\s+\"(?P<label>[^\"]+)\"$")


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


def list_mtd_sysfs(sys_mtd: str = _SYS_MTD) -> List[dict]:
    if not os.path.isdir(sys_mtd):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_mtd)):
        if not _MTD_DIR_RE.match(name):
            continue
        d = os.path.join(sys_mtd, name)
        out.append({
            "id": name,
            "name": _read(os.path.join(d, "name")),
            "type": _read(os.path.join(d, "type")),
            "size": _read_int(os.path.join(d, "size")),
            "erasesize": _read_int(
                os.path.join(d, "erasesize")),
            "writesize": _read_int(
                os.path.join(d, "writesize")),
            "flags": _read_int(os.path.join(d, "flags")),
            "numeraseregions": _read_int(
                os.path.join(d, "numeraseregions")),
            "bad_blocks": _read_int(
                os.path.join(d, "bad_blocks")),
        })
    return out


def parse_proc_mtd(text: Optional[str]) -> List[dict]:
    out: List[dict] = []
    if not text:
        return out
    for line in text.splitlines():
        m = _PROC_MTD_LINE_RE.match(line.strip())
        if not m:
            continue
        out.append({
            "name": m.group("name"),
            "size": int(m.group("size"), 16),
            "erasesize": int(m.group("erasesize"), 16),
            "label": m.group("label"),
        })
    return out


def classify(sysfs: List[dict], proc: List[dict],
              sys_mtd_present: bool,
              proc_mtd_present: bool) -> dict:
    if not sys_mtd_present and not proc_mtd_present:
        return {"verdict": "unknown",
                "reason": ("Both /sys/class/mtd and /proc/mtd "
                          "absent — kernel without MTD or no "
                          "flash exposed (typical desktop / "
                          "VM)."),
                "recommendation": ""}

    # 1) nor_bad_blocks
    bad = [m for m in sysfs
              if m.get("bad_blocks") is not None and
                 m["bad_blocks"] > 0]
    if bad:
        sample = ", ".join(
            f"{m['id']}({m.get('name')})={m['bad_blocks']}"
            for m in bad[:3])
        return {"verdict": "nor_bad_blocks",
                "reason": (f"{len(bad)} MTD device(s) reporting "
                          f"bad blocks : {sample}. NAND end-of-"
                          f"life indicator."),
                "recommendation": _recipe_bad_blocks()}

    # 2) write_protect_drift — a partition named like BIOS region
    #    AND flags missing NO_ERASE bit (0x1 in MTD flags).
    #    Heuristic ; real check needs MTD_WRITEABLE flag.
    bios_drift = []
    for m in sysfs:
        nm = (m.get("name") or "").lower()
        if any(k in nm for k in ("bios", "ifd", "bootblock",
                                       "platform-data")):
            flags = m.get("flags") or 0
            # MTD_WRITEABLE = 0x400 ; if set, writeable.
            if flags & 0x400:
                bios_drift.append(m["id"])
    if bios_drift:
        sample = ", ".join(bios_drift[:3])
        return {"verdict": "write_protect_drift",
                "reason": (f"{len(bios_drift)} BIOS-region MTD "
                          f"partition(s) flagged WRITEABLE : "
                          f"{sample}. Verify SPI flash WP pin."),
                "recommendation": _recipe_wp()}

    # 3) unmapped_partition — /proc/mtd lists entries without
    #    /sys/class/mtd/<N> match.
    sysfs_names = {m["id"] for m in sysfs}
    unmapped = [p["name"] for p in proc
                   if p["name"] not in sysfs_names]
    if unmapped:
        sample = ", ".join(unmapped[:3])
        return {"verdict": "unmapped_partition",
                "reason": (f"{len(unmapped)} /proc/mtd partition(s) "
                          f"without /sys/class/mtd entry : "
                          f"{sample}. Driver / udev mismatch."),
                "recommendation": _recipe_unmapped()}

    return {"verdict": "ok",
            "reason": (f"{len(sysfs)} MTD device(s), "
                      f"{len(proc)} /proc/mtd entries — "
                      f"flash subsystem consistent."),
            "recommendation": ""}


def status(config=None,
            sys_mtd: str = _SYS_MTD,
            proc_mtd: str = _PROC_MTD) -> dict:
    sys_mtd_present = os.path.isdir(sys_mtd)
    proc_mtd_present = os.path.isfile(proc_mtd)
    sysfs = list_mtd_sysfs(sys_mtd)
    proc = parse_proc_mtd(_read(proc_mtd))
    ok = sys_mtd_present or proc_mtd_present
    verdict = classify(sysfs, proc, sys_mtd_present,
                          proc_mtd_present)
    return {"ok": ok,
              "sys_mtd_present": sys_mtd_present,
              "proc_mtd_present": proc_mtd_present,
              "sysfs_count": len(sysfs),
              "sysfs_devices": sysfs,
              "proc_count": len(proc),
              "proc_partitions": proc,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_bad_blocks() -> str:
    return ("# Inspect bad blocks (root) :\n"
            "for m in /sys/class/mtd/mtd*; do\n"
            "  bb=$(cat $m/bad_blocks 2>/dev/null)\n"
            "  [ \"$bb\" -gt 0 ] && echo \"$(cat $m/name) : "
            "$bb bad blocks\"\n"
            "done\n"
            "# Bad-block growth is normal on NAND ; plan replacement\n"
            "# when count approaches manufacturer's reserve.\n")


def _recipe_wp() -> str:
    return ("# A BIOS-region MTD shouldn't be writeable. Verify\n"
            "# the SPI flash WP# pin is asserted (motherboard\n"
            "# jumper / vendor service mode) before next reboot.\n"
            "# Software side : add `flashrom -p ... --wp-enable`\n"
            "# or vendor `mfg-fpt-update` utility.\n")


def _recipe_unmapped() -> str:
    return ("# /proc/mtd lists partitions without matching sysfs.\n"
            "# Reload the MTD driver (vendor-specific module name) :\n"
            "lsmod | grep -E 'mtd|spi-nor|nand'\n"
            "# Then : sudo modprobe -r <driver> && sudo modprobe <driver>\n")
