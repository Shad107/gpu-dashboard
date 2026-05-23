"""Module sata_link_pm_audit — SATA Aggressive Link PM (R&D #56.1).

Reads /sys/class/scsi_host/host*/link_power_management_policy (and
the per-link /sys/class/ata_link/link*/sata_spd_limit fallback).

Why this matters on an LLM rig with a SATA swap / dataset SSD :

* SATA Aggressive Link PM (ALPM) puts the PHY into Partial or
  Slumber when idle. Coming back up adds 10-200 ms of tail
  latency to the first IO after a quiet period.
* On the host running llama.cpp / vLLM, that first mmap page-in
  after an idle window stalls inference for tens of ms — invisible
  in `iostat` (the device looks fast once it's awake), but
  measurable in tokens-per-second jitter.

Reads :
  /sys/class/scsi_host/host*/link_power_management_policy
  /sys/class/ata_link/link*/sata_spd_limit            (fallback)
  /sys/class/ata_link/link*/sata_spd

Verdicts (priority-ordered, most aggressive first) :
  min_power                    ≥1 host on 'min_power' (Slumber
                               state — worst wakeup latency).
  med_power_with_dipm          ≥1 host on 'med_power_with_dipm'
                               (Partial + DIPM).
  medium_power                 ≥1 host on 'medium_power' (Partial
                               state, no DIPM).
  ok                           all hosts on 'max_performance' OR
                               policy file absent.
  unknown                      /sys/class/scsi_host empty.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "sata_link_pm_audit"


_SYS_SCSI_HOST = "/sys/class/scsi_host"
_SYS_ATA_LINK = "/sys/class/ata_link"


_HOST_DIR_RE = re.compile(r"^host(\d+)$")
_LINK_DIR_RE = re.compile(r"^link(\d+)$")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def list_host_policies(sys_scsi: str = _SYS_SCSI_HOST) -> List[dict]:
    """Returns one entry per SCSI host with link_power_management_policy."""
    if not os.path.isdir(sys_scsi):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_scsi)):
        if not _HOST_DIR_RE.match(name):
            continue
        d = os.path.join(sys_scsi, name)
        policy = _read(os.path.join(d,
                                         "link_power_management_policy"))
        out.append({"id": name, "policy": policy})
    return out


def list_link_speeds(sys_ata_link: str = _SYS_ATA_LINK) -> List[dict]:
    """Returns per-link sata_spd + sata_spd_limit (best-effort)."""
    if not os.path.isdir(sys_ata_link):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_ata_link)):
        if not _LINK_DIR_RE.match(name):
            continue
        d = os.path.join(sys_ata_link, name)
        out.append({
            "id": name,
            "sata_spd": _read(os.path.join(d, "sata_spd")),
            "sata_spd_limit": _read(
                os.path.join(d, "sata_spd_limit")),
        })
    return out


def classify(host_policies: List[dict]) -> dict:
    hosts_with_policy = [h for h in host_policies
                              if h.get("policy")]
    if not host_policies:
        return {"verdict": "unknown",
                "reason": ("/sys/class/scsi_host is empty — no "
                          "SCSI / SATA hosts present."),
                "recommendation": ""}

    if not hosts_with_policy:
        return {"verdict": "ok",
                "reason": (f"{len(host_policies)} host(s) present, "
                          f"no link_power_management_policy file "
                          f"on any — non-SATA transports."),
                "recommendation": ""}

    min_pwr = [h["id"] for h in hosts_with_policy
                  if h["policy"] == "min_power"]
    if min_pwr:
        sample = ", ".join(min_pwr[:3])
        return {"verdict": "min_power",
                "reason": (f"{len(min_pwr)} host(s) on 'min_power' "
                          f"(Slumber state) : {sample}. Tail "
                          f"latency adds 50-200 ms to first IO "
                          f"after idle."),
                "recommendation": _recipe_alpm_off(min_pwr[0])}

    dipm = [h["id"] for h in hosts_with_policy
              if h["policy"] == "med_power_with_dipm"]
    if dipm:
        sample = ", ".join(dipm[:3])
        return {"verdict": "med_power_with_dipm",
                "reason": (f"{len(dipm)} host(s) on "
                          f"'med_power_with_dipm' (Partial + "
                          f"Device-Initiated PM) : {sample}."),
                "recommendation": _recipe_alpm_off(dipm[0])}

    med = [h["id"] for h in hosts_with_policy
             if h["policy"] == "medium_power"]
    if med:
        sample = ", ".join(med[:3])
        return {"verdict": "medium_power",
                "reason": (f"{len(med)} host(s) on 'medium_power' "
                          f"(Partial state) : {sample}."),
                "recommendation": _recipe_alpm_off(med[0])}

    return {"verdict": "ok",
            "reason": (f"{len(hosts_with_policy)} SATA host(s) on "
                      f"'max_performance' — no ALPM tail latency."),
            "recommendation": ""}


def status(config=None,
            sys_scsi: str = _SYS_SCSI_HOST,
            sys_ata_link: str = _SYS_ATA_LINK) -> dict:
    host_policies = list_host_policies(sys_scsi)
    link_speeds = list_link_speeds(sys_ata_link)
    ok = bool(host_policies)
    verdict = classify(host_policies)
    return {"ok": ok,
              "host_count": len(host_policies),
              "hosts": host_policies,
              "link_count": len(link_speeds),
              "links": link_speeds,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_alpm_off(sample_host: str) -> str:
    return ("# Disable SATA Aggressive Link PM on every host :\n"
            "for h in /sys/class/scsi_host/host*; do\n"
            "  [ -e $h/link_power_management_policy ] && \\\n"
            "    echo max_performance | sudo tee $h/link_power_management_policy\n"
            "done\n"
            f"# Verify : cat /sys/class/scsi_host/{sample_host}/link_power_management_policy\n"
            "# Persist via /etc/tmpfiles.d/99-sata-alpm.conf :\n"
            "#   w /sys/class/scsi_host/host*/link_power_management_policy - - - - max_performance\n"
            "# Or systemd-tmpfiles --create.\n")
