"""Module installer — F6 generalized one-click wrapper installer.

Generalizes the password-prompt install pattern shipped for the PCIe
recovery wrapper (F4.4) to ALL of the project's install scripts.
The user types their sudo password once in the dashboard UI; the
backend pipes it to `sudo -S bash <script>` via subprocess stdin.

Whitelist-driven for security: only scripts registered in
SCRIPT_REGISTRY are allowed. The dashboard cannot run arbitrary sudo
commands — only the install scripts that ship in this repo.

API surface :
  list_available() → list of {id, label, description, installed}
  check_installed(script_id) → bool
  install_script(script_id, password) → result dict

stdlib only.
"""
from __future__ import annotations

import os
import pwd
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional


def _repo_root() -> Optional[Path]:
    here = Path(__file__).resolve()
    if len(here.parents) >= 4:
        return here.parents[3]
    return None


def _scripts_dir() -> Optional[Path]:
    """Find the scripts/ dir relative to the package, with fallbacks
    for non-source installs (pip, /opt/, ~/)."""
    root = _repo_root()
    if root is not None:
        candidate = root / "scripts"
        if candidate.is_dir():
            return candidate
    for fallback in (Path("/opt/gpu-dashboard/scripts"),
                     Path.home() / "gpu-dashboard-oss" / "scripts",
                     Path.home() / "gpu-dashboard" / "scripts"):
        if fallback.is_dir():
            return fallback
    return None


def _current_user() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError):
        return os.environ.get("USER", "")


# ── whitelist registry ──────────────────────────────────────────────
# Adding a new install script = adding a row here. Keep ids stable
# (the frontend references them).

SCRIPT_REGISTRY: Dict[str, dict] = {
    "pcie_recovery_wrapper": {
        "filename": "install-pcie-recovery-wrapper.sh",
        "label": "PCIe Recovery wrapper",
        "description": ("Sudoers wrapper for the PCIe recovery "
                         "wizard. Allows execute-mode recovery "
                         "from the OcuLink card."),
        "needs_user_flag": True,  # script accepts --user <name>
    },
    "oculink_watchdog": {
        "filename": "install-oculink-watchdog.sh",
        "label": "OcuLink watchdog daemon",
        "description": ("systemd service that polls the GPU link "
                         "and logs drops/recoveries. Source of "
                         "truth for the OcuLink card's history."),
        "needs_user_flag": False,
    },
    "power_limit_wrapper": {
        "filename": "install-power-limit-wrapper.sh",
        "label": "Power limit wrapper",
        "description": ("Sudoers wrapper for `nvidia-smi -pl`. "
                         "Required by the power_limit module's "
                         "live TDP slider."),
        "needs_user_flag": True,
    },
    "coolbits_xorg": {
        "filename": "install-coolbits-xorg.sh",
        "label": "Xorg CoolBits",
        "description": ("Adds the CoolBits option to xorg.conf so "
                         "nvidia-settings can drive fan curves "
                         "and clock offsets."),
        "needs_user_flag": False,
    },
}


def _script_path(script_id: str) -> Optional[Path]:
    spec = SCRIPT_REGISTRY.get(script_id)
    if spec is None:
        return None
    sdir = _scripts_dir()
    if sdir is None:
        return None
    candidate = sdir / spec["filename"]
    return candidate if candidate.exists() else None


def list_available() -> List[dict]:
    """Inventory of registered scripts with their current install
    status. Used by the UI to decide which install CTAs to show."""
    out: List[dict] = []
    for script_id, spec in SCRIPT_REGISTRY.items():
        path = _script_path(script_id)
        out.append({
            "id": script_id,
            "label": spec["label"],
            "description": spec["description"],
            "script_path": str(path) if path else None,
            "script_exists": path is not None,
            "installed": check_installed(script_id) if path else False,
        })
    return out


def check_installed(script_id: str) -> bool:
    """Run the install script's --check mode. The script's own
    convention is exit 0 = installed, 1 = not installed."""
    path = _script_path(script_id)
    if path is None:
        return False
    try:
        r = subprocess.run(["bash", str(path), "--check"],
                          capture_output=True, timeout=5)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def install_script(script_id: str,
                    password: str,
                    timeout: float = 60.0) -> dict:
    """Run the install script via `sudo -S` with the user's
    password piped on stdin. Same pattern as the F4.4 PCIe
    installer, generalized.

    Returns a structured result; the password is scrubbed before
    this function returns. Error codes the frontend can handle :
      not_in_registry / script_not_found / bad_user_context /
      wrong_password / timeout / os_error / exit_<N>
    """
    spec = SCRIPT_REGISTRY.get(script_id)
    if spec is None:
        return {"ok": False, "error": "not_in_registry",
                "message": (f"script_id '{script_id}' is not in "
                            f"the install whitelist."),
                "script_id": script_id}
    path = _script_path(script_id)
    if path is None:
        return {"ok": False, "error": "script_not_found",
                "message": (f"{spec['filename']} not found in "
                            f"scripts/ — repo install layout "
                            f"may have changed."),
                "script_id": script_id}
    user = _current_user()
    if not user or user == "root":
        return {"ok": False, "error": "bad_user_context",
                "message": (f"dashboard process owner is "
                            f"'{user or '<unknown>'}' — cannot "
                            f"determine non-root install target."),
                "script_id": script_id,
                "user": user, "script": str(path)}
    cmd = ["sudo", "-S", "-p", "", "bash", str(path)]
    if spec.get("needs_user_flag"):
        cmd += ["--user", user]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            input=(password + "\n").encode("utf-8"),
            capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout",
                "message": f"sudo timed out after {timeout}s",
                "script_id": script_id, "user": user,
                "script": str(path),
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0}
    except OSError as e:
        return {"ok": False, "error": "os_error",
                "message": f"failed to invoke sudo: {e}",
                "script_id": script_id, "user": user,
                "script": str(path),
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0}
    finally:
        password = "x" * len(password)  # noqa: F841 — intentional scrub
        del password

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    stderr_lc = stderr.lower()
    wrong_password = (
        proc.returncode != 0 and
        any(m in stderr_lc for m in (
            "incorrect password",
            "sorry, try again",
            "authentication failure",
            "authentication failed",
            "incorrect authentication",
            "3 incorrect",
            "mot de passe incorrect",
            "mot de passe erron",
            "désolé, essayez")))
    if wrong_password:
        return {"ok": False, "error": "wrong_password",
                "message": "sudo rejected the password",
                "script_id": script_id, "user": user,
                "script": str(path),
                "stdout": stdout, "stderr": stderr,
                "elapsed_ms": elapsed_ms}
    if proc.returncode != 0:
        return {"ok": False, "error": f"exit_{proc.returncode}",
                "message": (stderr.strip().splitlines()[-1]
                            if stderr.strip()
                            else f"install failed with rc={proc.returncode}"),
                "script_id": script_id, "user": user,
                "script": str(path),
                "stdout": stdout, "stderr": stderr,
                "elapsed_ms": elapsed_ms}
    return {"ok": True,
            "script_id": script_id, "user": user,
            "script": str(path),
            "stdout": stdout, "stderr": stderr,
            "elapsed_ms": elapsed_ms,
            "installed": check_installed(script_id)}
