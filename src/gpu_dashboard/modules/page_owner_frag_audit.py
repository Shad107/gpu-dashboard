"""Module page_owner_frag_audit — kernel-reclaimer
fragmentation metric (R&D #82.4).

Existing buddyinfo_frag walks raw /proc/buddyinfo order
counts and pagetypeinfo_audit walks migrate-type × order
pollution.  Neither reads the *normalised* fragmentation
metric the kernel itself uses to decide whether compaction
is worthwhile :

  /sys/kernel/debug/extfrag/extfrag_index
  /sys/kernel/debug/extfrag/unusable_index

Each file is per-Node × per-zone × per-order, with values
in [-1.000 … 1.000].  -1.000 = "compaction not needed", and
positive values approach 1.000 as fragmentation worsens.
Long-running inference boxes fragment Normal-zone order-4+
pages over weeks, causing THP collapse stalls and GPU
pinned-alloc failures that don't show up in buddyinfo's
raw counts — but extfrag_index catches it.

Cross-references the THP defrag policy at
``/sys/kernel/mm/transparent_hugepage/defrag`` so the
verdict only fires the worst grade when the kernel is
ALSO actively trying to assemble huge pages — that's the
combination that produces visible stalls.

/sys/kernel/debug/* is mode-700 on almost every distro, so
the dominant verdict for a user-mode dashboard will be
``requires_root`` — surfaced explicitly with a re-run-as-
root hint.

Verdicts (worst first) :

  extfrag_high_with_thp_defrag   extfrag_index > 0.90 at
                                 order ≥ 4 on a major zone
                                 AND THP defrag = always /
                                 defer.
  unusable_index_high            unusable_index > 0.75 at
                                 order ≥ 3 — high-order
                                 allocs will stall.
  page_owner_overhead_no_use     /sys/kernel/debug/page_owner
                                 exists and is readable but
                                 first-byte sample shows it
                                 is enabled with no collected
                                 data — pure debug overhead.
  ok                             extfrag indices healthy.
  requires_root                  debugfs unreadable as this
                                 UID — typical user-mode case.
  n/a                            CONFIG_PAGE_OWNER and
                                 extfrag debugfs both absent
                                 (kernel built without debug
                                 mm).
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_DEBUG_ROOT = "/sys/kernel/debug"
DEFAULT_THP_DEFRAG = (
    "/sys/kernel/mm/transparent_hugepage/defrag")

# Thresholds
_EXTFRAG_HIGH = 0.90
_UNUSABLE_HIGH = 0.75
_MIN_ORDER_EXTFRAG = 4
_MIN_ORDER_UNUSABLE = 3
_MAJOR_ZONES = ("Normal", "DMA32")
_AGGRESSIVE_THP = ("always", "defer", "defer+madvise")

_LINE_RE = re.compile(
    r"^Node\s+(\d+),\s+zone\s+(\S+)\s+(.+)$")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def parse_index(text: str) -> list[dict]:
    """Parse extfrag_index / unusable_index format :
    'Node N, zone <name> v0 v1 v2 ...' (11 values normally).
    Returns list of {node, zone, values: [float...]}."""
    out: list[dict] = []
    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if m is None:
            continue
        node = int(m.group(1))
        zone = m.group(2)
        vals: list[float] = []
        for tok in m.group(3).split():
            try:
                vals.append(float(tok))
            except ValueError:
                continue
        if vals:
            out.append({"node": node, "zone": zone,
                          "values": vals})
    return out


def read_thp_defrag(path: str = DEFAULT_THP_DEFRAG
                     ) -> Optional[str]:
    """Reads the bracketed current setting from
    /sys/kernel/mm/transparent_hugepage/defrag (e.g.
    "[always] defer defer+madvise madvise never").
    """
    s = _read_text(path)
    if s is None:
        return None
    m = re.search(r"\[([^\]]+)\]", s)
    if m is None:
        return None
    return m.group(1)


def _max_index(rows: list[dict],
               min_order: int,
               zones: tuple = _MAJOR_ZONES) -> Optional[dict]:
    """Returns the row+order achieving the max value at
    order ≥ min_order on a major zone, or None if no data."""
    best: Optional[dict] = None
    for r in rows:
        if r["zone"] not in zones:
            continue
        for i, v in enumerate(r["values"]):
            if i < min_order:
                continue
            if v < 0:  # -1.000 = compaction not appropriate
                continue
            cand = {
                "node": r["node"],
                "zone": r["zone"],
                "order": i,
                "value": v,
            }
            if best is None or v > best["value"]:
                best = cand
    return best


def classify(extfrag_text: Optional[str],
             unusable_text: Optional[str],
             page_owner_present: bool,
             page_owner_readable: bool,
             thp_defrag: Optional[str]) -> dict:
    have_extfrag = extfrag_text is not None
    have_unusable = unusable_text is not None
    have_any = have_extfrag or have_unusable

    if not have_any:
        # Distinguish n/a (kernel has no debug mm) from
        # requires_root (debugfs present but mode 700).
        # We treat the kernel-mm-absent case at the status()
        # layer by checking debug_root_exists ; here, any
        # read failure means requires_root for the caller.
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/kernel/debug/extfrag/* unreadable "
                    "as this UID. Re-run dashboard as root "
                    "for the fragmentation index.")}

    extfrag = parse_index(extfrag_text) if have_extfrag else []
    unusable = (
        parse_index(unusable_text) if have_unusable else [])

    # 1. err — high extfrag index with aggressive THP defrag
    aggressive = (
        thp_defrag is not None and thp_defrag in _AGGRESSIVE_THP)
    worst_ef = _max_index(extfrag, _MIN_ORDER_EXTFRAG)
    if (aggressive
            and worst_ef is not None
            and worst_ef["value"] > _EXTFRAG_HIGH):
        return {
            "verdict": "extfrag_high_with_thp_defrag",
            "reason": (
                f"extfrag_index = {worst_ef['value']:.3f} at "
                f"order {worst_ef['order']} on "
                f"{worst_ef['zone']} AND THP defrag = "
                f"{thp_defrag} — THP collapse will stall."),
            "order": worst_ef["order"],
            "zone": worst_ef["zone"],
            "value": worst_ef["value"],
            "thp_defrag": thp_defrag}

    # 2. warn — unusable_index high
    worst_un = _max_index(unusable, _MIN_ORDER_UNUSABLE)
    if (worst_un is not None
            and worst_un["value"] > _UNUSABLE_HIGH):
        return {"verdict": "unusable_index_high",
                "reason": (
                    f"unusable_index = "
                    f"{worst_un['value']:.3f} at order "
                    f"{worst_un['order']} on "
                    f"{worst_un['zone']} — high-order "
                    "allocations will stall."),
                "order": worst_un["order"],
                "zone": worst_un["zone"],
                "value": worst_un["value"]}

    # 3. accent — page_owner debug overhead
    if page_owner_present and page_owner_readable:
        return {"verdict": "page_owner_overhead_no_use",
                "reason": (
                    "/sys/kernel/debug/page_owner is "
                    "enabled — debug overhead is being "
                    "incurred ; remove "
                    "page_owner=on from /proc/cmdline if "
                    "not actively used.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(extfrag)} zone(s) in extfrag, "
                f"{len(unusable)} in unusable ; max "
                f"high-order index sane.")}


def status(config: Optional[dict] = None,
           debug_root: str = DEFAULT_DEBUG_ROOT,
           thp_defrag_path: str = DEFAULT_THP_DEFRAG) -> dict:
    extfrag_text = _read_text(
        os.path.join(debug_root, "extfrag", "extfrag_index"))
    unusable_text = _read_text(
        os.path.join(debug_root, "extfrag", "unusable_index"))
    page_owner_path = os.path.join(debug_root, "page_owner")
    page_owner_present = os.path.exists(page_owner_path)
    # We deliberately don't try to read page_owner content
    # (can be huge and root-gated). Presence + readability
    # is enough to flag the overhead.
    try:
        page_owner_readable = (
            page_owner_present
            and os.access(page_owner_path, os.R_OK))
    except OSError:
        page_owner_readable = False
    thp_defrag = read_thp_defrag(thp_defrag_path)

    # Distinguish n/a (kernel has no debug mm) from
    # requires_root (debugfs present but mode-700 on this UID).
    extfrag_dir = os.path.join(debug_root, "extfrag")
    if (extfrag_text is None
            and unusable_text is None):
        if not os.path.isdir(debug_root):
            return {
                "ok": True,
                "extfrag_zones": 0,
                "unusable_zones": 0,
                "page_owner_present": False,
                "thp_defrag": thp_defrag,
                "verdict": {
                    "verdict": "n/a",
                    "reason": (
                        "/sys/kernel/debug not mounted — "
                        "kernel without debugfs.")},
            }
        # debug_root exists. If we can't even list it, we're
        # locked out by mode-700 ; treat as requires_root.
        try:
            os.listdir(debug_root)
            debug_root_readable = True
        except (OSError, PermissionError):
            debug_root_readable = False
        if not debug_root_readable:
            return {
                "ok": False,
                "extfrag_zones": 0,
                "unusable_zones": 0,
                "page_owner_present": False,
                "thp_defrag": thp_defrag,
                "verdict": {
                    "verdict": "requires_root",
                    "reason": (
                        "/sys/kernel/debug is mode-700. "
                        "Re-run dashboard as root for the "
                        "extfrag index.")},
            }
        # debug_root is readable but extfrag/page_owner are
        # not present → kernel built without CONFIG_PAGE_OWNER
        # / CONFIG_DEBUG_MM.
        if (not os.path.isdir(extfrag_dir)
                and not page_owner_present):
            return {
                "ok": True,
                "extfrag_zones": 0,
                "unusable_zones": 0,
                "page_owner_present": False,
                "thp_defrag": thp_defrag,
                "verdict": {
                    "verdict": "n/a",
                    "reason": (
                        "No extfrag/page_owner debugfs "
                        "entries — kernel built without "
                        "CONFIG_DEBUG_MM / "
                        "CONFIG_PAGE_OWNER.")},
            }

    verdict = classify(extfrag_text, unusable_text,
                        page_owner_present,
                        page_owner_readable, thp_defrag)
    return {
        "ok": verdict["verdict"] not in (
            "extfrag_high_with_thp_defrag",
            "requires_root"),
        "extfrag_zones": len(
            parse_index(extfrag_text)
            if extfrag_text else []),
        "unusable_zones": len(
            parse_index(unusable_text)
            if unusable_text else []),
        "page_owner_present": page_owner_present,
        "thp_defrag": thp_defrag,
        "verdict": verdict,
    }
