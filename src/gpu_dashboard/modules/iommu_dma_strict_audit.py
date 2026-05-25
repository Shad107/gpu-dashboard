"""Module iommu_dma_strict_audit — IOMMU strict/lazy DMA mode
+ passthrough cmdline auditor (R&D #92.1).

Two existing IOMMU modules :

  * iommu_groups_audit         — group count + passthrough
                                 token presence
  * iommu_reserved_regions_audit — per-group `type` (DMA /
                                 DMA-FQ / identity) +
                                 reserved_regions

Neither inspects the *strict-vs-lazy* DMA mode (the
module-level parameter controlling IOTLB invalidation
timing) or the global `iommu.passthrough=1` cmdline footgun.

Reads :

  /sys/module/iommu/parameters/strict             0 / 1
  /sys/module/intel_iommu/parameters/strict       0 / 1
  /sys/module/amd_iommu/parameters/dma_mode       lazy/strict
                                                  /passthrough
  /sys/kernel/iommu_groups/*/type                 DMA / DMA-FQ
                                                  / identity
  /proc/cmdline                                   iommu.strict=,
                                                  iommu.passthrough=,
                                                  iommu=pt tokens

Verdicts (worst-first) :

  iommu_passthrough_with_pcie_devices  err   cmdline forces
                                             passthrough but
                                             /sys/kernel/iommu_groups
                                             has devices —
                                             DMA isolation
                                             defeated.
  iommu_lazy_mode_active               warn  strict=0 / dma_mode
                                             =lazy — IOTLB
                                             invalidation
                                             deferred ; perf
                                             win but DMA bugs
                                             go silent.
  mixed_group_types                    accent both 'DMA' and
                                             'identity' types
                                             coexist — partial
                                             passthrough drift.
  ok                                   strict mode on every
                                       resolvable surface.
  requires_root                        module params unreadable.
  unknown                              no IOMMU module loaded.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "iommu_dma_strict_audit"

DEFAULT_PROC_CMDLINE = "/proc/cmdline"
DEFAULT_IOMMU_GROUPS = "/sys/kernel/iommu_groups"

_INTEL_STRICT = "/sys/module/intel_iommu/parameters/strict"
_AMD_DMA_MODE = "/sys/module/amd_iommu/parameters/dma_mode"
_GENERIC_STRICT = "/sys/module/iommu/parameters/strict"

# Cap how many groups we sample for mixed-type detection.
_MIXED_TYPE_SAMPLE = 50


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _file_exists(path: str) -> bool:
    return os.path.exists(path)


def read_strict_mode() -> dict:
    """Return {'intel': '0'|'1'|None, 'amd': 'lazy'|'strict'|
    'passthrough'|None, 'generic': '0'|'1'|None,
    'any_unreadable': bool}."""
    out: dict = {"intel": None, "amd": None,
                 "generic": None, "any_unreadable": False}
    for key, path in (("intel", _INTEL_STRICT),
                       ("amd", _AMD_DMA_MODE),
                       ("generic", _GENERIC_STRICT)):
        if _file_exists(path):
            v = _read_text(path)
            if v is None:
                out["any_unreadable"] = True
            else:
                out[key] = v
    return out


def parse_cmdline_tokens(text: str) -> dict:
    """Return {'passthrough': bool, 'strict': '0'|'1'|None}."""
    out = {"passthrough": False, "strict": None}
    if not text:
        return out
    for tok in text.split():
        if tok in ("iommu=pt", "iommu.passthrough=1"):
            out["passthrough"] = True
        elif tok.startswith("iommu.strict="):
            v = tok.split("=", 1)[1].strip()
            if v in ("0", "1"):
                out["strict"] = v
    return out


def sample_group_types(root: str = DEFAULT_IOMMU_GROUPS,
                       limit: int = _MIXED_TYPE_SAMPLE
                       ) -> list:
    """Return up to `limit` group type strings (lower-cased)."""
    if not os.path.isdir(root):
        return []
    try:
        groups = sorted(os.listdir(root))[:limit]
    except OSError:
        return []
    types: list = []
    for gid in groups:
        t = _read_text(os.path.join(root, gid, "type"))
        if t:
            types.append(t.lower())
    return types


def classify(strict: dict, cmdline: dict,
             group_types: list,
             groups_present: bool) -> dict:
    # No IOMMU modules at all → unknown.
    if (strict["intel"] is None
            and strict["amd"] is None
            and strict["generic"] is None
            and not strict["any_unreadable"]
            and not cmdline["passthrough"]
            and not groups_present):
        return {"verdict": "unknown",
                "reason": (
                    "No IOMMU modules under /sys/module/* "
                    "and no /sys/kernel/iommu_groups — IOMMU "
                    "disabled in firmware or kernel built "
                    "without CONFIG_IOMMU_SUPPORT.")}

    # err — passthrough enabled and the IOMMU groups dir has
    # content (devices are exposed to peer DMA).
    if cmdline["passthrough"] and groups_present:
        return {
            "verdict": "iommu_passthrough_with_pcie_devices",
            "reason": (
                "cmdline forces IOMMU passthrough "
                "(iommu=pt / iommu.passthrough=1) and "
                "/sys/kernel/iommu_groups has device entries "
                "— PCIe peer DMA is unconstrained by the "
                "IOMMU.")}

    # warn — lazy mode active on any of the surfaces
    lazy_signals: list = []
    if strict["intel"] == "0":
        lazy_signals.append("intel_iommu.strict=0")
    if strict["generic"] == "0":
        lazy_signals.append("iommu.strict=0 (module)")
    if strict["amd"] == "lazy":
        lazy_signals.append("amd_iommu.dma_mode=lazy")
    if cmdline["strict"] == "0":
        lazy_signals.append("iommu.strict=0 (cmdline)")
    if lazy_signals:
        return {
            "verdict": "iommu_lazy_mode_active",
            "reason": (
                f"Lazy IOTLB invalidation: {lazy_signals}. "
                "Perf win, but DMA bugs go silent. Re-enable "
                "strict mode for production servers."),
            "signals": lazy_signals}

    # requires_root — module params unreadable AND no clarity
    # from cmdline or groups.
    if (strict["any_unreadable"]
            and not cmdline["passthrough"]
            and not group_types):
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/module/*_iommu/parameters/* "
                    "unreadable — re-run as root.")}

    # accent — both 'identity' and 'DMA' / 'DMA-FQ' types in
    # the sampled groups.
    has_dma = any(t.startswith("dma") for t in group_types)
    has_identity = "identity" in group_types
    if has_dma and has_identity:
        return {
            "verdict": "mixed_group_types",
            "reason": (
                "Both 'DMA' and 'identity' IOMMU group types "
                "coexist — partial passthrough drift. Audit "
                "the identity groups (likely VFIO-bound "
                "devices left over from a prior session)."),
            "types_seen": sorted(set(group_types))}

    return {"verdict": "ok",
            "reason": (
                "IOMMU strict mode active ; no passthrough "
                f"; types: {sorted(set(group_types)) or 'n/a'}.")}


def status(config: Optional[dict] = None,
           proc_cmdline: str = DEFAULT_PROC_CMDLINE,
           iommu_groups: str = DEFAULT_IOMMU_GROUPS) -> dict:
    strict = read_strict_mode()
    cmdline = parse_cmdline_tokens(
        _read_text(proc_cmdline) or "")
    types = sample_group_types(iommu_groups)
    groups_present = (os.path.isdir(iommu_groups)
                      and any(True for _ in (types or [None])
                              if _ is not None and len(types)))
    # Simpler: any non-empty listing means groups_present.
    groups_present = bool(types)
    verdict = classify(strict, cmdline, types, groups_present)
    return {
        "ok": verdict["verdict"] == "ok",
        "strict_intel": strict["intel"],
        "strict_amd": strict["amd"],
        "strict_generic": strict["generic"],
        "cmdline_passthrough": cmdline["passthrough"],
        "cmdline_strict": cmdline["strict"],
        "group_type_sample": sorted(set(types)),
        "verdict": verdict,
    }
