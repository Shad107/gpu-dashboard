"""Module pcie_rpm_audit — PCIe runtime-PM auditor (R&D #28.1).

Linux's PCIe runtime power management can put an idle GPU into
D3cold. Coming back out takes 50-500 ms — fine for a desktop in
sleep, catastrophic for LLM inference TTFT. On hybrid laptops
(Optimus, NVIDIA Turbo) and OcuLink eGPU rigs, runtime-PM defaults
to "auto" and the GPU silently goes to sleep between requests.

Reads two sysfs files per NVIDIA GPU :

  /sys/bus/pci/devices/<bdf>/power/control       — auto | on
  /sys/bus/pci/devices/<bdf>/power/runtime_status — active | suspended

Verdicts :
  - active            (control=on OR control=auto + status=active)
  - suspended_now     (control=auto + status=suspended — wake stalls)
  - error             (status=error — driver bug)
  - unknown           (sysfs unreadable)

Emits the systemd Drop-In recipe that overrides runtime-PM to "on"
for the GPU's PCI device — survives reboots without UEFI changes.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "pcie_rpm_audit"


_PCI_ROOT = "/sys/bus/pci/devices"


def list_nvidia_bdfs(sys_root: str = _PCI_ROOT) -> list[str]:
    out: list[str] = []
    try:
        for name in sorted(os.listdir(sys_root)):
            vp = os.path.join(sys_root, name, "vendor")
            try:
                with open(vp) as f:
                    if f.read().strip().lower() == "0x10de":
                        out.append(name)
            except OSError:
                continue
    except OSError:
        return []
    return out


def read_text(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_rpm_state(bdf: str, sys_root: str = _PCI_ROOT) -> dict:
    base = os.path.join(sys_root, bdf, "power")
    return {
        "bdf": bdf,
        "control": read_text(os.path.join(base, "control")),
        "runtime_status": read_text(os.path.join(base, "runtime_status")),
        "runtime_suspended_time": read_text(
            os.path.join(base, "runtime_suspended_time")),
        "runtime_active_time": read_text(
            os.path.join(base, "runtime_active_time")),
    }


def systemd_dropin_recipe(bdf: str) -> str:
    """Recipe to pin runtime-PM to 'on' across reboots."""
    return (f"# Save as /etc/udev/rules.d/90-nvidia-pm.rules\n"
            f"SUBSYSTEM==\"pci\", ATTR{{vendor}}==\"0x10de\", "
            f"ATTRS{{kernel}}==\"{bdf}\", "
            f"ATTR{{power/control}}=\"on\"\n"
            f"# Apply : sudo udevadm control --reload-rules && "
            f"sudo udevadm trigger")


def classify(state: dict) -> dict:
    """Per-GPU verdict."""
    control = state.get("control")
    status = state.get("runtime_status")
    if control is None and status is None:
        return {"verdict": "unknown",
                "reason": "Runtime-PM sysfs unreadable.",
                "recommendation": ""}
    if status == "error":
        return {"verdict": "error",
                "reason": "Runtime-PM reports error state — driver bug.",
                "recommendation": "Inspect dmesg for the original failure."}
    if control == "on":
        return {"verdict": "active",
                "reason": ("Runtime-PM disabled (control=on) — GPU stays "
                           "powered. Best for inference rigs."),
                "recommendation": ""}
    if control == "auto" and status == "active":
        return {"verdict": "active",
                "reason": ("Runtime-PM auto but currently active. Wake "
                           "stalls only happen IF the GPU goes idle long "
                           "enough."),
                "recommendation": ("Consider pinning control=on for "
                                    "deterministic TTFT.")}
    if control == "auto" and status == "suspended":
        return {"verdict": "suspended_now",
                "reason": ("GPU is in runtime-suspend right now. Next "
                           "CUDA call will pay a 50-500 ms wake stall."),
                "recommendation": ("Pin runtime-PM with the udev rule "
                                    "below or `echo on > .../power/control`.")}
    return {"verdict": "unknown",
            "reason": f"Unexpected state : control={control}, status={status}.",
            "recommendation": ""}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    bdfs = list_nvidia_bdfs()
    if not bdfs:
        return {"ok": True,
                "device_count": 0,
                "cards": [],
                "worst_verdict": "no_gpus"}
    cards: list = []
    rank = {"active": 0, "unknown": 1, "suspended_now": 2, "error": 3}
    worst = "active"
    for bdf in bdfs:
        state = read_rpm_state(bdf)
        verdict = classify(state)
        r = rank.get(verdict["verdict"], 0)
        if r > rank.get(worst, 0):
            worst = verdict["verdict"]
        cards.append({
            **state,
            "verdict": verdict,
            "udev_recipe": (systemd_dropin_recipe(bdf)
                              if verdict["verdict"] in ("suspended_now", "active")
                              else ""),
        })
    return {"ok": True,
            "device_count": len(cards),
            "cards": cards,
            "worst_verdict": worst}
