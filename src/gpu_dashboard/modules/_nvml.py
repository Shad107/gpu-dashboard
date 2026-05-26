"""NVML via ctypes — direct binding to libnvidia-ml.so (F1).

Replaces the `subprocess.run(['nvidia-smi', '--query-gpu=...'])` path
used by metrics.py and several module-level callers. NVML is the
underlying C library nvidia-smi itself calls, so this is the
canonical fast path with a stable ABI documented by NVIDIA
(docs.nvidia.com/deploy/nvml-api/).

Why this matters:
  * subprocess fork + CLI parse ≈ 50–200 ms per sample
  * NVML ctypes call ≈ sub-millisecond
  * NVML exposes fields nvidia-smi hides by default:
      - clocks throttle reasons (HW thermal / SW thermal / power
        cap / sync boost / display clock setting / …)
      - per-process GPU utilization (workload attribution)
  * No hard dependency on the nvidia-smi binary being present
    (some minimal containers / NixOS configs lack it even when
    the driver is loaded)

This module is best-effort: if libnvidia-ml.so can't load, it
exposes `is_available() → False` and callers fall back to the
subprocess path. Stdlib only — `ctypes` is in the standard library.

ABI references:
  https://docs.nvidia.com/deploy/nvml-api/group__nvmlDeviceQueries.html
  https://docs.nvidia.com/deploy/nvml-api/nvml-api-reference.html

Behaviour matches NVML R535+ (current as of R595, April 2026).
The functions we use are ABI-stable across drivers since R450.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import threading
from typing import List, Optional, Tuple


# ── library loader ──────────────────────────────────────────────────

_LIB = None
_INIT_OK = False
_INIT_LOCK = threading.Lock()
_LOAD_ATTEMPTED = False


def _try_load() -> Optional[ctypes.CDLL]:
    """Find libnvidia-ml.so. Try the SONAME first (most reliable on
    distro-packaged drivers), then bare name (fallback)."""
    for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    # ctypes.util.find_library may resolve via ld.so cache.
    fallback = ctypes.util.find_library("nvidia-ml")
    if fallback:
        try:
            return ctypes.CDLL(fallback)
        except OSError:
            pass
    return None


# ── NVML constants ──────────────────────────────────────────────────

NVML_SUCCESS = 0
NVML_ERROR_UNINITIALIZED = 1
NVML_ERROR_NOT_SUPPORTED = 3
NVML_ERROR_NO_PERMISSION = 4
NVML_ERROR_NOT_FOUND = 6

NVML_TEMPERATURE_GPU = 0

NVML_CLOCK_GRAPHICS = 0
NVML_CLOCK_SM = 1
NVML_CLOCK_MEM = 2

# Throttle reason bitmask values (subset; the rest are reserved).
# Reference: nvmlClocksThrottleReasons enum in nvml.h.
THROTTLE_REASONS = {
    0x0000000000000001: "GpuIdle",
    0x0000000000000002: "ApplicationsClocksSetting",
    0x0000000000000004: "SwPowerCap",
    0x0000000000000008: "HwSlowdown",  # critical: hot/voltage/power
    0x0000000000000010: "SyncBoost",
    0x0000000000000020: "SwThermalSlowdown",
    0x0000000000000040: "HwThermalSlowdown",
    0x0000000000000080: "HwPowerBrakeSlowdown",
    0x0000000000000100: "DisplayClockSetting",
}


# ── ctypes structs ──────────────────────────────────────────────────

class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint),
                ("memory", ctypes.c_uint)]


class _NvmlMemory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


# ── lifecycle ───────────────────────────────────────────────────────

def init() -> bool:
    """Load libnvidia-ml.so and call nvmlInit_v2. Idempotent.

    Returns True on success, False if the library is unloadable
    (no driver, container without nvidia mounts, AMD-only host, …)
    or if nvmlInit_v2 rejects the call (broken driver state).
    """
    global _LIB, _INIT_OK, _LOAD_ATTEMPTED
    with _INIT_LOCK:
        if _INIT_OK:
            return True
        if _LOAD_ATTEMPTED and _LIB is None:
            return False
        _LOAD_ATTEMPTED = True
        lib = _try_load()
        if lib is None:
            return False
        # Declare signatures we use.
        lib.nvmlInit_v2.restype = ctypes.c_int
        lib.nvmlShutdown.restype = ctypes.c_int
        lib.nvmlErrorString.restype = ctypes.c_char_p
        lib.nvmlErrorString.argtypes = [ctypes.c_int]
        lib.nvmlDeviceGetCount_v2.restype = ctypes.c_int
        lib.nvmlDeviceGetCount_v2.argtypes = [
            ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
        lib.nvmlDeviceGetHandleByIndex_v2.argtypes = [
            ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)]
        lib.nvmlDeviceGetName.restype = ctypes.c_int
        lib.nvmlDeviceGetName.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]
        lib.nvmlDeviceGetTemperature.restype = ctypes.c_int
        lib.nvmlDeviceGetTemperature.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetPowerUsage.restype = ctypes.c_int
        lib.nvmlDeviceGetPowerUsage.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetUtilizationRates.restype = ctypes.c_int
        lib.nvmlDeviceGetUtilizationRates.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_NvmlUtilization)]
        lib.nvmlDeviceGetMemoryInfo.restype = ctypes.c_int
        lib.nvmlDeviceGetMemoryInfo.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_NvmlMemory)]
        lib.nvmlDeviceGetClockInfo.restype = ctypes.c_int
        lib.nvmlDeviceGetClockInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetCurrentClocksThrottleReasons.restype = (
            ctypes.c_int)
        lib.nvmlDeviceGetCurrentClocksThrottleReasons.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulonglong)]
        # init
        rc = lib.nvmlInit_v2()
        if rc != NVML_SUCCESS:
            return False
        _LIB = lib
        _INIT_OK = True
        return True


def shutdown() -> None:
    """Call nvmlShutdown if previously initialised."""
    global _LIB, _INIT_OK
    with _INIT_LOCK:
        if _INIT_OK and _LIB is not None:
            try:
                _LIB.nvmlShutdown()
            except OSError:
                pass
        _INIT_OK = False
        _LIB = None


def is_available() -> bool:
    """True if NVML was initialised and is ready for queries."""
    return _INIT_OK and _LIB is not None


def _err(rc: int) -> str:
    if _LIB is None:
        return f"rc={rc}"
    try:
        msg = _LIB.nvmlErrorString(rc)
        return msg.decode("ascii", "replace") if msg else f"rc={rc}"
    except OSError:
        return f"rc={rc}"


# ── query API ───────────────────────────────────────────────────────

def device_count() -> int:
    """Number of NVML-visible devices, or 0 if unavailable."""
    if not is_available():
        return 0
    n = ctypes.c_uint(0)
    rc = _LIB.nvmlDeviceGetCount_v2(ctypes.byref(n))
    return n.value if rc == NVML_SUCCESS else 0


def _handle(index: int) -> Optional[ctypes.c_void_p]:
    if not is_available():
        return None
    handle = ctypes.c_void_p()
    rc = _LIB.nvmlDeviceGetHandleByIndex_v2(
        ctypes.c_uint(index), ctypes.byref(handle))
    return handle if rc == NVML_SUCCESS else None


def decode_throttle_reasons(mask: int) -> List[str]:
    """Translate the bitmask returned by
    nvmlDeviceGetCurrentClocksThrottleReasons into a list of
    human-readable reason names."""
    out: List[str] = []
    for bit, name in THROTTLE_REASONS.items():
        if mask & bit:
            out.append(name)
    return out


def sample_device(index: int) -> Optional[dict]:
    """One full sample of a single device. Returns None if NVML
    is unavailable or the device handle can't be obtained."""
    handle = _handle(index)
    if handle is None:
        return None
    out: dict = {"index": index}

    # name
    buf = ctypes.create_string_buffer(96)
    rc = _LIB.nvmlDeviceGetName(handle, buf, ctypes.c_uint(96))
    out["name"] = (buf.value.decode("ascii", "replace")
                   if rc == NVML_SUCCESS else None)

    # temperature (°C)
    temp = ctypes.c_uint(0)
    rc = _LIB.nvmlDeviceGetTemperature(
        handle, ctypes.c_int(NVML_TEMPERATURE_GPU), ctypes.byref(temp))
    out["temperature_c"] = temp.value if rc == NVML_SUCCESS else None

    # power (mW → W)
    pw = ctypes.c_uint(0)
    rc = _LIB.nvmlDeviceGetPowerUsage(handle, ctypes.byref(pw))
    out["power_w"] = (pw.value / 1000.0
                      if rc == NVML_SUCCESS else None)

    # utilization
    util = _NvmlUtilization()
    rc = _LIB.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util))
    if rc == NVML_SUCCESS:
        out["gpu_util_pct"] = util.gpu
        out["mem_util_pct"] = util.memory
    else:
        out["gpu_util_pct"] = None
        out["mem_util_pct"] = None

    # memory (bytes)
    mem = _NvmlMemory()
    rc = _LIB.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem))
    if rc == NVML_SUCCESS:
        out["mem_total_bytes"] = mem.total
        out["mem_used_bytes"] = mem.used
        out["mem_free_bytes"] = mem.free
    else:
        out["mem_total_bytes"] = None
        out["mem_used_bytes"] = None
        out["mem_free_bytes"] = None

    # clocks (MHz)
    for label, ck in (("graphics_mhz", NVML_CLOCK_GRAPHICS),
                      ("sm_mhz", NVML_CLOCK_SM),
                      ("mem_mhz", NVML_CLOCK_MEM)):
        clk = ctypes.c_uint(0)
        rc = _LIB.nvmlDeviceGetClockInfo(
            handle, ctypes.c_int(ck), ctypes.byref(clk))
        out[label] = clk.value if rc == NVML_SUCCESS else None

    # throttle reasons — the field that nvidia-smi hides
    mask = ctypes.c_ulonglong(0)
    rc = _LIB.nvmlDeviceGetCurrentClocksThrottleReasons(
        handle, ctypes.byref(mask))
    if rc == NVML_SUCCESS:
        out["throttle_mask"] = mask.value
        out["throttle_reasons"] = decode_throttle_reasons(mask.value)
    else:
        out["throttle_mask"] = None
        out["throttle_reasons"] = []

    return out


def sample_all() -> List[dict]:
    """Sample every NVML-visible device. Empty list if unavailable."""
    return [s for s in (sample_device(i)
                         for i in range(device_count()))
            if s is not None]


# ── module-level audit shim ─────────────────────────────────────────
# Lets collection_profile_audit discover this module without
# crashing the fleet harness on hosts where NVML is unloadable.

NAME = "_nvml"


def status(cfg=None) -> dict:
    """Best-effort init + sample summary, for the fleet harness."""
    ok = init()
    if not ok:
        return {"ok": False, "available": False,
                "device_count": 0,
                "verdict": {"verdict": "unknown",
                            "reason": ("libnvidia-ml.so not loadable "
                                       "— NVML backend unavailable."),
                            "recommendation": ""}}
    count = device_count()
    devices = sample_all()
    # Real-world edge case: count > 0 but handles unavailable —
    # the driver sees the GPU in PCI space but can't open a
    # device context. Common after VM hibernate, after a kernel
    # module reload, or when nvidia-persistenced isn't running.
    if count > 0 and not devices:
        return {"ok": False, "available": True,
                "device_count": count,
                "devices": [],
                "verdict": {"verdict": "device_handle_unavailable",
                            "reason": (f"NVML sees {count} GPU(s) but "
                                       "cannot acquire a device "
                                       "handle — driver state likely "
                                       "stuck (post-suspend, post-"
                                       "module-reload, or persistence "
                                       "mode off)."),
                            "recommendation": _recipe_handle_fail()}}
    any_throttling = any(d.get("throttle_reasons") for d in devices)
    if any_throttling:
        offenders = [(d["index"], d["throttle_reasons"])
                     for d in devices if d.get("throttle_reasons")]
        return {"ok": True, "available": True,
                "device_count": count,
                "devices": devices,
                "verdict": {"verdict": "throttle_active",
                            "reason": (f"{len(offenders)} GPU(s) "
                                       f"currently throttling: "
                                       f"{offenders}"),
                            "recommendation": _recipe_throttle()}}
    return {"ok": True, "available": True,
            "device_count": count,
            "devices": devices,
            "verdict": {"verdict": "ok",
                        "reason": (f"NVML backend active, "
                                   f"{count} device(s), no throttle."),
                        "recommendation": ""}}


def _recipe_handle_fail() -> str:
    return ("# NVML sees the GPU but cannot open a device context.\n"
            "# Most common fixes (try in order):\n"
            "#   1. Verify it isn't a permission issue :\n"
            "sudo nvidia-smi --query-gpu=name --format=csv\n"
            "#   2. Restart the persistence daemon :\n"
            "sudo systemctl restart nvidia-persistenced\n"
            "#   3. Reload the kernel module (kills GPU workloads) :\n"
            "sudo rmmod nvidia_uvm nvidia_drm nvidia_modeset nvidia\n"
            "sudo modprobe nvidia nvidia_modeset nvidia_uvm "
            "nvidia_drm\n"
            "#   4. If post-suspend, full reboot is usually fastest.\n")


def _recipe_throttle() -> str:
    return ("# A GPU is currently throttling. Identify cause :\n"
            "PYTHONPATH=src python3 -c \"\\\n"
            "from gpu_dashboard.modules import _nvml; \\\n"
            "_nvml.init(); \\\n"
            "import json; print(json.dumps(\\\n"
            "  _nvml.sample_all(), indent=2))\"\n"
            "# HwSlowdown / HwThermalSlowdown → check cooling.\n"
            "# SwPowerCap → check nvidia-smi -pl setting.\n"
            "# HwPowerBrakeSlowdown → PSU / breaker / brownout.\n")
