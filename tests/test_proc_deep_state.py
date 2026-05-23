"""R&D #23.6 — procfs deep-state diff tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import proc_deep_state as pds


def _with_baseline(td):
    return patch.object(pds, "baseline_path",
                        lambda: os.path.join(td, "proc_deep_baseline.json"))


# ── parse_information ──────────────────────────────────────────────────


_REAL_BLOCK = """\
Model: 		 NVIDIA GeForce RTX 3090
IRQ:   		 77
GPU UUID: 	 GPU-eaf23aa1-4d3d-596c-da66-b86b0e5db584
Video BIOS: 	 94.02.26.40.81
Bus Type: 	 PCI
DMA Size: 	 47 bits
DMA Mask: 	 0x7fffffffffff
Bus Location: 	 0000:01:00.0
Device Minor: 	 0
GPU Firmware: 	 590.48.01
GPU Excluded:	 No
"""


def test_parse_real_block():
    d = pds.parse_information(_REAL_BLOCK)
    assert d["Model"] == "NVIDIA GeForce RTX 3090"
    assert d["GPU UUID"] == "GPU-eaf23aa1-4d3d-596c-da66-b86b0e5db584"
    assert d["GPU Firmware"] == "590.48.01"
    assert d["GPU Excluded"] == "No"


def test_parse_empty():
    assert pds.parse_information("") == {}


def test_parse_strips_whitespace():
    d = pds.parse_information("Model:    NVIDIA  GeForce\n")
    assert d["Model"] == "NVIDIA GeForce"


# ── list_gpu_dirs ──────────────────────────────────────────────────────


def test_list_gpu_dirs(tmp_path):
    (tmp_path / "0000:01:00.0").mkdir()
    (tmp_path / "0000:02:00.0").mkdir()
    (tmp_path / "not_a_bdf").mkdir()
    out = pds.list_gpu_dirs(root=str(tmp_path))
    assert len(out) == 2


def test_list_gpu_dirs_missing(tmp_path):
    assert pds.list_gpu_dirs(root=str(tmp_path / "missing")) == []


# ── read_gpu_information ───────────────────────────────────────────────


def test_read_gpu_info(tmp_path):
    d = tmp_path / "0000:01:00.0"; d.mkdir()
    (d / "information").write_text(_REAL_BLOCK)
    info = pds.read_gpu_information(str(d))
    assert info["Model"] == "NVIDIA GeForce RTX 3090"


def test_read_gpu_info_missing(tmp_path):
    d = tmp_path / "0000:01:00.0"; d.mkdir()
    assert pds.read_gpu_information(str(d)) is None


# ── detect_drift ───────────────────────────────────────────────────────


def test_drift_none():
    base = {"Model": "X", "Video BIOS": "94.02"}
    cur = {"Model": "X", "Video BIOS": "94.02"}
    assert pds.detect_drift(base, cur) == []


def test_drift_vbios_change():
    base = {"Model": "X", "Video BIOS": "94.02"}
    cur = {"Model": "X", "Video BIOS": "94.10"}
    out = pds.detect_drift(base, cur)
    assert len(out) == 1
    assert out[0]["field"] == "Video BIOS"
    assert out[0]["before"] == "94.02"
    assert out[0]["after"] == "94.10"


def test_drift_excluded_yes():
    base = {"GPU Excluded": "No"}
    cur = {"GPU Excluded": "Yes"}
    out = pds.detect_drift(base, cur)
    assert any(d["field"] == "GPU Excluded" for d in out)


def test_drift_skips_unknown_fields():
    """Field not in TRACKED_FIELDS → ignored."""
    base = {"FooBar": "a"}
    cur = {"FooBar": "b"}
    assert pds.detect_drift(base, cur) == []


# ── classify ───────────────────────────────────────────────────────────


def test_classify_no_gpus():
    v = pds.classify([])
    assert v["verdict"] == "no_gpus"


def test_classify_clean():
    reports = [{"excluded": False, "drift": []}]
    v = pds.classify(reports)
    assert v["verdict"] == "clean"


def test_classify_excluded_takes_priority():
    reports = [
        {"excluded": True,  "drift": [{"field": "Video BIOS",
                                          "before": "1", "after": "2"}]},
    ]
    v = pds.classify(reports)
    assert v["verdict"] == "excluded"
    assert v["severity"] == "critical"


def test_classify_firmware_drift():
    reports = [{"excluded": False,
                "drift": [{"field": "GPU Firmware",
                            "before": "535", "after": "555"}]}]
    v = pds.classify(reports)
    assert v["verdict"] == "firmware_drift"


def test_classify_vbios_drift():
    reports = [{"excluded": False,
                "drift": [{"field": "Video BIOS",
                            "before": "94.02", "after": "94.10"}]}]
    v = pds.classify(reports)
    assert v["verdict"] == "vbios_drift"


def test_classify_minor_drift():
    reports = [{"excluded": False,
                "drift": [{"field": "DMA Size",
                            "before": "47 bits", "after": "48 bits"}]}]
    v = pds.classify(reports)
    assert v["verdict"] == "minor_drift"


# ── status integration ────────────────────────────────────────────────


def test_status_seeds_baseline(tmp_path):
    """First call : new GPU should be auto-baselined."""
    gpu_dir = tmp_path / "0000:01:00.0"; gpu_dir.mkdir()
    (gpu_dir / "information").write_text(_REAL_BLOCK)
    with _with_baseline(str(tmp_path)):
        with patch.object(pds, "list_gpu_dirs", return_value=[str(gpu_dir)]):
            s = pds.status()
            base = pds.load_baseline()
    assert s["gpu_count"] == 1
    assert s["gpus"][0]["first_seen"] is True
    assert "GPU-eaf23aa1-4d3d-596c-da66-b86b0e5db584" in base


def test_status_drift_detected_on_subsequent_call(tmp_path):
    """First call seeds, second call with changed vbios → vbios_drift."""
    gpu_dir = tmp_path / "0000:01:00.0"; gpu_dir.mkdir()
    info_file = gpu_dir / "information"
    info_file.write_text(_REAL_BLOCK)
    with _with_baseline(str(tmp_path)):
        with patch.object(pds, "list_gpu_dirs", return_value=[str(gpu_dir)]):
            pds.status()  # seed
            # Mutate vbios
            mutated = _REAL_BLOCK.replace("94.02.26.40.81", "94.10.99.99.99")
            info_file.write_text(mutated)
            s2 = pds.status()
    assert s2["verdict"]["verdict"] == "vbios_drift"


def test_status_excluded_critical(tmp_path):
    gpu_dir = tmp_path / "0000:01:00.0"; gpu_dir.mkdir()
    info_file = gpu_dir / "information"
    info_file.write_text(_REAL_BLOCK)
    with _with_baseline(str(tmp_path)):
        with patch.object(pds, "list_gpu_dirs", return_value=[str(gpu_dir)]):
            pds.status()  # seed
            mutated = _REAL_BLOCK.replace("GPU Excluded:	 No",
                                           "GPU Excluded:	 Yes")
            info_file.write_text(mutated)
            s2 = pds.status()
    assert s2["verdict"]["verdict"] == "excluded"
    assert s2["excluded_count"] == 1
