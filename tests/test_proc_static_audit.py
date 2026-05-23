"""R&D #26.1 — per-boot static PCI auditor tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import proc_static_audit as ps


def _with_baseline(td):
    return patch.object(ps, "baseline_path",
                        lambda: os.path.join(td, "proc_static_baseline.json"))


# ── read_attr ──────────────────────────────────────────────────────────


def test_read_attr_present(tmp_path):
    bdf = tmp_path / "0000:01:00.0"; bdf.mkdir()
    (bdf / "vendor").write_text("0x10de\n")
    assert ps.read_attr("0000:01:00.0", "vendor",
                          sys_root=str(tmp_path)) == "0x10de"


def test_read_attr_missing(tmp_path):
    assert ps.read_attr("0000:99:00.0", "vendor",
                          sys_root=str(tmp_path)) is None


# ── read_resource ──────────────────────────────────────────────────────


def test_read_resource_keeps_nonzero(tmp_path):
    bdf = tmp_path / "0000:01:00.0"; bdf.mkdir()
    (bdf / "resource").write_text(
        "0x80000000 0x80ffffff 0x00040200\n"
        "0x0000000000000000 0x0000000000000000 0x0000000000000000\n"
    )
    out = ps.read_resource("0000:01:00.0", sys_root=str(tmp_path))
    assert len(out) == 1
    assert "0x80000000" in out[0]


def test_read_resource_missing(tmp_path):
    assert ps.read_resource("0000:99:00.0", sys_root=str(tmp_path)) is None


# ── list_nvidia_bdfs ───────────────────────────────────────────────────


def test_list_nvidia(tmp_path):
    n = tmp_path / "0000:01:00.0"; n.mkdir()
    (n / "vendor").write_text("0x10de\n")
    other = tmp_path / "0000:02:00.0"; other.mkdir()
    (other / "vendor").write_text("0x8086\n")  # Intel
    out = ps.list_nvidia_bdfs(sys_root=str(tmp_path))
    assert out == ["0000:01:00.0"]


# ── collect_static ─────────────────────────────────────────────────────


def test_collect_static_full(tmp_path):
    bdf = tmp_path / "0000:01:00.0"; bdf.mkdir()
    (bdf / "vendor").write_text("0x10de\n")
    (bdf / "device").write_text("0x2204\n")
    (bdf / "subsystem_vendor").write_text("0x19da\n")
    (bdf / "subsystem_device").write_text("0x1613\n")
    (bdf / "revision").write_text("0xa1\n")
    (bdf / "irq").write_text("77\n")
    (bdf / "boot_vga").write_text("0\n")
    (bdf / "resource").write_text("0x80000000 0x80ffffff 0x00040200\n")
    out = ps.collect_static("0000:01:00.0", sys_root=str(tmp_path))
    assert out["vendor"] == "0x10de"
    assert out["device"] == "0x2204"
    assert out["irq"] == "77"
    assert len(out["resource"]) == 1


# ── fingerprint ────────────────────────────────────────────────────────


def test_fingerprint_deterministic():
    s1 = {"vendor": "0x10de", "device": "0x2204", "subsystem_vendor": "0x19da",
          "subsystem_device": "0x1613", "revision": "0xa1", "irq": "77",
          "boot_vga": "0", "resource": ["0x1 0x2 0x3"]}
    s2 = dict(s1)
    assert ps.fingerprint(s1) == ps.fingerprint(s2)


def test_fingerprint_differs_on_change():
    s1 = {"vendor": "0x10de", "device": "0x2204", "irq": "77",
          "subsystem_vendor": "0x19da", "subsystem_device": "0x1613",
          "revision": "0xa1", "boot_vga": "0", "resource": []}
    s2 = dict(s1, irq="78")
    assert ps.fingerprint(s1) != ps.fingerprint(s2)


# ── diff_attrs ─────────────────────────────────────────────────────────


def test_diff_no_change():
    s1 = {"vendor": "0x10de", "device": "0x2204",
          "subsystem_vendor": "0x19da", "subsystem_device": "0x1613",
          "revision": "0xa1", "irq": "77", "boot_vga": "0",
          "resource": ["a"]}
    assert ps.diff_attrs(s1, s1) == []


def test_diff_irq_change():
    s1 = {"vendor": "0x10de", "device": "0x2204",
          "subsystem_vendor": "0x19da", "subsystem_device": "0x1613",
          "revision": "0xa1", "irq": "77", "boot_vga": "0",
          "resource": []}
    s2 = dict(s1, irq="120")
    out = ps.diff_attrs(s1, s2)
    assert len(out) == 1
    assert out[0]["field"] == "irq"
    assert out[0]["before"] == "77"
    assert out[0]["after"] == "120"


def test_diff_resource_change():
    s1 = {"vendor": "0x10de", "device": "0x2204",
          "subsystem_vendor": "0x19da", "subsystem_device": "0x1613",
          "revision": "0xa1", "irq": "77", "boot_vga": "0",
          "resource": ["row1"]}
    s2 = dict(s1, resource=["row1", "row2"])
    out = ps.diff_attrs(s1, s2)
    assert any(d["field"] == "resource" for d in out)


# ── classify ───────────────────────────────────────────────────────────


def test_classify_clean():
    v = ps.classify([])
    assert v["verdict"] == "clean"


def test_classify_subsystem_critical():
    v = ps.classify([{"field": "subsystem_vendor", "before": "x",
                       "after": "y"}])
    assert v["verdict"] == "subsystem_swap"
    assert v["severity"] == "critical"


def test_classify_identity_swap():
    v = ps.classify([{"field": "vendor", "before": "x", "after": "y"}])
    assert v["verdict"] == "identity_swap"
    assert v["severity"] == "critical"


def test_classify_bar_reshuffle():
    v = ps.classify([{"field": "resource", "before_rows": 5, "after_rows": 4}])
    assert v["verdict"] == "bar_reshuffle"
    assert v["severity"] == "warn"


def test_classify_irq_changed():
    v = ps.classify([{"field": "irq", "before": "1", "after": "2"}])
    assert v["verdict"] == "irq_changed"


def test_classify_subsystem_beats_other():
    """Multiple drifts → most severe wins."""
    v = ps.classify([
        {"field": "irq", "before": "1", "after": "2"},
        {"field": "subsystem_vendor", "before": "x", "after": "y"},
    ])
    assert v["verdict"] == "subsystem_swap"


# ── status integration ───────────────────────────────────────────────


def test_status_seeds_baseline(tmp_path):
    bdf = tmp_path / "0000:01:00.0"; bdf.mkdir()
    (bdf / "vendor").write_text("0x10de\n")
    (bdf / "device").write_text("0x2204\n")
    (bdf / "subsystem_vendor").write_text("0x19da\n")
    (bdf / "subsystem_device").write_text("0x1613\n")
    (bdf / "revision").write_text("0xa1\n")
    (bdf / "irq").write_text("77\n")
    (bdf / "boot_vga").write_text("0\n")
    (bdf / "resource").write_text("0x80000000 0x80ffffff 0x00040200\n")
    with _with_baseline(str(tmp_path)):
        with patch.object(ps, "_PCI_ROOT", str(tmp_path)):
            with patch.object(ps, "list_nvidia_bdfs",
                              return_value=["0000:01:00.0"]):
                with patch.object(ps, "read_attr",
                                   side_effect=lambda b, a, sys_root=ps._PCI_ROOT:
                                   ps.read_attr.__wrapped__(b, a, str(tmp_path))
                                   if hasattr(ps.read_attr, "__wrapped__")
                                   else ps.read_attr(b, a, str(tmp_path))):
                    pass
    # Simpler: bypass patching and just run with the synthetic sys_root
    with _with_baseline(str(tmp_path)):
        # collect_static still reads from _PCI_ROOT by default;
        # easier route: directly test that with a small wrapper.
        # Use end-to-end via list_nvidia_bdfs + collect_static patching.
        with patch.object(ps, "list_nvidia_bdfs",
                          return_value=["0000:01:00.0"]):
            with patch.object(ps, "collect_static",
                              return_value={
                                  "vendor": "0x10de", "device": "0x2204",
                                  "subsystem_vendor": "0x19da",
                                  "subsystem_device": "0x1613",
                                  "revision": "0xa1", "irq": "77",
                                  "boot_vga": "0",
                                  "resource": ["x"],
                              }):
                s = ps.status()
                base = ps.load_baseline()
    assert "0000:01:00.0" in base
    assert s["cards"][0]["verdict"]["verdict"] == "first_seen"


def test_status_detects_drift_on_2nd_call(tmp_path):
    with _with_baseline(str(tmp_path)):
        with patch.object(ps, "list_nvidia_bdfs",
                          return_value=["0000:01:00.0"]):
            with patch.object(ps, "collect_static",
                              return_value={
                                  "vendor": "0x10de", "device": "0x2204",
                                  "subsystem_vendor": "0x19da",
                                  "subsystem_device": "0x1613",
                                  "revision": "0xa1", "irq": "77",
                                  "boot_vga": "0",
                                  "resource": ["x"],
                              }):
                ps.status()  # seed
            # Now change irq → expect irq_changed verdict
            with patch.object(ps, "collect_static",
                              return_value={
                                  "vendor": "0x10de", "device": "0x2204",
                                  "subsystem_vendor": "0x19da",
                                  "subsystem_device": "0x1613",
                                  "revision": "0xa1", "irq": "120",
                                  "boot_vga": "0",
                                  "resource": ["x"],
                              }):
                s2 = ps.status()
    assert s2["cards"][0]["verdict"]["verdict"] == "irq_changed"


def test_status_no_gpus():
    with patch.object(ps, "list_nvidia_bdfs", return_value=[]):
        s = ps.status()
    assert s["card_count"] == 0
