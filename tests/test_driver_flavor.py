"""R&D #22.2 — Open vs proprietary driver advisor tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import driver_flavor as df


# ── detect_flavor ──────────────────────────────────────────────────────


def test_detect_proprietary():
    assert df.detect_flavor({"license": "NVIDIA"}) == "proprietary"


def test_detect_open_dual_license():
    assert df.detect_flavor({"license": "Dual MIT/GPL"}) == "open"


def test_detect_open_gpl():
    assert df.detect_flavor({"license": "GPL"}) == "open"


def test_detect_open_mit():
    assert df.detect_flavor({"license": "MIT"}) == "open"


def test_detect_open_via_description():
    """If license is missing but description says 'open', call it open."""
    assert df.detect_flavor({"license": "",
                              "description": "NVIDIA open kernel module"}) == "open"


def test_detect_unknown():
    assert df.detect_flavor(None) == "unknown"
    assert df.detect_flavor({}) == "unknown"


def test_detect_license_as_list():
    """modinfo sometimes returns multiple license lines."""
    assert df.detect_flavor({"license": ["NVIDIA", "extra"]}) == "proprietary"


# ── classify ───────────────────────────────────────────────────────────


def test_classify_no_gpus():
    v = df.classify(flavor="open", gpus=[])
    assert v["verdict"] == "unknown"


def test_classify_open_ok_on_turing():
    gpus = [{"index": 0, "name": "RTX 2080", "compute_cap": "7.5"}]
    v = df.classify(flavor="open", gpus=gpus)
    assert v["verdict"] == "ok"


def test_classify_open_ok_on_ada():
    gpus = [{"index": 0, "name": "RTX 4090", "compute_cap": "8.9"}]
    v = df.classify(flavor="open", gpus=gpus)
    assert v["verdict"] == "ok"


def test_classify_wrong_flavor_open_on_pascal():
    gpus = [{"index": 0, "name": "GTX 1080", "compute_cap": "6.1"}]
    v = df.classify(flavor="open", gpus=gpus)
    assert v["verdict"] == "wrong_flavor"
    assert "Pascal-or-older" in v["reason"]
    assert "purge" in v["recommendation"]


def test_classify_could_upgrade_proprietary_on_ampere():
    """Ampere on legacy driver — open would work, but proprietary is fine."""
    gpus = [{"index": 0, "name": "RTX 3090", "compute_cap": "8.6"}]
    v = df.classify(flavor="proprietary", gpus=gpus)
    assert v["verdict"] == "could_upgrade"


def test_classify_proprietary_ok_on_pascal():
    gpus = [{"index": 0, "name": "GTX 1080", "compute_cap": "6.1"}]
    v = df.classify(flavor="proprietary", gpus=gpus)
    assert v["verdict"] == "ok"


def test_classify_mixed_system():
    gpus = [
        {"index": 0, "name": "RTX 4090", "compute_cap": "8.9"},
        {"index": 1, "name": "GTX 1080", "compute_cap": "6.1"},
    ]
    v = df.classify(flavor="proprietary", gpus=gpus)
    # Both arches present but proprietary is correct — stays "ok"
    # because the legacy_only list is non-empty
    assert v["verdict"] == "ok"


def test_classify_unknown_flavor():
    gpus = [{"index": 0, "name": "RTX 3090", "compute_cap": "8.6"}]
    v = df.classify(flavor="unknown", gpus=gpus)
    assert v["verdict"] == "unknown"


# ── read_module_version ────────────────────────────────────────────────


def test_module_version_missing():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert df.read_module_version() is None


# ── run_modinfo ────────────────────────────────────────────────────────


def test_modinfo_no_binary(monkeypatch):
    monkeypatch.setattr(df.shutil, "which", lambda x: None)
    assert df.run_modinfo() is None


# ── list_gpu_compute_caps ──────────────────────────────────────────────


def test_compute_caps_no_smi(monkeypatch):
    monkeypatch.setattr(df.shutil, "which", lambda x: None)
    assert df.list_gpu_compute_caps() == []


# ── status ─────────────────────────────────────────────────────────────


def test_status_clean_open_on_turing():
    with patch.object(df, "read_module_version", return_value="555.42"):
        with patch.object(df, "run_modinfo",
                          return_value={"license": "Dual MIT/GPL",
                                         "filename": "/lib/modules/.../nvidia.ko"}):
            with patch.object(df, "list_gpu_compute_caps",
                              return_value=[{"index": 0, "name": "RTX 4090",
                                              "compute_cap": "8.9"}]):
                s = df.status()
    assert s["flavor"] == "open"
    assert s["verdict"]["verdict"] == "ok"
    assert s["gpus"][0]["open_supported"] is True


def test_status_pascal_on_open_warns():
    with patch.object(df, "read_module_version", return_value="555.42"):
        with patch.object(df, "run_modinfo",
                          return_value={"license": "Dual MIT/GPL"}):
            with patch.object(df, "list_gpu_compute_caps",
                              return_value=[{"index": 0, "name": "GTX 1080",
                                              "compute_cap": "6.1"}]):
                s = df.status()
    assert s["verdict"]["verdict"] == "wrong_flavor"
    assert s["gpus"][0]["open_supported"] is False


def test_status_no_gpus_returns_unknown_verdict():
    with patch.object(df, "read_module_version", return_value=None):
        with patch.object(df, "run_modinfo", return_value=None):
            with patch.object(df, "list_gpu_compute_caps", return_value=[]):
                s = df.status()
    assert s["flavor"] == "unknown"
    assert s["verdict"]["verdict"] == "unknown"
