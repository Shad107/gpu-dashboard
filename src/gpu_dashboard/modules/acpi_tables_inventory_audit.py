"""Module acpi_tables_inventory_audit — ACPI table set sanity
(R&D #109.3, strong pick).

/sys/firmware/acpi/tables/ exposes each ACPI table as a file.
The standard set + sizes tells us a lot about firmware health:

  - SRAT missing on multi-node host → kernel uses broken
    topology heuristic
  - DSDT > 256 KiB → vendor BIOS bloat, correlates with broken
    methods
  - > 20 SSDTs → extreme firmware fragmentation
  - HMAT absent on tiered-memory setups → CXL/PMEM placement
    uses fallback heuristic

No existing module enumerates this directory. acpi_audit covers
platform_profile / pm_profile / interrupts / wakeup ; cooling_devices
and numa_hmat_access_audit reference `ls .../tables/` in shell
snippets only.

Reads :

  /sys/firmware/acpi/tables/                     directory list
  /sys/devices/system/node/online                multi-node hint

Verdicts (worst-first) :

  missing_srat_with_multinode    warn    multi-node host but no
                                         SRAT. Kernel topology
                                         heuristics fire.
  huge_dsdt                      accent  DSDT > 256 KiB ; vendor
                                         BIOS bloat.
  excess_ssdts                   accent  > 20 SSDTs ; extreme
                                         firmware fragmentation.
  ok                                     standard set.
  requires_root                          /sys/firmware/acpi/tables
                                         unreadable.
  unknown                                ACPI tables dir absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "acpi_tables_inventory_audit"

DEFAULT_TABLES = "/sys/firmware/acpi/tables"
DEFAULT_NODE_ONLINE = "/sys/devices/system/node/online"

_DSDT_HUGE_BYTES = 256 * 1024
_SSDT_MAX = 20


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def is_multi_node(online_text: Optional[str]) -> bool:
    if not online_text:
        return False
    s = online_text.strip()
    return "-" in s or "," in s


def walk_tables(tables_dir: str = DEFAULT_TABLES) -> dict:
    """Return {names: set, sizes: {name: bytes}}."""
    out: dict = {"names": set(), "sizes": {}}
    if not os.path.isdir(tables_dir):
        return out
    try:
        entries = os.listdir(tables_dir)
    except OSError:
        return out
    for ent in entries:
        path = os.path.join(tables_dir, ent)
        if not os.path.isfile(path):
            continue
        out["names"].add(ent)
        try:
            out["sizes"][ent] = os.path.getsize(path)
        except OSError:
            continue
    return out


def count_ssdts(names: set) -> int:
    """Count tables matching SSDT or SSDT2..N."""
    return sum(1 for n in names if n.startswith("SSDT"))


def classify(tables_present: bool,
             tables_readable: bool,
             tables: dict,
             multi_node: bool) -> dict:
    if not tables_present:
        return {"verdict": "unknown",
                "reason": (
                    "/sys/firmware/acpi/tables absent — "
                    "kernel built without ACPI table "
                    "exposure.")}
    if not tables_readable:
        return {"verdict": "requires_root",
                "reason": (
                    "ACPI tables dir unreadable — re-run "
                    "as root.")}

    names = tables.get("names", set())
    sizes = tables.get("sizes", {})

    # warn — multi-node without SRAT
    if multi_node and "SRAT" not in names:
        return {
            "verdict": "missing_srat_with_multinode",
            "reason": (
                "Multi-node NUMA host but no SRAT table "
                "— kernel topology heuristics fire. "
                "Update firmware to expose SRAT.")}

    # accent — huge DSDT
    dsdt_size = sizes.get("DSDT", 0)
    if dsdt_size > _DSDT_HUGE_BYTES:
        return {
            "verdict": "huge_dsdt",
            "reason": (
                f"DSDT = {dsdt_size} bytes "
                f"(> {_DSDT_HUGE_BYTES}). Vendor BIOS "
                "bloat — often correlates with broken "
                "ACPI methods.")}

    # accent — excess SSDTs
    ssdt_count = count_ssdts(names)
    if ssdt_count > _SSDT_MAX:
        return {
            "verdict": "excess_ssdts",
            "reason": (
                f"{ssdt_count} SSDTs (> {_SSDT_MAX}). "
                "Extreme firmware fragmentation ; OEM "
                "pasted vendor blobs.")}

    return {"verdict": "ok",
            "reason": (
                f"{len(names)} table(s) ; DSDT={dsdt_size} "
                f"B ; SSDT count={ssdt_count}. Standard.")}


def status(config: Optional[dict] = None,
           tables_dir: str = DEFAULT_TABLES,
           node_online: str = DEFAULT_NODE_ONLINE) -> dict:
    tables_present = os.path.isdir(tables_dir)
    tables_readable = (
        tables_present and os.access(tables_dir, os.R_OK))
    tables = walk_tables(tables_dir) if tables_readable else {}
    multi = is_multi_node(_read_text(node_online))
    verdict = classify(tables_present, tables_readable,
                       tables, multi)
    return {
        "ok": verdict["verdict"] == "ok",
        "table_count": len(tables.get("names", set())),
        "dsdt_size": tables.get("sizes", {}).get("DSDT"),
        "ssdt_count": count_ssdts(
            tables.get("names", set())),
        "has_srat": "SRAT" in tables.get("names", set()),
        "has_hmat": "HMAT" in tables.get("names", set()),
        "multi_node": multi,
        "verdict": verdict,
    }
