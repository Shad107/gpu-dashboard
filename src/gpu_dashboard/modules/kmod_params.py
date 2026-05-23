"""Module kmod_params — NVIDIA kmod parameter auditor (R&D #29.1).

The nvidia kernel module ships with ~40 tunables exposed under
`/sys/module/nvidia/parameters/`. Most users never look at them.
Two of them are notorious foot-guns :

  NVreg_PreserveVideoMemoryAllocations=0  (default)
       → VRAM contents are LOST across suspend/resume.
         For LLM inference, the model has to reload after S3/S2idle.

  NVreg_EnableGpuFirmware=0
       → forces legacy host-RM (the slower path that GSP-fallback
         already triggers on bug). User would only set this if
         working around a GSP crash.

Other interesting params : DynamicPowerManagement (laptop), MSI,
OpenRmEnableUnsupportedGpus, S0ixPowerManagement, PreserveVMemAllocs.

This module reads every parameter, flags known foot-guns, and
emits a modprobe.d Drop-In snippet for any fix.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "kmod_params"


_KMOD_PARAMS_ROOT = "/sys/module/nvidia/parameters"

# Known foot-gun rules : (param, expected_value, severity, advice).
# Each rule fires only if param exists AND value != expected.
FOOTGUN_RULES = [
    ("NVreg_PreserveVideoMemoryAllocations", "1", "warn",
     ("VRAM contents are LOST across suspend/resume — model has to "
      "reload after S3/S2idle. Set to 1 to preserve.")),
    ("NVreg_EnableGpuFirmware", "1", "info",
     ("GSP firmware is disabled (forcing legacy host-RM). Only set "
      "this when working around a GSP crash bug — see R&D #21.3.")),
    ("NVreg_DynamicPowerManagement", None, "info",
     ("Dynamic power management is enabled. Fine for laptops, but "
      "see R&D #28.1 (PCIe runtime-PM) if TTFT spikes.")),
]


def read_param(name: str, root: str = _KMOD_PARAMS_ROOT) -> Optional[str]:
    p = os.path.join(root, name)
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def list_all_params(root: str = _KMOD_PARAMS_ROOT) -> dict:
    """Return {name: value} for every readable parameter file."""
    out: dict = {}
    try:
        for name in sorted(os.listdir(root)):
            value = read_param(name, root)
            if value is not None:
                out[name] = value
    except OSError:
        return {}
    return out


def modprobe_dropin_recipe(param: str, value: str) -> str:
    """Recipe to persist a param across reboots."""
    return (f"# Save as /etc/modprobe.d/nvidia-{param.lower()}.conf\n"
            f"options nvidia {param}={value}\n"
            f"# Apply : sudo update-initramfs -u && reboot")


def evaluate(params: dict) -> list[dict]:
    """Return list of {param, current, recommended, severity, advice,
    recipe} entries. Only fires for params that exist AND violate the rule."""
    out: list[dict] = []
    for name, expected, severity, advice in FOOTGUN_RULES:
        current = params.get(name)
        if current is None:
            continue
        if expected is None:
            # informational, only emit if non-zero
            if current.strip() in ("0", "", "N", "false"):
                continue
            out.append({
                "param": name,
                "current": current,
                "recommended": None,
                "severity": severity,
                "advice": advice,
                "recipe": "",
            })
            continue
        if current.strip() == expected.strip():
            continue
        out.append({
            "param": name,
            "current": current,
            "recommended": expected,
            "severity": severity,
            "advice": advice,
            "recipe": modprobe_dropin_recipe(name, expected),
        })
    return out


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    if not os.path.isdir(_KMOD_PARAMS_ROOT):
        return {
            "ok": False,
            "reason": ("/sys/module/nvidia/parameters not found. "
                        "Either nvidia kmod is not loaded or the kernel "
                        "doesn't expose parameters via sysfs."),
            "params": {},
            "footguns": [],
        }
    params = list_all_params(_KMOD_PARAMS_ROOT)
    footguns = evaluate(params)
    rank = {"info": 0, "warn": 1, "critical": 2}
    worst = "info"
    for f in footguns:
        if rank.get(f["severity"], 0) > rank.get(worst, 0):
            worst = f["severity"]
    return {
        "ok": True,
        "params": params,
        "param_count": len(params),
        "footguns": footguns,
        "footgun_count": len(footguns),
        "worst_severity": worst if footguns else "info",
    }
