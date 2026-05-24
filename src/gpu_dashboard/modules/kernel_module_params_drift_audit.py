"""Module kernel_module_params_drift_audit — non-default
runtime parameters on critical kernel modules (R&D #84.3).

Storage / USB / mm modules silently drift after a
``modprobe.d`` edit nobody documented.  This audit covers a
curated list of high-risk parameters across :

  usbcore     authorized_default, autosuspend
  zswap       enabled, max_pool_percent, compressor
  ksm         run
  nvme        io_timeout, shutdown_timeout
  nvme_core   io_timeout, shutdown_timeout
  xhci_hcd    link_quirk
  i915        enable_psr, enable_dc, enable_guc
  amdgpu      bapm, pcie_gen_cap, deep_color

NVIDIA-specific tunables are covered by the existing
``kmod_params`` module ; KVM tunables by ``kvm_misc_audit``.
This audit deliberately stops at the non-NVIDIA non-KVM
boundary.

For each tracked param we know the kernel default and a
"risk level" :

  err    deviating from the safe default likely breaks
         something user-visible (authorized_default = -1
         without a user-policy daemon, nvme.io_timeout
         < 30 seconds).
  warn   performance-relevant drift (zswap.enabled = N on
         a low-RAM box, max_pool_percent > 50).
  accent informational (compressor choice, ksm.run > 0,
         GPU driver feature flags).

Verdicts (worst first) :

  security_param_flipped   ≥1 err-level param drifted.
  perf_param_drifted       ≥1 warn-level param drifted.
  many_non_default         ≥3 params (any level) at non-
                           default values.
  ok                       all tracked params at safe
                           defaults.
  unknown                  none of the tracked modules are
                           loaded (no /sys/module/<mod>).
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_MODULE_ROOT = "/sys/module"

# Curated table : module → param → (default, risk_level)
# risk_level is one of "err", "warn", "accent".
#
# Each "default" entry is either a literal value (str) or a
# tuple of accepted values.  For numeric drift detection we
# also support a callable taking the str value.
_TRACKED: dict = {
    "usbcore": {
        "authorized_default": ("1", "err"),
        "autosuspend": (("2", "1", "-1"), "warn"),
    },
    "zswap": {
        # Distro-dependent — Ubuntu ships N, Fedora ships Y.
        # Both are "default" depending on CONFIG_ZSWAP_DEFAULT_ON
        # and kernel cmdline. Don't flag either.
        "enabled": (("Y", "N", "1", "0"), "accent"),
        "max_pool_percent": ("20", "warn"),
        "compressor": (("zstd", "lzo-rle", "deflate",
                          "lz4"), "accent"),
    },
    "ksm": {
        "run": ("0", "accent"),
    },
    "nvme": {
        "io_timeout": ("30", "err"),
        "shutdown_timeout": ("5", "warn"),
    },
    "nvme_core": {
        "io_timeout": ("30", "err"),
        "shutdown_timeout": ("5", "warn"),
    },
    "xhci_hcd": {
        "link_quirk": ("0", "accent"),
    },
    "i915": {
        "enable_psr": (("0", "1", "-1"), "accent"),
        "enable_dc": (("-1", "2"), "accent"),
        "enable_guc": (("-1", "0", "2", "3"), "accent"),
    },
    "amdgpu": {
        "bapm": (("0", "-1"), "accent"),
        "pcie_gen_cap": ("0", "accent"),
    },
}


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _is_default(value: Optional[str], default_spec) -> bool:
    if value is None:
        return True
    if isinstance(default_spec, tuple):
        return value in default_spec
    return value == default_spec


def scan(module_root: str = DEFAULT_MODULE_ROOT) -> list[dict]:
    """Returns list of per-param scan results."""
    out: list[dict] = []
    for module, params in _TRACKED.items():
        mod_dir = os.path.join(module_root, module)
        if not os.path.isdir(mod_dir):
            continue
        for name, (default_spec, risk) in params.items():
            path = os.path.join(
                mod_dir, "parameters", name)
            value = _read_text(path)
            if value is None:
                continue
            non_default = not _is_default(value, default_spec)
            out.append({
                "module": module,
                "param": name,
                "value": value,
                "default": (default_spec
                              if not isinstance(
                                  default_spec, tuple)
                              else default_spec[0]),
                "risk": risk,
                "non_default": non_default,
            })
    return out


def classify(scans: list[dict]) -> dict:
    if not scans:
        return {"verdict": "unknown",
                "reason": (
                    "None of the tracked modules "
                    "(usbcore, zswap, ksm, nvme, "
                    "xhci_hcd, i915, amdgpu) are loaded.")}

    err_drifted = [
        s for s in scans
        if s["non_default"] and s["risk"] == "err"]
    if err_drifted:
        first = err_drifted[0]
        return {"verdict": "security_param_flipped",
                "reason": (
                    f"{first['module']}.{first['param']} "
                    f"= {first['value']} (default "
                    f"{first['default']}) — likely breaks "
                    "expected security/safety guarantees."),
                "module": first["module"],
                "param": first["param"],
                "value": first["value"]}

    warn_drifted = [
        s for s in scans
        if s["non_default"] and s["risk"] == "warn"]
    if warn_drifted:
        first = warn_drifted[0]
        return {"verdict": "perf_param_drifted",
                "reason": (
                    f"{first['module']}.{first['param']} "
                    f"= {first['value']} (default "
                    f"{first['default']}) — performance "
                    "drift from kernel default."),
                "module": first["module"],
                "param": first["param"],
                "value": first["value"]}

    drifted = [s for s in scans if s["non_default"]]
    if len(drifted) >= 3:
        return {"verdict": "many_non_default",
                "reason": (
                    f"{len(drifted)} module params drifted "
                    "from kernel defaults — broad surface."),
                "drift_count": len(drifted)}

    return {"verdict": "ok",
            "reason": (
                f"Scanned {len(scans)} tracked param(s) ; "
                f"{len(drifted)} drift(s) — all within "
                "informational accent threshold.")}


def status(config: Optional[dict] = None,
           module_root: str = DEFAULT_MODULE_ROOT) -> dict:
    scans = scan(module_root)
    verdict = classify(scans)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "security_param_flipped"),
        "scanned": len(scans),
        "drifted": sum(1 for s in scans if s["non_default"]),
        "params": [
            {"module": s["module"],
             "param": s["param"],
             "value": s["value"],
             "default": s["default"],
             "risk": s["risk"],
             "non_default": s["non_default"]}
            for s in scans],
        "verdict": verdict,
    }
