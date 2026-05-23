"""Module devlink_smartnic_audit — kernel device-links (R&D #64.1).

Reads /sys/class/devlink/<supplier--consumer>/{status, runtime_pm,
auto_remove_on, sync_state_only}.

Note : the original survey intent ("SmartNIC devlink eswitch")
was based on a name clash — netlink-devlink is a separate
subsystem accessed via netlink protocol, not sysfs. The sysfs
/sys/class/devlink path is the kernel *device-link framework*,
which represents supplier→consumer driver dependencies between
devices.

Why the kernel device-link audit is still useful on an LLM rig :

* A device-link stuck in `supplier_not_ready` blocks the
  consumer's driver from probing — typical when one component
  of a composite device (e.g., GPU + audio + USB-C controller)
  is missing firmware.
* `dormant` or `consumer_unbinding` left over from a partial
  driver reload can wedge runtime-PM resume after suspend.

Reads :
  /sys/class/devlink/<link>/{status, runtime_pm, auto_remove_on,
                                sync_state_only}

Verdicts (priority-ordered) :
  supplier_not_ready          ≥1 link with status =
                              'supplier_not_ready' — consumer
                              driver probe blocked.
  consumer_unbinding          ≥1 link mid-unbind, runtime-PM
                              may wedge.
  dormant_links_present       ≥1 link in 'dormant' — informational.
  ok                          all links in active/not-tracked.
  unknown                     /sys/class/devlink absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "devlink_smartnic_audit"


_SYS_DEVLINK = "/sys/class/devlink"


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


def list_devlinks(sys_devlink: str = _SYS_DEVLINK) -> List[dict]:
    if not os.path.isdir(sys_devlink):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_devlink)):
        d = os.path.join(sys_devlink, name)
        if not os.path.isdir(d):
            continue
        out.append({
            "id": name,
            "status": _read(os.path.join(d, "status")),
            "runtime_pm": _read_int(
                os.path.join(d, "runtime_pm")),
            "auto_remove_on": _read(
                os.path.join(d, "auto_remove_on")),
            "sync_state_only": _read_int(
                os.path.join(d, "sync_state_only")),
        })
    return out


def classify(links: List[dict]) -> dict:
    if not links:
        return {"verdict": "unknown",
                "reason": ("/sys/class/devlink absent — kernel "
                          "without device-link framework or no "
                          "links registered."),
                "recommendation": ""}

    snr = [l for l in links
              if l.get("status") == "supplier_not_ready"]
    if snr:
        sample = ", ".join(l["id"] for l in snr[:3])
        return {"verdict": "supplier_not_ready",
                "reason": (f"{len(snr)} device-link(s) in "
                          f"supplier_not_ready : {sample}. "
                          f"Consumer driver probe blocked — "
                          f"missing firmware / sub-device."),
                "recommendation": _recipe_snr()}

    unbinding = [l for l in links
                    if l.get("status") == "consumer_unbinding"]
    if unbinding:
        sample = ", ".join(l["id"] for l in unbinding[:3])
        return {"verdict": "consumer_unbinding",
                "reason": (f"{len(unbinding)} device-link(s) "
                          f"mid-unbind : {sample}. Runtime-PM "
                          f"resume may wedge."),
                "recommendation": _recipe_unbinding()}

    dormant = [l for l in links if l.get("status") == "dormant"]
    if dormant:
        sample = ", ".join(l["id"] for l in dormant[:3])
        return {"verdict": "dormant_links_present",
                "reason": (f"{len(dormant)} device-link(s) in "
                          f"'dormant' state : {sample}. "
                          f"Informational — supplier hasn't "
                          f"completed probe."),
                "recommendation": ""}

    return {"verdict": "ok",
            "reason": (f"{len(links)} device-link(s), all "
                      f"active / not-tracked."),
            "recommendation": ""}


def status(config=None, sys_devlink: str = _SYS_DEVLINK) -> dict:
    links = list_devlinks(sys_devlink)
    ok = bool(links)
    verdict = classify(links)
    # Build a status histogram for the UI.
    histogram: dict = {}
    for l in links:
        s = l.get("status") or "unknown"
        histogram[s] = histogram.get(s, 0) + 1
    return {"ok": ok,
              "link_count": len(links),
              "status_histogram": histogram,
              "links_sample": links[:8],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_snr() -> str:
    return ("# Find the missing supplier device — usually firmware\n"
            "# load failure :\n"
            "grep . /sys/class/devlink/*/status | grep supplier_not\n"
            "dmesg | grep -iE 'firmware|deferred probe' | tail\n"
            "# If a known driver, retry probe :\n"
            "echo 1 | sudo tee /sys/bus/pci/devices/<supplier>/remove\n"
            "echo 1 | sudo tee /sys/bus/pci/rescan\n")


def _recipe_unbinding() -> str:
    return ("# Mid-unbind links can wedge resume :\n"
            "grep . /sys/class/devlink/*/status\n"
            "# A reboot is the safe escape ; otherwise force-rebind\n"
            "# the consumer :\n"
            "echo <bdf> | sudo tee /sys/bus/pci/drivers/<drv>/bind\n")
