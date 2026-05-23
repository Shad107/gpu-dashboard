"""Module pcie_aspm — PCIe ASPM audit (R&D #23.4).

PCIe Active State Power Management (ASPM) transitions the link to a
low-power state when idle. On consumer Z690 / B650 / X870 motherboards
the exit-latency advertised by some root ports is wrong, and the GPU
sees 50-200 ms PCIe stalls on the first packet after idle — directly
showing up as 'first token latency' in LLM inference.

Two distinct knobs :

  1. Global kernel policy : /sys/module/pcie_aspm/parameters/policy
     'default' / 'powersave' / 'powersupersave' enable ASPM ;
     'performance' disables it.
  2. Per-device override : 'lspci -vvv' LnkCtl ASPM bits (needs root).

This module reads what it can without sudo, plus the motherboard
DMI vendor/board strings (publicly readable) to flag known-risky
boards.

stdlib only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Optional


NAME = "pcie_aspm"


_ASPM_POLICY_PATH = "/sys/module/pcie_aspm/parameters/policy"
_DMI_BOARD_VENDOR = "/sys/class/dmi/id/board_vendor"
_DMI_BOARD_NAME = "/sys/class/dmi/id/board_name"


def read_aspm_policy(path: str = _ASPM_POLICY_PATH) -> Optional[dict]:
    """Read /sys/module/pcie_aspm/parameters/policy. Format :
    '[default] performance powersave powersupersave' — bracketed = active.
    Returns {active, options} or None if unreadable."""
    try:
        with open(path) as f:
            raw = f.read().strip()
    except OSError:
        return None
    options: list[str] = []
    active: Optional[str] = None
    for tok in raw.split():
        if tok.startswith("[") and tok.endswith("]"):
            inner = tok[1:-1]
            options.append(inner)
            active = inner
        else:
            options.append(tok)
    return {"active": active, "options": options, "raw": raw}


def read_board_info() -> dict:
    """Best-effort DMI board vendor + name."""
    out: dict = {"vendor": None, "name": None}
    for key, path in (("vendor", _DMI_BOARD_VENDOR),
                       ("name", _DMI_BOARD_NAME)):
        try:
            with open(path) as f:
                out[key] = f.read().strip() or None
        except OSError:
            pass
    return out


# Known-risky board substrings (case-insensitive). These chipsets have
# reported ASPM stalls in various forum threads.
_RISKY_BOARD_SUBSTRINGS = (
    "Z690", "Z790", "Z890",  # Intel 12th-gen+ consumer
    "B650", "B760", "B850",  # AMD Ryzen 7000+
    "X670", "X870",
)


def board_known_risky(board: dict) -> bool:
    name = (board.get("name") or "").upper()
    for s in _RISKY_BOARD_SUBSTRINGS:
        if s in name:
            return True
    return False


def list_nvidia_pci_devs(sys_root: str = "/sys/bus/pci/devices") -> list[str]:
    """Find every /sys/bus/pci/devices/<bdf> whose vendor is 0x10de."""
    out: list[str] = []
    try:
        names = os.listdir(sys_root)
    except OSError:
        return out
    for name in sorted(names):
        p = os.path.join(sys_root, name, "vendor")
        try:
            with open(p) as f:
                if f.read().strip().lower() == "0x10de":
                    out.append(os.path.join(sys_root, name))
        except OSError:
            continue
    return out


def read_per_dev_status(dev_path: str) -> dict:
    """Read the publicly-readable PCIe attrs for a device."""
    keys = ("current_link_speed", "current_link_width",
             "max_link_speed", "max_link_width",
             "d3cold_allowed", "power_state")
    out: dict = {"bdf": os.path.basename(dev_path)}
    for k in keys:
        p = os.path.join(dev_path, k)
        try:
            with open(p) as f:
                out[k] = f.read().strip()
        except OSError:
            out[k] = None
    return out


def lspci_aspm_bits(bdf: str, timeout: float = 2.0) -> Optional[dict]:
    """Parse `lspci -vvv -s <bdf>` for LnkCtl ASPM L0s/L1 state. Often
    requires root — returns None gracefully if denied."""
    if not shutil.which("lspci"):
        return None
    try:
        r = subprocess.run(
            ["lspci", "-vvv", "-s", bdf],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    txt = r.stdout
    if "LnkCtl" not in txt:
        return None  # likely access denied — fields stripped without root
    out: dict = {}
    m = re.search(r"LnkCtl:.*?ASPM\s+(\S+)", txt)
    if m:
        out["lnkctl_aspm"] = m.group(1).strip(",")
    m = re.search(r"LnkCap:.*?ASPM\s+(\S+\s+\S+)", txt)
    if m:
        out["lnkcap_aspm"] = m.group(1).strip(",")
    m = re.search(r"Exit Latency L0s ?<? ?(\S+)", txt)
    if m:
        out["exit_latency_l0s"] = m.group(1).strip(",")
    m = re.search(r"L1 ?<? ?(\S+)us", txt)
    if m:
        out["exit_latency_l1_us"] = m.group(1)
    return out if out else None


def classify(policy: Optional[dict], board: dict,
              devs: list[dict]) -> dict:
    """Return {verdict, reason, recommendation}."""
    if policy is None:
        return {"verdict": "unknown",
                "reason": "Cannot read /sys/module/pcie_aspm/parameters/policy.",
                "recommendation": ""}
    active = policy.get("active")
    risky_board = board_known_risky(board)
    if active == "performance":
        return {"verdict": "ok",
                "reason": "ASPM globally disabled — no risk of low-power stalls.",
                "recommendation": ""}
    if active in ("powersave", "powersupersave"):
        if risky_board:
            return {"verdict": "risky",
                    "reason": (f"ASPM in '{active}' mode on a known-risky "
                               f"chipset ({board.get('name')}). Forum reports "
                               "200ms PCIe stalls under load."),
                    "recommendation": (
                        "Add 'pcie_aspm=off' to GRUB_CMDLINE_LINUX_DEFAULT, "
                        "then update-grub + reboot.")}
        return {"verdict": "warn",
                "reason": (f"ASPM in '{active}' mode. Safe on most server-grade "
                           "boards but worth verifying if you see latency spikes."),
                "recommendation": "Watch for inference stalls ; switch to "
                                    "'performance' if any."}
    if active == "default":
        if risky_board:
            return {"verdict": "risky",
                    "reason": (f"ASPM in default mode (kernel chooses based "
                               f"on board) on a known-risky chipset "
                               f"({board.get('name')}). Likely active behind "
                               "the scenes."),
                    "recommendation": (
                        "Disable explicitly with 'pcie_aspm=off' kernel "
                        "parameter if you see inference latency spikes.")}
        return {"verdict": "ok",
                "reason": ("ASPM in default mode — kernel picks based on "
                           "board capabilities. No specific risk detected."),
                "recommendation": ""}
    return {"verdict": "unknown",
            "reason": f"Unrecognized ASPM policy '{active}'.",
            "recommendation": ""}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    policy = read_aspm_policy()
    board = read_board_info()
    devs_paths = list_nvidia_pci_devs()
    devs: list[dict] = []
    for d in devs_paths:
        info = read_per_dev_status(d)
        bits = lspci_aspm_bits(info["bdf"])
        if bits:
            info["aspm"] = bits
        devs.append(info)
    verdict = classify(policy, board, devs)
    return {
        "ok": True,
        "policy": policy,
        "board": board,
        "board_known_risky": board_known_risky(board),
        "nvidia_pci_devs": devs,
        "verdict": verdict,
    }
