"""CLI one-shot status output : `python3 -m gpu_dashboard --status`.

Prints a colored, single-screen summary of the GPU + dashboard state, then
exits. Useful for SSH sessions and cron-based monitoring scripts.

Re-uses the same handlers as the HTTP API so the output is always consistent
with what the web UI shows.
"""
from __future__ import annotations

import os
import sys

from . import __version__


_C_RESET   = "\033[0m"
_C_BOLD    = "\033[1m"
_C_DIM     = "\033[2m"
_C_GREEN   = "\033[32m"
_C_YELLOW  = "\033[33m"
_C_RED     = "\033[31m"
_C_CYAN    = "\033[36m"
_C_MAGENTA = "\033[35m"


def _color_temp(t):
    if t is None: return _C_DIM
    if t < 50:  return _C_CYAN
    if t < 70:  return _C_GREEN
    if t < 80:  return _C_YELLOW
    return _C_RED


def _color_for_health(ok: bool) -> str:
    return _C_GREEN if ok else _C_RED


def _print_box(lines, width=64):
    """Print a unicode-bordered box."""
    top    = "┌" + "─" * (width - 2) + "┐"
    bottom = "└" + "─" * (width - 2) + "┘"
    print(top)
    for line in lines:
        # Strip ANSI for width calculation
        import re
        plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
        pad = width - 2 - len(plain) - 2  # leading + trailing space
        print("│ " + line + (" " * max(0, pad)) + " │")
    print(bottom)


def render_status_lines(state: dict, electricity: dict = None, llm: dict = None,
                         health: dict = None) -> list:
    """Pure function : build the list of formatted lines from a /api/state dict.

    Kept pure for testability — formatting only, no I/O.
    """
    lines = []
    gpu = state.get("gpu") or {}
    if gpu.get("alive"):
        # GPU title
        lines.append(f"{_C_BOLD}{gpu.get('name', '?')}{_C_RESET}")

        # Live metrics line
        temp = gpu.get("temp")
        mem_temp = gpu.get("mem_temp")
        c_temp = _color_temp(temp)
        c_mem = _color_temp(mem_temp + 15 if mem_temp else None)  # offset for junction
        power = gpu.get("power", 0)
        pl = gpu.get("power_limit", 0)
        util = gpu.get("util_gpu", 0)

        line = (
            f"Temp: {c_temp}{temp}°C{_C_RESET}"
        )
        if mem_temp is not None:
            line += f"  Mem: {c_mem}{mem_temp}°C{_C_RESET}"
        line += f"  Power: {power:.0f}/{pl:.0f}W"
        line += f"  Util: {util}%"
        lines.append(line)

        # Memory + Fans
        mem_used = gpu.get("mem_used_mib", 0) / 1024
        mem_total = gpu.get("mem_total_mib", 0) / 1024
        # Pick the most recent fan RPM from sampler if available
        metrics = state.get("metrics") or []
        last = metrics[-1] if metrics else {}
        f0 = last.get("fan0_rpm")
        f1 = last.get("fan1_rpm")
        fan_str = ""
        if f0 is not None:
            fan_str = f"  Fans: {f0} / {f1} RPM" if f1 is not None else f"  Fan: {f0} RPM"
        lines.append(f"VRAM: {mem_used:.1f}/{mem_total:.1f} GiB{fan_str}")
    else:
        lines.append(f"{_C_RED}{_C_BOLD}GPU not alive — nvidia-smi unreachable{_C_RESET}")

    # Profile + electricity
    if electricity and electricity.get("ok") is not False:
        e_avg = electricity.get("avg_power_watts", 0)
        e_day = electricity.get("daily_cost", 0)
        e_mo = electricity.get("monthly_cost", 0)
        cur = electricity.get("currency", "EUR")
        sym = "€" if cur == "EUR" else "$" if cur == "USD" else cur
        lines.append("")
        lines.append(
            f"{_C_MAGENTA}⚡{_C_RESET} avg {e_avg:.0f}W → "
            f"{e_day:.2f}{sym}/day · {_C_GREEN}{e_mo:.2f}{sym}/month{_C_RESET}"
        )

    # LLM throughput
    if llm and llm.get("available"):
        tokens = llm.get("tokens_generated_total", 0)
        tpw = llm.get("tokens_per_watt")
        line = f"{_C_YELLOW}🪙{_C_RESET} {tokens:,} tokens generated"
        if tpw:
            line += f"  ({_C_GREEN}{tpw:.2f} tok/W{_C_RESET})"
        lines.append(line)

    # Watchdog / OcuLink
    w = state.get("watchdog") or {}
    if w.get("available"):
        drops = w.get("drops", 0)
        uptime = w.get("last_uptime", "?")
        c_drops = _C_GREEN if drops == 0 else _C_YELLOW
        lines.append(f"OcuLink up {uptime}  · {c_drops}{drops} drops{_C_RESET}")

    # Health summary
    if health:
        ok = health.get("status") == "ok"
        c = _color_for_health(ok)
        comps = health.get("components", {})
        parts = []
        for k, v in comps.items():
            parts.append(f"{k}={'✓' if v else '✗'}")
        lines.append("")
        lines.append(f"Health: {c}{health.get('status', '?')}{_C_RESET}  ({'  '.join(parts)})")

    return lines


def run_status(profiles_dir: str = "profiles") -> int:
    """Build context locally + invoke handlers + print box. Returns exit code."""
    # Lazy imports to avoid loading the world if not needed
    from .config import Config
    from .server import DEFAULTS, _default_config_path
    from . import api as _api

    # Build a minimal ctx without starting the sampler / DB
    config_files = []
    home = os.path.expanduser("~")
    cfg_path = os.path.join(home, ".config/gpu-dashboard/config.env")
    if os.path.isfile(cfg_path):
        config_files.append(cfg_path)
    sec_path = os.path.join(home, ".config/gpu-dashboard/secrets.env")
    if os.path.isfile(sec_path):
        config_files.append(sec_path)
    cfg = Config(defaults=DEFAULTS, files=config_files)

    # We need to call /api/state — but it needs a sampler. Minimal: feed an empty one.
    class _EmptySampler:
        def snapshot(self): return []

    import time as _t
    ctx = {
        "config": cfg, "sampler": _EmptySampler(),
        "started_at": _t.time(), "config_path": cfg_path,
    }

    _, state = _api.handle_state(ctx)
    _, electricity = _api.handle_electricity(ctx, {})
    _, llm = _api.handle_llm_stats(ctx)
    _, health = _api.handle_health(ctx)

    header = f"{_C_BOLD}gpu-dashboard {__version__}{_C_RESET}"
    lines = [header, ""] + render_status_lines(state, electricity, llm, health)

    _print_box(lines, width=70)
    return 0 if state.get("gpu", {}).get("alive") else 2
