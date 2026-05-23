"""Module spi_firmware_loader_audit — SPI + firmware loader (R&D #66.4).

Joins three under-covered observability surfaces :

  /sys/class/spi_master/spi*/    BIOS/BMC SPI flash controllers
                                    (cs count, devices bound).
  /sys/class/firmware/<name>/    in-flight firmware load requests
                                    (udev fallback path). A stuck
                                    entry means the requested
                                    firmware blob is missing.
  /sys/kernel/profiling          legacy oprofile sysfs toggle —
                                    rare but a security/obs signal
                                    when non-zero.

Why this matters on an LLM rig :

* `radeon`, `iwlwifi`, NVIDIA GSP rebinds via `gpu_pci_bind`
  fail silently when the requested firmware blob is missing —
  /sys/class/firmware/<name>/loading == 1 indefinitely.
* Knowing the BIOS SPI flash controller is useful for fwupd /
  flashrom workflows on Talos / Ampere / BMC hosts.

Reads :
  /sys/class/spi_master/spi*/
  /sys/class/firmware/<name>/{loading, ...}
  /sys/kernel/profiling

Verdicts (priority-ordered) :
  firmware_load_stuck      ≥1 entry under /sys/class/firmware/
                           with loading != 0 (in-flight) AND no
                           consumer claiming it.
  profiling_enabled        /sys/kernel/profiling is non-zero.
  spi_no_master            /sys/class/spi_master exists but
                           empty (informational — typical on
                           desktop without SPI peripherals).
  ok                       All quiet.
  unknown                  All three paths absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "spi_firmware_loader_audit"


_SYS_SPI_MASTER = "/sys/class/spi_master"
_SYS_FIRMWARE = "/sys/class/firmware"
_SYS_PROFILING = "/sys/kernel/profiling"


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


def list_spi_masters(sys_spi: str = _SYS_SPI_MASTER) -> List[dict]:
    if not os.path.isdir(sys_spi):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_spi)):
        d = os.path.join(sys_spi, name)
        if not os.path.isdir(d):
            continue
        out.append({"id": name})
    return out


def list_firmware_requests(sys_firmware: str = _SYS_FIRMWARE
                              ) -> List[dict]:
    """Each entry under /sys/class/firmware is a request name.
    A loading=1 file means the kernel is currently waiting for
    udev to load that blob."""
    if not os.path.isdir(sys_firmware):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_firmware)):
        if name == "timeout":
            continue
        d = os.path.join(sys_firmware, name)
        if not os.path.isdir(d):
            continue
        out.append({
            "name": name,
            "loading": _read_int(os.path.join(d, "loading")),
        })
    return out


def classify(spi_masters: List[dict],
              fw_requests: List[dict],
              profiling: Optional[int],
              spi_present: bool,
              fw_present: bool,
              prof_present: bool) -> dict:
    if not (spi_present or fw_present or prof_present):
        return {"verdict": "unknown",
                "reason": ("Neither /sys/class/spi_master, "
                          "/sys/class/firmware nor /sys/kernel/"
                          "profiling present."),
                "recommendation": ""}

    # 1) firmware_load_stuck
    stuck = [r for r in fw_requests
                if r.get("loading") not in (0, None)]
    if stuck:
        sample = ", ".join(r["name"] for r in stuck[:3])
        return {"verdict": "firmware_load_stuck",
                "reason": (f"{len(stuck)} firmware request(s) "
                          f"stuck in-flight : {sample}. Missing "
                          f"firmware blob — driver init blocked."),
                "recommendation": _recipe_firmware_stuck(
                    stuck[0]["name"])}

    # 2) profiling_enabled
    if profiling is not None and profiling != 0:
        return {"verdict": "profiling_enabled",
                "reason": (f"/sys/kernel/profiling = {profiling}. "
                          f"Legacy oprofile sampling enabled."),
                "recommendation": _recipe_profiling_off()}

    # 3) spi_no_master — informational accent
    if spi_present and not spi_masters:
        return {"verdict": "spi_no_master",
                "reason": ("/sys/class/spi_master directory present "
                          "but empty — no SPI controllers exposed "
                          "(typical desktop)."),
                "recommendation": ""}

    return {"verdict": "ok",
            "reason": (f"{len(spi_masters)} SPI master(s), "
                      f"{len(fw_requests)} firmware request(s), "
                      f"profiling={profiling}."),
            "recommendation": ""}


def status(config=None,
            sys_spi: str = _SYS_SPI_MASTER,
            sys_firmware: str = _SYS_FIRMWARE,
            sys_profiling: str = _SYS_PROFILING) -> dict:
    spi_present = os.path.isdir(sys_spi)
    fw_present = os.path.isdir(sys_firmware)
    prof_present = os.path.isfile(sys_profiling)
    spi_masters = list_spi_masters(sys_spi)
    fw_requests = list_firmware_requests(sys_firmware)
    profiling = _read_int(sys_profiling)
    ok = spi_present or fw_present or prof_present
    verdict = classify(spi_masters, fw_requests, profiling,
                          spi_present, fw_present, prof_present)
    return {"ok": ok,
              "spi_master_count": len(spi_masters),
              "spi_masters": spi_masters,
              "firmware_request_count": len(fw_requests),
              "firmware_requests": fw_requests,
              "profiling": profiling,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_firmware_stuck(name: str) -> str:
    return (f"# A firmware blob is missing for '{name}'.\n"
            f"# Find which driver requested it :\n"
            f"dmesg | grep -iE 'firmware|{name}' | tail\n"
            f"# Install the linux-firmware package :\n"
            f"sudo apt install linux-firmware  # Debian/Ubuntu\n"
            f"# Or place the blob manually under /lib/firmware/.\n")


def _recipe_profiling_off() -> str:
    return ("# Disable oprofile sampling :\n"
            "echo 0 | sudo tee /sys/kernel/profiling\n"
            "# Persist if it's coming back via tuned / sysctl-style\n"
            "# tweak. Verify : cat /sys/kernel/profiling\n")
