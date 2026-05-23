"""Module d3cold_policy — Parent-bridge D3cold-policy auditor (R&D #29.3).

R&D #28.1 audits the GPU's own runtime-PM (`power/control`). But a
subtle worst-case happens at the *parent PCIe bridge* : even with
the GPU set to control=on, if the bridge's `d3cold_allowed=1` and
the GPU goes into D3hot, the bridge may pull the whole port into
D3cold, costing 50-500 ms on wake. Conversely, if `d3cold_allowed=0`
on the bridge but the GPU is in `control=auto`, you get the worst
of both worlds : the GPU tries to D3cold but the bridge blocks it,
spinning idle without sleeping.

This module walks the PCI parent of each NVIDIA GPU, reads the
bridge's d3cold attributes, and produces a coherent verdict :

  - aligned_on        bridge d3cold_allowed=0 AND GPU control=on
  - aligned_off       bridge d3cold_allowed=1 AND GPU control=auto
                      (the suspend-friendly default)
  - mismatched_strict bridge=0 + GPU=auto → GPU spins idle, can't
                      sleep (silent perf-no-power-savings tax)
  - mismatched_eager  bridge=1 + GPU=on → bridge can drag GPU to
                      D3cold despite GPU's wish to stay on
  - unknown           cannot read bridge attrs

Pure sysfs. No sudo.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "d3cold_policy"


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


_BDF_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d$")


def find_parent_bridge(bdf: str,
                        sys_root: str = _PCI_ROOT) -> Optional[str]:
    """Read the device symlink to extract the parent bridge BDF.
    /sys/bus/pci/devices/<bdf> → ../../../devices/pci0000:00/<parent>/<bdf>
    """
    try:
        link = os.readlink(os.path.join(sys_root, bdf))
    except OSError:
        return None
    # Split the path ; the segment immediately before <bdf> at the end is
    # the parent.
    parts = link.split("/")
    if not parts or parts[-1] != bdf:
        return None
    for p in reversed(parts[:-1]):
        if _BDF_RE.match(p):
            return p
    return None


def read_text(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_bridge_d3(bridge_bdf: str,
                    sys_root: str = _PCI_ROOT) -> dict:
    base = os.path.join(sys_root, bridge_bdf)
    return {
        "bdf": bridge_bdf,
        "d3cold_allowed": read_text(os.path.join(base, "d3cold_allowed")),
        # d3cold_delay_ms not on all kernels; absent = unknown
        "d3cold_delay_ms": read_text(os.path.join(base, "d3cold_delay_ms")),
        "power_control": read_text(os.path.join(base, "power", "control")),
    }


def read_gpu_control(bdf: str, sys_root: str = _PCI_ROOT) -> Optional[str]:
    return read_text(os.path.join(sys_root, bdf, "power", "control"))


def classify(gpu_control: Optional[str],
              bridge_d3cold_allowed: Optional[str]) -> dict:
    """Cross-reference verdict."""
    if gpu_control is None or bridge_d3cold_allowed is None:
        return {"verdict": "unknown",
                "reason": "Could not read bridge or GPU sysfs attrs.",
                "recommendation": ""}
    bridge_d3 = bridge_d3cold_allowed.strip() == "1"
    gpu_on = gpu_control.strip() == "on"
    if not bridge_d3 and gpu_on:
        return {"verdict": "aligned_on",
                "reason": ("Bridge d3cold_allowed=0 and GPU control=on. "
                           "Neither will sleep — best for deterministic TTFT."),
                "recommendation": ""}
    if bridge_d3 and not gpu_on:
        return {"verdict": "aligned_off",
                "reason": ("Bridge d3cold_allowed=1 and GPU control=auto. "
                           "Both will sleep — best for laptops."),
                "recommendation": ""}
    if not bridge_d3 and not gpu_on:
        return {"verdict": "mismatched_strict",
                "reason": ("GPU control=auto but bridge d3cold_allowed=0. "
                           "GPU tries to D3cold ; bridge blocks it. Result : "
                           "GPU spins idle, can't actually sleep. Silent "
                           "perf-no-power-savings tax."),
                "recommendation": ("Either set GPU control=on (deterministic "
                                    "TTFT) OR set bridge d3cold_allowed=1 "
                                    "(real sleep — laptops only).")}
    # bridge=1, gpu_on=True
    return {"verdict": "mismatched_eager",
            "reason": ("GPU control=on but bridge d3cold_allowed=1. "
                       "Bridge can drag the whole port to D3cold "
                       "despite the GPU's wish to stay on. Wake stalls "
                       "still possible."),
            "recommendation": ("Set bridge d3cold_allowed=0 via udev "
                                "rule on the bridge BDF to truly pin "
                                "GPU power state.")}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    bdfs = list_nvidia_bdfs()
    cards: list = []
    worst = "aligned_on"
    rank = {"aligned_on": 0, "aligned_off": 0,
            "mismatched_eager": 1, "mismatched_strict": 2, "unknown": 1}
    for bdf in bdfs:
        bridge = find_parent_bridge(bdf)
        gpu_ctrl = read_gpu_control(bdf)
        bridge_state = (read_bridge_d3(bridge)
                         if bridge else {"bdf": None,
                                          "d3cold_allowed": None,
                                          "d3cold_delay_ms": None,
                                          "power_control": None})
        verdict = classify(gpu_ctrl, bridge_state.get("d3cold_allowed"))
        r = rank.get(verdict["verdict"], 0)
        if r > rank.get(worst, 0):
            worst = verdict["verdict"]
        cards.append({
            "gpu_bdf": bdf,
            "gpu_control": gpu_ctrl,
            "bridge_bdf": bridge,
            "bridge_d3cold_allowed": bridge_state["d3cold_allowed"],
            "bridge_d3cold_delay_ms": bridge_state["d3cold_delay_ms"],
            "verdict": verdict,
        })
    if not cards:
        return {"ok": True,
                "device_count": 0,
                "cards": [],
                "worst_verdict": "no_gpus"}
    return {"ok": True,
            "device_count": len(cards),
            "cards": cards,
            "worst_verdict": worst}
