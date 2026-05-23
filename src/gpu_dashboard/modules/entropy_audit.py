"""Module entropy_audit — kernel entropy + hwrng auditor (R&D #45.4).

On a homelab box that serves TLS-fronted inference (HTTPS to
OpenWebUI, SSH-tunnelled llama-server, mTLS-protected Discord
bots), the kernel CRNG seeding affects every connection setup
latency at boot.

Modern Linux (5.x+) uses a ChaCha20-based CRNG that's always "full"
once the boot-time `crng_init=1` state is reached — entropy_avail
is mostly a vestige. But the *seeding* matters : without a hwrng
(virtio-rng on guests, TPM RNG, Intel RDRAND quality boost,
external USB-RNG), early-boot TLS / SSH handshakes can stall
seconds waiting for crng_init.

  /proc/sys/kernel/random/entropy_avail        legacy : current
                                                pool fill. Modern :
                                                always ≈ poolsize.
  /proc/sys/kernel/random/poolsize             pool capacity
                                                (256 on modern,
                                                4096 on legacy).
  /proc/sys/kernel/random/urandom_min_reseed_secs
                                                reseed cadence.
  /proc/sys/kernel/random/write_wakeup_threshold
                                                kernel wakes
                                                writers below this.
  /sys/class/misc/hw_random/rng_current        active hwrng (e.g.
                                                "virtio_rng.0",
                                                "tpm-rng-0",
                                                "intel-rng") or
                                                "none".
  /sys/class/misc/hw_random/rng_available      space-separated
                                                list of options.
  /sys/class/misc/hw_random/rng_quality        bits-per-byte
                                                quality estimate.

Verdicts (priority-ordered) :
  no_hwrng              rng_current == "none" → no hardware RNG
                        is feeding the CRNG ; relying on
                        jitterentropy + boot noise alone. On
                        a VM guest with no virtio-rng exposed,
                        early-boot TLS / SSH handshakes can
                        stall seconds.
  low_entropy           entropy_avail < poolsize / 4 (only
                        actually meaningful on pre-5.x kernels ;
                        modern always reports full).
  ok                    hwrng active OR modern kernel with
                        full CRNG.
  unknown               /proc/sys/kernel/random unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "entropy_audit"


_PROC_SYS_RANDOM = "/proc/sys/kernel/random"
_SYS_HWRNG = "/sys/class/misc/hw_random"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


_RANDOM_FIELDS = (
    "entropy_avail", "poolsize", "urandom_min_reseed_secs",
    "write_wakeup_threshold", "read_wakeup_threshold",
)


def read_random(sys_random: str = _PROC_SYS_RANDOM) -> dict:
    out: dict = {}
    for f in _RANDOM_FIELDS:
        v = _read_int(os.path.join(sys_random, f))
        if v is not None:
            out[f] = v
    return out


def read_hwrng(sys_hwrng: str = _SYS_HWRNG) -> dict:
    if not os.path.isdir(sys_hwrng):
        return {"available": False}
    out: dict = {"available": True}
    cur = _read(os.path.join(sys_hwrng, "rng_current"))
    out["current"] = cur.strip() if cur else None
    avail = _read(os.path.join(sys_hwrng, "rng_available"))
    out["available_list"] = avail.split() if avail else []
    q = _read_int(os.path.join(sys_hwrng, "rng_quality"))
    if q is not None:
        out["quality"] = q
    return out


_RECIPE_HWRNG = (
    "# No hardware RNG attached to the kernel CRNG. The userspace\n"
    "# fix is rngd from the rng-tools package — it feeds jitter-\n"
    "# entropy + RDRAND (Intel) / TPM-RNG / virtio-rng into the\n"
    "# kernel pool :\n"
    "sudo apt install rng-tools5         # Debian/Ubuntu\n"
    "sudo systemctl enable --now rngd\n"
    "# On a qemu/KVM guest, also expose virtio-rng to the VM via\n"
    "# libvirt :\n"
    "#   <rng model='virtio'>\n"
    "#     <backend model='random'>/dev/urandom</backend>\n"
    "#   </rng>\n"
    "# (Or use a TPM-RNG passthrough.)"
)

_RECIPE_LOW_ENTROPY = (
    "# entropy_avail is unusually low. On modern kernels (5.x+)\n"
    "# this is rare — usually means a pre-5.x kernel or a very\n"
    "# write-heavy /dev/random consumer. Bump rngd urgency :\n"
    "sudo apt install rng-tools5\n"
    "sudo systemctl enable --now rngd\n"
    "# Or, if userspace is consuming /dev/random heavily, switch\n"
    "# it to /dev/urandom (same crypto-grade output, never blocks)."
)


def classify(rand: dict, hw: dict) -> dict:
    hw_available = hw.get("available") if hw else False
    if not rand and not hw_available:
        return {"verdict": "unknown",
                "reason": "/proc/sys/kernel/random unreadable.",
                "recommendation": ""}
    cur = (hw.get("current") or "").strip()
    if hw.get("available") and (not cur or cur == "none"):
        return {"verdict": "no_hwrng",
                "reason": ("No hardware RNG is feeding the CRNG "
                           "(rng_current=" + (cur or "missing") +
                           "). Boot-time TLS / SSH handshakes may "
                           "stall waiting for entropy."),
                "recommendation": _RECIPE_HWRNG}
    entropy = rand.get("entropy_avail")
    pool = rand.get("poolsize")
    if (entropy is not None and pool is not None
            and pool > 0 and entropy < pool // 4):
        return {"verdict": "low_entropy",
                "reason": (f"entropy_avail={entropy} of "
                           f"poolsize={pool} — pool < 25 % full. "
                           f"Mostly a relic on modern kernels but "
                           f"can affect pre-5.x systems."),
                "recommendation": _RECIPE_LOW_ENTROPY}
    return {"verdict": "ok",
            "reason": (f"hwrng={cur or 'kernel default'}, "
                       f"entropy_avail={entropy or '?'}/"
                       f"{pool or '?'} ; CRNG seeded."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    rand = read_random(_PROC_SYS_RANDOM)
    hw = read_hwrng(_SYS_HWRNG)
    verdict = classify(rand, hw)
    return {
        "ok": bool(rand) or hw.get("available", False),
        "random": rand,
        "hwrng": hw,
        "verdict": verdict,
    }
