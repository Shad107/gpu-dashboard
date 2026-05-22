"""R&D #14.5 — hot-swap drift detector tests."""
import json
import os
import tempfile
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import hot_swap as hs


@pytest.fixture(autouse=True)
def _reset_buffer():
    """Reset the in-memory event buffer between tests."""
    with hs._lock:
        hs._events.clear()
    yield
    with hs._lock:
        hs._events.clear()


def _state_paths(td):
    return patch.object(hs, "state_path",
                        return_value=os.path.join(td, "state.json"))


# ── snapshot_pci / snapshot_drm via _read_text mocks ─────────────────────


def test_list_nvidia_filters_by_vendor_and_class():
    fake = {
        "/sys/bus/pci/devices": ["0000:01:00.0", "0000:01:00.1", "0000:02:00.0"],
    }
    # 01:00.0 = NVIDIA display ; 01:00.1 = NVIDIA audio ; 02:00.0 = Intel
    read_map = {
        "/sys/bus/pci/devices/0000:01:00.0/vendor": "0x10de",
        "/sys/bus/pci/devices/0000:01:00.0/class": "0x030200",
        "/sys/bus/pci/devices/0000:01:00.1/vendor": "0x10de",
        "/sys/bus/pci/devices/0000:01:00.1/class": "0x040300",  # audio → skip
        "/sys/bus/pci/devices/0000:02:00.0/vendor": "0x8086",   # Intel → skip
        "/sys/bus/pci/devices/0000:02:00.0/class": "0x030000",
    }
    with patch("glob.glob", return_value=[f"/sys/bus/pci/devices/{b}" for b in fake["/sys/bus/pci/devices"]]), \
         patch.object(hs, "_read_text", side_effect=lambda p: read_map.get(p)):
        out = hs.list_nvidia_pci_devices()
    assert out == ["0000:01:00.0"]


# ── _link_speed_gts / _link_width_int ────────────────────────────────────


def test_link_speed_gts_parses_format():
    assert hs._link_speed_gts("8.0 GT/s PCIe") == 8.0
    assert hs._link_speed_gts("16.0 GT/s PCIe") == 16.0
    assert hs._link_speed_gts("2.5 GT/s PCIe") == 2.5


def test_link_speed_gts_handles_missing():
    assert hs._link_speed_gts(None) is None
    assert hs._link_speed_gts("not-parseable") is None


def test_link_width_int_parses():
    assert hs._link_width_int("16") == 16
    assert hs._link_width_int("4") == 4
    assert hs._link_width_int(None) is None
    assert hs._link_width_int("not-int") is None


# ── diff_snapshots ──────────────────────────────────────────────────────


def _pci_snap(bdf="0000:01:00.0", cur_speed="8.0 GT/s PCIe", cur_width="16",
              max_speed="8.0 GT/s PCIe", max_width="16", power="D0"):
    return {bdf: {
        "current_link_speed": cur_speed, "current_link_width": cur_width,
        "max_link_speed": max_speed, "max_link_width": max_width,
        "power_state": power,
    }}


def test_diff_no_change_yields_no_events():
    s = {"ts": 1000, "pci": _pci_snap(), "drm": {"card0-DP-1": "connected"}}
    assert hs.diff_snapshots(s, s) == []


def test_diff_link_renegotiate():
    old = {"ts": 100, "pci": _pci_snap(cur_speed="8.0 GT/s PCIe"), "drm": {}}
    new = {"ts": 200, "pci": _pci_snap(cur_speed="2.5 GT/s PCIe"), "drm": {}}
    events = hs.diff_snapshots(old, new)
    kinds = [e["kind"] for e in events]
    assert "link-renegotiate" in kinds


def test_diff_link_downgrade():
    """current < max should emit a link-downgrade event (only on first diff)."""
    old = {"ts": 100, "pci": _pci_snap(cur_speed="8.0 GT/s PCIe", cur_width="16"),
           "drm": {}}
    new = {"ts": 200, "pci": _pci_snap(cur_speed="2.5 GT/s PCIe", cur_width="4",
                                         max_speed="8.0 GT/s PCIe", max_width="16"),
           "drm": {}}
    events = hs.diff_snapshots(old, new)
    kinds = [e["kind"] for e in events]
    assert "link-downgrade" in kinds


def test_diff_power_state_change():
    old = {"ts": 100, "pci": _pci_snap(power="D0"), "drm": {}}
    new = {"ts": 200, "pci": _pci_snap(power="D3"), "drm": {}}
    events = hs.diff_snapshots(old, new)
    kinds = [e["kind"] for e in events]
    assert "power-state-change" in kinds
    psc = next(e for e in events if e["kind"] == "power-state-change")
    assert psc["before"] == "D0"
    assert psc["after"] == "D3"


def test_diff_drm_disconnect():
    old = {"ts": 100, "pci": _pci_snap(),
           "drm": {"card0-DP-1": "connected"}}
    new = {"ts": 200, "pci": _pci_snap(),
           "drm": {"card0-DP-1": "disconnected"}}
    events = hs.diff_snapshots(old, new)
    kinds = [e["kind"] for e in events]
    assert "drm-disconnect" in kinds


def test_diff_drm_reconnect():
    old = {"ts": 100, "pci": _pci_snap(),
           "drm": {"card0-DP-1": "disconnected"}}
    new = {"ts": 200, "pci": _pci_snap(),
           "drm": {"card0-DP-1": "connected"}}
    events = hs.diff_snapshots(old, new)
    assert any(e["kind"] == "drm-reconnect" for e in events)


# ── evaluate end-to-end ─────────────────────────────────────────────────


def test_evaluate_first_run_no_events_saves_state():
    """No previous state → no events emitted, state file written."""
    with tempfile.TemporaryDirectory() as td, _state_paths(td):
        with patch.object(hs, "snapshot_all",
                          return_value={"ts": 1000, "pci": _pci_snap(),
                                        "drm": {"card0-DP-1": "connected"}}):
            r = hs.evaluate()
    assert r["new_events"] == []
    assert r["gpu_count"] == 1
    assert r["drm_connector_count"] == 1


def test_evaluate_second_run_with_change_emits_events():
    with tempfile.TemporaryDirectory() as td, _state_paths(td):
        # First snapshot at full link speed
        with patch.object(hs, "snapshot_all",
                          return_value={"ts": 1000, "pci": _pci_snap(power="D0"),
                                        "drm": {"card0-DP-1": "connected"}}):
            hs.evaluate()
        # Second snapshot with power state change
        with patch.object(hs, "snapshot_all",
                          return_value={"ts": 1100, "pci": _pci_snap(power="D3"),
                                        "drm": {"card0-DP-1": "connected"}}):
            r2 = hs.evaluate()
    assert len(r2["new_events"]) >= 1
    assert any(e["kind"] == "power-state-change" for e in r2["new_events"])


# ── events buffer ───────────────────────────────────────────────────────


def test_get_events_newest_first():
    hs._append_events(
        [{"kind": "drm-disconnect", "target": "card0-DP-1"}], ts=1000
    )
    hs._append_events(
        [{"kind": "drm-reconnect", "target": "card0-DP-1"}], ts=2000
    )
    out = hs.get_events()
    assert out[0]["kind"] == "drm-reconnect"
    assert out[1]["kind"] == "drm-disconnect"


def test_get_events_limit_clamps():
    for i in range(10):
        hs._append_events([{"kind": "drm-disconnect", "target": f"x{i}"}], ts=i)
    assert len(hs.get_events(limit=3)) == 3


def test_buffer_bound_caps_at_max():
    for i in range(hs._BUFFER_MAX + 50):
        hs._append_events([{"kind": "x"}], ts=i)
    assert len(hs.get_events(limit=10_000)) == hs._BUFFER_MAX
