"""Module thp_audit — Transparent Hugepage auditor (R&D #34.1).

The Linux Transparent Hugepage (THP) subsystem can back anonymous
memory with 2 MiB (huge) pages instead of 4 KiB (base) pages,
reducing TLB pressure substantially for workloads with large
working sets. For LLM inference the KV cache + runtime allocator
arenas are exactly that — but Ubuntu/Debian ship `madvise` as the
default, meaning huge pages only happen when the allocator
explicitly calls madvise(MADV_HUGEPAGE). llama.cpp / vllm /
ComfyUI sometimes do, sometimes don't ; leaving 1-5 % perf on the
table.

Three sysfs files:

  /sys/kernel/mm/transparent_hugepage/enabled
      `always` | `madvise` | `never`     (with the active one bracketed)

  /sys/kernel/mm/transparent_hugepage/defrag
      `always` | `defer` | `defer+madvise` | `madvise` | `never`

  /sys/kernel/mm/transparent_hugepage/khugepaged/scan_sleep_millisecs
      How aggressively the bg compactor runs (default 10000 = 10 s).

Verdicts:
  optimal           enabled=always + defrag ∈ {defer, defer+madvise,
                    madvise} — best for inference, no sync stalls
  madvise_default   enabled=madvise + defrag=madvise — Ubuntu/Debian
                    default, acceptable, can elevate to always for
                    LLM workloads
  aggressive_defrag defrag=always — synchronous compaction during
                    every alloc, can cause TTFT stalls
  disabled          enabled=never — TLB pressure significantly
                    higher on large mmap'd regions
  unknown           sysfs absent

Recipe includes:
  - Runtime sysfs flips (`echo always > .../enabled`)
  - Persistent GRUB cmdline `transparent_hugepage=always`

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "thp_audit"


_THP_ROOT = "/sys/kernel/mm/transparent_hugepage"


_BRACKET_RE = re.compile(r"\[([^\]]+)\]")


def parse_bracketed(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    m = _BRACKET_RE.search(s)
    return m.group(1) if m else None


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_enabled(root: str = _THP_ROOT) -> Optional[str]:
    return parse_bracketed(_read(os.path.join(root, "enabled")))


def read_defrag(root: str = _THP_ROOT) -> Optional[str]:
    return parse_bracketed(_read(os.path.join(root, "defrag")))


def read_khugepaged_scan_ms(root: str = _THP_ROOT) -> Optional[int]:
    s = _read(os.path.join(root, "khugepaged", "scan_sleep_millisecs"))
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return None


_SAFE_DEFRAG = {"defer", "defer+madvise", "madvise", "never"}


_RECIPE_BASE = (
    "# Runtime (sysfs) — takes effect immediately:\n"
    "echo always | sudo tee /sys/kernel/mm/transparent_hugepage/enabled\n"
    "echo defer+madvise | sudo tee /sys/kernel/mm/transparent_hugepage/defrag\n"
    "# Persistent — GRUB cmdline survives reboots:\n"
    "# Edit /etc/default/grub : add `transparent_hugepage=always` to\n"
    "# GRUB_CMDLINE_LINUX_DEFAULT, then `sudo update-grub && reboot`."
)


def classify(enabled: Optional[str], defrag: Optional[str]) -> dict:
    if enabled is None and defrag is None:
        return {"verdict": "unknown",
                "reason": "Cannot read THP enabled/defrag.",
                "recommendation": ""}
    if defrag == "always":
        return {
            "verdict": "aggressive_defrag",
            "reason": (f"defrag=always — every huge-page alloc triggers "
                       f"synchronous memory compaction, which can stall "
                       f"inference threads for 10-100 ms at a time."),
            "recommendation": (
                "# Switch to a safer defrag mode:\n"
                "echo defer+madvise | sudo tee "
                "/sys/kernel/mm/transparent_hugepage/defrag"
            ),
        }
    if enabled == "never":
        return {
            "verdict": "disabled",
            "reason": ("THP is fully disabled — every 4 KiB page of KV "
                       "cache + arena memory hits the TLB individually. "
                       "Measurably worse on long contexts."),
            "recommendation": _RECIPE_BASE,
        }
    if enabled == "always" and (defrag in _SAFE_DEFRAG):
        return {
            "verdict": "optimal",
            "reason": (f"enabled=always + defrag={defrag} — huge pages "
                       f"for all anon allocations, async compaction. "
                       f"Best for LLM inference."),
            "recommendation": "",
        }
    if enabled == "madvise":
        return {
            "verdict": "madvise_default",
            "reason": (f"enabled=madvise (Ubuntu/Debian default) — huge "
                       f"pages only when the allocator explicitly hints. "
                       f"llama.cpp / vllm don't always madvise, leaving "
                       f"1-5 % perf on the table for KV cache."),
            "recommendation": _RECIPE_BASE,
        }
    # always + a defrag mode we didn't enumerate
    return {
        "verdict": "optimal",
        "reason": f"enabled={enabled}, defrag={defrag}.",
        "recommendation": "",
    }


def status(cfg=None) -> dict:
    if not os.path.isdir(_THP_ROOT):
        return {"ok": False, "error": "thp_unavailable",
                "reason": f"{_THP_ROOT} not present (kernel without "
                           f"CONFIG_TRANSPARENT_HUGEPAGE)."}
    enabled = read_enabled(_THP_ROOT)
    defrag = read_defrag(_THP_ROOT)
    scan = read_khugepaged_scan_ms(_THP_ROOT)
    verdict = classify(enabled, defrag)
    return {
        "ok": True,
        "enabled": enabled,
        "defrag": defrag,
        "khugepaged_scan_sleep_ms": scan,
        "verdict": verdict,
    }
