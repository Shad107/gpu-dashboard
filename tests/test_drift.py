"""R&D #5.2 — Driver/kernel drift detector tests."""
import json
import os
import tempfile
import subprocess
from unittest.mock import patch
from gpu_dashboard import api


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _run_mock(cmd, **kw):
    """Generic subprocess.run mock based on which command was called."""
    if "nvidia-smi" in cmd[0]:
        return FakeProc(stdout="590.48.01, 94.02.26.40.81, NVIDIA GeForce RTX 3090, Disabled, [N/A], [N/A]")
    if cmd[0] == "uname":
        return FakeProc(stdout="6.17.0-29-generic")
    return FakeProc(stdout="", returncode=1)


def test_diff_snapshots_empty_when_identical():
    a = {"ts": 1, "driver_version": "590.48.01", "kernel_release": "6.17.0"}
    b = {"ts": 2, "driver_version": "590.48.01", "kernel_release": "6.17.0"}
    diffs = api._diff_snapshots(a, b)
    assert diffs == []


def test_diff_snapshots_finds_changes():
    a = {"ts": 1, "driver_version": "590.48.01", "kernel_release": "6.17.0"}
    b = {"ts": 2, "driver_version": "595.10.05", "kernel_release": "6.18.0"}
    diffs = api._diff_snapshots(a, b)
    keys = {d["field"] for d in diffs}
    assert keys == {"driver_version", "kernel_release"}


def test_diff_ignores_ts():
    a = {"ts": 1, "driver_version": "590.48.01"}
    b = {"ts": 99, "driver_version": "590.48.01"}
    diffs = api._diff_snapshots(a, b)
    assert diffs == []


def test_first_boot_creates_baseline_returns_none():
    """First-ever boot : no baseline → silently creates one, returns None."""
    with tempfile.TemporaryDirectory() as td:
        with patch.object(os.path, "expanduser", return_value=td + "/drift_baseline.json"), \
             patch.object(subprocess, "run", side_effect=_run_mock):
            # Force expanduser to also redirect history path. Easier : patch both helpers.
            with patch.object(api._monolith, "_drift_snapshot_path", return_value=td + "/baseline.json"), \
                 patch.object(api._monolith, "_drift_history_path", return_value=td + "/history.json"):
                diffs = api.detect_drift_on_startup()
            assert diffs is None
            assert os.path.exists(td + "/baseline.json")


def test_unchanged_drift_returns_empty_list():
    """If baseline exists + matches current : returns []."""
    with tempfile.TemporaryDirectory() as td:
        # Pre-seed baseline matching what _run_mock returns
        baseline = {
            "ts": 0,
            "driver_version": "590.48.01",
            "vbios_version": "94.02.26.40.81",
            "name": "NVIDIA GeForce RTX 3090",
            "persistence_mode": "Disabled",
            "ecc_mode_current": "[N/A]",
            "mig_mode_current": "[N/A]",
            "kernel_release": "6.17.0-29-generic",
        }
        baseline_path = td + "/baseline.json"
        with open(baseline_path, "w") as f:
            json.dump(baseline, f)
        with patch.object(api._monolith, "_drift_snapshot_path", return_value=baseline_path), \
             patch.object(api._monolith, "_drift_history_path", return_value=td + "/history.json"), \
             patch.object(subprocess, "run", side_effect=_run_mock):
            diffs = api.detect_drift_on_startup()
        assert diffs == []


def test_changed_driver_records_history_and_returns_diff():
    """Driver version changes between baseline + current → diff + history append."""
    with tempfile.TemporaryDirectory() as td:
        baseline = {
            "ts": 0,
            "driver_version": "585.40.00",   # OLD
            "vbios_version": "94.02.26.40.81",
            "name": "NVIDIA GeForce RTX 3090",
            "persistence_mode": "Disabled",
            "ecc_mode_current": "[N/A]",
            "mig_mode_current": "[N/A]",
            "kernel_release": "6.17.0-29-generic",
        }
        baseline_path = td + "/baseline.json"
        history_path = td + "/history.json"
        with open(baseline_path, "w") as f:
            json.dump(baseline, f)

        with patch.object(api._monolith, "_drift_snapshot_path", return_value=baseline_path), \
             patch.object(api._monolith, "_drift_history_path", return_value=history_path), \
             patch.object(subprocess, "run", side_effect=_run_mock):
            diffs = api.detect_drift_on_startup()

        # One diff (driver_version)
        keys = {d["field"] for d in diffs}
        assert "driver_version" in keys
        # History file was created
        assert os.path.exists(history_path)
        with open(history_path) as f:
            history = json.load(f)
        assert len(history) == 1
        assert history[0]["diffs"][0]["field"] == "driver_version"
        # Baseline was updated to current
        with open(baseline_path) as f:
            new_baseline = json.load(f)
        assert new_baseline["driver_version"] == "590.48.01"
