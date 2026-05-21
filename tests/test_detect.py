"""Tests pour gpu_dashboard.detect — sondage d'environnement.

Les fonctions de détection appellent souvent subprocess (nvidia-smi, lspci, etc.)
ou lisent des fichiers système. On utilise monkeypatch pour mocker proprement
sans dépendre de la machine de test.
"""
from __future__ import annotations

import os
import subprocess
import pytest

from gpu_dashboard.detect import (
    detect_os,
    detect_nvidia,
    detect_coolbits,
    detect_virt,
    detect_external_gpu_link,
)


# ─────────────────── helpers pour mocker subprocess.run ────────────────────


class _FakeCompleted:
    """Stub minimaliste pour remplacer subprocess.CompletedProcess."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _make_run_stub(responses: dict):
    """Renvoie un faux `subprocess.run` qui matche le premier argument (cmd[0])."""

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)):
            bin_name = os.path.basename(cmd[0])
        else:
            bin_name = os.path.basename(cmd.split()[0])
        if bin_name in responses:
            r = responses[bin_name]
            if isinstance(r, Exception):
                raise r
            return _FakeCompleted(**r)
        return _FakeCompleted(returncode=127, stderr=f"not found: {bin_name}")

    return fake_run


# ────────────────────────────── detect_os ──────────────────────────────────


class TestDetectOS:
    def test_ubuntu(self, tmp_path, monkeypatch):
        f = tmp_path / "os-release"
        f.write_text(
            'NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04 LTS"\n'
        )
        monkeypatch.setattr("gpu_dashboard.detect.OS_RELEASE_PATHS", [str(f)])
        info = detect_os()
        assert info["id"] == "ubuntu"
        assert info["package_manager"] == "apt"
        assert "Ubuntu" in info["pretty_name"]

    def test_fedora(self, tmp_path, monkeypatch):
        f = tmp_path / "os-release"
        f.write_text('ID=fedora\nPRETTY_NAME="Fedora Linux 40 (Server Edition)"\n')
        monkeypatch.setattr("gpu_dashboard.detect.OS_RELEASE_PATHS", [str(f)])
        info = detect_os()
        assert info["id"] == "fedora"
        assert info["package_manager"] == "dnf"

    def test_arch(self, tmp_path, monkeypatch):
        f = tmp_path / "os-release"
        f.write_text('ID=arch\nPRETTY_NAME="Arch Linux"\n')
        monkeypatch.setattr("gpu_dashboard.detect.OS_RELEASE_PATHS", [str(f)])
        info = detect_os()
        assert info["id"] == "arch"
        assert info["package_manager"] == "pacman"

    def test_unknown(self, tmp_path, monkeypatch):
        f = tmp_path / "os-release"
        f.write_text('ID=weirdix\nPRETTY_NAME="Some Weird OS"\n')
        monkeypatch.setattr("gpu_dashboard.detect.OS_RELEASE_PATHS", [str(f)])
        info = detect_os()
        assert info["id"] == "weirdix"
        assert info["package_manager"] is None

    def test_missing_os_release(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gpu_dashboard.detect.OS_RELEASE_PATHS", [str(tmp_path / "nope")])
        info = detect_os()
        assert info["id"] is None
        assert info["package_manager"] is None

    def test_id_like_fallback(self, tmp_path, monkeypatch):
        """Si ID n'est pas connu mais ID_LIKE l'est, on prend ID_LIKE."""
        f = tmp_path / "os-release"
        f.write_text('ID=pop\nID_LIKE="ubuntu debian"\nPRETTY_NAME="Pop!_OS"\n')
        monkeypatch.setattr("gpu_dashboard.detect.OS_RELEASE_PATHS", [str(f)])
        info = detect_os()
        assert info["package_manager"] == "apt"


# ──────────────────────────── detect_nvidia ────────────────────────────────


class TestDetectNvidia:
    def test_nvidia_smi_absent(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            _make_run_stub({"nvidia-smi": FileNotFoundError("not installed")}),
        )
        info = detect_nvidia()
        assert info["available"] is False
        assert info["driver_version"] is None
        assert info["gpus"] == []

    def test_nvidia_smi_present_one_gpu(self, monkeypatch):
        stdout = "NVIDIA GeForce RTX 3090, 00000000:01:00.0, 24576 MiB, 590.48.01\n"
        monkeypatch.setattr(
            subprocess, "run",
            _make_run_stub({"nvidia-smi": {"stdout": stdout, "returncode": 0}}),
        )
        info = detect_nvidia()
        assert info["available"] is True
        assert info["driver_version"] == "590.48.01"
        assert len(info["gpus"]) == 1
        gpu = info["gpus"][0]
        assert gpu["name"] == "NVIDIA GeForce RTX 3090"
        assert gpu["bus_id"] == "00000000:01:00.0"
        assert gpu["vram_mib"] == 24576

    def test_nvidia_smi_multiple_gpus(self, monkeypatch):
        stdout = (
            "NVIDIA GeForce RTX 3090, 00000000:01:00.0, 24576 MiB, 590.48.01\n"
            "NVIDIA GeForce RTX 4090, 00000000:02:00.0, 24576 MiB, 590.48.01\n"
        )
        monkeypatch.setattr(
            subprocess, "run",
            _make_run_stub({"nvidia-smi": {"stdout": stdout, "returncode": 0}}),
        )
        info = detect_nvidia()
        assert len(info["gpus"]) == 2
        assert {g["name"] for g in info["gpus"]} == {
            "NVIDIA GeForce RTX 3090",
            "NVIDIA GeForce RTX 4090",
        }

    def test_nvidia_smi_error_returncode(self, monkeypatch):
        """nvidia-smi présent mais qui crashe → available True, mais gpus vide."""
        monkeypatch.setattr(
            subprocess, "run",
            _make_run_stub({"nvidia-smi": {"stdout": "", "returncode": 1, "stderr": "Unknown Error"}}),
        )
        info = detect_nvidia()
        assert info["available"] is True
        assert info["gpus"] == []


# ─────────────────────────── detect_coolbits ───────────────────────────────


class TestDetectCoolbits:
    def test_coolbits_in_drop_in(self, tmp_path, monkeypatch):
        d = tmp_path / "xorg.conf.d"
        d.mkdir()
        (d / "20-nvidia.conf").write_text(
            'Section "Device"\n  Option "Coolbits" "12"\nEndSection\n'
        )
        monkeypatch.setattr(
            "gpu_dashboard.detect.XORG_CONF_PATHS",
            [str(tmp_path / "xorg.conf"), str(d)],
        )
        info = detect_coolbits()
        assert info["enabled"] is True
        assert info["value"] == 12

    def test_coolbits_in_main_conf(self, tmp_path, monkeypatch):
        f = tmp_path / "xorg.conf"
        f.write_text('Option "Coolbits" "28"\n')
        monkeypatch.setattr(
            "gpu_dashboard.detect.XORG_CONF_PATHS",
            [str(f), str(tmp_path / "xorg.conf.d")],
        )
        info = detect_coolbits()
        assert info["enabled"] is True
        assert info["value"] == 28

    def test_coolbits_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "gpu_dashboard.detect.XORG_CONF_PATHS",
            [str(tmp_path / "x1"), str(tmp_path / "x2")],
        )
        info = detect_coolbits()
        assert info["enabled"] is False
        assert info["value"] is None


# ────────────────────────────── detect_virt ────────────────────────────────


class TestDetectVirt:
    def test_kvm(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            _make_run_stub({"systemd-detect-virt": {"stdout": "kvm\n", "returncode": 0}}),
        )
        info = detect_virt()
        assert info["is_vm"] is True
        assert info["type"] == "kvm"

    def test_bare_metal(self, monkeypatch):
        # systemd-detect-virt returncode 1 + stdout="none" → bare metal
        monkeypatch.setattr(
            subprocess, "run",
            _make_run_stub({"systemd-detect-virt": {"stdout": "none\n", "returncode": 1}}),
        )
        info = detect_virt()
        assert info["is_vm"] is False
        assert info["type"] == "none"

    def test_command_missing(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            _make_run_stub({"systemd-detect-virt": FileNotFoundError("nope")}),
        )
        info = detect_virt()
        assert info["is_vm"] is False
        assert info["type"] == "unknown"


# ──────────────────── detect_external_gpu_link (OcuLink) ───────────────────


def _setup_fake_pci(tmp_path, monkeypatch, bus_id, width, speed):
    """Crée une fausse arborescence /sys/bus/pci/devices/<bus_id>/ pour les tests."""
    base = tmp_path / "pci"
    base.mkdir()
    if bus_id is not None:
        dev = base / bus_id
        dev.mkdir()
        if width is not None:
            (dev / "current_link_width").write_text(str(width) + "\n")
        if speed is not None:
            (dev / "current_link_speed").write_text(speed + "\n")
    monkeypatch.setattr("gpu_dashboard.detect.PCI_DEVICES_PATH", str(base))


class TestDetectExternalGpuLink:
    def test_x4_link_likely_external(self, tmp_path, monkeypatch):
        _setup_fake_pci(tmp_path, monkeypatch, "0000:01:00.0", 4, "16 GT/s PCIe")
        info = detect_external_gpu_link("0000:01:00.0")
        assert info["link_width"] == 4
        assert info["likely_external"] is True

    def test_x16_link_internal(self, tmp_path, monkeypatch):
        _setup_fake_pci(tmp_path, monkeypatch, "0000:01:00.0", 16, "16 GT/s PCIe")
        info = detect_external_gpu_link("0000:01:00.0")
        assert info["link_width"] == 16
        assert info["likely_external"] is False

    def test_bus_id_8char_format(self, tmp_path, monkeypatch):
        """nvidia-smi renvoie un domain en 8 hex chars — on doit le normaliser."""
        _setup_fake_pci(tmp_path, monkeypatch, "0000:01:00.0", 16, "16 GT/s PCIe")
        info = detect_external_gpu_link("00000000:01:00.0")
        assert info["link_width"] == 16

    def test_bus_id_short_format(self, tmp_path, monkeypatch):
        """lspci -s renvoie sans domain — on assume 0000."""
        _setup_fake_pci(tmp_path, monkeypatch, "0000:01:00.0", 16, "16 GT/s PCIe")
        info = detect_external_gpu_link("01:00.0")
        assert info["link_width"] == 16

    def test_garbage_width_ignored(self, tmp_path, monkeypatch):
        """GPU en header-7f → width = 63 (garbage). On doit l'ignorer."""
        _setup_fake_pci(tmp_path, monkeypatch, "0000:01:00.0", 63, "Unknown")
        info = detect_external_gpu_link("0000:01:00.0")
        assert info["link_width"] is None
        assert info["likely_external"] is False

    def test_missing_device(self, tmp_path, monkeypatch):
        _setup_fake_pci(tmp_path, monkeypatch, None, None, None)
        info = detect_external_gpu_link("0000:01:00.0")
        assert info["link_width"] is None
        assert info["likely_external"] is False
