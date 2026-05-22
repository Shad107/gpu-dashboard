"""Module watchdog_setup — install/uninstall systemd user-level watchdog
units that poll /readyz and auto-restart the service on failure (R&D #11.1b).

The watchdog runs via two systemd user units :
  ~/.config/systemd/user/gpu-dashboard-watchdog.service  (the check)
  ~/.config/systemd/user/gpu-dashboard-watchdog.timer    (the schedule)

stdlib only — wraps `systemctl --user` subprocess calls.
"""
from __future__ import annotations

import os
import subprocess
from typing import Tuple


NAME = "watchdog_setup"


SERVICE_NAME = "gpu-dashboard-watchdog.service"
TIMER_NAME = "gpu-dashboard-watchdog.timer"


def _systemd_user_dir() -> str:
    return os.path.expanduser("~/.config/systemd/user")


def service_path() -> str:
    return os.path.join(_systemd_user_dir(), SERVICE_NAME)


def timer_path() -> str:
    return os.path.join(_systemd_user_dir(), TIMER_NAME)


def _service_content(port: int, strict: bool) -> str:
    suffix = "?strict=1" if strict else ""
    return (
        "[Unit]\n"
        "Description=Restart gpu-dashboard if /readyz fails\n"
        "After=gpu-dashboard.service\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart=/bin/sh -c 'curl -fs --max-time 5 http://localhost:{port}/readyz{suffix} >/dev/null || systemctl --user restart gpu-dashboard.service'\n"
    )


def _timer_content(interval_s: int) -> str:
    return (
        "[Unit]\n"
        "Description=Run readyz check every minute\n"
        "\n"
        "[Timer]\n"
        f"OnBootSec=2min\n"
        f"OnUnitActiveSec={interval_s}s\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _systemctl(*args) -> Tuple[bool, str]:
    try:
        r = subprocess.run(
            ["systemctl", "--user", *args],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as e:
        return False, f"systemctl unavailable: {e}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()
    return True, (r.stdout or "ok").strip()


def is_installed() -> bool:
    return os.path.isfile(service_path()) and os.path.isfile(timer_path())


def is_active() -> bool:
    if not is_installed():
        return False
    ok, _ = _systemctl("is-active", "--quiet", TIMER_NAME)
    return ok


def install(port: int = 9999, strict: bool = False, interval_s: int = 60) -> Tuple[bool, str]:
    """Write the unit files + enable + start the timer."""
    os.makedirs(_systemd_user_dir(), exist_ok=True)
    try:
        with open(service_path(), "w") as f:
            f.write(_service_content(port=port, strict=strict))
        with open(timer_path(), "w") as f:
            f.write(_timer_content(interval_s=interval_s))
    except OSError as e:
        return False, f"write failed: {e}"
    ok, msg = _systemctl("daemon-reload")
    if not ok:
        return False, f"daemon-reload: {msg}"
    ok, msg = _systemctl("enable", "--now", TIMER_NAME)
    return ok, msg


def uninstall() -> Tuple[bool, str]:
    """Disable + remove the unit files."""
    if is_installed():
        _systemctl("disable", "--now", TIMER_NAME)
    for p in (service_path(), timer_path()):
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    _systemctl("daemon-reload")
    return True, "uninstalled"


def status() -> dict:
    """Return current watchdog state."""
    return {
        "installed": is_installed(),
        "active": is_active(),
        "service_path": service_path(),
        "timer_path": timer_path(),
    }
