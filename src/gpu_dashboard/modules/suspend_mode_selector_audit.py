"""Module suspend_mode_selector_audit — /sys/power selector
files (R&D #88.2).

Two existing modules cover the suspend *runtime* surface :

  * suspend_stats_audit — /sys/power/suspend_stats counters
    (success/failure history).
  * suspend_guard — process-list / lid / idle pre-flight gate.

Neither inspects the *selectors* — which suspend mode the
kernel will actually use the next time the user pushes the
power button. This audit owns that gap.

Reads :

  /sys/power/state       supported transitions (space list)
                         e.g. "freeze mem disk"
  /sys/power/mem_sleep   active S3-class mode, format
                         "[s2idle] deep" — bracketed token
                         is selected ; absence of "deep"
                         means S3 isn't exposed by firmware.
  /sys/power/disk        hibernate method, format
                         "[shutdown] reboot suspend ..."
                         — "[disabled]" if hibernation isn't
                         available.
  /sys/power/pm_test     "[none]" by default ; any other
                         bracketed token (devices/core/...)
                         means the next suspend will FAKE-
                         resume after that stage and silently
                         no-op.
  /proc/swaps            (cross-ref) — for hibernate_disabled
                         vs swap-present heuristic.

Verdicts (worst-first) :

  pm_test_armed                    warn   pm_test != "none"
  s2idle_only_no_deep              warn   mem_sleep has no
                                          "deep" token at all
  mem_sleep_s2idle_with_deep       accent [s2idle] selected
                                          but "deep" is
                                          available — switch
                                          to deep for lower
                                          idle power.
  hibernate_disabled_with_swap     accent disk == "[disabled]"
                                          but /proc/swaps has
                                          a swap area — wasted
                                          swap space.
  mem_sleep_deep_selected          ok     [deep] active.
  no_suspend_support               ok     /sys/power/state has
                                          no "mem" — non-x86
                                          / VM with no S3 hw.
  unknown                          /sys/power absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "suspend_mode_selector_audit"

DEFAULT_POWER_ROOT = "/sys/power"
DEFAULT_PROC_SWAPS = "/proc/swaps"

_BRACKETED = re.compile(r"\[([^\]]+)\]")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _selected(text: str) -> str:
    m = _BRACKETED.search(text or "")
    return m.group(1) if m else ""


def _tokens(text: str) -> list:
    """Return all whitespace-separated tokens with brackets
    stripped — both selected and non-selected."""
    if not text:
        return []
    cleaned = text.replace("[", "").replace("]", "")
    return cleaned.split()


def read_power_state(root: str = DEFAULT_POWER_ROOT) -> dict:
    return {
        "state": _read_text(os.path.join(root, "state")) or "",
        "mem_sleep": _read_text(
            os.path.join(root, "mem_sleep")) or "",
        "disk": _read_text(os.path.join(root, "disk")) or "",
        "pm_test": _read_text(
            os.path.join(root, "pm_test")) or "",
    }


def has_swap(proc_swaps: str = DEFAULT_PROC_SWAPS) -> bool:
    text = _read_text(proc_swaps)
    if not text:
        return False
    # Header row + at least one entry = swap present.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return len(lines) >= 2


def classify(state: dict, swap_present: bool) -> dict:
    if not state.get("state"):
        return {"verdict": "unknown",
                "reason": (
                    "/sys/power/state absent — kernel built "
                    "without CONFIG_SUSPEND or non-x86 board.")}

    if "mem" not in state["state"].split():
        return {"verdict": "no_suspend_support",
                "reason": (
                    "/sys/power/state has no 'mem' token — "
                    "no S3-class suspend on this hardware "
                    "(VM / minimal kernel).")}

    pm_test_sel = _selected(state["pm_test"])
    if pm_test_sel and pm_test_sel != "none":
        return {
            "verdict": "pm_test_armed",
            "reason": (
                f"/sys/power/pm_test = [{pm_test_sel}] — next "
                "suspend will fake-resume after that stage "
                "and silently no-op. Reset with: echo none > "
                "/sys/power/pm_test"),
            "pm_test": pm_test_sel,
        }

    mem_tokens = _tokens(state["mem_sleep"])
    mem_sel = _selected(state["mem_sleep"])

    if mem_tokens and "deep" not in mem_tokens:
        return {
            "verdict": "s2idle_only_no_deep",
            "reason": (
                "/sys/power/mem_sleep exposes only "
                f"{mem_tokens} — firmware doesn't surface "
                "deep S3. On a laptop this means several "
                "watts of idle drain in 'sleep'."),
            "mem_sleep_options": mem_tokens,
        }

    if mem_sel == "s2idle" and "deep" in mem_tokens:
        return {
            "verdict": "mem_sleep_s2idle_with_deep",
            "reason": (
                "/sys/power/mem_sleep = [s2idle] but 'deep' "
                "is available — switch for lower idle power: "
                "echo deep > /sys/power/mem_sleep"),
            "mem_sleep_options": mem_tokens,
        }

    disk_sel = _selected(state["disk"])
    if disk_sel == "disabled" and swap_present:
        return {
            "verdict": "hibernate_disabled_with_swap",
            "reason": (
                "/sys/power/disk = [disabled] but swap is "
                "configured — swap space allocated for "
                "hibernation is unused."),
        }

    if mem_sel == "deep":
        return {"verdict": "mem_sleep_deep_selected",
                "reason": (
                    "/sys/power/mem_sleep = [deep] ; "
                    "suspend will reach S3, lowest "
                    "idle power available.")}

    return {"verdict": "mem_sleep_deep_selected",
            "reason": (
                f"Suspend selectors coherent (mem_sleep="
                f"{state['mem_sleep']}, disk={state['disk']}, "
                f"pm_test={state['pm_test']}).")}


def status(config: Optional[dict] = None,
           power_root: str = DEFAULT_POWER_ROOT,
           proc_swaps: str = DEFAULT_PROC_SWAPS) -> dict:
    state = read_power_state(power_root)
    swap_present = has_swap(proc_swaps)
    verdict = classify(state, swap_present)
    return {
        "ok": verdict["verdict"] in (
            "mem_sleep_deep_selected",
            "no_suspend_support"),
        "state": state.get("state"),
        "mem_sleep": state.get("mem_sleep"),
        "disk": state.get("disk"),
        "pm_test": state.get("pm_test"),
        "swap_present": swap_present,
        "verdict": verdict,
    }
