"""Module dma_buf_bufinfo_audit — per-exporter dma-buf
inventory + RAM ratio (R&D #91.2).

Three modules touch the dma-buf surface :

  * dma_heap_audit  — /dev/dma_heap/* allocator nodes
  * dma_audit       — ftrace dma tracepoints
  * dri_debugfs_audit — /sys/kernel/debug/dri/* framebuffers

None read the per-buffer inventory at /sys/kernel/debug/
dma_buf/bufinfo, which is the only place the kernel exposes
who EXPORTED each shared GPU buffer and how big it is. This
is the signal you need to attribute "4 GiB of GPU-shared
memory mysteriously pinned with no nvidia-smi process".

Reads :

  /sys/kernel/debug/dma_buf/bufinfo    one row per buffer ;
                                       columns include
                                       exp_name + size.
  /proc/meminfo                        MemTotal cross-ref.

Verdicts (worst-first) :

  exporter_dominates       err   one exporter (drm / amdgpu /
                                 i915 / nvidia / system_heap)
                                 holds > 50 % of MemTotal —
                                 likely leaked WebGL /
                                 compositor buffer.
  top3_high_footprint      warn  top 3 exporters combined
                                 hold > 25 % MemTotal.
  dmabuf_footprint_high    accent total dma-buf footprint
                                  > 10 % MemTotal.
  ok                       footprint < 10 % MemTotal.
  requires_root            /sys/kernel/debug/dma_buf/bufinfo
                           unreadable (debugfs mode-700).
  unknown                  bufinfo file absent (kernel built
                           without CONFIG_DMA_SHARED_BUFFER
                           or CONFIG_DEBUG_FS).

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional

NAME = "dma_buf_bufinfo_audit"

DEFAULT_BUFINFO = "/sys/kernel/debug/dma_buf/bufinfo"
DEFAULT_MEMINFO = "/proc/meminfo"

_DOMINATES_THRESHOLD = 0.50
_TOP3_THRESHOLD = 0.25
_HIGH_FOOTPRINT_THRESHOLD = 0.10

_MEMTOTAL_RE = re.compile(r"^MemTotal:\s*(\d+)\s*kB",
                          re.MULTILINE)

# Bufinfo row formats vary across kernel versions, but the
# usual pattern is whitespace-separated tokens with the first
# column being the size in hex bytes (often '0x000XXXXX') or
# decimal, and an exporter token. Skip lines with no leading
# digit token.
_SIZE_RE = re.compile(r"^\s*(0x[0-9a-fA-F]+|\d+)\b")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except PermissionError:
        return None
    except OSError:
        return None


def _file_present(path: str) -> bool:
    return os.path.isfile(path)


def parse_meminfo_total_bytes(text: str) -> Optional[int]:
    if not text:
        return None
    m = _MEMTOTAL_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1)) * 1024
    except ValueError:
        return None


def parse_bufinfo(text: str) -> dict:
    """Parse /sys/kernel/debug/dma_buf/bufinfo.

    Returns dict mapping exporter name → total bytes.
    Tolerates header/banner rows and 'Total:' summary lines.
    """
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        if not line.strip():
            continue
        stripped = line.strip()
        # Skip the 'Total:' summary and section headers.
        low = stripped.lower()
        if (low.startswith("total")
                or "buffer object" in low
                or "attached device" in low
                or low.startswith("size")):
            continue
        m = _SIZE_RE.match(line)
        if not m:
            continue
        try:
            size_str = m.group(1)
            if size_str.lower().startswith("0x"):
                size = int(size_str, 16)
            else:
                size = int(size_str)
        except ValueError:
            continue
        # Find the exporter — heuristic: shortest alphanumeric
        # token of >=3 chars from token index 4..6. Common
        # values: drm, amdgpu, i915, nouveau, nvidia,
        # system_heap, cma_heap.
        tokens = line.split()
        exporter = ""
        if len(tokens) >= 5:
            for idx in (4, 5, 6, 3):
                if idx < len(tokens):
                    tok = tokens[idx]
                    # Accept exporters like 'drm', 'i915',
                    # 'amdgpu', 'system_heap', 'nouveau'.
                    # Must start with a letter and contain
                    # only [A-Za-z0-9_].
                    if (tok and tok[0].isalpha()
                            and tok.replace(
                                "_", "").isalnum()):
                        exporter = tok
                        break
        if not exporter:
            exporter = "unknown"
        out[exporter] = out.get(exporter, 0) + size
    return out


def classify(present: bool, readable: bool,
             by_exporter: dict,
             mem_total: Optional[int]) -> dict:
    if not present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/kernel/debug/dma_buf/bufinfo absent — "
                    "kernel built without "
                    "CONFIG_DMA_SHARED_BUFFER or debugfs not "
                    "mounted.")}
    if not readable:
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/kernel/debug/dma_buf/bufinfo "
                    "unreadable (debugfs mode-700) — re-run "
                    "as root.")}

    if not by_exporter or not mem_total:
        return {"verdict": "ok",
                "reason": (
                    "No dma-buf buffers exported "
                    "(or MemTotal unreadable).")}

    total = sum(by_exporter.values())
    sorted_by_size = sorted(
        by_exporter.items(), key=lambda kv: -kv[1])
    biggest_name, biggest_size = sorted_by_size[0]

    if biggest_size > _DOMINATES_THRESHOLD * mem_total:
        return {
            "verdict": "exporter_dominates",
            "reason": (
                f"Exporter '{biggest_name}' holds "
                f"{biggest_size / 2**30:.2f} GiB "
                f"({100 * biggest_size / mem_total:.0f}% of "
                "RAM) — likely a leaked WebGL/Wayland "
                "buffer."),
            "exporter": biggest_name,
            "bytes": biggest_size,
        }

    top3 = sum(s for _, s in sorted_by_size[:3])
    if top3 > _TOP3_THRESHOLD * mem_total:
        names = [n for n, _ in sorted_by_size[:3]]
        return {
            "verdict": "top3_high_footprint",
            "reason": (
                f"Top 3 exporters {names} combined hold "
                f"{top3 / 2**30:.2f} GiB "
                f"({100 * top3 / mem_total:.0f}% of RAM)."),
            "top3": names,
        }

    if total > _HIGH_FOOTPRINT_THRESHOLD * mem_total:
        return {
            "verdict": "dmabuf_footprint_high",
            "reason": (
                f"Total dma-buf footprint = "
                f"{total / 2**30:.2f} GiB "
                f"({100 * total / mem_total:.0f}% of RAM) "
                "across all exporters — watch growth."),
            "total_bytes": total,
        }

    return {"verdict": "ok",
            "reason": (
                f"{len(by_exporter)} exporter(s), total "
                f"{total / 2**30:.2f} GiB "
                f"({100 * total / max(mem_total, 1):.1f}% of "
                "RAM) — coherent.")}


def status(config: Optional[dict] = None,
           bufinfo_path: str = DEFAULT_BUFINFO,
           meminfo_path: str = DEFAULT_MEMINFO) -> dict:
    present = _file_present(bufinfo_path)
    text = _read_text(bufinfo_path) if present else None
    readable = text is not None
    by_exporter = parse_bufinfo(text or "")
    mem_total = parse_meminfo_total_bytes(
        _read_text(meminfo_path) or "")
    verdict = classify(present, readable, by_exporter,
                       mem_total)
    total = sum(by_exporter.values())
    return {
        "ok": verdict["verdict"] == "ok",
        "exporter_count": len(by_exporter),
        "total_bytes": total,
        "mem_total": mem_total,
        "verdict": verdict,
    }
