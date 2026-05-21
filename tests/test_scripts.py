"""Tests pour les scripts d'install sudo (scripts/install-*.sh).

Ces scripts sont bash, mais on les teste depuis Python : présence, exécutables,
--print/--check ne nécessitant pas root.
"""
from __future__ import annotations

import os
import subprocess

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")

ALL_SCRIPTS = [
    "install-power-limit-wrapper.sh",
    "install-coolbits-xorg.sh",
    "install-oculink-watchdog.sh",
]


@pytest.mark.parametrize("script", ALL_SCRIPTS)
class TestScriptsExist:
    def test_file_exists(self, script):
        path = os.path.join(SCRIPTS_DIR, script)
        assert os.path.isfile(path), f"{path} missing"

    def test_is_executable(self, script):
        path = os.path.join(SCRIPTS_DIR, script)
        assert os.access(path, os.X_OK), f"{path} not executable"

    def test_has_shebang(self, script):
        path = os.path.join(SCRIPTS_DIR, script)
        with open(path) as f:
            first = f.readline().strip()
        assert first.startswith("#!/"), f"{path}: missing shebang"

    def test_set_strict_mode(self, script):
        """Tous les scripts doivent avoir `set -euo pipefail` quelque part."""
        path = os.path.join(SCRIPTS_DIR, script)
        with open(path) as f:
            content = f.read()
        assert "set -euo pipefail" in content

    def test_print_mode_runs_without_root(self, script):
        """--print ne doit jamais demander root."""
        path = os.path.join(SCRIPTS_DIR, script)
        # power-limit a besoin de --user en print mode
        args = [path, "--print"]
        if script == "install-power-limit-wrapper.sh":
            args = [path, "--user", "testuser", "--print"]
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
        assert r.returncode == 0, f"--print failed: {r.stderr}"
        # Doit produire du contenu
        assert len(r.stdout) > 100

    def test_check_mode_exits_cleanly(self, script):
        """--check renvoie 0 ou 1 mais ne crash jamais."""
        path = os.path.join(SCRIPTS_DIR, script)
        r = subprocess.run([path, "--check"], capture_output=True, text=True, timeout=5)
        # 0 (installé) ou 1 (pas installé) — pas de 2 (crash arg parsing)
        assert r.returncode in (0, 1), f"--check returned {r.returncode}: {r.stderr}"

    def test_unknown_arg_fails(self, script):
        path = os.path.join(SCRIPTS_DIR, script)
        r = subprocess.run([path, "--garbage-flag"], capture_output=True, text=True, timeout=5)
        assert r.returncode == 2


class TestPowerLimitScript:
    def test_print_includes_wrapper_content(self):
        path = os.path.join(SCRIPTS_DIR, "install-power-limit-wrapper.sh")
        r = subprocess.run([path, "--user", "alice", "--print"],
                           capture_output=True, text=True, timeout=5)
        assert "nvidia-smi -pl" in r.stdout
        assert "alice ALL=(root) NOPASSWD: /usr/local/bin/set-power-limit" in r.stdout

    def test_user_flag_required_or_inferred(self):
        """Sans --user et sans SUDO_USER/USER root, le script refuse d'installer."""
        path = os.path.join(SCRIPTS_DIR, "install-power-limit-wrapper.sh")
        # On simule un environnement sans USER
        env = {"PATH": os.environ.get("PATH", "")}
        r = subprocess.run([path, "--user", "root"],
                           capture_output=True, text=True, timeout=5, env=env)
        # User=root est explicitement refusé
        assert r.returncode != 0
        assert "non-root user" in r.stderr or "root" in r.stderr.lower()


class TestCoolbitsScript:
    def test_print_default_simple_mode(self):
        path = os.path.join(SCRIPTS_DIR, "install-coolbits-xorg.sh")
        r = subprocess.run([path, "--print"], capture_output=True, text=True, timeout=5)
        assert 'Option "Coolbits" "12"' in r.stdout
        # Pas de ServerLayout en mode simple
        assert "ServerLayout" not in r.stdout

    def test_print_headless_includes_serverflags(self):
        path = os.path.join(SCRIPTS_DIR, "install-coolbits-xorg.sh")
        r = subprocess.run([path, "--headless", "--print"],
                           capture_output=True, text=True, timeout=5)
        assert "ServerFlags" in r.stdout
        assert "AllowEmptyInitialConfiguration" in r.stdout
        assert "ConnectedMonitor" in r.stdout

    def test_custom_bus_id(self):
        path = os.path.join(SCRIPTS_DIR, "install-coolbits-xorg.sh")
        r = subprocess.run([path, "--bus-id", "PCI:3:0:0", "--print"],
                           capture_output=True, text=True, timeout=5)
        assert 'BusID "PCI:3:0:0"' in r.stdout


class TestWatchdogScript:
    def test_print_includes_systemd_unit(self):
        path = os.path.join(SCRIPTS_DIR, "install-oculink-watchdog.sh")
        r = subprocess.run([path, "--print"], capture_output=True, text=True, timeout=5)
        assert "[Unit]" in r.stdout
        assert "ExecStart=/usr/local/bin/gpu-oculink-watchdog.sh" in r.stdout

    def test_custom_interval(self):
        path = os.path.join(SCRIPTS_DIR, "install-oculink-watchdog.sh")
        r = subprocess.run([path, "--interval", "30", "--print"],
                           capture_output=True, text=True, timeout=5)
        assert "sleep 30" in r.stdout
