"""R&D #9.1 — VFIO sentinel tests."""
import os
import tempfile
from unittest.mock import patch
from gpu_dashboard.modules import vfio_sentinel as vs


def _make_pci_dev(td, bdf, vendor, device, klass, driver=None):
    """Create a fake /sys/bus/pci/devices/<bdf> tree."""
    dev_dir = os.path.join(td, bdf)
    os.makedirs(dev_dir, exist_ok=True)
    with open(os.path.join(dev_dir, "vendor"), "w") as f:
        f.write(vendor + "\n")
    with open(os.path.join(dev_dir, "device"), "w") as f:
        f.write(device + "\n")
    with open(os.path.join(dev_dir, "class"), "w") as f:
        f.write(klass + "\n")
    if driver:
        drivers_dir = os.path.join(td, "_drivers", driver)
        os.makedirs(drivers_dir, exist_ok=True)
        os.symlink(drivers_dir, os.path.join(dev_dir, "driver"))
    return dev_dir


def test_list_nvidia_gpus_no_nvidia_returns_empty():
    with tempfile.TemporaryDirectory() as td:
        _make_pci_dev(td, "0000:00:00.0", "0x8086", "0x1234", "0x060000")  # intel host
        with patch("glob.glob", return_value=[os.path.join(td, "0000:00:00.0")]):
            assert vs.list_nvidia_gpus_with_state() == []


def test_list_nvidia_gpus_detects_nvidia_vga():
    with tempfile.TemporaryDirectory() as td:
        _make_pci_dev(td, "0000:01:00.0", "0x10de", "0x2204", "0x030000", driver="nvidia")
        with patch("glob.glob", return_value=[os.path.join(td, "0000:01:00.0")]):
            gpus = vs.list_nvidia_gpus_with_state()
    assert len(gpus) == 1
    assert gpus[0]["bdf"] == "0000:01:00.0"
    assert gpus[0]["driver"] == "nvidia"
    assert gpus[0]["is_vfio"] is False


def test_list_nvidia_gpus_detects_vfio_binding():
    with tempfile.TemporaryDirectory() as td:
        _make_pci_dev(td, "0000:01:00.0", "0x10de", "0x2204", "0x030000", driver="vfio-pci")
        with patch("glob.glob", return_value=[os.path.join(td, "0000:01:00.0")]):
            gpus = vs.list_nvidia_gpus_with_state()
    assert gpus[0]["is_vfio"] is True
    assert gpus[0]["driver"] == "vfio-pci"


def test_list_nvidia_gpus_skips_non_gpu_class():
    """NVIDIA audio devices (class 0x040300) shouldn't show as GPUs."""
    with tempfile.TemporaryDirectory() as td:
        # Audio device shouldn't show — class 0x040300 doesn't start with 0x030
        _make_pci_dev(td, "0000:06:11.0", "0x10de", "0x1aef", "0x040300", driver="snd_hda_intel")
        with patch("glob.glob", return_value=[os.path.join(td, "0000:06:11.0")]):
            gpus = vs.list_nvidia_gpus_with_state()
    assert gpus == []


def test_list_nvidia_gpus_unbound_returns_unbound_marker():
    """Device with no driver/ symlink → driver='unbound'."""
    with tempfile.TemporaryDirectory() as td:
        _make_pci_dev(td, "0000:01:00.0", "0x10de", "0x2204", "0x030000", driver=None)
        with patch("glob.glob", return_value=[os.path.join(td, "0000:01:00.0")]):
            gpus = vs.list_nvidia_gpus_with_state()
    assert gpus[0]["driver"] == "unbound"
    assert gpus[0]["is_vfio"] is False


def test_find_qemu_holders_no_qemu():
    """No qemu running → empty list."""
    with patch("glob.glob", return_value=[]):
        assert vs.find_qemu_holders_for_bdf("0000:01:00.0") == []


def test_find_qemu_holders_matches_bdf_in_cmdline(tmp_path):
    """Mock /proc/<pid>/cmdline of a qemu process holding the PCI device."""
    pid_dir = tmp_path / "12345"
    pid_dir.mkdir()
    cmdline = "qemu-system-x86_64\0-name\0win11-gaming\0-device\0vfio-pci,host=01:00.0\0"
    (pid_dir / "cmdline").write_text(cmdline)
    (pid_dir / "stat").write_text("12345 (qemu) S " + " ".join(["0"] * 20) + " 0")

    with patch("glob.glob") as m:
        # First call : list of /proc/[0-9]*
        m.return_value = [str(pid_dir)]
        with patch.object(vs, "_read_file", side_effect=lambda p: (
            cmdline if "cmdline" in p else
            "/proc/uptime placeholder" if "uptime" in p else
            None
        )):
            holders = vs.find_qemu_holders_for_bdf("0000:01:00.0")
    assert len(holders) == 1
    assert holders[0]["pid"] == 12345
    assert holders[0]["name"] == "win11-gaming"


def test_status_aggregates_count_and_vm_holders():
    """status() should attach vm_holders to vfio-bound GPUs only."""
    fake_gpus = [
        {"bdf": "0000:01:00.0", "vendor_id": "0x10de", "device_id": "0x2204",
         "driver": "vfio-pci", "is_vfio": True, "class": "0x030000"},
        {"bdf": "0000:02:00.0", "vendor_id": "0x10de", "device_id": "0x2208",
         "driver": "nvidia", "is_vfio": False, "class": "0x030000"},
    ]
    with patch.object(vs, "list_nvidia_gpus_with_state", return_value=fake_gpus), \
         patch.object(vs, "find_qemu_holders_for_bdf",
                      return_value=[{"pid": 123, "name": "vm1", "uptime_s": 300}]):
        result = vs.status()
    assert result["gpus_count"] == 2
    assert result["vfio_bound_count"] == 1
    assert result["any_passthrough_active"] is True
    # Only the vfio-bound GPU should have non-empty vm_holders
    vfio_gpu = next(g for g in result["gpus"] if g["is_vfio"])
    nvidia_gpu = next(g for g in result["gpus"] if not g["is_vfio"])
    assert len(vfio_gpu["vm_holders"]) == 1
    assert nvidia_gpu["vm_holders"] == []
