"""Module scsi_transport_audit — SCSI mid-layer (R&D #58.3).

Distinct from R&D #56.1 sata_link_pm_audit (which only reads
link_power_management_policy) and from block-queue scheduler
modules — this targets the SCSI mid-layer attributes that quietly
murder NVMe/SATA throughput on an LLM host.

Reads :
  /sys/class/scsi_disk/*/{cache_type, FUA, protection_type,
                              manage_start_stop, allow_restart}
  /sys/class/scsi_device/*/device/{queue_depth, queue_type,
                                       state, type, eh_timeout,
                                       timeout, iocounterbits}
  /sys/class/scsi_host/host*/{use_blk_mq, can_queue, cmd_per_lun}

Catches :

* SATA SSD holding ~/.cache/huggingface reports cache_type =
  "write through" — BIOS quirk or post-power-loss safe default
  drops sequential write throughput by 5-10× and no other module
  flags this.
* queue_depth = 1 on a non-cdrom device — typical sign of a fall-
  back PIO mode after AHCI re-init, throttles every IO.
* SCSI device in state != "running" — usually a kernel-removed
  device that the user forgot to clean up.

Verdicts (priority-ordered) :
  write_cache_disabled        ≥1 SCSI disk on 'write through'
                              cache_type.
  queue_depth_starved         ≥1 non-cdrom device with
                              queue_depth ≤ 1.
  device_offline              ≥1 device state != 'running'
                              (excluding cdrom).
  ok                          mid-layer healthy.
  unknown                     /sys/class/scsi_disk + /sys/class/
                              scsi_device both absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "scsi_transport_audit"


_SYS_SCSI_DISK = "/sys/class/scsi_disk"
_SYS_SCSI_DEVICE = "/sys/class/scsi_device"
_SYS_SCSI_HOST = "/sys/class/scsi_host"

_HOST_DIR_RE = re.compile(r"^host(\d+)$")


# SCSI type codes : 0 = direct-access (disk), 5 = CD-ROM, 7 = tape
_SCSI_TYPE_DISK = 0
_SCSI_TYPE_CDROM = 5


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


def list_scsi_disks(sys_scsi_disk: str = _SYS_SCSI_DISK
                      ) -> List[dict]:
    if not os.path.isdir(sys_scsi_disk):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_scsi_disk)):
        d = os.path.join(sys_scsi_disk, name)
        out.append({
            "id": name,
            "cache_type": _read(os.path.join(d, "cache_type")),
            "FUA": _read_int(os.path.join(d, "FUA")),
            "protection_type": _read_int(
                os.path.join(d, "protection_type")),
            "manage_start_stop": _read_int(
                os.path.join(d, "manage_start_stop")),
            "allow_restart": _read_int(
                os.path.join(d, "allow_restart")),
        })
    return out


def list_scsi_devices(sys_scsi_device: str = _SYS_SCSI_DEVICE
                        ) -> List[dict]:
    if not os.path.isdir(sys_scsi_device):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_scsi_device)):
        dev = os.path.join(sys_scsi_device, name, "device")
        if not os.path.isdir(dev):
            continue
        out.append({
            "id": name,
            "queue_depth": _read_int(
                os.path.join(dev, "queue_depth")),
            "state": _read(os.path.join(dev, "state")),
            "type": _read_int(os.path.join(dev, "type")),
            "timeout": _read_int(os.path.join(dev, "timeout")),
            "eh_timeout": _read_int(
                os.path.join(dev, "eh_timeout")),
        })
    return out


def list_scsi_hosts(sys_scsi_host: str = _SYS_SCSI_HOST
                      ) -> List[dict]:
    if not os.path.isdir(sys_scsi_host):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_scsi_host)):
        if not _HOST_DIR_RE.match(name):
            continue
        d = os.path.join(sys_scsi_host, name)
        out.append({
            "id": name,
            "use_blk_mq": _read_int(
                os.path.join(d, "use_blk_mq")),
            "can_queue": _read_int(
                os.path.join(d, "can_queue")),
            "cmd_per_lun": _read_int(
                os.path.join(d, "cmd_per_lun")),
        })
    return out


def classify(disks: List[dict], devices: List[dict],
              hosts: List[dict]) -> dict:
    if not disks and not devices:
        return {"verdict": "unknown",
                "reason": ("Neither /sys/class/scsi_disk nor "
                          "/sys/class/scsi_device present — no "
                          "SCSI / SATA transport visible."),
                "recommendation": ""}

    # 1) write_cache_disabled
    bad = [d for d in disks
              if (d.get("cache_type") or "").lower() == "write through"]
    if bad:
        sample = ", ".join(d["id"] for d in bad[:3])
        return {"verdict": "write_cache_disabled",
                "reason": (f"{len(bad)} SCSI disk(s) on cache_type "
                          f"= 'write through' : {sample}. "
                          f"Sequential write throughput drops "
                          f"5-10×."),
                "recommendation": _recipe_writeback(bad[0]["id"])}

    # 2) queue_depth_starved — exclude cdrom (type 5)
    starved = []
    for d in devices:
        if d.get("type") in (_SCSI_TYPE_CDROM,):
            continue
        qd = d.get("queue_depth")
        if qd is not None and qd <= 1:
            starved.append(d["id"])
    if starved:
        sample = ", ".join(starved[:3])
        return {"verdict": "queue_depth_starved",
                "reason": (f"{len(starved)} non-cdrom SCSI device(s) "
                          f"with queue_depth ≤ 1 : {sample}. "
                          f"Likely PIO fallback after AHCI re-init."),
                "recommendation": _recipe_qd()}

    # 3) device_offline — state != running
    offline = []
    for d in devices:
        if d.get("type") == _SCSI_TYPE_CDROM:
            continue
        st = d.get("state")
        if st and st != "running":
            offline.append(f"{d['id']}({st})")
    if offline:
        sample = ", ".join(offline[:3])
        return {"verdict": "device_offline",
                "reason": (f"{len(offline)} SCSI device(s) not "
                          f"running : {sample}."),
                "recommendation": _recipe_rescan()}

    return {"verdict": "ok",
            "reason": (f"{len(disks)} disk(s), {len(devices)} "
                      f"device(s), {len(hosts)} host(s) — "
                      f"mid-layer healthy."),
            "recommendation": ""}


def status(config=None,
            sys_scsi_disk: str = _SYS_SCSI_DISK,
            sys_scsi_device: str = _SYS_SCSI_DEVICE,
            sys_scsi_host: str = _SYS_SCSI_HOST) -> dict:
    disks = list_scsi_disks(sys_scsi_disk)
    devices = list_scsi_devices(sys_scsi_device)
    hosts = list_scsi_hosts(sys_scsi_host)
    ok = bool(disks or devices)
    verdict = classify(disks, devices, hosts)
    return {"ok": ok,
              "disk_count": len(disks),
              "disks": disks,
              "device_count": len(devices),
              "devices": devices,
              "host_count": len(hosts),
              "hosts": hosts,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_writeback(sample_id: str) -> str:
    return (f"# Restore write-back cache on the affected disk :\n"
            f"echo 'write back' | sudo tee /sys/class/scsi_disk/{sample_id}/cache_type\n"
            f"# This is runtime only — persist via udev or your\n"
            f"# storage controller BIOS (some HBAs force write-\n"
            f"# through after a UPS event).\n")


def _recipe_qd() -> str:
    return ("# Check why queue depth is 1 — often a kernel rescue\n"
            "# attempt after IO errors :\n"
            "dmesg --since '10 minutes ago' | grep -E 'sd[a-z]|ata|scsi'\n"
            "# Bump queue depth :\n"
            "echo 31 | sudo tee /sys/class/scsi_device/<id>/device/queue_depth\n"
            "# Inspect the underlying ATA link :\n"
            "cat /sys/class/ata_link/link*/sata_spd 2>/dev/null\n")


def _recipe_rescan() -> str:
    return ("# Rescan / unblock the device :\n"
            "echo running | sudo tee /sys/class/scsi_device/<id>/device/state\n"
            "# Or schedule a full SCSI bus rescan :\n"
            "echo '- - -' | sudo tee /sys/class/scsi_host/<host>/scan\n")
