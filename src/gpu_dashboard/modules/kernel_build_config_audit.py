"""Module kernel_build_config_audit — /boot/config-* (R&D #58.2).

Reads the static kernel build config (/boot/config-$(uname -r) with
fallback to /proc/config.gz via stdlib gzip). Distinct from existing
cmdline_audit (boot-time args only) — this surfaces compile-time
choices that no runtime sysctl can fix.

Why this matters on an LLM rig :

* CONFIG_PREEMPT_NONE=y (the server preset) gives the kernel
  permission to hold a CPU for tens of ms during a soft-IRQ
  storm. CUDA kernel launches → variable latency that no
  governor / RAPL tweak can flatten.
* CONFIG_DEBUG_PAGEALLOC=y / CONFIG_DEBUG_VM=y / CONFIG_DEBUG_SLAB=y
  builds reroute every allocation through validation code — 30 %+
  memory throughput penalty that the user attributes to "slow
  hardware".
* CONFIG_TRANSPARENT_HUGEPAGE_ALWAYS=y as the default conflicts
  with our own #52.1 (which recommends madvise default) and
  cannot be tuned away — only the runtime sysfs override.

Reads :
  /boot/config-$(uname -r)        primary
  /proc/config.gz                  fallback (gzipped)
  os.uname().release               for resolution

Verdicts (priority-ordered) :
  debug_kernel_in_use            CONFIG_DEBUG_PAGEALLOC=y OR
                                 CONFIG_DEBUG_VM=y OR
                                 CONFIG_DEBUG_SLAB=y OR
                                 CONFIG_DEBUG_SPINLOCK=y.
  preempt_none_for_desktop       CONFIG_PREEMPT_NONE=y (and
                                 CONFIG_PREEMPT_VOLUNTARY/
                                 CONFIG_PREEMPT not set).
  thp_madvise_default_mismatch   CONFIG_TRANSPARENT_HUGEPAGE_ALWAYS
                                 =y (compile-time default →
                                 contradicts #52.1 advice).
  ok                             config sensible for a desktop /
                                 homelab LLM host.
  unknown                        no readable config found.

stdlib only.
"""
from __future__ import annotations

import gzip
import os
from typing import Dict, Optional


NAME = "kernel_build_config_audit"


_BOOT_CONFIG_FMT = "/boot/config-{release}"
_PROC_CONFIG_GZ = "/proc/config.gz"


_KEYS_OF_INTEREST = (
    "CONFIG_PREEMPT_NONE", "CONFIG_PREEMPT_VOLUNTARY",
    "CONFIG_PREEMPT", "CONFIG_PREEMPT_DYNAMIC",
    "CONFIG_HZ", "CONFIG_NO_HZ_FULL", "CONFIG_NO_HZ_IDLE",
    "CONFIG_TRANSPARENT_HUGEPAGE",
    "CONFIG_TRANSPARENT_HUGEPAGE_ALWAYS",
    "CONFIG_TRANSPARENT_HUGEPAGE_MADVISE",
    "CONFIG_NUMA_BALANCING",
    "CONFIG_NUMA_BALANCING_DEFAULT_ENABLED",
    "CONFIG_RCU_NOCB_CPU",
    "CONFIG_RANDOMIZE_BASE",
    "CONFIG_PAGE_TABLE_ISOLATION",
    "CONFIG_RETPOLINE",
    "CONFIG_DEBUG_KERNEL",
    "CONFIG_DEBUG_PAGEALLOC",
    "CONFIG_DEBUG_PAGEALLOC_ENABLE_DEFAULT",
    "CONFIG_DEBUG_VM",
    "CONFIG_DEBUG_VM_PGFLAGS",
    "CONFIG_DEBUG_SLAB",
    "CONFIG_DEBUG_SPINLOCK",
)


def _read_text(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_gz(p: str) -> Optional[str]:
    try:
        with gzip.open(p, "rt") as f:
            return f.read()
    except OSError:
        return None


def read_config(release: Optional[str] = None,
                  boot_config_fmt: str = _BOOT_CONFIG_FMT,
                  proc_config_gz: str = _PROC_CONFIG_GZ
                  ) -> Optional[str]:
    if release is None:
        release = os.uname().release
    text = _read_text(boot_config_fmt.format(release=release))
    if text is not None:
        return text
    return _read_gz(proc_config_gz)


def parse_config(text: Optional[str]) -> Dict[str, str]:
    """Returns {KEY: value_string}.  Skips comments + unset lines."""
    out: Dict[str, str] = {}
    if not text:
        return out
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k not in _KEYS_OF_INTEREST:
            continue
        out[k] = v.strip()
    return out


def _is_y(cfg: Dict[str, str], key: str) -> bool:
    return cfg.get(key) == "y"


def classify(cfg: Dict[str, str]) -> dict:
    if not cfg:
        return {"verdict": "unknown",
                "reason": ("No /boot/config-* and /proc/config.gz "
                          "absent — can't read static kernel "
                          "config."),
                "recommendation": ""}

    # 1) debug_kernel_in_use — only the *expensive* debug knobs.
    debug_flags = []
    for k in ("CONFIG_DEBUG_PAGEALLOC",
                "CONFIG_DEBUG_PAGEALLOC_ENABLE_DEFAULT",
                "CONFIG_DEBUG_VM",
                "CONFIG_DEBUG_VM_PGFLAGS",
                "CONFIG_DEBUG_SLAB",
                "CONFIG_DEBUG_SPINLOCK"):
        if _is_y(cfg, k):
            debug_flags.append(k)
    if debug_flags:
        return {"verdict": "debug_kernel_in_use",
                "reason": (f"Kernel built with expensive debug "
                          f"flags : {', '.join(debug_flags[:3])}. "
                          f"30 %+ memory throughput penalty."),
                "recommendation": _recipe_swap_kernel()}

    # 2) preempt_none_for_desktop — server preset on a desktop
    #    host. We treat it as "desktop" by default ; users can
    #    accept on dedicated server.
    if _is_y(cfg, "CONFIG_PREEMPT_NONE") and not \
            _is_y(cfg, "CONFIG_PREEMPT_VOLUNTARY") and not \
            _is_y(cfg, "CONFIG_PREEMPT"):
        return {"verdict": "preempt_none_for_desktop",
                "reason": ("CONFIG_PREEMPT_NONE=y (server preset). "
                          "Interactivity / CUDA-launch latency "
                          "suffers under soft-IRQ storms."),
                "recommendation": _recipe_swap_preempt()}

    # 3) thp_madvise_default_mismatch
    if _is_y(cfg, "CONFIG_TRANSPARENT_HUGEPAGE_ALWAYS"):
        return {"verdict": "thp_madvise_default_mismatch",
                "reason": ("CONFIG_TRANSPARENT_HUGEPAGE_ALWAYS=y at "
                          "compile time. Conflicts with our "
                          "runtime advice (madvise default) — only "
                          "the /sys/kernel/mm override can "
                          "mitigate."),
                "recommendation": _recipe_thp_runtime()}

    return {"verdict": "ok",
            "reason": (f"Kernel build config sensible for an "
                      f"LLM host (HZ={cfg.get('CONFIG_HZ', '?')}, "
                      f"PREEMPT_VOLUNTARY="
                      f"{cfg.get('CONFIG_PREEMPT_VOLUNTARY', 'n')})."),
            "recommendation": ""}


def status(config=None,
            release: Optional[str] = None,
            boot_config_fmt: str = _BOOT_CONFIG_FMT,
            proc_config_gz: str = _PROC_CONFIG_GZ) -> dict:
    text = read_config(release, boot_config_fmt, proc_config_gz)
    cfg = parse_config(text)
    ok = bool(cfg)
    verdict = classify(cfg)
    return {"ok": ok,
              "release": release or os.uname().release,
              "key_count": len(cfg),
              "interesting": cfg,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_swap_kernel() -> str:
    return ("# Replace the debug kernel with a release build :\n"
            "sudo apt install linux-generic  # Debian/Ubuntu\n"
            "# … or for HWE :\n"
            "sudo apt install linux-generic-hwe-$(lsb_release -rs)\n"
            "# Verify the next boot uses the non-debug variant :\n"
            "grep -E '^CONFIG_DEBUG_VM=|^CONFIG_DEBUG_PAGEALLOC=' /boot/config-*\n")


def _recipe_swap_preempt() -> str:
    return ("# Install a desktop/voluntary-preempt kernel :\n"
            "# Debian/Ubuntu HWE :\n"
            "sudo apt install linux-image-generic-hwe-$(lsb_release -rs)\n"
            "# Or via /sys/kernel/debug at runtime (only with\n"
            "# CONFIG_PREEMPT_DYNAMIC=y) :\n"
            "sudo bash -c 'echo voluntary > /sys/kernel/debug/sched/preempt'\n")


def _recipe_thp_runtime() -> str:
    return ("# Override THP at runtime to madvise (matches R&D #52.1)\n"
            "echo madvise | sudo tee /sys/kernel/mm/transparent_hugepage/enabled\n"
            "echo madvise | sudo tee /sys/kernel/mm/transparent_hugepage/defrag\n"
            "# Persist via /etc/default/grub :\n"
            "#   transparent_hugepage=madvise\n")
