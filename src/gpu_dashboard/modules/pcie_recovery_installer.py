"""Module pcie_recovery_installer — F4.4 one-click wrapper install.

The recovery wrapper at /usr/local/bin/gpu-dashboard-pcie-recover
needs root to install (write to /usr/local/bin/ + /etc/sudoers.d/).
The dashboard runs as a regular user. Rather than force the user
to copy-paste the install command into a terminal, we pipe their
sudo password into `sudo -S` via subprocess stdin — same trade-off
that Webmin / Cockpit accept for admin web UIs.

Security model:
- Password lives in memory ONLY during the subprocess.run() call,
  then is overwritten and discarded. No logging.
- Sudo's authentication runs as the current user, so the user
  needs sudo rights AS THEMSELVES (typical homelab setup).
- The install script itself is in-repo and signed by git history —
  it doesn't accept arbitrary arguments, so even a bug in this
  module can't escalate to running arbitrary root commands.
- Recommended deployment: bind the dashboard to localhost or use
  an auth token. HTTPS is best if the dashboard is on LAN.

stdlib only.
"""
from __future__ import annotations

import os
import pwd
import subprocess
import time
from pathlib import Path
from typing import Optional


def _script_path() -> Optional[Path]:
    """Find install-pcie-recovery-wrapper.sh relative to the dashboard
    package, with sensible fallbacks for non-source installs."""
    here = Path(__file__).resolve()
    # here = <repo>/src/gpu_dashboard/modules/pcie_recovery_installer.py
    if len(here.parents) >= 4:
        candidate = here.parents[3] / "scripts" / "install-pcie-recovery-wrapper.sh"
        if candidate.exists():
            return candidate
    for fallback in (
            Path("/opt/gpu-dashboard/scripts/install-pcie-recovery-wrapper.sh"),
            Path.home() / "gpu-dashboard-oss/scripts/install-pcie-recovery-wrapper.sh",
            Path.home() / "gpu-dashboard/scripts/install-pcie-recovery-wrapper.sh"):
        if fallback.exists():
            return fallback
    return None


def _current_user() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except (KeyError, OSError):
        return os.environ.get("USER", "")


def install_wrapper(password: str, timeout: float = 30.0) -> dict:
    """Run the install script via `sudo -S` with the user-supplied
    password piped on stdin. Returns a structured result; the
    password is scrubbed before this function returns."""
    script = _script_path()
    if script is None:
        return {"ok": False,
                "error": "script_not_found",
                "message": ("install-pcie-recovery-wrapper.sh not "
                            "found relative to the dashboard package "
                            "or in any fallback location."),
                "user": "",
                "script": ""}
    user = _current_user()
    if not user or user == "root":
        return {"ok": False,
                "error": "bad_user_context",
                "message": (f"dashboard process owner is "
                            f"'{user or '<unknown>'}' — cannot "
                            f"determine non-root install target."),
                "user": user, "script": str(script)}
    t0 = time.perf_counter()
    cmd = ["sudo", "-S", "-p", "", "bash", str(script),
           "--user", user]
    try:
        proc = subprocess.run(
            cmd,
            input=(password + "\n").encode("utf-8"),
            capture_output=True,
            timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout",
                "message": (f"sudo timed out after {timeout}s "
                            f"— password prompt may have stalled."),
                "user": user, "script": str(script),
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0}
    except OSError as e:
        return {"ok": False, "error": "os_error",
                "message": f"failed to invoke sudo: {e}",
                "user": user, "script": str(script),
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0}
    finally:
        # Best-effort scrub of the password from this scope.
        password = "x" * len(password)  # noqa: F841 — overwrite intentional
        del password

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    # Detect "wrong password" across classic sudo + sudo-rs +
    # localised messages. Anything mentioning an auth/password
    # error after a non-zero exit is treated as wrong-password.
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
                "user": user, "script": str(script),
                "stdout": stdout, "stderr": stderr,
                "elapsed_ms": elapsed_ms}
    if proc.returncode != 0:
        return {"ok": False,
                "error": f"exit_{proc.returncode}",
                "message": (stderr.strip().splitlines()[-1]
                            if stderr.strip()
                            else f"install failed with rc={proc.returncode}"),
                "user": user, "script": str(script),
                "stdout": stdout, "stderr": stderr,
                "elapsed_ms": elapsed_ms}
    return {"ok": True,
            "user": user, "script": str(script),
            "stdout": stdout, "stderr": stderr,
            "elapsed_ms": elapsed_ms}
