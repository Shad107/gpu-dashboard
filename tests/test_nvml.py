"""Tests for modules/_nvml.py — F1 NVML ctypes backend."""
from __future__ import annotations

import ctypes

import pytest

from gpu_dashboard.modules import _nvml


# Always reset module-level state before each test.
@pytest.fixture(autouse=True)
def _reset_nvml():
    _nvml._LIB = None
    _nvml._INIT_OK = False
    _nvml._LOAD_ATTEMPTED = False
    yield
    _nvml._LIB = None
    _nvml._INIT_OK = False
    _nvml._LOAD_ATTEMPTED = False


# ── library loader ──────────────────────────────────────────────────

def test_try_load_returns_none_when_lib_missing(monkeypatch):
    """Bypass the real loader; simulate no libnvidia-ml available."""
    def fake_cdll(name):
        raise OSError(f"no such library: {name}")
    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(ctypes.util, "find_library", lambda n: None)
    assert _nvml._try_load() is None


def test_try_load_falls_back_to_find_library(monkeypatch, tmp_path):
    """If direct SONAME load fails but find_library resolves to a
    path, the loader should retry that path."""
    fake_lib_obj = object()
    attempts = []
    def fake_cdll(name):
        attempts.append(name)
        if name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
            raise OSError("no")
        if name == "/some/resolved/path":
            return fake_lib_obj
        raise OSError("no")
    monkeypatch.setattr(ctypes, "CDLL", fake_cdll)
    monkeypatch.setattr(ctypes.util, "find_library",
                          lambda n: "/some/resolved/path")
    got = _nvml._try_load()
    assert got is fake_lib_obj
    # Both SONAMEs tried, then the resolved path.
    assert attempts == [
        "libnvidia-ml.so.1",
        "libnvidia-ml.so",
        "/some/resolved/path"]


# ── init / shutdown lifecycle ───────────────────────────────────────

class _FakeLib:
    """Minimal stand-in for libnvidia-ml.so.

    Each NVML_* method has restype/argtypes set on the attribute
    (mirroring how ctypes.CDLL would expose them), but calling
    the attribute is the real behaviour. We model success / error
    via instance attributes on `behaviour`.
    """

    def __init__(self, **behaviour):
        self.behaviour = behaviour
        self.shutdown_called = False
        # Each entry must be settable: ctypes wrapper assigns
        # restype/argtypes on each. We use a generic _Stub.
        for fn in (
                "nvmlInit_v2", "nvmlShutdown", "nvmlErrorString",
                "nvmlDeviceGetCount_v2",
                "nvmlDeviceGetHandleByIndex_v2",
                "nvmlDeviceGetName",
                "nvmlDeviceGetTemperature",
                "nvmlDeviceGetPowerUsage",
                "nvmlDeviceGetUtilizationRates",
                "nvmlDeviceGetMemoryInfo",
                "nvmlDeviceGetClockInfo",
                "nvmlDeviceGetCurrentClocksThrottleReasons"):
            setattr(self, fn, self._make(fn))

    def _make(self, fn):
        # Returns a callable with restype/argtypes attributes that
        # ctypes assigns to it.
        outer = self

        class _Stub:
            restype = None
            argtypes = None

            def __call__(self_, *args):
                return outer._dispatch(fn, args)
        return _Stub()

    def _dispatch(self, fn, args):
        beh = self.behaviour.get(fn)
        if beh is None:
            return 0  # NVML_SUCCESS by default
        if callable(beh):
            return beh(*args)
        return beh


def test_init_returns_false_when_lib_missing(monkeypatch):
    monkeypatch.setattr(_nvml, "_try_load", lambda: None)
    assert _nvml.init() is False
    assert _nvml.is_available() is False


def test_init_returns_false_when_nvmlInit_fails(monkeypatch):
    fake = _FakeLib(nvmlInit_v2=1)  # non-zero = error
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    assert _nvml.init() is False
    assert _nvml.is_available() is False


def test_init_succeeds_with_clean_lib(monkeypatch):
    fake = _FakeLib(nvmlInit_v2=0)
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    assert _nvml.init() is True
    assert _nvml.is_available() is True


def test_init_is_idempotent(monkeypatch):
    fake = _FakeLib(nvmlInit_v2=0)
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    assert _nvml.init() is True
    # Second call short-circuits.
    assert _nvml.init() is True


def test_shutdown_clears_state(monkeypatch):
    fake = _FakeLib(nvmlInit_v2=0)
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    _nvml.init()
    assert _nvml.is_available()
    _nvml.shutdown()
    assert not _nvml.is_available()


# ── throttle decode ─────────────────────────────────────────────────

def test_decode_throttle_reasons_empty():
    assert _nvml.decode_throttle_reasons(0) == []


def test_decode_throttle_reasons_single():
    out = _nvml.decode_throttle_reasons(0x0000000000000008)
    assert out == ["HwSlowdown"]


def test_decode_throttle_reasons_multiple():
    # SwPowerCap (0x4) + HwThermalSlowdown (0x40)
    out = _nvml.decode_throttle_reasons(0x44)
    assert "SwPowerCap" in out
    assert "HwThermalSlowdown" in out
    assert len(out) == 2


# ── query API ───────────────────────────────────────────────────────

def test_device_count_zero_when_unavailable(monkeypatch):
    monkeypatch.setattr(_nvml, "_try_load", lambda: None)
    assert _nvml.device_count() == 0


def test_device_count_reads_from_lib(monkeypatch):
    def count_fn(out_ptr):
        out_ptr._obj.value = 2
        return 0
    fake = _FakeLib(nvmlInit_v2=0, nvmlDeviceGetCount_v2=count_fn)
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    _nvml.init()
    assert _nvml.device_count() == 2


def test_sample_device_returns_none_when_handle_fails(monkeypatch):
    fake = _FakeLib(nvmlInit_v2=0,
                    nvmlDeviceGetHandleByIndex_v2=lambda i, h: 6)
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    _nvml.init()
    assert _nvml.sample_device(0) is None


def test_sample_device_fills_fields(monkeypatch):
    """Drive a fake lib that returns synthetic values for each
    field and verify they flow through sample_device."""
    def handle(i, h):
        h._obj.value = 0xCAFE
        return 0
    def name(handle, buf, length):
        b = b"RTX 3090 (FAKE)\x00"
        ctypes.memmove(buf, b, len(b))
        return 0
    def temp(handle, sensor, out):
        out._obj.value = 67
        return 0
    def power(handle, out):
        out._obj.value = 250_000  # mW
        return 0
    def util(handle, out):
        out._obj.gpu = 88
        out._obj.memory = 42
        return 0
    def mem(handle, out):
        out._obj.total = 24 * 1024**3
        out._obj.used = 12 * 1024**3
        out._obj.free = 12 * 1024**3
        return 0
    def clock(handle, ck_type, out):
        # ck_type arrives as ctypes.c_int — unwrap to get the
        # python int for arithmetic.
        out._obj.value = 1800 + ck_type.value * 100
        return 0
    def throttle(handle, out):
        out._obj.value = 0x40  # HwThermalSlowdown
        return 0
    fake = _FakeLib(
        nvmlInit_v2=0,
        nvmlDeviceGetHandleByIndex_v2=handle,
        nvmlDeviceGetName=name,
        nvmlDeviceGetTemperature=temp,
        nvmlDeviceGetPowerUsage=power,
        nvmlDeviceGetUtilizationRates=util,
        nvmlDeviceGetMemoryInfo=mem,
        nvmlDeviceGetClockInfo=clock,
        nvmlDeviceGetCurrentClocksThrottleReasons=throttle)
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    _nvml.init()
    s = _nvml.sample_device(0)
    assert s is not None
    assert s["index"] == 0
    assert "RTX 3090" in s["name"]
    assert s["temperature_c"] == 67
    assert s["power_w"] == 250.0
    assert s["gpu_util_pct"] == 88
    assert s["mem_util_pct"] == 42
    assert s["mem_total_bytes"] == 24 * 1024**3
    assert s["graphics_mhz"] == 1800
    assert s["sm_mhz"] == 1900
    assert s["mem_mhz"] == 2000
    assert s["throttle_mask"] == 0x40
    assert s["throttle_reasons"] == ["HwThermalSlowdown"]


def test_sample_all_returns_one_per_device(monkeypatch):
    def count_fn(out_ptr):
        out_ptr._obj.value = 2
        return 0
    def handle(i, h):
        # i arrives as ctypes.c_uint — unwrap to .value
        h._obj.value = 0x1000 + i.value
        return 0
    def name(handle, buf, length):
        return 0
    fake = _FakeLib(
        nvmlInit_v2=0,
        nvmlDeviceGetCount_v2=count_fn,
        nvmlDeviceGetHandleByIndex_v2=handle,
        nvmlDeviceGetName=name)
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    _nvml.init()
    out = _nvml.sample_all()
    assert len(out) == 2
    assert [s["index"] for s in out] == [0, 1]


# ── status() — module-level audit shim ──────────────────────────────

def test_status_reports_unavailable_when_lib_missing(monkeypatch):
    monkeypatch.setattr(_nvml, "_try_load", lambda: None)
    s = _nvml.status()
    assert s["ok"] is False
    assert s["available"] is False
    assert s["verdict"]["verdict"] == "unknown"


def test_status_reports_ok_with_no_throttle(monkeypatch):
    def count_fn(out_ptr):
        out_ptr._obj.value = 1
        return 0
    def handle(i, h):
        h._obj.value = 0xBEEF
        return 0
    def name(handle, buf, length):
        return 0
    fake = _FakeLib(
        nvmlInit_v2=0,
        nvmlDeviceGetCount_v2=count_fn,
        nvmlDeviceGetHandleByIndex_v2=handle,
        nvmlDeviceGetName=name)
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    s = _nvml.status()
    assert s["ok"] is True
    assert s["device_count"] == 1
    assert s["verdict"]["verdict"] == "ok"


def test_status_reports_handle_failure(monkeypatch):
    """Real-world edge case observed on a VM with broken GPU
    passthrough: count_v2 reports 1 device, but handle acquisition
    fails (rc != 0). Status should surface this distinctly from
    'no devices' (count=0) and from 'unavailable' (lib missing)."""
    def count_fn(out_ptr):
        out_ptr._obj.value = 1
        return 0
    def handle(i, h):
        return 999  # NVML_ERROR_UNKNOWN — handle stays null
    fake = _FakeLib(
        nvmlInit_v2=0,
        nvmlDeviceGetCount_v2=count_fn,
        nvmlDeviceGetHandleByIndex_v2=handle)
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    s = _nvml.status()
    assert s["ok"] is False
    assert s["available"] is True
    assert s["device_count"] == 1
    assert s["verdict"]["verdict"] == "device_handle_unavailable"
    assert "persistence" in s["verdict"]["recommendation"].lower()


def test_status_reports_throttle_when_mask_nonzero(monkeypatch):
    def count_fn(out_ptr):
        out_ptr._obj.value = 1
        return 0
    def handle(i, h):
        h._obj.value = 0xBEEF
        return 0
    def name(handle, buf, length):
        return 0
    def throttle(handle, out):
        out._obj.value = 0x8  # HwSlowdown
        return 0
    fake = _FakeLib(
        nvmlInit_v2=0,
        nvmlDeviceGetCount_v2=count_fn,
        nvmlDeviceGetHandleByIndex_v2=handle,
        nvmlDeviceGetName=name,
        nvmlDeviceGetCurrentClocksThrottleReasons=throttle)
    monkeypatch.setattr(_nvml, "_try_load", lambda: fake)
    s = _nvml.status()
    assert s["verdict"]["verdict"] == "throttle_active"
    assert "HwSlowdown" in s["verdict"]["reason"]
