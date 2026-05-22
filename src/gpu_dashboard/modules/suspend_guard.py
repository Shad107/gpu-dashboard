"""Module suspend_guard — Hibernate/suspend safety preflight (R&D #20.5).

Laptop-RTX users (Asus ROG, Lenovo Legion, MSI Stealth) routinely lose
data when systemd-logind suspends mid-CUDA-kernel. Same risk on small
homelab boxes that auto-suspend on idle. The driver does not always
restore CUDA contexts cleanly across suspend, and an in-flight
training step or LLM inference can corrupt VRAM or hang the next boot.

This module is a *preflight check*, not an active inhibitor :

  1. Lists in-flight CUDA processes via `nvidia-smi --query-compute-apps`
  2. Detects laptop lid state and suspend-on-idle config
  3. Returns a verdict — "safe to suspend" / "risky" / "blocked" —
     with a copy-paste systemd-inhibit one-liner the user can wrap
     long-running jobs in.

stdlib only.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional


NAME = "suspend_guard"


def list_compute_pids(timeout: float = 2.0) -> list[dict]:
    """`nvidia-smi --query-compute-apps=pid,process_name,used_memory`."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-compute-apps=pid,process_name,used_gpu_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if r.returncode != 0:
        return []
    out: list[dict] = []
    for line in r.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        try:
            mib = int(parts[2])
        except ValueError:
            mib = 0
        out.append({"pid": int(parts[0]), "name": parts[1], "vram_mib": mib})
    return out


def detect_lid_state(sys_root: str = "/proc/acpi/button/lid") -> Optional[str]:
    """Read /proc/acpi/button/lid/<dir>/state — returns 'open' / 'closed' / None."""
    if not os.path.isdir(sys_root):
        return None
    try:
        for name in os.listdir(sys_root):
            p = os.path.join(sys_root, name, "state")
            if os.path.exists(p):
                with open(p) as f:
                    txt = f.read().strip().lower()
                if "open" in txt:
                    return "open"
                if "closed" in txt:
                    return "closed"
    except OSError:
        return None
    return None


def detect_idle_action() -> Optional[str]:
    """Read logind's HandleLidSwitch / IdleAction from /etc/systemd/logind.conf
    (best effort, no root needed)."""
    p = "/etc/systemd/logind.conf"
    out: dict = {}
    try:
        with open(p) as f:
            for line in f:
                s = line.strip()
                if s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        return None
    # Most common = lid switch action
    return out.get("HandleLidSwitch") or out.get("IdleAction") or "default"


def systemd_inhibit_oneliner(reason: str) -> str:
    """Suggest a shell snippet that wraps a long-running job."""
    safe = reason.replace('"', '\\"')
    return (f'systemd-inhibit --what=sleep --who=gpu-dashboard '
            f'--why="{safe}" --mode=block sleep 24h &')


def classify(compute_pids: list[dict], lid: Optional[str],
              idle_action: Optional[str]) -> dict:
    """Return {verdict, reason, recommendation}."""
    if compute_pids:
        active_names = ", ".join(sorted({p["name"] for p in compute_pids})[:3])
        if lid == "closed":
            return {
                "verdict": "blocked",
                "reason": (f"Lid closed AND {len(compute_pids)} CUDA process(es) "
                           f"running ({active_names}). Suspend now will likely "
                           "corrupt your in-flight work."),
                "recommendation": (
                    "Open the lid, OR finish/stop the jobs, OR wrap them in "
                    "systemd-inhibit (see snippet below)."),
            }
        return {
            "verdict": "risky",
            "reason": (f"{len(compute_pids)} CUDA process(es) running "
                       f"({active_names}). Suspend may not restore the CUDA "
                       "context cleanly — depends on driver/kernel."),
            "recommendation": (
                "Wrap long jobs in `systemd-inhibit`. The dashboard cannot "
                "block suspend on its own (no root); the snippet below "
                "registers an inhibitor without sudo."),
        }
    if lid == "closed" and idle_action and idle_action != "ignore":
        return {
            "verdict": "safe",
            "reason": ("Lid closed but no CUDA work in-flight. "
                       "Suspend / hibernate is safe."),
            "recommendation": "",
        }
    return {
        "verdict": "safe",
        "reason": "No CUDA work in-flight. Suspend is safe.",
        "recommendation": "",
    }


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    pids = list_compute_pids()
    lid = detect_lid_state()
    idle = detect_idle_action()
    verdict = classify(pids, lid, idle)
    return {
        "ok": True,
        "compute_processes": pids,
        "compute_count": len(pids),
        "lid_state": lid,
        "logind_action": idle,
        "verdict": verdict,
        "inhibit_snippet": systemd_inhibit_oneliner("GPU work in progress"),
    }
