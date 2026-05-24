"""Module cpu_isolation_audit — CPU isolation / nohz_full /
offline audit (R&D #74.1).

A single-GPU desktop usually has zero isolated/nohz/offline
CPUs. Stale `isolcpus=` or `nohz_full=` tokens left in the
kernel cmdline from a previous RT / tickless tinkering session
silently steal scheduler slots from CUDA worker threads — the
classic symptom is "training throughput dropped 25 % after a
distro upgrade and we can't figure out why."

Reads :
  /sys/devices/system/cpu/{isolated,nohz_full,offline,
                            possible,present,kernel_max}
  /proc/cmdline (parses isolcpus= and nohz_full= tokens)

Verdicts (priority order) :
  isolation_misaligned_cmdline   /sys isolated set !=
                                   /proc/cmdline isolcpus
                                   tokens (drift).
  nohz_full_without_isolcpus     nohz_full populated AND
                                   isolated empty (one half of
                                   tickless setup applied).
  heavy_isolation_on_desktop     > 50 % of present CPUs are
                                   isolated (RT profile on a
                                   single-GPU box).
  partial_offline_unexpected     ≥1 CPU offline that's in
                                   `possible`.
  ok                              clean (all defaults).
  unknown                         /sys/devices/system/cpu absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Set


NAME = "cpu_isolation_audit"


_SYS_CPU = "/sys/devices/system/cpu"
_PROC_CMDLINE = "/proc/cmdline"


_ISOLCPUS_RE = re.compile(r"\bisolcpus=([^\s]+)")
_NOHZ_FULL_RE = re.compile(r"\bnohz_full=([^\s]+)")


_HEAVY_ISOLATION_FRAC = 0.50


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


def parse_cpu_list(text: Optional[str]) -> Set[int]:
    """Parses '0-3,5,7-9' → {0,1,2,3,5,7,8,9}. Tolerates
    '(null)' literal that the kernel writes when nohz_full
    isn't set."""
    if not text:
        return set()
    text = text.strip()
    if not text or text == "(null)":
        return set()
    out: Set[int] = set()
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            try:
                a, b = tok.split("-", 1)
                out.update(range(int(a), int(b) + 1))
            except ValueError:
                continue
        else:
            try:
                out.add(int(tok))
            except ValueError:
                continue
    return out


def parse_cmdline(text: Optional[str]) -> dict:
    if text is None:
        return {"isolcpus": set(), "nohz_full": set(),
                  "had_cmdline": False}
    m_iso = _ISOLCPUS_RE.search(text)
    m_nohz = _NOHZ_FULL_RE.search(text)
    # isolcpus may carry domain-flags : isolcpus=managed_irq,1-3
    iso_token = m_iso.group(1) if m_iso else ""
    # Strip leading flag word (e.g. "managed_irq," or
    # "domain,")
    if iso_token and "," in iso_token:
        head, _, tail = iso_token.partition(",")
        if not any(c.isdigit() for c in head):
            iso_token = tail
    return {"isolcpus": parse_cpu_list(iso_token),
              "nohz_full": parse_cpu_list(
                  m_nohz.group(1) if m_nohz else ""),
              "had_cmdline": True}


def classify(present: bool,
              isolated: Set[int],
              nohz_full: Set[int],
              offline: Set[int],
              possible: Set[int],
              cmdline_isolcpus: Set[int],
              cmdline_nohz_full: Set[int]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": ("/sys/devices/system/cpu absent — "
                          "unusual non-x86/ARM kernel build."),
                "recommendation": ""}

    # 1) isolation_misaligned_cmdline — sysfs ≠ cmdline
    if (cmdline_isolcpus
            and isolated != cmdline_isolcpus):
        return {"verdict": "isolation_misaligned_cmdline",
                "reason": (f"isolcpus= on cmdline lists "
                          f"{sorted(cmdline_isolcpus)} but "
                          f"/sys reports isolated="
                          f"{sorted(isolated)}. Drift between "
                          f"requested and effective."),
                "recommendation": _recipe_misaligned()}

    # 2) nohz_full_without_isolcpus
    if nohz_full and not isolated:
        return {"verdict": "nohz_full_without_isolcpus",
                "reason": (f"nohz_full = {sorted(nohz_full)} "
                          f"but isolated set is empty. "
                          f"Tickless without isolation steals "
                          f"timer ticks from CUDA threads."),
                "recommendation": _recipe_nohz_without_iso()}

    # 3) heavy_isolation_on_desktop
    if possible and isolated:
        frac = len(isolated) / len(possible)
        if frac > _HEAVY_ISOLATION_FRAC:
            return {"verdict": "heavy_isolation_on_desktop",
                    "reason": (f"{len(isolated)} / "
                              f"{len(possible)} CPUs isolated "
                              f"({100*frac:.0f}%). RT profile "
                              f"on a single-GPU box is unusual."),
                    "recommendation": _recipe_heavy_iso()}

    # 4) partial_offline_unexpected
    if offline:
        offline_in_possible = offline & possible
        if offline_in_possible:
            return {"verdict": "partial_offline_unexpected",
                    "reason": (f"{sorted(offline_in_possible)} "
                              f"CPU(s) offline. Manual hot-"
                              f"unplug or thermal lockout."),
                    "recommendation": _recipe_offline()}

    return {"verdict": "ok",
            "reason": (f"{len(possible)} CPUs possible ; "
                      f"isolated={sorted(isolated) or 'none'} ; "
                      f"nohz_full="
                      f"{sorted(nohz_full) or 'none'} ; "
                      f"offline="
                      f"{sorted(offline) or 'none'}."),
            "recommendation": ""}


def status(config=None,
            sys_cpu: str = _SYS_CPU,
            proc_cmdline: str = _PROC_CMDLINE) -> dict:
    present = os.path.isdir(sys_cpu)
    isolated = parse_cpu_list(
        _read(os.path.join(sys_cpu, "isolated"))) \
        if present else set()
    nohz_full = parse_cpu_list(
        _read(os.path.join(sys_cpu, "nohz_full"))) \
        if present else set()
    offline = parse_cpu_list(
        _read(os.path.join(sys_cpu, "offline"))) \
        if present else set()
    possible = parse_cpu_list(
        _read(os.path.join(sys_cpu, "possible"))) \
        if present else set()
    present_set = parse_cpu_list(
        _read(os.path.join(sys_cpu, "present"))) \
        if present else set()
    kernel_max = (_read_int(os.path.join(sys_cpu, "kernel_max"))
                       if present else None)
    cmd = parse_cmdline(_read(proc_cmdline))
    verdict = classify(present, isolated, nohz_full,
                          offline, possible,
                          cmd["isolcpus"], cmd["nohz_full"])
    return {"ok": present,
              "present": present,
              "isolated": sorted(isolated),
              "nohz_full": sorted(nohz_full),
              "offline": sorted(offline),
              "possible_count": len(possible),
              "present_count": len(present_set),
              "kernel_max": kernel_max,
              "cmdline_isolcpus": sorted(cmd["isolcpus"]),
              "cmdline_nohz_full": sorted(cmd["nohz_full"]),
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_misaligned() -> str:
    return ("# Cmdline asks for isolcpus= but /sys/...isolated\n"
            "# is different. Audit :\n"
            "cat /sys/devices/system/cpu/isolated\n"
            "cat /proc/cmdline | tr ' ' '\\n' | grep -E "
            "'isolcpus|nohz'\n"
            "# Reconcile via bootloader (GRUB_CMDLINE_LINUX) and\n"
            "# regenerate :\n"
            "sudo update-grub      # Debian/Ubuntu\n"
            "sudo grub2-mkconfig   # Fedora/RHEL\n")


def _recipe_nohz_without_iso() -> str:
    return ("# nohz_full without matching isolcpus is a half-\n"
            "# tickless config. Either remove nohz_full= or\n"
            "# add isolcpus=<same set> to /etc/default/grub :\n"
            "cat /proc/cmdline | tr ' ' '\\n' | grep nohz\n")


def _recipe_heavy_iso() -> str:
    return ("# > 50 % of CPUs are isolated. Most desktops want\n"
            "# isolated to be empty. Remove isolcpus= from the\n"
            "# bootloader cmdline and reboot :\n"
            "sudo $EDITOR /etc/default/grub  # GRUB_CMDLINE_LINUX\n"
            "sudo update-grub\n")


def _recipe_offline() -> str:
    return ("# CPU is offline. Bring it back online :\n"
            "for c in /sys/devices/system/cpu/cpu*/online; do\n"
            "  echo 1 | sudo tee \"$c\" 2>/dev/null\n"
            "done\n"
            "# Investigate why it dropped :\n"
            "sudo dmesg | grep -iE 'cpu.*down|thermal' | tail\n")
