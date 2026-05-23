"""Module bdi_writeback_audit — per-BDI writeback + readahead (R&D #56.2).

The Linux block-device infrastructure tracks per-device dirty-page
quotas + readahead in /sys/class/bdi/<major:minor>/. Distinct from
existing vm_sysctl_audit (which covers global vm.dirty_ratio etc.)
and from disk-IO modules (which look at queue depth and latency).

Why this matters on an LLM rig :

* read_ahead_kb = 128 (typical default) on NVMe leaves 2-4× of
  sequential bandwidth on the table during cold model load
  (loading a 30-GB GGUF benefits hugely from 1024+ KiB readahead).
* max_ratio = 1 on a real storage BDI (sometimes left over from
  an SD-card flush hack) starves the device of dirty-page quota.
* dirty_writeback_centisecs > 3000 (30 s) makes power-loss the
  guarantee not the exception for writes — bad on a CUDA box that
  trips OCP and reboots.

Reads :
  /sys/class/bdi/<maj:min>/{read_ahead_kb, max_ratio, min_ratio,
                              stable_pages_required}
  /sys/block/<dev>/dev                  (resolve maj:min → name)
  /sys/block/<dev>/queue/rotational
  /proc/partitions                      (filter real devices)
  /proc/sys/vm/dirty_writeback_centisecs
  /proc/sys/vm/dirty_expire_centisecs

Verdicts (priority-ordered) :
  stuck_max_ratio_1              ≥1 real storage BDI has
                                 max_ratio = 1 (1 % cap on dirty
                                 page quota).
  readahead_below_128k_on_nvme   ≥1 nvme device has read_ahead_kb
                                 ≤ 128 (cold-load throttle).
  writeback_centisecs_above_3000 dirty_writeback_centisecs > 3000.
  ok                             everything sane.
  unknown                        /sys/class/bdi absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "bdi_writeback_audit"


_SYS_BDI = "/sys/class/bdi"
_SYS_BLOCK = "/sys/block"
_PROC_PARTITIONS = "/proc/partitions"
_PROC_SYS_VM = "/proc/sys/vm"


_MAJMIN_RE = re.compile(r"^\d+:\d+$")


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


def list_bdis(sys_bdi: str = _SYS_BDI) -> List[dict]:
    """Enumerate /sys/class/bdi/<maj:min>/ entries."""
    if not os.path.isdir(sys_bdi):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_bdi)):
        if not _MAJMIN_RE.match(name):
            continue
        d = os.path.join(sys_bdi, name)
        out.append({
            "id": name,
            "read_ahead_kb": _read_int(
                os.path.join(d, "read_ahead_kb")),
            "max_ratio": _read_int(os.path.join(d, "max_ratio")),
            "min_ratio": _read_int(os.path.join(d, "min_ratio")),
            "stable_pages_required": _read_int(os.path.join(
                d, "stable_pages_required")),
        })
    return out


def map_devices(sys_block: str = _SYS_BLOCK
                  ) -> Dict[str, dict]:
    """Returns {maj:min → {name, rotational, is_nvme}}."""
    out: Dict[str, dict] = {}
    if not os.path.isdir(sys_block):
        return out
    for name in sorted(os.listdir(sys_block)):
        d = os.path.join(sys_block, name)
        dev = _read(os.path.join(d, "dev"))
        if dev is None:
            continue
        rot = _read_int(os.path.join(d, "queue", "rotational"))
        out[dev] = {
            "name": name,
            "rotational": rot,
            "is_nvme": name.startswith("nvme"),
        }
    return out


def parse_partitions(text: Optional[str]) -> List[str]:
    """Returns the major:minor of partitions present in
    /proc/partitions (used to filter out pseudo-BDIs)."""
    if not text:
        return []
    out: List[str] = []
    for line in text.splitlines()[2:]:  # skip header
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0].isdigit() and parts[1].isdigit():
            out.append(f"{parts[0]}:{parts[1]}")
    return out


def is_real_device(bdi_id: str,
                     real_majmin: List[str],
                     device_map: Dict[str, dict]) -> bool:
    """A BDI corresponds to a real storage device if its maj:min is
    in /proc/partitions or has a /sys/block/<name>/dev entry."""
    if bdi_id in real_majmin:
        return True
    if bdi_id in device_map:
        return True
    return False


def classify(bdis: List[dict],
              device_map: Dict[str, dict],
              real_majmin: List[str],
              writeback_cs: Optional[int],
              expire_cs: Optional[int]) -> dict:
    if not bdis:
        return {"verdict": "unknown",
                "reason": "/sys/class/bdi not readable.",
                "recommendation": ""}

    real_bdis = [b for b in bdis
                    if is_real_device(b["id"], real_majmin,
                                        device_map)]

    # 1) stuck_max_ratio_1 — real storage BDI capped at 1 %
    stuck = [b for b in real_bdis
                if (b.get("max_ratio") or 0) == 1]
    if stuck:
        sample = ", ".join(
            f"{b['id']}({device_map.get(b['id'], {}).get('name', '?')})"
            for b in stuck[:3])
        return {"verdict": "stuck_max_ratio_1",
                "reason": (f"{len(stuck)} real storage BDI(s) "
                          f"capped at max_ratio = 1 : {sample}. "
                          f"Likely a leftover SD-card flush hack."),
                "recommendation": _recipe_max_ratio(stuck[0]["id"])}

    # 2) readahead_below_128k_on_nvme
    low_nvme = []
    for b in real_bdis:
        info = device_map.get(b["id"])
        if info and info["is_nvme"]:
            ra = b.get("read_ahead_kb")
            if ra is not None and ra <= 128:
                low_nvme.append((b["id"], info["name"], ra))
    if low_nvme:
        sample = ", ".join(f"{name}({ra}kb)"
                              for _, name, ra in low_nvme[:3])
        return {"verdict": "readahead_below_128k_on_nvme",
                "reason": (f"{len(low_nvme)} NVMe device(s) with "
                          f"read_ahead_kb ≤ 128 : {sample}. Cold "
                          f"model load runs 2-4× slower."),
                "recommendation": _recipe_nvme_ra(low_nvme[0][1])}

    # 3) writeback_centisecs_above_3000
    if writeback_cs is not None and writeback_cs > 3000:
        return {"verdict": "writeback_centisecs_above_3000",
                "reason": (f"dirty_writeback_centisecs = "
                          f"{writeback_cs} (> 30 s). Power-loss "
                          f"data-loss window is wide."),
                "recommendation": _recipe_writeback()}

    return {"verdict": "ok",
            "reason": (f"{len(real_bdis)} real BDI(s), readahead "
                      f"and quotas look sane."),
            "recommendation": ""}


def status(config=None,
            sys_bdi: str = _SYS_BDI,
            sys_block: str = _SYS_BLOCK,
            proc_partitions: str = _PROC_PARTITIONS,
            proc_sys_vm: str = _PROC_SYS_VM) -> dict:
    bdis = list_bdis(sys_bdi)
    device_map = map_devices(sys_block)
    real_majmin = parse_partitions(_read(proc_partitions))
    writeback_cs = _read_int(os.path.join(
        proc_sys_vm, "dirty_writeback_centisecs"))
    expire_cs = _read_int(os.path.join(
        proc_sys_vm, "dirty_expire_centisecs"))
    ok = bool(bdis)
    verdict = classify(bdis, device_map, real_majmin,
                          writeback_cs, expire_cs)
    return {"ok": ok,
              "bdi_count": len(bdis),
              "bdis": bdis,
              "device_map": device_map,
              "real_partitions": real_majmin,
              "dirty_writeback_centisecs": writeback_cs,
              "dirty_expire_centisecs": expire_cs,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_max_ratio(bdi_id: str) -> str:
    return (f"# Restore the default 100 % max_ratio cap :\n"
            f"echo 100 | sudo tee /sys/class/bdi/{bdi_id}/max_ratio\n"
            f"# Persist via /etc/tmpfiles.d/99-bdi.conf :\n"
            f"#   w /sys/class/bdi/{bdi_id}/max_ratio - - - - 100\n")


def _recipe_nvme_ra(dev_name: str) -> str:
    return (f"# Raise readahead on the NVMe device :\n"
            f"echo 1024 | sudo tee /sys/block/{dev_name}/queue/read_ahead_kb\n"
            f"# Persist via /etc/udev/rules.d/60-readahead.rules :\n"
            f"#   ACTION==\"add|change\", KERNEL==\"nvme[0-9]*n[0-9]*\",\\\n"
            f"#     ATTR{{queue/read_ahead_kb}}=\"1024\"\n")


def _recipe_writeback() -> str:
    return ("# Tighten the writeback interval (5 s is the kernel\n"
            "# default) :\n"
            "echo 500 | sudo tee /proc/sys/vm/dirty_writeback_centisecs\n"
            "# Persist via /etc/sysctl.d/99-writeback.conf :\n"
            "#   vm.dirty_writeback_centisecs = 500\n")
