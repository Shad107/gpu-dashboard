"""Module kfence_runtime_audit — KFENCE memory-safety probe
runtime posture (R&D #101.1).

KFENCE (Kernel Electric-Fence) is a sampling-based memory
safety canary. It catches use-after-free / out-of-bounds /
slab freelist corruption inside the kernel — invaluable on
hosts that load the closed-source NVIDIA driver, since
nvidia.ko regressions tend to manifest as silent slab
corruption days before they panic.

Three knobs decide whether the canary is even armed :

  /sys/module/kfence/parameters/sample_interval   ms (0 = off)
  /sys/module/kfence/parameters/skip_covered_thresh
  /sys/module/kfence/parameters/burst             (boot-time)

Plus the kernel config :

  CONFIG_KFENCE                       built in ?
  CONFIG_KFENCE_SAMPLE_INTERVAL       default sample_interval
                                      (0 disables at boot)

No existing module touches kfence — kasan is compile-time
(covered in kernel_build_config_audit), slab_audit is slab
counters only, kpageflags reads page bits.

Verdicts (worst-first) :

  kfence_disabled               warn    sample_interval = 0
                                        (no canary). Either
                                        CONFIG bakes 0 or
                                        runtime turned it off.
  kfence_sample_interval_high   accent  sample_interval > 1000
                                        ms — coverage is very
                                        thin on a desktop.
  ok                                    100 <= interval <= 1000
                                        ms (typical default).
  requires_root                         sample_interval
                                        unreadable.
  unknown                               /sys/module/kfence
                                        absent (CONFIG_KFENCE=n).

stdlib only.
"""
from __future__ import annotations

import gzip
import os
import re
from typing import Optional

NAME = "kfence_runtime_audit"

DEFAULT_KFENCE_SYSFS = "/sys/module/kfence/parameters"
DEFAULT_BOOT_DIR = "/boot"
DEFAULT_PROC_CONFIG = "/proc/config.gz"

_INTERVAL_HIGH_MS = 1000


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def _read_gz(path: str) -> Optional[str]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, gzip.BadGzipFile, UnicodeDecodeError):
        return None


def find_config_sample_interval(uname: str,
                                  boot_dir: str,
                                  proc_config: str
                                  ) -> Optional[int]:
    """Return CONFIG_KFENCE_SAMPLE_INTERVAL value or None."""
    text = _read_text(
        os.path.join(boot_dir, f"config-{uname}"))
    if text is None:
        text = _read_gz(proc_config)
    if text is None:
        return None
    m = re.search(
        r"^CONFIG_KFENCE_SAMPLE_INTERVAL=(\d+)",
        text, re.MULTILINE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def classify(sysfs_present: bool,
             sample_interval: Optional[int],
             config_interval: Optional[int]) -> dict:
    if not sysfs_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/module/kfence absent — "
                    "CONFIG_KFENCE=n in this kernel.")}

    effective = sample_interval
    if effective is None and config_interval is not None:
        effective = config_interval

    if effective is None:
        return {"verdict": "requires_root",
                "reason": (
                    "sample_interval unreadable and no "
                    "kernel config found — re-run as root.")}

    # warn — kfence disabled
    if effective == 0:
        return {
            "verdict": "kfence_disabled",
            "reason": (
                f"KFENCE sample_interval=0 (effective from "
                f"{'sysfs' if sample_interval is not None else 'CONFIG'})"
                " — canary disabled. Slab corruption from "
                "nvidia.ko / closed drivers will be invisible "
                "until panic.")}

    # accent — interval too high
    if effective > _INTERVAL_HIGH_MS:
        return {
            "verdict": "kfence_sample_interval_high",
            "reason": (
                f"sample_interval={effective} ms (> "
                f"{_INTERVAL_HIGH_MS} ms) — KFENCE coverage "
                "is too thin to catch most regressions.")}

    return {"verdict": "ok",
            "reason": (
                f"KFENCE armed ; sample_interval="
                f"{effective} ms. Canary active.")}


def status(config: Optional[dict] = None,
           sysfs: str = DEFAULT_KFENCE_SYSFS,
           boot_dir: str = DEFAULT_BOOT_DIR,
           proc_config: str = DEFAULT_PROC_CONFIG,
           uname: Optional[str] = None) -> dict:
    if uname is None:
        uname = os.uname().release

    sysfs_present = os.path.isdir(sysfs)
    sample_interval = (
        _read_int(os.path.join(sysfs, "sample_interval"))
        if sysfs_present else None)
    skip_covered = (
        _read_int(os.path.join(sysfs, "skip_covered_thresh"))
        if sysfs_present else None)
    config_interval = find_config_sample_interval(
        uname, boot_dir, proc_config)
    verdict = classify(sysfs_present, sample_interval,
                       config_interval)
    return {
        "ok": verdict["verdict"] == "ok",
        "sample_interval": sample_interval,
        "skip_covered_thresh": skip_covered,
        "config_sample_interval": config_interval,
        "verdict": verdict,
    }
