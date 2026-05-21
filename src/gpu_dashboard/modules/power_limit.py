"""Module Power Limit — slider de plafond de consommation GPU via sudoers wrapper.

Prérequis :
- Un wrapper root à `/usr/local/bin/set-power-limit` qui valide son arg et appelle `nvidia-smi -pl`
- Une règle sudoers passwordless ciblant uniquement ce wrapper

L'API publique du module :
- `can_enable(wrapper_path) → (bool, str)` : ce module peut-il fonctionner ici ?
- `validate_watts(profile, watts)` : raise ValueError si hors plage du profil
- `apply_power_limit(profile, watts, wrapper_path) → dict` : applique via sudo -n
- `get_current_limit() → int | None` : lit la valeur courante via nvidia-smi
- `parse_nvidia_smi_power_limit(stdout) → int | None` : helper de parsing
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Optional, Tuple


NAME = "power_limit"
DEFAULT_WRAPPER_PATH = "/usr/local/bin/set-power-limit"


# ─────────────────────────────── validation ────────────────────────────────


def validate_watts(profile: dict, watts) -> None:
    """Lève ValueError si watts hors de la plage du profil.

    Fallback à 100-350 si profile n'a pas de power.min/max.
    """
    if not isinstance(watts, int) and not (isinstance(watts, str) and watts.lstrip("-").isdigit()):
        raise ValueError(f"watts must be an integer, got {watts!r}")
    w = int(watts)
    power = profile.get("power", {}) or {}
    pmin = int(power.get("min", 100))
    pmax = int(power.get("max", 350))
    if w < pmin or w > pmax:
        raise ValueError(f"watts out of range [{pmin}, {pmax}], got {w}")


# ──────────────────────────── parsing nvidia-smi ───────────────────────────


_POWER_LIMIT_RX = re.compile(r"(\d+(?:\.\d+)?)\s*W")


def parse_nvidia_smi_power_limit(stdout: str) -> Optional[int]:
    """Parse `250.00 W` ou `350 W` → int (watts, arrondi). None si invalide."""
    if not stdout:
        return None
    m = _POWER_LIMIT_RX.search(stdout)
    if not m:
        return None
    try:
        # Rounding traditionnel (half-up), pas banker's rounding de Python 3
        return int(float(m.group(1)) + 0.5)
    except (ValueError, TypeError):
        return None


# ────────────────────────────── éligibilité ────────────────────────────────


def can_enable(wrapper_path: str = DEFAULT_WRAPPER_PATH) -> Tuple[bool, str]:
    """Le module peut-il être activé sur cette machine ?

    Vérifie :
    1. Le wrapper existe au path attendu
    2. Le wrapper est exécutable
    3. `sudo -n -l <wrapper>` réussit (sudoers passwordless en place)
    """
    if not os.path.isfile(wrapper_path):
        return False, f"Wrapper missing at {wrapper_path}. Run install.sh to set it up."
    if not os.access(wrapper_path, os.X_OK):
        return False, f"Wrapper {wrapper_path} is not executable."

    try:
        r = subprocess.run(
            ["sudo", "-n", "-l", wrapper_path],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return False, f"sudo unavailable: {e}"

    if r.returncode != 0:
        return False, "Passwordless sudoers not configured for this wrapper (rerun install.sh)."

    return True, "OK"


# ─────────────────────────────── application ───────────────────────────────


def apply_power_limit(
    profile: dict,
    watts: int,
    wrapper_path: str = DEFAULT_WRAPPER_PATH,
) -> dict:
    """Applique le power-limit via le wrapper sudoers.

    Lève ValueError si watts hors plage (avant tout appel système).
    Retourne {ok, watts, output, error?}.
    """
    validate_watts(profile, watts)
    try:
        r = subprocess.run(
            ["sudo", "-n", wrapper_path, str(int(watts))],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return {"ok": False, "watts": int(watts), "output": "", "error": str(e)}

    ok = r.returncode == 0 and "was set to" in r.stdout
    out = {"ok": ok, "watts": int(watts), "output": r.stdout}
    if not ok:
        out["error"] = r.stderr or r.stdout or f"exit {r.returncode}"
    return out


def get_current_limit() -> Optional[int]:
    """Lit le power-limit actuel via `nvidia-smi --query-gpu=power.limit`."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.limit", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0:
        return None
    return parse_nvidia_smi_power_limit(r.stdout)
