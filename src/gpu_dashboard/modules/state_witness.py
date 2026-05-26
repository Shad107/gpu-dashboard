"""F2 — State Witness: snapshot the system state at a moment in time
and diff snapshots so the user can answer "what changed?" when LLM
tok/s suddenly tanks after a driver bump, kernel update, or
seemingly-unrelated apt upgrade.

Design constraints:
  - stdlib only (+ existing _nvml ctypes wrapper)
  - JSON-serializable, gzipped to ~/.local/state/gpu-dashboard/snapshots/
  - schema versioned so old snapshots stay readable
  - collectors must be cheap (<2s total wall time)
  - each collector tolerant of missing kernel surfaces (returns
    {available: False, reason: "..."}); never raises
"""
from __future__ import annotations

import datetime
import glob
import gzip
import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

from . import _nvml

SCHEMA_VERSION = 1
STATE_DIR = os.path.expanduser("~/.local/state/gpu-dashboard/snapshots")
KEEP_LAST = 20

# Packages we actually care about. Diffing the full apt inventory
# would be noisy; this whitelist surfaces the changes that
# historically cause GPU/inference regressions.
_PKG_PATTERNS = [
    r"^nvidia-.*",
    r"^libcuda.*",
    r"^libcudnn.*",
    r"^libnccl.*",
    r"^libnvidia.*",
    r"^cuda-.*",
    r"^linux-image-.*",
    r"^linux-headers-.*",
    r"^linux-modules-.*",
    r"^qemu-system-.*",
    r"^libvirt.*",
    r"^vfio-.*",
]
_PKG_RE = re.compile("|".join(_PKG_PATTERNS))

# Systemd units we want to track state for (active/inactive +
# enabled/disabled). These are the units whose state directly
# influences GPU/inference behaviour.
_SYSTEMD_UNITS = [
    "nvidia-persistenced.service",
    "nvidia-fabricmanager.service",
    "nvidia-powerd.service",
    "dcgm-exporter.service",
    "gpu-oculink-watchdog.service",
    "gpu-dashboard.service",
]

# Kernel modules we want to track parameters for (modparams hide
# behaviour-changing flags that don't show up in package versions).
_TRACKED_MODULES = [
    "nvidia", "nvidia_uvm", "nvidia_drm", "nvidia_modeset",
    "nouveau",
    "vfio", "vfio_pci", "vfio_iommu_type1",
    "i915",
    "pcieport",
]


# ----------------------------------------------------------------- helpers


def _read(path: str, max_bytes: int = 4096) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            return f.read(max_bytes).decode("utf-8", "replace").strip()
    except (OSError, IOError):
        return None


def _run(args: List[str], timeout: float = 2.0) -> Optional[str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                            timeout=timeout)
        if r.returncode == 0:
            return r.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None


# --------------------------------------------------------------- collectors


def _collect_kernel() -> Dict[str, Any]:
    return {
        "uname_r": _read("/proc/sys/kernel/osrelease"),
        "uname_v": _read("/proc/version"),
        "cmdline": _read("/proc/cmdline"),
        "hostname": _read("/proc/sys/kernel/hostname"),
    }


def _collect_driver() -> Dict[str, Any]:
    """NVIDIA driver + CUDA version, primarily from /proc."""
    out: Dict[str, Any] = {
        "nvml_available": _nvml.is_available(),
    }
    proc_ver = _read("/proc/driver/nvidia/version")
    if proc_ver:
        # First line is the canonical "NVRM version: NVIDIA UNIX
        # Open Kernel Module for x86_64  580.95.05  ..." string
        out["proc_driver_nvidia"] = proc_ver.split("\n")[0].strip()
    # nvidia-smi -q gives CUDA Version + Driver Version in a stable
    # text format; cheap to parse, no GPU access required.
    raw = _run(["nvidia-smi", "--query-gpu=driver_version",
                "--format=csv,noheader"], timeout=2.0)
    if raw:
        out["driver_version"] = raw.split("\n")[0].strip()
    return out


def _collect_modules() -> Dict[str, Any]:
    """Loaded modules + parameter snapshot for the tracked set."""
    out: Dict[str, Any] = {"loaded": {}, "params": {}}
    proc_modules = _read("/proc/modules", max_bytes=131072)
    if proc_modules:
        for line in proc_modules.split("\n"):
            parts = line.split()
            if len(parts) < 4:
                continue
            name, size, refcount, deps = (parts[0], parts[1],
                                            parts[2], parts[3])
            # Live state may be at index 4 ("Live"/"Loading"/"Unloading")
            state = parts[4] if len(parts) > 4 else None
            out["loaded"][name] = {
                "size": int(size) if size.isdigit() else size,
                "refcount": int(refcount) if refcount.isdigit() else refcount,
                "deps": deps,
                "state": state,
            }
    for mod in _TRACKED_MODULES:
        param_dir = f"/sys/module/{mod}/parameters"
        if not os.path.isdir(param_dir):
            continue
        params: Dict[str, str] = {}
        try:
            for entry in os.listdir(param_dir):
                v = _read(os.path.join(param_dir, entry), max_bytes=256)
                if v is not None:
                    params[entry] = v
        except OSError:
            continue
        out["params"][mod] = params
    return out


def _gpu_pci_devices() -> List[str]:
    """Return a list of NVIDIA GPU BDFs (0000:xx:xx.x)."""
    bdfs = []
    for dev in sorted(glob.glob("/sys/bus/pci/devices/*")):
        vendor = _read(os.path.join(dev, "vendor"), max_bytes=16)
        if vendor and vendor.strip() == "0x10de":
            cls = _read(os.path.join(dev, "class"), max_bytes=16) or ""
            # 0x030000 = VGA, 0x030200 = 3D — skip USB-C controllers
            # (0x0c8000) and audio (0x040300) sibling functions.
            if cls.startswith("0x0300") or cls.startswith("0x0302"):
                bdfs.append(os.path.basename(dev))
    return bdfs


def _collect_pcie_for(bdf: str) -> Dict[str, Any]:
    base = f"/sys/bus/pci/devices/{bdf}"
    out = {
        "current_link_speed": _read(f"{base}/current_link_speed"),
        "current_link_width": _read(f"{base}/current_link_width"),
        "max_link_speed": _read(f"{base}/max_link_speed"),
        "max_link_width": _read(f"{base}/max_link_width"),
        "power_state": _read(f"{base}/power_state"),
        "power_control": _read(f"{base}/power/control"),
        "d3cold_allowed": _read(f"{base}/d3cold_allowed"),
        "numa_node": _read(f"{base}/numa_node"),
        "msi_irqs": sorted(os.listdir(f"{base}/msi_irqs"))
            if os.path.isdir(f"{base}/msi_irqs") else None,
    }
    # AER counters (uncorrectable in particular point at link health).
    for tag in ("aer_dev_fatal", "aer_dev_nonfatal",
                "aer_dev_correctable"):
        out[tag] = _read(f"{base}/{tag}")
    # Resolve upstream root port for slot-level context.
    try:
        link_target = os.readlink(base)
    except OSError:
        link_target = ""
    out["sysfs_target"] = link_target
    return out


def _collect_pcie() -> Dict[str, Any]:
    gpus = _gpu_pci_devices()
    out: Dict[str, Any] = {"gpus": {}}
    for bdf in gpus:
        # The raw BDF (0000:01:00.0) contains a dot which would
        # collide with the dot-separated diff path. Sanitize for
        # use as a dict key; expose the original BDF as a field.
        key = bdf.replace(":", "_").replace(".", "_")
        entry = _collect_pcie_for(bdf)
        entry["bdf"] = bdf
        out["gpus"][key] = entry
    return out


def _collect_gpu() -> List[Dict[str, Any]]:
    """Per-GPU NVML state — power limit, persistence, compute mode,
    MIG mode, memory total."""
    out: List[Dict[str, Any]] = []
    if not _nvml.init():
        return out
    try:
        n = _nvml.device_count()
    except Exception:
        return out
    for i in range(n or 0):
        try:
            dev = _nvml.sample_device(i) or {}
        except Exception as e:
            dev = {"sample_error": str(e)}
        out.append({
            "index": i,
            "name": dev.get("name"),
            "uuid": dev.get("uuid"),
            "memory_total_mb": dev.get("memory_total_mb"),
            "power_limit_w": dev.get("power_limit_w"),
            "power_limit_default_w": dev.get("power_limit_default_w"),
            "power_limit_min_w": dev.get("power_limit_min_w"),
            "power_limit_max_w": dev.get("power_limit_max_w"),
            "persistence_mode": dev.get("persistence_mode"),
            "compute_mode": dev.get("compute_mode"),
            "pstate": dev.get("pstate"),
        })
    return out


def _collect_packages() -> Dict[str, str]:
    """Targeted apt/dpkg package inventory.

    Only returns the packages matching _PKG_PATTERNS — diffing all
    20k apt packages would drown the user in noise. Returns a flat
    {name: version} mapping."""
    out: Dict[str, str] = {}
    # dpkg path
    raw = _run(["dpkg-query", "-W", "-f=${Package}\t${Version}\n"],
                timeout=4.0)
    if raw:
        for line in raw.split("\n"):
            if "\t" not in line:
                continue
            name, ver = line.split("\t", 1)
            if _PKG_RE.match(name):
                out[name] = ver
        return out
    # rpm fallback
    raw = _run(["rpm", "-qa", "--qf",
                "%{NAME}\t%{VERSION}-%{RELEASE}\n"], timeout=4.0)
    if raw:
        for line in raw.split("\n"):
            if "\t" not in line:
                continue
            name, ver = line.split("\t", 1)
            if _PKG_RE.match(name):
                out[name] = ver
    return out


def _collect_systemd() -> Dict[str, Dict[str, str]]:
    """is-active + is-enabled for tracked units. We call systemctl
    twice per unit which is slow; batch with show --property."""
    out: Dict[str, Dict[str, str]] = {}
    if not _run(["systemctl", "--version"], timeout=1.0):
        return out
    for unit in _SYSTEMD_UNITS:
        raw = _run(["systemctl", "show", unit, "--property=ActiveState",
                     "--property=UnitFileState",
                     "--property=SubState"], timeout=2.0)
        if not raw:
            continue
        kv: Dict[str, str] = {}
        for line in raw.split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k] = v
        if not kv:
            continue
        out[unit] = {
            "active": kv.get("ActiveState", ""),
            "sub": kv.get("SubState", ""),
            "enabled": kv.get("UnitFileState", ""),
        }
    return out


# ---------------------------------------------------------------- snapshot


def take_snapshot(reason: str = "manual") -> Dict[str, Any]:
    """Run every collector, return the full snapshot dict.

    Each collector is wrapped in a try so a single failure can't
    blow up the whole snapshot — failures land in the snapshot as
    {"_error": "..."} entries instead."""
    snap: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "taken_at": datetime.datetime.utcnow().isoformat(
            timespec="seconds") + "Z",
        "reason": reason,
    }
    for key, fn in (
        ("kernel", _collect_kernel),
        ("driver", _collect_driver),
        ("modules", _collect_modules),
        ("pcie", _collect_pcie),
        ("gpu", _collect_gpu),
        ("packages", _collect_packages),
        ("systemd", _collect_systemd),
    ):
        try:
            snap[key] = fn()
        except Exception as e:
            snap[key] = {"_error": f"{type(e).__name__}: {e}"}
    return snap


# ------------------------------------------------------------- persistence


def _ensure_dir() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def _snapshot_id_from_ts(ts: str) -> str:
    """ts is ISO 8601 UTC like 2026-05-26T13:55:00Z; the id is the
    same string sanitised for a filename."""
    return ts.replace(":", "").replace("-", "")


def _prune() -> None:
    files = sorted(glob.glob(os.path.join(STATE_DIR, "*.json.gz")))
    if len(files) <= KEEP_LAST:
        return
    for f in files[:len(files) - KEEP_LAST]:
        try:
            os.unlink(f)
        except OSError:
            pass


def save_snapshot(snap: Dict[str, Any]) -> str:
    """Write the snapshot gzipped under STATE_DIR. Returns the
    snapshot id (the filename without extension)."""
    _ensure_dir()
    sid = _snapshot_id_from_ts(snap["taken_at"])
    if snap.get("reason") and snap["reason"] != "manual":
        sid = f"{sid}_{re.sub(r'[^a-zA-Z0-9_]', '', snap['reason'])[:20]}"
    path = os.path.join(STATE_DIR, f"{sid}.json.gz")
    with gzip.open(path, "wb") as f:
        f.write(json.dumps(snap, sort_keys=True).encode("utf-8"))
    _prune()
    return sid


def list_snapshots() -> List[Dict[str, Any]]:
    """Return snapshot metadata sorted by taken_at descending.

    Each gzipped snapshot is small enough (<20KB compressed) that
    loading them in full to extract taken_at/reason isn't a
    bottleneck."""
    _ensure_dir()
    out: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(STATE_DIR, "*.json.gz"))):
        sid = os.path.basename(path)[:-len(".json.gz")]
        try:
            with gzip.open(path, "rb") as f:
                snap = json.loads(f.read().decode("utf-8"))
            out.append({
                "id": sid,
                "taken_at": snap.get("taken_at"),
                "reason": snap.get("reason"),
                "size_bytes": os.path.getsize(path),
                "schema_version": snap.get("schema_version"),
                "hostname": (snap.get("kernel") or {}).get("hostname"),
            })
        except Exception:
            out.append({
                "id": sid,
                "taken_at": None,
                "reason": "unreadable",
                "size_bytes": os.path.getsize(path),
            })
    out.sort(key=lambda s: s.get("taken_at") or "", reverse=True)
    return out


def load_snapshot(sid: str) -> Optional[Dict[str, Any]]:
    """Load a snapshot by id. Returns None if not found."""
    if not re.fullmatch(r"[a-zA-Z0-9_]+", sid):
        return None
    path = os.path.join(STATE_DIR, f"{sid}.json.gz")
    if not os.path.isfile(path):
        return None
    try:
        with gzip.open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


# -------------------------------------------------------------------- diff


def _walk(prefix: str, before: Any, after: Any,
           out: List[Dict[str, Any]]) -> None:
    """Recursive deep-diff. Records added/removed/changed entries."""
    if type(before) != type(after):
        if before is None and after is not None:
            out.append({"path": prefix, "kind": "added", "after": after})
            return
        if after is None and before is not None:
            out.append({"path": prefix, "kind": "removed", "before": before})
            return
        # Different scalar types — treat as changed.
        out.append({"path": prefix, "kind": "changed",
                     "before": before, "after": after})
        return
    if isinstance(before, dict):
        keys = set(before.keys()) | set(after.keys())
        for k in sorted(keys):
            sub_prefix = f"{prefix}.{k}" if prefix else k
            if k not in before:
                out.append({"path": sub_prefix, "kind": "added",
                             "after": after[k]})
            elif k not in after:
                out.append({"path": sub_prefix, "kind": "removed",
                             "before": before[k]})
            else:
                _walk(sub_prefix, before[k], after[k], out)
        return
    if isinstance(before, list):
        # Walk lists by index when lengths match — order is stable
        # in practice (NVML device index, sorted module names). If
        # lengths differ, fall back to a single 'changed' record so
        # the ranker can score the section.
        if len(before) == len(after):
            for i, (bv, av) in enumerate(zip(before, after)):
                _walk(f"{prefix}.{i}", bv, av, out)
        elif before != after:
            out.append({"path": prefix, "kind": "changed",
                         "before": before, "after": after})
        return
    if before != after:
        out.append({"path": prefix, "kind": "changed",
                     "before": before, "after": after})


def diff_snapshots(before_id: str, after_id: str) -> Dict[str, Any]:
    """Diff two snapshots by id. Returns {ok, error?, changes: [...]}.

    Each change has {path, kind: added|removed|changed, before?, after?,
    score?, reason?, severity?} with the score field populated by
    rank_changes() so the UI can sort by likely cause-of-regression."""
    a = load_snapshot(before_id)
    b = load_snapshot(after_id)
    if a is None:
        return {"ok": False, "error": "before_not_found",
                 "message": f"snapshot {before_id!r} not found"}
    if b is None:
        return {"ok": False, "error": "after_not_found",
                 "message": f"snapshot {after_id!r} not found"}
    changes: List[Dict[str, Any]] = []
    # We diff every top-level section EXCEPT metadata that's
    # expected to differ between any two snapshots.
    skip = {"taken_at", "schema_version", "reason"}
    keys = (set(a.keys()) | set(b.keys())) - skip
    for k in sorted(keys):
        _walk(k, a.get(k), b.get(k), changes)
    rank_changes(changes)
    changes.sort(key=lambda c: c.get("score", 0), reverse=True)
    return {
        "ok": True,
        "before": {"id": before_id, "taken_at": a.get("taken_at")},
        "after": {"id": after_id, "taken_at": b.get("taken_at")},
        "changes": changes,
        "change_count": len(changes),
    }


# ------------------------------------------------------------------ ranker


# Heuristic weights — calibrated against the bisect-stuck threads
# from the R&D survey (vLLM/llama.cpp/Ollama/ROCm/RTX 5090).
#
# The single highest-signal change is a driver version bump: the
# nvidia 570→580 → 115→5 tok/s regression is the canonical
# example. Kernel image bumps come second (DKMS rebuilds, module
# ABI shifts). Module parameter changes are extremely high signal
# because they hide behaviour switches (nvidia NVreg_*).
#
# Mapping is (path_predicate, score, severity, reason_template).
# Higher score = more likely to be the cause of an inference
# regression. Severity is one of: critical/high/medium/low/info.

def _path_re(pat: str):
    return re.compile(pat)


_RANKER_RULES = [
    # Driver version — the elephant in the room
    (_path_re(r"^driver\.driver_version$"), 100, "critical",
     "Driver NVIDIA changed — primary suspect for tok/s regressions"),
    (_path_re(r"^driver\.proc_driver_nvidia$"), 95, "critical",
     "Kernel NVRM string changed — driver rebuild or major bump"),
    (_path_re(r"^driver\.nvml_available$"), 90, "critical",
     "NVML availability flipped — driver/runtime broken"),

    # Kernel
    (_path_re(r"^kernel\.uname_r$"), 90, "critical",
     "Kernel version changed — module ABI may have shifted"),
    (_path_re(r"^kernel\.cmdline$"), 80, "high",
     "Kernel cmdline changed — IOMMU/passthrough/ASPM flags may differ"),

    # Module parameters (nvidia + vfio especially)
    (_path_re(r"^modules\.params\.nvidia(_uvm|_modeset|_drm)?\."), 85, "high",
     "Kernel module parameter changed — behaviour-altering flag"),
    (_path_re(r"^modules\.params\.vfio.*\."), 75, "high",
     "VFIO parameter changed — passthrough behaviour affected"),
    (_path_re(r"^modules\.params\.pcieport\."), 70, "high",
     "pcieport parameter changed — ASPM/link policy affected"),

    # Module refcounts shift between any two snapshots as workloads
    # come and go — pure noise. Score 0 hides them.
    # MUST come before the broader nvidia/nouveau/vfio rule.
    (_path_re(r"^modules\.loaded\.[^.]+\.refcount$"), 0, "info",
     "Module refcount drift (load activity)"),

    # Modules loaded/unloaded (only matches when a module appears
    # or disappears entirely, since refcount changes are filtered
    # above).
    (_path_re(r"^modules\.loaded\.(nvidia|nouveau|vfio)"), 80, "high",
     "GPU-related module add/remove"),

    # PCIe link health
    (_path_re(r"^pcie\.gpus\.[^.]+\.current_link_speed$"), 85, "critical",
     "PCIe link speed changed — bandwidth degradation"),
    (_path_re(r"^pcie\.gpus\.[^.]+\.current_link_width$"), 85, "critical",
     "PCIe link width changed — lanes lost"),
    (_path_re(r"^pcie\.gpus\.[^.]+\.aer_dev_fatal$"), 70, "high",
     "AER fatal counter changed — link errors detected"),
    (_path_re(r"^pcie\.gpus\.[^.]+\.aer_dev_nonfatal$"), 50, "medium",
     "AER non-fatal counter changed"),
    (_path_re(r"^pcie\.gpus\.[^.]+\.power_state$"), 60, "medium",
     "PCIe device power state changed"),

    # GPU NVML
    (_path_re(r"^gpu\.[0-9]+\.power_limit_w$"), 75, "high",
     "GPU power limit changed — thermal/power throttling shift"),
    (_path_re(r"^gpu\.[0-9]+\.persistence_mode$"), 55, "medium",
     "GPU persistence mode changed"),
    (_path_re(r"^gpu\.[0-9]+\.compute_mode$"), 50, "medium",
     "GPU compute mode changed"),
    (_path_re(r"^gpu\.[0-9]+\.uuid$"), 95, "critical",
     "GPU UUID changed — different physical card"),

    # Packages — nvidia/cuda/cudnn/nccl bumps are common culprits
    (_path_re(r"^packages\.(nvidia|libnvidia|libcuda|libcudnn|libnccl|cuda-)"), 70, "high",
     "NVIDIA/CUDA package changed"),
    (_path_re(r"^packages\.linux-(image|headers|modules)-"), 65, "high",
     "Kernel package changed"),
    (_path_re(r"^packages\."), 30, "low",
     "Package changed"),

    # Systemd
    (_path_re(r"^systemd\.nvidia-persistenced\.active$"), 60, "medium",
     "nvidia-persistenced active state flipped"),
    (_path_re(r"^systemd\.nvidia-fabricmanager\.active$"), 60, "medium",
     "nvidia-fabricmanager active state flipped"),
    (_path_re(r"^systemd\.[^.]+\.active$"), 35, "low",
     "systemd unit state flipped"),
    (_path_re(r"^systemd\."), 20, "info",
     "systemd unit metadata changed"),
]


def rank_changes(changes: List[Dict[str, Any]]) -> None:
    """Annotate each change in-place with score/severity/reason.

    Scoring is purely heuristic but calibrated against the
    bisect-stuck threads from the R&D survey. We don't pretend
    causation; we just sort the most likely suspects first."""
    for c in changes:
        path = c.get("path", "")
        c["score"] = 5  # default — surface unknown changes faintly
        c["severity"] = "info"
        c["reason"] = "Other change"
        for pattern, score, sev, msg in _RANKER_RULES:
            if pattern.match(path):
                c["score"] = score
                c["severity"] = sev
                c["reason"] = msg
                break
