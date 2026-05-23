"""Module pcie_width_watcher — Silent PCIe link-width downgrade watcher (R&D #26.5).

OcuLink risers, M.2-to-PCIe adapters, and corroded slot pins all
cause a *static* link-width downgrade where the GPU comes up at
x8 / x4 / x1 even though it advertises x16. Performance impact is
substantial (50%+ for high-throughput inference), and the only
symptom is "GPU feels slow".

Distinct from R&D #18.6 PCIe link-state thrasher (event-driven
flapping) and R&D #24.2 PCIe-AER (correctable errors). This module
captures the quasi-static "advertised x16 but running x4" state by
diffing current_link_width vs max_link_width on every NVIDIA GPU.

Also reads current_link_speed vs max_link_speed to spot Gen3 ↔ Gen4
downgrades on Z690+/B650 boards. Pairs naturally with R&D #18.6
histogram and #23.4 ASPM audit.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "pcie_width_watcher"


_PCI_ROOT = "/sys/bus/pci/devices"


def list_nvidia_bdfs(sys_root: str = _PCI_ROOT) -> list[str]:
    out: list[str] = []
    try:
        for name in sorted(os.listdir(sys_root)):
            vendor_p = os.path.join(sys_root, name, "vendor")
            try:
                with open(vendor_p) as f:
                    if f.read().strip().lower() == "0x10de":
                        out.append(name)
            except OSError:
                continue
    except OSError:
        pass
    return out


def read_text(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip() or None
    except OSError:
        return None


def parse_width(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    s = s.strip()
    if s.lower() == "unknown" or not s.isdigit():
        return None
    return int(s)


def parse_speed_gts(s: Optional[str]) -> Optional[float]:
    """'16.0 GT/s PCIe' → 16.0."""
    if not s or s.lower() == "unknown":
        return None
    m = re.match(r"^([\d.]+)\s*GT", s)
    return float(m.group(1)) if m else None


def gen_from_gts(gts: Optional[float]) -> Optional[int]:
    if gts is None:
        return None
    table = {2.5: 1, 5.0: 2, 8.0: 3, 16.0: 4, 32.0: 5, 64.0: 6}
    return table.get(round(gts, 1))


def read_link(bdf: str, sys_root: str = _PCI_ROOT) -> dict:
    base = os.path.join(sys_root, bdf)
    cur_w = parse_width(read_text(os.path.join(base, "current_link_width")))
    max_w = parse_width(read_text(os.path.join(base, "max_link_width")))
    cur_s = parse_speed_gts(read_text(os.path.join(base, "current_link_speed")))
    max_s = parse_speed_gts(read_text(os.path.join(base, "max_link_speed")))
    return {
        "bdf": bdf,
        "current_width": cur_w,
        "max_width": max_w,
        "current_speed_gts": cur_s,
        "max_speed_gts": max_s,
        "current_gen": gen_from_gts(cur_s),
        "max_gen": gen_from_gts(max_s),
    }


def classify_link(link: dict) -> dict:
    """Compare current vs max link width + speed. Spurious x63 reading
    (driver bug on some Ampere cards under OcuLink) is filtered : if
    both current and max equal the same out-of-spec value (e.g. 63),
    we report 'unknown' instead of 'ok'."""
    cur_w = link.get("current_width")
    max_w = link.get("max_width")
    cur_g = link.get("current_gen")
    max_g = link.get("max_gen")
    if cur_w is None or max_w is None:
        return {"verdict": "unknown",
                "reason": "Link width unreadable from sysfs.",
                "recovery": ""}
    # x63 / out-of-spec values indicate driver / connection bug
    if cur_w not in (1, 2, 4, 8, 16, 32) or max_w not in (1, 2, 4, 8, 16, 32):
        return {"verdict": "unknown",
                "reason": (f"Out-of-spec width values "
                           f"(current={cur_w}, max={max_w}). "
                           "Likely driver bug — reload nvidia modules."),
                "recovery": ("sudo modprobe -r nvidia_uvm nvidia_drm "
                              "nvidia_modeset nvidia && sudo modprobe nvidia")}
    width_downgrade = cur_w < max_w
    gen_downgrade = (cur_g is not None and max_g is not None and cur_g < max_g)
    if width_downgrade and gen_downgrade:
        return {"verdict": "downgraded_both",
                "reason": (f"Running x{cur_w} Gen{cur_g} but slot advertises "
                           f"x{max_w} Gen{max_g}. Major perf loss — check "
                           "cable / riser / slot."),
                "recovery": "Reseat cable + riser. Try another PCIe slot."}
    if width_downgrade:
        return {"verdict": "downgraded_width",
                "reason": (f"Running x{cur_w} but slot advertises x{max_w}. "
                           "Cable / slot likely losing lanes."),
                "recovery": "Reseat cable + riser. Inspect slot pins."}
    if gen_downgrade:
        return {"verdict": "downgraded_speed",
                "reason": (f"Running Gen{cur_g} but slot advertises "
                           f"Gen{max_g}. Disable PCIe ASPM or check signal "
                           "integrity."),
                "recovery": "Try adding 'pcie_aspm=off' to GRUB cmdline."}
    return {"verdict": "ok",
            "reason": f"Link at full x{cur_w} Gen{cur_g or '?'}.",
            "recovery": ""}


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    bdfs = list_nvidia_bdfs()
    devices: list = []
    worst_rank = 0
    rank = {"ok": 0, "unknown": 1, "downgraded_speed": 2,
            "downgraded_width": 3, "downgraded_both": 4}
    worst_verdict = "ok"
    for bdf in bdfs:
        link = read_link(bdf)
        verdict = classify_link(link)
        link["verdict"] = verdict
        devices.append(link)
        r = rank.get(verdict["verdict"], 0)
        if r > worst_rank:
            worst_rank = r
            worst_verdict = verdict["verdict"]
    if not devices:
        return {"ok": True,
                "devices": [],
                "device_count": 0,
                "worst_verdict": "no_gpus",
                "summary_reason": "No NVIDIA PCI devices found."}
    return {
        "ok": True,
        "devices": devices,
        "device_count": len(devices),
        "worst_verdict": worst_verdict,
        "summary_reason": next(
            (d["verdict"]["reason"] for d in devices
             if d["verdict"]["verdict"] == worst_verdict),
            "All links at advertised width/speed."
        ),
    }
