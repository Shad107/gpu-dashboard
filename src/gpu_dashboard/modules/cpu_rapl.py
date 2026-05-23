"""Module cpu_rapl — CPU-package power via RAPL (R&D #27.3).

The flagship tokens/Watt + €/month widgets currently report GPU
power only. Inference also burns CPU cycles (tokenizer, sampler,
host-side copies) and on small models the CPU can draw 50-100 W
sustained — distorting both metrics if left out.

Linux exposes per-CPU-package energy via Intel's RAPL interface,
which AMD has also implemented on kernel ≥ 5.11. Reads from :

  /sys/class/powercap/intel-rapl/intel-rapl:<i>/{name,energy_uj,
                                                  max_energy_range_uj}

We sample energy_uj twice with a short delay, handle the µJ counter
wrap (max_energy_range_uj), and compute power in watts as
  (delta_energy_uj / delta_time_us)

Returns per-package watts + aggregate total. Pairs with the shipped
tariff config (#15.2) and inference cost tracker (#14.6) so the
W/€ widgets reflect the *full socket*.

stdlib only.
"""
from __future__ import annotations

import os
import time
from typing import Optional


NAME = "cpu_rapl"


_POWERCAP_ROOT = "/sys/class/powercap"


def list_rapl_packages(root: str = _POWERCAP_ROOT) -> list[str]:
    """Return the rapl directory paths that look like 'intel-rapl:N'.
    Skip subdomains like 'intel-rapl:0:0' which are DRAM / GFX / cores."""
    out: list[str] = []
    try:
        for name in sorted(os.listdir(root)):
            # 'intel-rapl:0' is a package ; 'intel-rapl:0:0' is a subdomain
            if name.startswith("intel-rapl:") and name.count(":") == 1:
                out.append(os.path.join(root, name))
    except OSError:
        pass
    return out


def read_text(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_energy_uj(pkg_dir: str) -> Optional[int]:
    txt = read_text(os.path.join(pkg_dir, "energy_uj"))
    if txt is None or not txt.isdigit():
        return None
    return int(txt)


def read_max_energy_uj(pkg_dir: str) -> Optional[int]:
    txt = read_text(os.path.join(pkg_dir, "max_energy_range_uj"))
    if txt is None or not txt.isdigit():
        return None
    return int(txt)


def read_package_name(pkg_dir: str) -> str:
    return read_text(os.path.join(pkg_dir, "name")) or os.path.basename(pkg_dir)


def compute_watts(e0: int, e1: int, dt_s: float,
                    max_energy_uj: Optional[int]) -> Optional[float]:
    """Compute average watts between two RAPL readings, handling wrap."""
    if dt_s <= 0:
        return None
    delta = e1 - e0
    if delta < 0 and max_energy_uj is not None:
        delta = (max_energy_uj - e0) + e1
    if delta < 0:
        return None
    # µJ / s → µW → W
    return (delta / dt_s) / 1e6


def sample_package(pkg_dir: str, interval_s: float = 0.5) -> dict:
    """Take two energy readings interval_s apart, compute watts."""
    e0 = read_energy_uj(pkg_dir)
    t0 = time.time()
    if e0 is None:
        return {"name": read_package_name(pkg_dir),
                "watts": None, "error": "read failed"}
    time.sleep(interval_s)
    e1 = read_energy_uj(pkg_dir)
    t1 = time.time()
    if e1 is None:
        return {"name": read_package_name(pkg_dir),
                "watts": None, "error": "read failed mid-sample"}
    max_uj = read_max_energy_uj(pkg_dir)
    watts = compute_watts(e0, e1, t1 - t0, max_uj)
    return {
        "name": read_package_name(pkg_dir),
        "path": pkg_dir,
        "watts": (round(watts, 1) if watts is not None else None),
        "energy_uj_start": e0,
        "energy_uj_end": e1,
        "interval_s": round(t1 - t0, 3),
    }


def status(cfg=None) -> dict:
    """Aggregate snapshot."""
    interval_s = 0.5
    if cfg:
        try:
            interval_s = max(0.1, min(2.0,
                              float(cfg.get("CPU_RAPL_INTERVAL_S", "0.5"))))
        except (ValueError, TypeError):
            pass
    packages = list_rapl_packages()
    if not packages:
        return {
            "ok": False,
            "reason": ("No RAPL packages found at /sys/class/powercap. "
                        "Kernel module may be 'intel_rapl_msr' (load with "
                        "modprobe) or this is a VM where RAPL is hidden."),
            "supported": False,
            "samples": [],
            "total_watts": None,
        }
    samples: list = []
    total: float = 0.0
    have_any = False
    for pkg in packages:
        s = sample_package(pkg, interval_s=interval_s)
        samples.append(s)
        if s.get("watts") is not None:
            total += s["watts"]
            have_any = True
    return {
        "ok": True,
        "supported": True,
        "samples": samples,
        "total_watts": round(total, 1) if have_any else None,
        "package_count": len(packages),
    }
