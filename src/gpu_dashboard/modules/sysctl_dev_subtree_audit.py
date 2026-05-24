"""Module sysctl_dev_subtree_audit — /proc/sys/dev/* knobs
audit (R&D #73.2).

/proc/sys/dev is the forgotten sysctl peer of vm/net/kernel.
It contains real desktop-relevant device knobs that no other
module touches :

  /proc/sys/dev/cdrom/{autoclose,autoeject,lock}
  /proc/sys/dev/hpet/max-user-freq          poll-rate ceiling
  /proc/sys/dev/i915/perf_stream_paranoid   GPU perf gating
                                              (Intel only)
  /proc/sys/dev/mac_hid/{...}
  /proc/sys/dev/parport/{...}
  /proc/sys/dev/scsi/logging_level          bitmask ; non-zero
                                              floods dmesg
  /proc/sys/dev/tty/{ldisc_autoload,
                       legacy_tiocsti}

Why on a homelab :

* `scsi/logging_level != 0` is a common forgotten-debug-knob
  that floods kernel ring buffer and IO-starves NVMe.
* `i915/perf_stream_paranoid = 1` blocks unprivileged GPU
  profilers ; tooling silently degrades on Intel boards (and
  on hybrid hosts that ALSO have NVIDIA).
* `hpet/max-user-freq = 64` (default) throttles userland HPET
  poll loops used by media players ; ALSA / PulseAudio docs
  recommend raising to 1024.
* `cdrom/autoclose = 0` is unusual and breaks GUI eject.

Verdicts (priority order) :
  scsi_verbose_logging_on    scsi/logging_level != 0.
  i915_perf_paranoid_unset    i915/perf_stream_paranoid == 1
                              (blocks unprivileged GPU profiler).
  hpet_max_user_freq_low      hpet/max-user-freq < 1024.
  cdrom_autoclose_off         cdrom/autoclose == 0.
  ok                          knobs sane.
  unknown                     /proc/sys/dev absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "sysctl_dev_subtree_audit"


_PROC_SYS_DEV = "/proc/sys/dev"

_HPET_FLOOR = 1024


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        try:
            return int(t, 0)
        except ValueError:
            return None


def scan(sys_dev: str = _PROC_SYS_DEV) -> dict:
    return {
        "scsi_logging_level": _read_int(
            os.path.join(sys_dev, "scsi", "logging_level")),
        "i915_perf_stream_paranoid": _read_int(
            os.path.join(sys_dev, "i915",
                            "perf_stream_paranoid")),
        "hpet_max_user_freq": _read_int(
            os.path.join(sys_dev, "hpet", "max-user-freq")),
        "cdrom_autoclose": _read_int(
            os.path.join(sys_dev, "cdrom", "autoclose")),
    }


def classify(present: bool, knobs: dict) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": "/proc/sys/dev directory absent.",
                "recommendation": ""}

    # 1) scsi_verbose_logging_on
    scsi = knobs.get("scsi_logging_level")
    if scsi is not None and scsi != 0:
        return {"verdict": "scsi_verbose_logging_on",
                "reason": (f"/proc/sys/dev/scsi/logging_level = "
                          f"{scsi}. Verbose SCSI logging floods "
                          f"the kernel ring buffer and can stall "
                          f"NVMe queues."),
                "recommendation": _recipe_scsi(scsi)}

    # 2) i915_perf_paranoid_unset
    i915p = knobs.get("i915_perf_stream_paranoid")
    if i915p == 1:
        return {"verdict": "i915_perf_paranoid_unset",
                "reason": ("dev/i915/perf_stream_paranoid = 1 — "
                          "blocks unprivileged Intel GPU "
                          "profilers (intel_gpu_top, sysprof, "
                          "Renderdoc on Intel)."),
                "recommendation": _recipe_i915_paranoid()}

    # 3) hpet_max_user_freq_low
    hpet = knobs.get("hpet_max_user_freq")
    if hpet is not None and hpet < _HPET_FLOOR:
        return {"verdict": "hpet_max_user_freq_low",
                "reason": (f"dev/hpet/max-user-freq = {hpet} "
                          f"(floor {_HPET_FLOOR}). Userland "
                          f"HPET poll loops throttled — affects "
                          f"low-latency audio."),
                "recommendation": _recipe_hpet()}

    # 4) cdrom_autoclose_off
    cd = knobs.get("cdrom_autoclose")
    if cd == 0:
        return {"verdict": "cdrom_autoclose_off",
                "reason": ("dev/cdrom/autoclose = 0 — GUI eject "
                          "tools won't re-close the tray."),
                "recommendation": _recipe_cdrom()}

    return {"verdict": "ok",
            "reason": (f"scsi_logging_level={scsi} ; "
                      f"i915_perf_paranoid="
                      f"{i915p} ; "
                      f"hpet_max_user_freq={hpet} ; "
                      f"cdrom_autoclose={cd}."),
            "recommendation": ""}


def status(config=None,
            sys_dev: str = _PROC_SYS_DEV) -> dict:
    present = os.path.isdir(sys_dev)
    knobs = scan(sys_dev) if present else {}
    verdict = classify(present, knobs)
    return {"ok": present,
              "present": present,
              **knobs,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_scsi(level: int) -> str:
    return (f"# SCSI logging_level = {level} (0x{level:08x}). \n"
            f"# Reset to 0 :\n"
            f"echo 0 | sudo tee /proc/sys/dev/scsi/logging_level\n"
            f"# Persist (in case set via /etc/sysctl.d/) :\n"
            f"echo 'dev.scsi.logging_level = 0' \\\n"
            f"  | sudo tee /etc/sysctl.d/99-scsi-quiet.conf\n")


def _recipe_i915_paranoid() -> str:
    return ("# Allow Intel GPU profiling for non-root :\n"
            "echo 0 | sudo tee \\\n"
            "  /proc/sys/dev/i915/perf_stream_paranoid\n"
            "# Persist :\n"
            "echo 'dev.i915.perf_stream_paranoid = 0' \\\n"
            "  | sudo tee /etc/sysctl.d/99-i915-perf.conf\n")


def _recipe_hpet() -> str:
    return ("# Raise HPET userland poll rate :\n"
            "echo 1024 | sudo tee /proc/sys/dev/hpet/max-user-freq\n"
            "# Persist :\n"
            "echo 'dev.hpet.max-user-freq = 1024' \\\n"
            "  | sudo tee /etc/sysctl.d/99-hpet.conf\n")


def _recipe_cdrom() -> str:
    return ("# Re-enable CD-ROM autoclose :\n"
            "echo 1 | sudo tee /proc/sys/dev/cdrom/autoclose\n")
