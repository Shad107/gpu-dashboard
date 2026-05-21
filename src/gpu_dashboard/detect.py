"""Sondage de l'environnement local — utilisé par l'install script et les modules.

Toutes les fonctions retournent un dict structuré. Les commandes externes
(nvidia-smi, lspci, systemd-detect-virt) sont appelées avec timeout court et
les erreurs sont silencieusement converties en valeurs « non disponible ».
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Optional


# Chemins lus par detect_os() / detect_coolbits() — overridables pour les tests
OS_RELEASE_PATHS = ["/etc/os-release", "/usr/lib/os-release"]
XORG_CONF_PATHS = ["/etc/X11/xorg.conf", "/etc/X11/xorg.conf.d"]


# Mapping ID os-release → package manager
_PKG_MANAGER = {
    "ubuntu": "apt",
    "debian": "apt",
    "linuxmint": "apt",
    "pop": "apt",
    "fedora": "dnf",
    "rhel": "dnf",
    "centos": "dnf",
    "rocky": "dnf",
    "almalinux": "dnf",
    "arch": "pacman",
    "manjaro": "pacman",
    "endeavouros": "pacman",
    "opensuse": "zypper",
    "opensuse-leap": "zypper",
    "opensuse-tumbleweed": "zypper",
}


def _parse_os_release(path: str) -> dict:
    """Parse un fichier /etc/os-release → dict (clés en majuscules)."""
    data: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                data[k.strip()] = v
    except FileNotFoundError:
        pass
    return data


def detect_os() -> dict:
    """Détecte la distro et son gestionnaire de paquets via /etc/os-release.

    Retourne {"id": "ubuntu", "pretty_name": "Ubuntu 24.04 LTS", "package_manager": "apt"}.
    `package_manager` peut être None si la distro est inconnue.
    `id` est None si /etc/os-release est introuvable.
    """
    data: dict = {}
    for path in OS_RELEASE_PATHS:
        data = _parse_os_release(path)
        if data:
            break

    if not data:
        return {"id": None, "pretty_name": None, "package_manager": None}

    distro_id = data.get("ID", "").lower() or None
    pkg = _PKG_MANAGER.get(distro_id) if distro_id else None
    if pkg is None:
        # Fallback sur ID_LIKE
        id_like = data.get("ID_LIKE", "").lower().split()
        for cand in id_like:
            if cand in _PKG_MANAGER:
                pkg = _PKG_MANAGER[cand]
                break

    return {
        "id": distro_id,
        "pretty_name": data.get("PRETTY_NAME"),
        "package_manager": pkg,
    }


def detect_nvidia() -> dict:
    """Détecte la disponibilité de nvidia-smi et liste les GPU.

    Retourne :
      {
        "available": bool,
        "driver_version": str | None,
        "gpus": [
          {"name": ..., "bus_id": ..., "vram_mib": ..., "driver_version": ...},
          ...
        ]
      }
    """
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,pci.bus_id,memory.total,driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return {"available": False, "driver_version": None, "gpus": []}

    gpus: list = []
    if out.returncode == 0 and out.stdout.strip():
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                continue
            name, bus_id, vram_str, drv = parts[0], parts[1], parts[2], parts[3]
            # vram comme "24576 MiB"
            vram_mib = None
            m = re.match(r"^(\d+)", vram_str)
            if m:
                vram_mib = int(m.group(1))
            gpus.append({
                "name": name,
                "bus_id": bus_id,
                "vram_mib": vram_mib,
                "driver_version": drv,
            })

    driver = gpus[0]["driver_version"] if gpus else None
    return {"available": True, "driver_version": driver, "gpus": gpus}


def detect_coolbits() -> dict:
    """Cherche `Option "Coolbits" "<n>"` dans les fichiers de conf Xorg.

    Retourne {"enabled": bool, "value": int | None, "source": path | None}.
    """
    rx = re.compile(r'Option\s+"Coolbits"\s+"(\d+)"', re.IGNORECASE)

    def _scan(path: str) -> Optional[tuple]:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = rx.search(line)
                    if m:
                        return (int(m.group(1)), path)
        except (FileNotFoundError, OSError, IsADirectoryError):
            pass
        return None

    for p in XORG_CONF_PATHS:
        if os.path.isfile(p):
            result = _scan(p)
            if result:
                v, src = result
                return {"enabled": True, "value": v, "source": src}
        elif os.path.isdir(p):
            try:
                files = sorted(os.listdir(p))
            except OSError:
                continue
            for fname in files:
                if not fname.endswith(".conf"):
                    continue
                full = os.path.join(p, fname)
                result = _scan(full)
                if result:
                    v, src = result
                    return {"enabled": True, "value": v, "source": src}

    return {"enabled": False, "value": None, "source": None}


def detect_virt() -> dict:
    """Détecte si on tourne dans une VM via systemd-detect-virt.

    Retourne {"is_vm": bool, "type": str}. type peut être "kvm", "vmware",
    "lxc", "docker", "none", "unknown".
    """
    try:
        out = subprocess.run(
            ["systemd-detect-virt"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return {"is_vm": False, "type": "unknown"}

    vtype = out.stdout.strip() or "none"
    return {"is_vm": vtype not in ("", "none"), "type": vtype}


PCI_DEVICES_PATH = "/sys/bus/pci/devices"


def _normalize_bus_id(bus_id: str) -> Optional[str]:
    """Normalise différents formats de bus PCI vers la forme sysfs `0000:01:00.0`.

    Accepte :
      - `00000000:01:00.0` (nvidia-smi, 8-char domain)
      - `0000:01:00.0` (sysfs standard)
      - `01:00.0` (lspci court, on assume domain 0000)
    """
    parts = bus_id.split(":")
    if len(parts) == 2:
        return f"0000:{bus_id}"
    if len(parts) == 3:
        domain = parts[0]
        if len(domain) > 4:
            domain = domain[-4:]
        return f"{domain}:{parts[1]}:{parts[2]}"
    return None


def detect_external_gpu_link(bus_id: str) -> dict:
    """Sonde la largeur du lien PCIe d'un GPU pour repérer un setup eGPU/OcuLink.

    Lit `/sys/bus/pci/devices/<bus_id>/current_link_{width,speed}` — accessible
    sans privilèges, contrairement à `lspci -vv` qui demande root.

    Heuristique : x4 ou moins → probablement externe (OcuLink, Thunderbolt, M.2).

    Retourne {"link_width": int | None, "link_speed": str | None, "likely_external": bool}.
    """
    sysfs_bus = _normalize_bus_id(bus_id)
    if sysfs_bus is None:
        return {"link_width": None, "link_speed": None, "likely_external": False}

    base = os.path.join(PCI_DEVICES_PATH, sysfs_bus)

    def _read(attr: str) -> Optional[str]:
        try:
            with open(os.path.join(base, attr), "r") as f:
                return f.read().strip()
        except (FileNotFoundError, OSError):
            return None

    width_str = _read("current_link_width")
    speed_str = _read("current_link_speed")

    width: Optional[int] = None
    if width_str and width_str.isdigit():
        w = int(width_str)
        # Largeurs PCIe valides — filtre les valeurs garbage type 63 (GPU off the bus)
        if w in (1, 2, 4, 8, 16, 32):
            width = w

    likely_ext = width is not None and width <= 4
    return {"link_width": width, "link_speed": speed_str, "likely_external": likely_ext}
