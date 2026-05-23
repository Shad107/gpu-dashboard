"""R&D #23.4 — PCIe ASPM audit tests."""
import pytest
from unittest.mock import patch
from gpu_dashboard.modules import pcie_aspm as pa


# ── read_aspm_policy ───────────────────────────────────────────────────


def test_read_policy_default(tmp_path):
    p = tmp_path / "policy"
    p.write_text("[default] performance powersave powersupersave\n")
    out = pa.read_aspm_policy(str(p))
    assert out["active"] == "default"
    assert "performance" in out["options"]


def test_read_policy_performance(tmp_path):
    p = tmp_path / "policy"
    p.write_text("default [performance] powersave\n")
    assert pa.read_aspm_policy(str(p))["active"] == "performance"


def test_read_policy_missing():
    assert pa.read_aspm_policy("/nonexistent/path") is None


def test_read_policy_no_bracket(tmp_path):
    """If no token is bracketed, active=None but options still parsed."""
    p = tmp_path / "policy"
    p.write_text("default performance\n")
    out = pa.read_aspm_policy(str(p))
    assert out["active"] is None
    assert "default" in out["options"]


# ── board_known_risky ──────────────────────────────────────────────────


def test_risky_z690():
    assert pa.board_known_risky({"name": "PRO Z690-A WIFI"}) is True


def test_risky_b650():
    assert pa.board_known_risky({"name": "ROG STRIX B650E-F GAMING WIFI"}) is True


def test_not_risky_server_board():
    assert pa.board_known_risky({"name": "X11SCA-F"}) is False


def test_not_risky_missing_name():
    assert pa.board_known_risky({"name": None}) is False


# ── list_nvidia_pci_devs ───────────────────────────────────────────────


def test_list_nvidia_devs(tmp_path):
    dev1 = tmp_path / "0000:01:00.0"; dev1.mkdir()
    (dev1 / "vendor").write_text("0x10de\n")
    dev2 = tmp_path / "0000:02:00.0"; dev2.mkdir()
    (dev2 / "vendor").write_text("0x1002\n")  # AMD
    out = pa.list_nvidia_pci_devs(sys_root=str(tmp_path))
    assert len(out) == 1
    assert "0000:01:00.0" in out[0]


def test_list_nvidia_no_dir(tmp_path):
    assert pa.list_nvidia_pci_devs(sys_root=str(tmp_path / "nope")) == []


def test_list_nvidia_skips_unreadable_vendor(tmp_path):
    """If a /sys/bus/pci/devices/<bdf> has no vendor file, skip it."""
    (tmp_path / "0000:99:00.0").mkdir()
    assert pa.list_nvidia_pci_devs(sys_root=str(tmp_path)) == []


# ── read_per_dev_status ────────────────────────────────────────────────


def test_read_per_dev_full(tmp_path):
    d = tmp_path / "0000:01:00.0"; d.mkdir()
    (d / "current_link_speed").write_text("16.0 GT/s PCIe\n")
    (d / "current_link_width").write_text("16\n")
    (d / "d3cold_allowed").write_text("1\n")
    info = pa.read_per_dev_status(str(d))
    assert info["bdf"] == "0000:01:00.0"
    assert info["current_link_speed"] == "16.0 GT/s PCIe"
    assert info["current_link_width"] == "16"
    assert info["d3cold_allowed"] == "1"


def test_read_per_dev_missing_attrs(tmp_path):
    d = tmp_path / "0000:01:00.0"; d.mkdir()
    info = pa.read_per_dev_status(str(d))
    # All attrs should be None, but the function should not crash
    assert info["bdf"] == "0000:01:00.0"
    assert info["current_link_speed"] is None


# ── classify ───────────────────────────────────────────────────────────


def test_classify_unknown_no_policy():
    v = pa.classify(policy=None, board={"name": None}, devs=[])
    assert v["verdict"] == "unknown"


def test_classify_ok_performance():
    v = pa.classify(policy={"active": "performance", "options": []},
                     board={"name": None}, devs=[])
    assert v["verdict"] == "ok"


def test_classify_risky_powersave_on_z690():
    v = pa.classify(policy={"active": "powersave", "options": []},
                     board={"name": "Z690 AORUS MASTER"}, devs=[])
    assert v["verdict"] == "risky"
    assert "Z690" in v["reason"]
    assert "pcie_aspm=off" in v["recommendation"]


def test_classify_warn_powersave_safe_board():
    v = pa.classify(policy={"active": "powersave", "options": []},
                     board={"name": "Supermicro X11SCA-F"}, devs=[])
    assert v["verdict"] == "warn"


def test_classify_ok_default_safe_board():
    v = pa.classify(policy={"active": "default", "options": []},
                     board={"name": "Supermicro X11SCA-F"}, devs=[])
    assert v["verdict"] == "ok"


def test_classify_risky_default_on_b650():
    v = pa.classify(policy={"active": "default", "options": []},
                     board={"name": "ROG STRIX B650E-F"}, devs=[])
    assert v["verdict"] == "risky"


def test_classify_unknown_policy():
    v = pa.classify(policy={"active": "weird", "options": []},
                     board={"name": None}, devs=[])
    assert v["verdict"] == "unknown"


# ── status ─────────────────────────────────────────────────────────────


def test_status_aggregates():
    with patch.object(pa, "read_aspm_policy",
                      return_value={"active": "default", "options": ["default"]}):
        with patch.object(pa, "read_board_info",
                          return_value={"vendor": "ASUS", "name": "Z690-A"}):
            with patch.object(pa, "list_nvidia_pci_devs", return_value=[]):
                s = pa.status()
    assert s["ok"] is True
    assert s["board_known_risky"] is True
    assert s["verdict"]["verdict"] == "risky"


def test_status_no_nvidia_devs():
    with patch.object(pa, "read_aspm_policy",
                      return_value={"active": "performance", "options": []}):
        with patch.object(pa, "read_board_info",
                          return_value={"vendor": "X", "name": "Y"}):
            with patch.object(pa, "list_nvidia_pci_devs", return_value=[]):
                s = pa.status()
    assert s["nvidia_pci_devs"] == []
    assert s["verdict"]["verdict"] == "ok"
