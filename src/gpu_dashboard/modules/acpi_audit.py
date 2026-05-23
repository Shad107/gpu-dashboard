"""Module acpi_audit — ACPI platform-profile + GPE auditor (R&D #47.2).

Reads the ACPI control-surface files that affect a workstation's
sustained performance + spurious-wake behaviour :

  /sys/firmware/acpi/platform_profile           current power profile
                                                (low-power, balanced,
                                                balanced-performance,
                                                performance, cool,
                                                quiet) — exposed by
                                                Lenovo Legion / ASUS
                                                ROG / Dell Precision
                                                firmware. Switches CPU
                                                / GPU TDP envelopes
                                                behind the OS.
  /sys/firmware/acpi/platform_profile_choices   available choices
                                                space-separated.
  /sys/firmware/acpi/pm_profile                 SMBIOS PM profile
                                                numeric code (0=
                                                unspecified, 1=
                                                desktop, 2=mobile,
                                                7=workstation, ...).
  /proc/acpi/wakeup                             per-ACPI-device
                                                wakeup-enable status
                                                ("*disabled" or
                                                "*enabled").
  /sys/firmware/acpi/interrupts/{gpe*,ff_*}     per-GPE counter +
                                                state flags. Runaway
                                                GPE (>100/s) = broken
                                                DSDT method.
  /sys/firmware/acpi/bgrt/                      Boot Graphics
                                                Resource Table.
  /sys/firmware/acpi/fpdt/                      Firmware Performance
                                                Data Table — boot
                                                timing milestones.

Verdicts (priority-ordered) :
  gpe_storm                ≥1 GPE shows >10000 events since boot
                           (rough proxy for "fires hundreds /s on
                           an uptime <1 day").
  pcie_root_wakeup         /proc/acpi/wakeup has an *enabled
                           PCIe root port (RP05, RP09, etc.) —
                           blocks GPU D3cold + burns 30 W idle.
  quiet_profile_on_workstation
                           platform_profile=quiet AND pm_profile
                           ∈ {7,8} (workstation/SOHO server) →
                           losing 15-25 % sustained throughput
                           silently.
  ok                       sane state.
  no_platform_profile      /sys/firmware/acpi/platform_profile
                           absent (most desktops + VMs lack this).
  unknown                  /sys/firmware/acpi unreadable entirely.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "acpi_audit"


_SYS_ACPI = "/sys/firmware/acpi"
_PROC_ACPI_WAKEUP = "/proc/acpi/wakeup"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def read_platform_profile(sys_acpi: str = _SYS_ACPI) -> dict:
    out: dict = {}
    cur = _read(os.path.join(sys_acpi, "platform_profile"))
    if cur is not None:
        out["current"] = cur.strip() or None
    choices = _read(os.path.join(sys_acpi, "platform_profile_choices"))
    if choices:
        out["choices"] = choices.strip().split()
    pm = _read_int(os.path.join(sys_acpi, "pm_profile"))
    if pm is not None:
        out["pm_profile"] = pm
    return out


_GPE_LINE_RE = re.compile(r"^\s*(\d+)\s+(\S+)")


def parse_gpe(text: Optional[str]) -> dict:
    """First whitespace token is the count ; second is a status flag
    word ('EN', 'invalid', 'enabled', etc.)."""
    if not text:
        return {}
    m = _GPE_LINE_RE.match(text)
    if not m:
        return {}
    return {"count": int(m.group(1)), "flag": m.group(2)}


def walk_interrupts(sys_acpi: str = _SYS_ACPI) -> list:
    irq_dir = os.path.join(sys_acpi, "interrupts")
    if not os.path.isdir(irq_dir):
        return []
    out: list = []
    try:
        names = sorted(os.listdir(irq_dir))
    except OSError:
        return []
    for n in names:
        parsed = parse_gpe(_read(os.path.join(irq_dir, n)))
        if not parsed:
            continue
        parsed["name"] = n
        out.append(parsed)
    return out


def parse_wakeup(text: Optional[str]) -> list:
    """/proc/acpi/wakeup format :
        Device  S-state   Status   Sysfs node
        XHC     S3        *enabled  pci:0000:00:14.0
    """
    if not text:
        return []
    lines = text.splitlines()
    out: list = []
    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) < 3:
            continue
        device = parts[0]
        s_state = parts[1]
        status = parts[2]
        sysfs = parts[3] if len(parts) > 3 else ""
        enabled = status.startswith("*") and "enabled" in status
        out.append({"device": device, "s_state": s_state,
                      "status": status, "sysfs": sysfs,
                      "enabled": enabled})
    return out


_RECIPE_PROFILE_TO_PERF = (
    "# Switch platform_profile from 'quiet' to 'balanced-performance'\n"
    "# to lift CPU + GPU TDP envelopes :\n"
    "echo balanced-performance | \\\n"
    "  sudo tee /sys/firmware/acpi/platform_profile\n"
    "# Persistent (varies by distro) :\n"
    "#   GNOME : Settings → Power → Performance mode\n"
    "#   Manual: systemd unit that writes on boot."
)

_RECIPE_GPE_STORM = (
    "# A GPE is firing hundreds of times/s — broken DSDT method\n"
    "# retriggering. Inspect the offender :\n"
    "grep -ri \"gpe<NN>\" /sys/firmware/acpi/tables/*.dsl 2>/dev/null\n"
    "# Workaround : mask the GPE (effective until reboot) :\n"
    "echo mask | sudo tee /sys/firmware/acpi/interrupts/gpe<NN>\n"
    "# Long-term : BIOS update or kernel cmdline acpi=strict."
)

_RECIPE_PCIE_ROOT_WAKEUP = (
    "# PCIe root-port wakeup enabled — keeps the GPU out of D3cold\n"
    "# and burns 30 W of idle power. Disable :\n"
    "echo <DEVICE> | sudo tee /proc/acpi/wakeup\n"
    "# (writing the device name toggles its wakeup-enable bit)\n"
    "# Persist via udev rule under /etc/udev/rules.d/."
)


_GPE_STORM_COUNT = 10_000
_WORKSTATION_PM_PROFILES = (7, 8)  # workstation / SOHO server


def _is_pcie_root_port(device: str) -> bool:
    # Typical ACPI names for PCIe root ports : RPxx, PXSX
    return bool(re.match(r"^RP\d+$", device or "") or device == "PXSX")


def classify(profile: dict, wakeups: list, gpes: list) -> dict:
    if not profile and not wakeups and not gpes:
        return {"verdict": "unknown",
                "reason": "/sys/firmware/acpi unreadable.",
                "recommendation": ""}
    storms = [g for g in gpes
                if g["name"].startswith("gpe")
                and g.get("count", 0) > _GPE_STORM_COUNT]
    if storms:
        top = max(storms, key=lambda g: g.get("count", 0))
        return {"verdict": "gpe_storm",
                "reason": (f"{len(storms)} GPE(s) with > "
                           f"{_GPE_STORM_COUNT} events since boot. "
                           f"Hottest : {top['name']}={top['count']}."),
                "recommendation": _RECIPE_GPE_STORM}
    rp_enabled = [w for w in wakeups
                    if w.get("enabled")
                    and _is_pcie_root_port(w.get("device", ""))]
    if rp_enabled:
        names = ", ".join(w["device"] for w in rp_enabled)
        return {"verdict": "pcie_root_wakeup",
                "reason": (f"{len(rp_enabled)} PCIe root-port(s) have "
                           f"wakeup ENABLED : {names}. Likely blocks "
                           f"GPU D3cold."),
                "recommendation": _RECIPE_PCIE_ROOT_WAKEUP}
    cur = profile.get("current")
    pm = profile.get("pm_profile")
    if (cur == "quiet" and pm in _WORKSTATION_PM_PROFILES):
        return {"verdict": "quiet_profile_on_workstation",
                "reason": (f"platform_profile=quiet on a "
                           f"workstation-class chassis (pm_profile="
                           f"{pm}) — losing 15-25 % sustained "
                           f"throughput silently."),
                "recommendation": _RECIPE_PROFILE_TO_PERF}
    if not profile.get("current"):
        return {"verdict": "no_platform_profile",
                "reason": ("/sys/firmware/acpi/platform_profile "
                           "absent on this host (typical for "
                           "desktops + VMs). Wakeup + GPE state "
                           "audited."),
                "recommendation": ""}
    return {"verdict": "ok",
            "reason": (f"platform_profile={cur or '?'} "
                       f"(pm_profile={pm}), "
                       f"{len(wakeups)} wakeup device(s), "
                       f"{len(gpes)} ACPI interrupt source(s) — "
                       f"no storm, no PCIe-root wakeup."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    if not os.path.isdir(_SYS_ACPI):
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": ("/sys/firmware/acpi "
                                    "unreadable."),
                         "recommendation": ""},
            "platform_profile": {}, "wakeups": [], "gpes": [],
        }
    profile = read_platform_profile(_SYS_ACPI)
    wakeups = parse_wakeup(_read(_PROC_ACPI_WAKEUP))
    gpes = walk_interrupts(_SYS_ACPI)
    verdict = classify(profile, wakeups, gpes)
    return {
        "ok": True,
        "platform_profile": profile,
        "wakeup_count": len(wakeups),
        "wakeups_enabled": [w for w in wakeups if w["enabled"]],
        "gpe_count": len(gpes),
        "top_gpes": sorted(gpes,
                            key=lambda g: -(g.get("count", 0)))[:10],
        "verdict": verdict,
    }
