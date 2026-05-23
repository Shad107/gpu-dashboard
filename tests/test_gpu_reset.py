"""R&D #22.1 — GPU reset counter / RMA-candidate detector tests."""
import os
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import gpu_reset as gr


def _with_baseline(td):
    return patch.object(gr, "baseline_path",
                        lambda: os.path.join(td, "gpu_reset_baseline.json"))


# ── list_drm_cards ─────────────────────────────────────────────────────


def test_list_drm_cards(tmp_path):
    (tmp_path / "card0").mkdir()
    (tmp_path / "card1").mkdir()
    (tmp_path / "renderD128").mkdir()  # not a card device
    out = gr.list_drm_cards(drm_root=str(tmp_path))
    assert len(out) == 2
    assert all(p.endswith(("card0", "card1")) for p in out)


def test_list_drm_cards_no_dir(tmp_path):
    assert gr.list_drm_cards(drm_root=str(tmp_path / "missing")) == []


# ── read_reset_count ───────────────────────────────────────────────────


def test_read_reset_count_present(tmp_path):
    card = tmp_path / "card0"; dev = card / "device"; dev.mkdir(parents=True)
    (dev / "reset_count").write_text("3\n")
    assert gr.read_reset_count(str(card)) == 3


def test_read_reset_count_missing(tmp_path):
    card = tmp_path / "card0"; card.mkdir()
    assert gr.read_reset_count(str(card)) is None


def test_read_reset_count_garbage(tmp_path):
    card = tmp_path / "card0"; dev = card / "device"; dev.mkdir(parents=True)
    (dev / "reset_count").write_text("notanumber\n")
    assert gr.read_reset_count(str(card)) is None


# ── is_nvidia_card ─────────────────────────────────────────────────────


def test_is_nvidia_card_match(tmp_path):
    card = tmp_path / "card0"; dev = card / "device"; dev.mkdir(parents=True)
    (dev / "vendor").write_text("0x10de\n")
    assert gr.is_nvidia_card(str(card)) is True


def test_is_nvidia_card_other_vendor(tmp_path):
    card = tmp_path / "card0"; dev = card / "device"; dev.mkdir(parents=True)
    (dev / "vendor").write_text("0x1002\n")  # AMD
    assert gr.is_nvidia_card(str(card)) is False


def test_is_nvidia_card_missing(tmp_path):
    assert gr.is_nvidia_card(str(tmp_path / "card99")) is False


# ── scan_kernel_for_resets ─────────────────────────────────────────────


def test_scan_no_lines():
    assert gr.scan_kernel_for_resets([]) == []


def test_scan_detects_reset():
    lines = ["kernel: NVRM: Xid 79, GPU has been reset"]
    out = gr.scan_kernel_for_resets(lines)
    assert out[0]["kind"] == "reset"


def test_scan_detects_fallen_off_bus():
    lines = ["kernel: NVRM: GPU at PCI:0000:01:00.0 has fallen off the bus"]
    out = gr.scan_kernel_for_resets(lines)
    assert out[0]["kind"] == "fallen_off_bus"


def test_scan_detects_gr_exception():
    lines = ["kernel: NVRM: GR0 exception at 0x42"]
    out = gr.scan_kernel_for_resets(lines)
    assert out[0]["kind"] == "gr_exception"


def test_scan_ignores_unrelated():
    lines = ["kernel: systemd-logind: lid closed"]
    assert gr.scan_kernel_for_resets(lines) == []


# ── classify ───────────────────────────────────────────────────────────


def test_classify_clean():
    v = gr.classify(delta_resets=0, log_event_count=0)
    assert v["verdict"] == "clean"


def test_classify_occasional():
    v = gr.classify(delta_resets=1, log_event_count=0)
    assert v["verdict"] == "occasional"


def test_classify_frequent():
    v = gr.classify(delta_resets=2, log_event_count=3)
    assert v["verdict"] == "frequent"
    assert "journalctl" in v["recommendation"]


def test_classify_rma():
    v = gr.classify(delta_resets=5, log_event_count=10)
    assert v["verdict"] == "rma"
    assert "RMA" in v["reason"]


# ── update_baseline_and_get_delta ──────────────────────────────────────


def test_baseline_first_seen_returns_zero_delta(tmp_path):
    with _with_baseline(str(tmp_path)):
        deltas = gr.update_baseline_and_get_delta({"0000:01:00.0": 5})
    assert deltas == {"0000:01:00.0": 0}


def test_baseline_returns_delta_on_subsequent_call(tmp_path):
    with _with_baseline(str(tmp_path)):
        gr.update_baseline_and_get_delta({"0000:01:00.0": 5})
        deltas = gr.update_baseline_and_get_delta({"0000:01:00.0": 8})
    assert deltas == {"0000:01:00.0": 3}


def test_baseline_clamps_negative_to_zero(tmp_path):
    """If reset_count somehow regresses, delta is 0 not negative."""
    with _with_baseline(str(tmp_path)):
        gr.update_baseline_and_get_delta({"0000:01:00.0": 10})
        deltas = gr.update_baseline_and_get_delta({"0000:01:00.0": 3})
    assert deltas == {"0000:01:00.0": 0}


# ── status ─────────────────────────────────────────────────────────────


def test_status_empty_no_cards():
    with patch.object(gr, "list_drm_cards", return_value=[]):
        with patch.object(gr, "journalctl_kernel", return_value=[]):
            s = gr.status()
    assert s["card_count"] == 0
    assert s["verdict"]["verdict"] == "clean"


def test_status_aggregates(tmp_path):
    """Integration: stage card + journalctl mock → expect frequent."""
    with _with_baseline(str(tmp_path)):
        with patch.object(gr, "list_drm_cards",
                          return_value=["/sys/class/drm/card0"]):
            with patch.object(gr, "is_nvidia_card", return_value=True):
                with patch.object(gr, "read_reset_count", return_value=2):
                    with patch.object(gr, "read_bdf",
                                       return_value="0000:01:00.0"):
                        # Seed baseline at 0
                        gr.save_baseline({})
                        # First call seeds with 2 → delta 0
                        gr.status()
                        # Now bump to 5 → delta 3
                        with patch.object(gr, "read_reset_count",
                                          return_value=5):
                            with patch.object(gr, "journalctl_kernel",
                                               return_value=[]):
                                s = gr.status()
    assert s["total_delta_resets"] == 3
    assert s["verdict"]["verdict"] == "frequent"
