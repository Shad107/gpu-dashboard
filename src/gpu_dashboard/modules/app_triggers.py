"""Per-application profile triggers.

Watches running processes by name and returns a profile name to force when a
configured trigger app is detected. Designed as helpers — wired into the
auto_profile daemon in cycle 117 so app triggers override load-based heuristics.

Examples of triggers users typically configure :
  blender / cycles → boost
  llama-server / ollama → boost
  cyberpunk2077 / wukong-bin → boost
  steam-runtime / lutris → sweet (light gaming)

The match logic is intentionally simple : substring match (case-insensitive)
against /proc/*/comm names. This catches both `blender` and `Blender (running)`.
"""
from __future__ import annotations

import json
import os
from typing import Optional


NAME = "app_triggers"


def scan_running_apps() -> set[str]:
    """Return the set of process command names currently running on the system.

    Reads /proc/<pid>/comm which contains the executable name (max 15 chars).
    Silently skips PIDs that disappear during the scan (race-condition safe).
    """
    apps: set[str] = set()
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            comm_path = f"/proc/{pid_dir}/comm"
            try:
                with open(comm_path, "r") as f:
                    name = f.read().strip()
                if name:
                    apps.add(name)
            except (OSError, IOError):
                continue  # pid disappeared, permission denied, etc.
    except OSError:
        pass
    return apps


def load_triggers(path: Optional[str] = None) -> dict[str, str]:
    """Load {app_substring: profile_name} mapping from disk.

    Default path : ~/.config/gpu-dashboard/app_triggers.json

    Schema :
      {
        "blender": "boost",
        "llama-server": "boost",
        "cyberpunk": "boost"
      }

    Returns {} if file missing or unparseable. Never raises.
    """
    if path is None:
        path = os.path.expanduser("~/.config/gpu-dashboard/app_triggers.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # Keep only string→string entries
        return {
            str(k).lower(): str(v)
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str)
        }
    except (json.JSONDecodeError, OSError):
        return {}


def save_triggers(triggers: dict[str, str], path: Optional[str] = None) -> None:
    """Persist {app_substring: profile_name} mapping. Creates parent dir as needed.

    Used by the future API handler so the UI can write user-configured triggers.
    """
    if path is None:
        path = os.path.expanduser("~/.config/gpu-dashboard/app_triggers.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(triggers, f, indent=2, sort_keys=True)


def match_trigger(running_apps: set[str], triggers: dict[str, str]) -> Optional[str]:
    """Return the profile name to force, or None if no trigger matches.

    Match is case-insensitive substring : a trigger key `blender` matches any
    running app name containing `blender` (covers `Blender`, `blender-cycles`,
    `blender.bin`, etc.).

    When multiple triggers match, the one with the higher-priority profile
    wins. Priority order (descending) : boost > sweet > silent.
    """
    if not triggers or not running_apps:
        return None
    # Build a list of (key, profile) for each matching trigger
    matches: list[tuple[str, str]] = []
    lowered_apps = {a.lower() for a in running_apps}
    for key, profile in triggers.items():
        key_lc = key.lower()
        for app in lowered_apps:
            if key_lc in app:
                matches.append((key, profile))
                break
    if not matches:
        return None
    # Pick the highest-priority profile
    priority = {"boost": 3, "sweet": 2, "silent": 1}
    matches.sort(key=lambda m: priority.get(m[1], 0), reverse=True)
    return matches[0][1]
