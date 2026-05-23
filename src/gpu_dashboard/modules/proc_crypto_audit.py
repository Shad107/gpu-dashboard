"""Module proc_crypto_audit — /proc/crypto + FIPS + AES-NI (R&D #56.3).

Parses /proc/crypto (the kernel crypto API inventory) and checks
for the foot-guns that show up on LLM hosts using LUKS-encrypted
weight caches, encrypted swap, or signed boot chains.

Why this matters :

* FIPS mode silently turned on by a stray `fips=1` cmdline cripples
  LUKS unlock latency and disables aesni_intel — Hugging Face
  ~/.cache decrypt becomes a measurable bottleneck on first model
  load after boot.
* Microcode rollback can cause the kernel to pick `aes-generic`
  over `aes-aesni` (priority inversion). LUKS XTS throughput drops
  4-8× ; the user only sees "disk slow" until a perf-stat shows
  the missing AESNI cycles.
* selftest_failed on any cipher means the kernel has masked that
  algorithm — software fallbacks pick up.

Reads :
  /proc/crypto                           full inventory
  /proc/sys/crypto/fips_enabled          0 / 1
  /sys/devices/system/cpu/cpu0/flags     for AES-NI capability hint
                                          (informational only)

Verdicts (priority-ordered) :
  fips_mode_on                FIPS mode on — LUKS / AES paths slow
                              and userland workloads constrained.
  selftest_failed_entry       ≥1 cipher reports selftest != passed.
  aesni_missing_but_aes_used  /proc/cpuinfo claims AES-NI but no
                              aes-aesni entry in /proc/crypto.
  generic_only_aes            only aes-generic registered, no
                              hardware-accelerated path.
  ok                          ≥1 aes-aesni / aes-ce / aes-ni-like
                              entry present, no failures.
  unknown                     /proc/crypto absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "proc_crypto_audit"


_PROC_CRYPTO = "/proc/crypto"
_PROC_FIPS = "/proc/sys/crypto/fips_enabled"
_PROC_CPUINFO = "/proc/cpuinfo"


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


def parse_crypto(text: Optional[str]) -> List[dict]:
    """Parse /proc/crypto into a list of {name, driver, module,
    priority, refcnt, selftest, type, ...} dicts."""
    if not text:
        return []
    entries: List[dict] = []
    cur: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.rstrip()
        if not line:
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        cur[k.strip()] = v.strip()
    if cur:
        entries.append(cur)
    return entries


def has_aesni_cpu(proc_cpuinfo: str = _PROC_CPUINFO) -> bool:
    text = _read(proc_cpuinfo)
    if not text:
        return False
    # x86 CPU flag is 'aes', ARM CPUs publish 'aes' or 'pmull' in
    # /proc/cpuinfo features. We check the first 'flags' or
    # 'Features' line.
    for line in text.splitlines():
        if line.startswith("flags") or line.startswith("Features"):
            tokens = line.split(":", 1)[-1].split()
            if "aes" in tokens or "pmull" in tokens:
                return True
            return False
    return False


def classify(entries: List[dict], fips_enabled: Optional[int],
              cpu_has_aes: bool) -> dict:
    if not entries:
        return {"verdict": "unknown",
                "reason": "/proc/crypto is absent or unparseable.",
                "recommendation": ""}

    # 1) fips_mode_on
    if fips_enabled == 1:
        return {"verdict": "fips_mode_on",
                "reason": ("/proc/sys/crypto/fips_enabled = 1. "
                          "AES / LUKS paths are constrained to "
                          "FIPS-approved subset, often slower."),
                "recommendation": _recipe_fips_off()}

    # 2) selftest_failed_entry
    failed = [e for e in entries
                if e.get("selftest", "passed") != "passed"]
    if failed:
        sample = ", ".join(f"{e.get('name','?')} ({e.get('driver','?')})"
                              for e in failed[:3])
        return {"verdict": "selftest_failed_entry",
                "reason": (f"{len(failed)} cipher(s) failed "
                          f"selftest : {sample}."),
                "recommendation": _recipe_selftest_fail()}

    aes_entries = [e for e in entries
                       if e.get("name") == "aes"]
    aesni_present = any(
        ("aesni" in (e.get("driver", "") or "")
         or "aes-ni" in (e.get("driver", "") or "")
         or "aes-ce" in (e.get("driver", "") or ""))
        for e in aes_entries)

    # 3) aesni_missing_but_aes_used — CPU claims AES-NI but kernel
    #    didn't register the driver.
    if cpu_has_aes and aes_entries and not aesni_present:
        return {"verdict": "aesni_missing_but_aes_used",
                "reason": ("CPU advertises AES-NI but /proc/crypto "
                          "has no aes-aesni / aes-ce entry. LUKS "
                          "throughput will be 4-8× lower."),
                "recommendation": _recipe_load_aesni()}

    # 4) generic_only_aes — no hardware-accel path at all on a
    #    system that doesn't advertise the capability either.
    if aes_entries and not aesni_present and not cpu_has_aes:
        return {"verdict": "generic_only_aes",
                "reason": ("Only aes-generic registered ; CPU "
                          "doesn't advertise AES-NI. Workloads "
                          "involving disk encryption stay slow."),
                "recommendation": ""}

    return {"verdict": "ok",
            "reason": (f"{len(entries)} crypto entries, AES-NI "
                      f"path present, no selftest failures."),
            "recommendation": ""}


def status(config=None,
            proc_crypto: str = _PROC_CRYPTO,
            proc_fips: str = _PROC_FIPS,
            proc_cpuinfo: str = _PROC_CPUINFO) -> dict:
    entries = parse_crypto(_read(proc_crypto))
    fips_enabled = _read_int(proc_fips)
    cpu_has_aes = has_aesni_cpu(proc_cpuinfo)
    ok = bool(entries)
    verdict = classify(entries, fips_enabled, cpu_has_aes)
    # Build a compact summary by cipher name
    name_hist: Dict[str, int] = {}
    for e in entries:
        n = e.get("name") or "?"
        name_hist[n] = name_hist.get(n, 0) + 1
    return {"ok": ok,
              "entry_count": len(entries),
              "name_count": len(name_hist),
              "name_histogram": name_hist,
              "fips_enabled": fips_enabled,
              "cpu_has_aes_flag": cpu_has_aes,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_fips_off() -> str:
    return ("# Remove the fips=1 kernel cmdline argument :\n"
            "sudo sed -i 's/ fips=1//' /etc/default/grub\n"
            "sudo update-grub  # Debian/Ubuntu\n"
            "# Then reboot. Verify : cat /proc/sys/crypto/fips_enabled\n"
            "# (Some hardened distros also require dracut --force\n"
            "# before reboot for FIPS module removal.)\n")


def _recipe_selftest_fail() -> str:
    return ("# Inspect the failing entries :\n"
            "grep -B 1 -A 5 'selftest.*: \\(failed\\|fail\\)' /proc/crypto\n"
            "# Most often : a custom crypto module rebuild that\n"
            "# didn't link the right kernel API. Try unloading the\n"
            "# offending module and rebooting :\n"
            "lsmod | grep -E 'crypto|aes'\n")


def _recipe_load_aesni() -> str:
    return ("# Force-load the AES-NI driver :\n"
            "sudo modprobe aesni_intel  # Intel / AMD x86_64\n"
            "# Verify /proc/crypto picks it up :\n"
            "grep -A 1 '^name.*: aes$' /proc/crypto\n"
            "# If it still says aes-generic, a microcode-rollback\n"
            "# may have disabled AES-NI in the CPU. Re-install\n"
            "# intel-microcode / amd64-microcode and reboot.\n")
