"""Module pcie_recovery_runner — executor side of F4 (V1).

The advisor (pcie_recovery_advisor) tells the operator what to try.
This module actually EXECUTES the recovery commands when the
sudoers wrapper (`/usr/local/bin/gpu-dashboard-pcie-recover`) is
installed.

Two entry points:

  is_wrapper_available() → bool
      True if `sudo -n /usr/local/bin/gpu-dashboard-pcie-recover` is
      possible without a password prompt. The dashboard uses this to
      decide between "open execute modal" and "show install CTA".

  run_step(step_id, bdf=None, timeout=20) → dict
      {ok: bool, step_id, stdout, stderr, elapsed_ms,
       link_recovered: bool|None}
      Calls the wrapper with the given step id (and BDF when needed)
      via passwordless sudo. After running, re-checks the link state
      via pcie_recovery_advisor — link_recovered is True if the
      advisor's verdict flipped to 'ok' since the call.

stdlib only.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from typing import Optional


WRAPPER_PATH = "/usr/local/bin/gpu-dashboard-pcie-recover"

# Whitelist mirrored from the wrapper's case statement.
ALLOWED_STEPS = frozenset({
    "persistence_restart",
    "module_reload",
    "pcie_rescan",
    "flr",
})

# Steps that need a BDF argument.
STEPS_REQUIRING_BDF = frozenset({"pcie_rescan", "flr"})


def is_wrapper_available() -> bool:
    """True if the sudoers wrapper is installed AND passwordless sudo
    is granted for it. Mirrors the install script's --check mode."""
    if not shutil.which("sudo"):
        return False
    try:
        r = subprocess.run(
            ["sudo", "-n", "-l", WRAPPER_PATH],
            capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _check_link_recovered() -> Optional[bool]:
    """Best-effort: ask the advisor whether the link is now ok.
    Returns True / False / None (unknown if the advisor itself
    can't load — should never happen but be defensive)."""
    try:
        from . import pcie_recovery_advisor
        s = pcie_recovery_advisor.status()
        v = (s.get("verdict") or {}).get("verdict")
        # 'ok' means healthy; 'recovery_recommended' means still
        # broken; anything else (no_nvidia_gpu) → unknown.
        if v == "ok":
            return True
        if v == "recovery_recommended":
            return False
        return None
    except Exception:  # noqa: BLE001 — best-effort
        return None


def run_step(step_id: str,
              bdf: Optional[str] = None,
              timeout: float = 20.0) -> dict:
    """Execute one recovery step via the sudoers wrapper. Returns
    a structured result the dashboard frontend can render."""
    if step_id not in ALLOWED_STEPS:
        return {"ok": False, "step_id": step_id,
                "stdout": "", "stderr": (
                    f"step '{step_id}' not in allowed set "
                    f"({sorted(ALLOWED_STEPS)})"),
                "elapsed_ms": 0,
                "link_recovered": None,
                "error": "invalid_step"}
    if step_id in STEPS_REQUIRING_BDF and not bdf:
        return {"ok": False, "step_id": step_id,
                "stdout": "", "stderr": (
                    f"step '{step_id}' requires a BDF argument"),
                "elapsed_ms": 0,
                "link_recovered": None,
                "error": "missing_bdf"}
    if not is_wrapper_available():
        return {"ok": False, "step_id": step_id,
                "stdout": "", "stderr": (
                    "wrapper not installed or sudoers rule "
                    "missing. Run:\n"
                    "  sudo bash scripts/install-pcie-recovery-"
                    "wrapper.sh"),
                "elapsed_ms": 0,
                "link_recovered": None,
                "error": "wrapper_missing"}
    cmd = ["sudo", "-n", WRAPPER_PATH, step_id]
    if bdf:
        cmd.append(bdf)
    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        out = {"ok": r.returncode == 0,
               "step_id": step_id,
               "stdout": r.stdout or "",
               "stderr": r.stderr or "",
               "elapsed_ms": elapsed_ms,
               "returncode": r.returncode,
               "link_recovered": None}
        # Give the kernel a beat to settle, then check.
        time.sleep(1.0)
        out["link_recovered"] = _check_link_recovered()
        return out
    except subprocess.TimeoutExpired:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {"ok": False, "step_id": step_id,
                "stdout": "", "stderr": (
                    f"timeout after {timeout}s"),
                "elapsed_ms": elapsed_ms,
                "link_recovered": None,
                "error": "timeout"}
    except OSError as e:
        return {"ok": False, "step_id": step_id,
                "stdout": "", "stderr": str(e),
                "elapsed_ms": 0,
                "link_recovered": None,
                "error": "os_error"}
