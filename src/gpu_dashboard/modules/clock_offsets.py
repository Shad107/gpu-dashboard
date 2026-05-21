"""Module Clock Offsets — sliders d'offset GPU/mémoire (undervolt / overclock).

Prérequis :
- Coolbits ≥ 8 dans xorg.conf (bit 3 = sliders OC dans nvidia-settings)
- Un serveur X accessible avec DISPLAY + XAUTHORITY
- Le serveur X doit être attaché à la NVIDIA (pas un Xvnc software-only)

L'API publique :
- `can_enable(coolbits_info) → (bool, str)` : Coolbits permet-il l'écriture ?
- `classify_zone(value, zones) → "safe"|"moderate"|"aggressive"|"danger"`
- `validate_offsets(profile, gpu, mem)` : raise ValueError si hors plage
- `get_current_offsets(display, xauthority=None) → {"gpu": int|None, "mem": int|None}`
- `apply_offsets(profile, gpu, mem, display, xauthority=None) → dict`
- `parse_offsets_query(stdout) → {"gpu": int|None, "mem": int|None}` : helper
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Optional, Tuple


NAME = "clock_offsets"

# Bit 3 (valeur 8) du Coolbits = sliders OC dans nvidia-settings
_COOLBITS_OC_BIT = 8

# Attributs nvidia-settings utilisés
_ATTR_GPU = "GPUGraphicsClockOffsetAllPerformanceLevels"
_ATTR_MEM = "GPUMemoryTransferRateOffsetAllPerformanceLevels"

_RX_OFFSET_QUERY = re.compile(
    r"Attribute '(GPUGraphicsClockOffsetAllPerformanceLevels|GPUMemoryTransferRateOffsetAllPerformanceLevels)'"
    r"\s*\(desktop:\d+\[gpu:\d+\]\)\s*:\s*(-?\d+)\."
)


# ───────────────────────────── classify_zone ───────────────────────────────


def classify_zone(value, zones: dict) -> str:
    """Renvoie la zone de risque pour `value` selon les seuils `zones`.

    `zones` doit contenir les clés safe/moderate/aggressive/danger (entiers).
    Au-dessus de danger → "danger". En dessous de 0 → "safe" (underclock OK).
    """
    if not all(k in zones for k in ("safe", "moderate", "aggressive", "danger")):
        return "unknown"
    if value <= zones["safe"]:
        return "safe"
    if value <= zones["moderate"]:
        return "moderate"
    if value <= zones["aggressive"]:
        return "aggressive"
    return "danger"


# ─────────────────────────── validate_offsets ──────────────────────────────


def validate_offsets(profile: dict, gpu: int, mem: int) -> None:
    """Vérifie que gpu/mem sont dans les limites du profil. Lève ValueError sinon.

    Les valeurs négatives (underclock) sont tolérées (pas de min strict).
    """
    clocks = profile.get("clocks", {}) or {}
    gpu_max = clocks.get("gpu_offset_max", 200)
    mem_max = clocks.get("mem_offset_max", 1500)

    if gpu > gpu_max:
        raise ValueError(f"gpu offset out of range: {gpu} > max {gpu_max}")
    if mem > mem_max:
        raise ValueError(f"mem offset out of range: {mem} > max {mem_max}")


# ─────────────────────────── parse_offsets_query ───────────────────────────


def parse_offsets_query(stdout: str) -> dict:
    """Parse la sortie de `nvidia-settings -q <ATTR>` → dict {"gpu": int|None, "mem": int|None}."""
    result = {"gpu": None, "mem": None}
    if not stdout:
        return result
    for m in _RX_OFFSET_QUERY.finditer(stdout):
        attr, val = m.group(1), int(m.group(2))
        if attr == _ATTR_GPU:
            result["gpu"] = val
        elif attr == _ATTR_MEM:
            result["mem"] = val
    return result


# ──────────────────────────────── can_enable ───────────────────────────────


def can_enable(coolbits_info: dict) -> Tuple[bool, str]:
    """Le module peut-il être activé ? Vérifie le Coolbits + bit OC sliders.

    `coolbits_info` est le dict retourné par `gpu_dashboard.detect.detect_coolbits()`.
    """
    if not coolbits_info or not coolbits_info.get("enabled"):
        return False, "Coolbits not configured in xorg.conf — run install.sh to set it up."
    value = coolbits_info.get("value", 0)
    if not (value & _COOLBITS_OC_BIT):
        return False, (
            f"Coolbits={value} but OC bit (8) missing. "
            f"Set Coolbits=12 or higher in xorg.conf and restart X."
        )
    return True, "OK"


# ─────────────────────────── env helpers internes ──────────────────────────


def _build_env(display: str, xauthority: Optional[str]) -> dict:
    env = os.environ.copy()
    env["DISPLAY"] = display
    if xauthority:
        env["XAUTHORITY"] = xauthority
    return env


# ─────────────────────────── get_current_offsets ───────────────────────────


def get_current_offsets(display: str = ":0", xauthority: Optional[str] = None) -> dict:
    """Lit les offsets actuels via nvidia-settings sur le DISPLAY donné."""
    try:
        r = subprocess.run(
            ["nvidia-settings",
             "-q", f"[gpu:0]/{_ATTR_GPU}",
             "-q", f"[gpu:0]/{_ATTR_MEM}"],
            capture_output=True, text=True, timeout=5,
            env=_build_env(display, xauthority),
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return {"gpu": None, "mem": None}
    if r.returncode != 0:
        return {"gpu": None, "mem": None}
    return parse_offsets_query(r.stdout)


# ──────────────────────────────── apply_offsets ────────────────────────────


def apply_offsets(
    profile: dict,
    gpu: int,
    mem: int,
    display: str = ":0",
    xauthority: Optional[str] = None,
) -> dict:
    """Applique les deux offsets via nvidia-settings -a.

    Lève ValueError si hors plage du profil avant tout appel système.
    Retourne {ok, gpu, mem, output, error?}.
    """
    validate_offsets(profile, gpu, mem)
    try:
        r = subprocess.run(
            ["nvidia-settings",
             "-a", f"[gpu:0]/{_ATTR_GPU}={int(gpu)}",
             "-a", f"[gpu:0]/{_ATTR_MEM}={int(mem)}"],
            capture_output=True, text=True, timeout=8,
            env=_build_env(display, xauthority),
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "gpu": int(gpu), "mem": int(mem), "output": "", "error": str(e)}

    ok = r.returncode == 0 and "assigned value" in r.stdout and "ERROR" not in r.stdout.upper()
    out = {"ok": ok, "gpu": int(gpu), "mem": int(mem), "output": r.stdout}
    if not ok:
        out["error"] = r.stderr or r.stdout or f"exit {r.returncode}"
    return out
