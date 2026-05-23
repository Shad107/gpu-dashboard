"""Module slab_audit — SLUB slab-cache leak/fragmentation (R&D #44.2).

SLUB (the default Linux slab allocator since 2008) exposes per-
cache stats at /proc/slabinfo (root-only since CVE-2017-18017
mitigations) and /sys/kernel/slab/<name>/{slabs, partial, objects,
object_size, objs_per_slab, total_objects, cpu_slabs} (also
root-only on modern distros, mode 0400).

The actionable signals on a long-uptime homelab rig :

  fragmentation_ratio = (slabs - cpu_slabs - partial) / slabs
                        per cache → "how many full slabs vs
                        partials are we sitting on". > 30 % partial
                        slabs on a single cache often means a leak.
  resident_kb = slabs * objs_per_slab * object_size / 1024 — the
                top consumers (often `dentry` / `inode_cache` /
                `kmalloc-*` / `task_struct` / `vm_area_struct`).
  leak suspects : caches whose object count grew monotonically
                  across two snapshots (we'd need state — for
                  the v1 snapshot module, we surface the top-N
                  consumers + their partial slab ratio).

Verdicts :
  requires_root          /proc/slabinfo + /sys/kernel/slab fields
                         are 0400 root-only on this distro ; daemon
                         is running unprivileged. Recipe : add a
                         capability via systemd drop-in OR run
                         under sudo.
  fragmented             ≥1 large cache (≥ 50 MB resident) with
                         partial-slab ratio > 30 %.
  leak_suspect           ≥1 cache (≥ 10 MB resident) with > 80 %
                         partial slabs and > 10k objects — a typical
                         long-uptime accumulation pattern.
  ok                     no large caches over the fragmentation
                         threshold.
  no_slab_data           /sys/kernel/slab is empty (CONFIG_SLUB=n
                         on this kernel — exotic).
  unknown                /sys/kernel/slab unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "slab_audit"


_PROC_SLABINFO = "/proc/slabinfo"
_SYS_KERNEL_SLAB = "/sys/kernel/slab"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except PermissionError:
        return None
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


def parse_slabinfo(text: str) -> list:
    """Parse /proc/slabinfo lines.

    Header format (since 2.6.10) :
      slabinfo - version: 2.1
      # name <active_objs> <num_objs> <objsize> <objperslab>
      #   <pagesperslab> : tunables ... : slabdata ...

    We parse the data lines : the first 6 tokens are name +
    five numeric fields, then we have ' : tunables ... '.
    """
    out: list = []
    if not text:
        return out
    for line in text.splitlines():
        if not line or line.startswith("#") or line.startswith("slabinfo"):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue
        name = parts[0]
        try:
            active = int(parts[1])
            num = int(parts[2])
            objsize = int(parts[3])
            objperslab = int(parts[4])
            pagesperslab = int(parts[5])
        except ValueError:
            continue
        out.append({
            "name": name,
            "active_objs": active,
            "num_objs": num,
            "object_size": objsize,
            "objs_per_slab": objperslab,
            "pages_per_slab": pagesperslab,
            "resident_kb": (num * objsize) // 1024,
        })
    return out


def read_sysfs_slab(sys_kernel_slab: str = _SYS_KERNEL_SLAB) -> list:
    """Fall-back path : walk /sys/kernel/slab/<name>/{slabs,
    objects, object_size, objs_per_slab, partial, cpu_slabs}.

    Returns [{name, objects, object_size, partial, slabs, ...}]
    or empty list if everything is permission-denied (caller
    distinguishes between "no cache" and "no permission").
    """
    if not os.path.isdir(sys_kernel_slab):
        return []
    out: list = []
    permission_seen = False
    try:
        names = os.listdir(sys_kernel_slab)
    except OSError:
        return []
    for name in sorted(names):
        ddir = os.path.join(sys_kernel_slab, name)
        if not os.path.isdir(ddir):
            continue
        objects = _read_int(os.path.join(ddir, "objects"))
        if objects is None and _has_permission_error(
                os.path.join(ddir, "objects")):
            permission_seen = True
        object_size = _read_int(os.path.join(ddir, "object_size"))
        slabs = _read_int(os.path.join(ddir, "slabs"))
        partial = _read_int(os.path.join(ddir, "partial"))
        cpu_slabs = _read_int(os.path.join(ddir, "cpu_slabs"))
        objs_per_slab = _read_int(os.path.join(ddir, "objs_per_slab"))
        if (objects is None and object_size is None
                and slabs is None and partial is None):
            continue
        resident_kb = ((objects or 0) * (object_size or 0)) // 1024
        out.append({
            "name": name,
            "objects": objects,
            "object_size": object_size,
            "slabs": slabs,
            "partial": partial,
            "cpu_slabs": cpu_slabs,
            "objs_per_slab": objs_per_slab,
            "resident_kb": resident_kb,
        })
    if not out and permission_seen:
        return []  # caller will detect requires_root via _probe()
    return out


def _has_permission_error(path: str) -> bool:
    try:
        with open(path):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def _probe_permission(sys_kernel_slab: str = _SYS_KERNEL_SLAB) -> bool:
    """Return True if we *should* be able to read but can't (i.e.,
    a permission error)."""
    try:
        names = os.listdir(sys_kernel_slab)
    except OSError:
        return False
    for name in names:
        ddir = os.path.join(sys_kernel_slab, name)
        if not os.path.isdir(ddir):
            continue
        probe = os.path.join(ddir, "object_size")
        if _has_permission_error(probe):
            return True
        # If we can read this one, the user has access.
        return False
    return False


_RECIPE_REQUIRES_ROOT = (
    "# /proc/slabinfo + /sys/kernel/slab/*/object_size are 0400\n"
    "# root-only on this distro. To grant read access without\n"
    "# running the daemon as root, drop CAP_SYS_ADMIN into the\n"
    "# service unit :\n"
    "systemctl --user edit gpu-dashboard.service\n"
    "# Add :\n"
    "# [Service]\n"
    "# AmbientCapabilities=CAP_DAC_READ_SEARCH\n"
    "# Restart : systemctl --user daemon-reload &&\n"
    "#           systemctl --user restart gpu-dashboard.service\n"
    "# (CAP_DAC_READ_SEARCH bypasses the 0400 mode check.)"
)

_RECIPE_FRAGMENTED = (
    "# A slab cache is heavily fragmented (≥ 30 % partial slabs on\n"
    "# a > 50 MB cache). Most common culprit : `dentry` /\n"
    "# `inode_cache` accumulating across long uptime. Trim :\n"
    "echo 2 | sudo tee /proc/sys/vm/drop_caches   # 2 = dentries+inodes\n"
    "# Or 3 for pagecache + dentries + inodes — but warn : drop_caches\n"
    "# stalls every IO for 5-30 s on a busy box. Schedule for an\n"
    "# inference-idle window."
)

_RECIPE_LEAK_SUSPECT = (
    "# A slab cache shows leak-suspect pattern (> 80 % partial slabs\n"
    "# + > 10k objects on a > 10 MB cache). Sample the cache by name :\n"
    "sudo slabtop -o | head -20\n"
    "# Or directly tail the cache between snapshots :\n"
    "watch -n 5 'sudo cat /sys/kernel/slab/<NAME>/objects'\n"
    "# Growing monotonically = real leak ; report upstream (the\n"
    "# kernel may have a known regression for that cache name)."
)


_FRAG_RATIO_THRESHOLD = 0.30
_FRAG_MIN_RESIDENT_KB = 50 * 1024
_LEAK_PARTIAL_RATIO = 0.80
_LEAK_MIN_OBJECTS = 10_000
_LEAK_MIN_RESIDENT_KB = 10 * 1024


def _frag_ratio(d: dict) -> float:
    slabs = d.get("slabs") or 0
    if slabs <= 0:
        return 0.0
    partial = d.get("partial") or 0
    return partial / slabs


def classify(caches: list, requires_root_probe: bool) -> dict:
    if not caches and requires_root_probe:
        return {"verdict": "requires_root",
                "reason": ("/sys/kernel/slab/*/object_size is 0400 "
                           "root-only on this distro ; daemon is "
                           "running unprivileged."),
                "recommendation": _RECIPE_REQUIRES_ROOT}
    if not caches:
        return {"verdict": "no_slab_data",
                "reason": ("/sys/kernel/slab empty — CONFIG_SLUB=n "
                           "on this kernel (exotic)."),
                "recommendation": ""}
    fragged: list = []
    leak_susp: list = []
    for d in caches:
        size_kb = d.get("resident_kb") or 0
        objs = d.get("objects") or 0
        ratio = _frag_ratio(d)
        if (ratio >= _FRAG_RATIO_THRESHOLD
                and size_kb >= _FRAG_MIN_RESIDENT_KB):
            fragged.append(d)
        if (ratio >= _LEAK_PARTIAL_RATIO
                and objs >= _LEAK_MIN_OBJECTS
                and size_kb >= _LEAK_MIN_RESIDENT_KB):
            leak_susp.append(d)
    if leak_susp:
        names = ", ".join(
            f"{d['name']} ({d['objects']} obj, "
            f"{d['resident_kb']//1024} MB, "
            f"{_frag_ratio(d):.0%} partial)"
            for d in leak_susp[:5])
        return {"verdict": "leak_suspect",
                "reason": (f"{len(leak_susp)} slab cache(s) show "
                           f"leak-suspect pattern (> 80 % partial). "
                           f"{names}"),
                "recommendation": _RECIPE_LEAK_SUSPECT}
    if fragged:
        names = ", ".join(
            f"{d['name']} ({d['resident_kb']//1024} MB, "
            f"{_frag_ratio(d):.0%} partial)" for d in fragged[:5])
        return {"verdict": "fragmented",
                "reason": (f"{len(fragged)} slab cache(s) ≥ 50 MB "
                           f"with > 30 % partial-slab ratio. {names}"),
                "recommendation": _RECIPE_FRAGMENTED}
    return {"verdict": "ok",
            "reason": (f"{len(caches)} slab cache(s) ; no large "
                       f"cache exceeds the fragmentation or leak "
                       f"thresholds."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    # Try /proc/slabinfo first (richer schema).
    slabinfo_text = _read(_PROC_SLABINFO)
    caches = parse_slabinfo(slabinfo_text or "")
    # Then /sys/kernel/slab (different schema — has partial/cpu_slabs).
    sysfs_caches = read_sysfs_slab(_SYS_KERNEL_SLAB)
    requires_root = (not caches and not sysfs_caches
                       and _probe_permission(_SYS_KERNEL_SLAB))
    # Merge by name : prefer sysfs for partial/cpu_slabs/slabs counts,
    # fall back to slabinfo for size/active.
    by_name: dict = {}
    for d in caches:
        by_name[d["name"]] = dict(d)
    for d in sysfs_caches:
        merged = by_name.setdefault(d["name"], {"name": d["name"]})
        for k in ("objects", "object_size", "slabs", "partial",
                  "cpu_slabs", "objs_per_slab"):
            if d.get(k) is not None:
                merged[k] = d[k]
        if d.get("resident_kb"):
            merged.setdefault("resident_kb", d["resident_kb"])
    merged_list = sorted(by_name.values(),
                          key=lambda r: -(r.get("resident_kb") or 0))
    verdict = classify(merged_list, requires_root)
    return {
        "ok": bool(merged_list) or requires_root,
        "cache_count": len(merged_list),
        "top_caches": merged_list[:30],
        "requires_root": requires_root,
        "verdict": verdict,
    }
