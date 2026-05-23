"""Module cxl_dax_memory_audit — CXL + DAX + nvdimm region
audit (R&D #70.2).

Joins three closely-related memory-tier surfaces :

  /sys/bus/cxl/devices/{memN, decoderN, portN, rootN}
      Compute Express Link devices — CXL.mem capacity modules,
      decoders (translate host addresses to device addresses)
      and root / switch / endpoint ports.

  /sys/class/dax/dax<N>/{size, target_node, align}
      Device-DAX — direct-mapping access to persistent / volatile
      memory regions (DAX hat over nvdimm or CXL).

  /sys/bus/nd/devices/region<N>/*
      libnvdimm regions (legacy nvdimm-P / persistent memory
      regions backing the DAX layer).

Why on a homelab :

* CXL.mem is appearing on consumer-adjacent boards (Xeon-W,
  Sapphire/Granite Rapids workstation SKUs). A decoder stuck
  in "error" state silently denies the capacity to the kernel
  but doesn't surface anywhere obvious.
* DAX devices with size=0 mean a misconfigured nvdimm region
  binding ; user typically discovers only when daxctl reports
  "no DAX devices."
* PMEM hardware present but no DAX mapping = NVDIMM cost
  paid for no benefit.

Verdicts (priority order) :
  cxl_decoder_error           ≥1 CXL decoder reports state with
                                "error" / "failed" / "disabled".
  dax_size_zero_misconfigured ≥1 /sys/class/dax/dax* has
                                size=0 (region bind broken).
  target_node_unbound         ≥1 dax target_node == -1 (no
                                NUMA placement).
  pmem_present_unused         ≥1 /sys/bus/nd/devices/region*
                                exists AND no /sys/class/dax
                                entry references it.
  ok                          all healthy.
  unknown                     none of the three surfaces
                                present.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "cxl_dax_memory_audit"


_SYS_CXL = "/sys/bus/cxl/devices"
_SYS_DAX = "/sys/class/dax"
_SYS_ND = "/sys/bus/nd/devices"

_ERROR_RE = re.compile(r"\b(error|failed|disabled|broken)\b",
                            re.IGNORECASE)
_DECODER_RE = re.compile(r"^decoder\d+(?:\.\d+)?$")
_MEM_RE = re.compile(r"^mem\d+$")
_PORT_RE = re.compile(r"^(port|root)\d+$")


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
        return int(t, 0)
    except ValueError:
        return None


def list_cxl_devices(sys_cxl: str = _SYS_CXL) -> dict:
    out = {"decoders": [], "mems": [], "ports": []}
    if not os.path.isdir(sys_cxl):
        return out
    try:
        names = sorted(os.listdir(sys_cxl))
    except OSError:
        return out
    for n in names:
        d = os.path.join(sys_cxl, n)
        if not os.path.isdir(d):
            continue
        if _DECODER_RE.match(n):
            out["decoders"].append({
                "id": n,
                "state": _read(os.path.join(d, "mode")),
                "size": _read(os.path.join(d, "size")),
            })
        elif _MEM_RE.match(n):
            out["mems"].append({
                "id": n,
                "ram_size": _read_int(os.path.join(
                    d, "ram", "size")),
                "pmem_size": _read_int(os.path.join(
                    d, "pmem", "size")),
            })
        elif _PORT_RE.match(n):
            out["ports"].append({
                "id": n,
                "uport": _read(os.path.join(d, "uport")),
            })
    return out


def list_dax_devices(sys_dax: str = _SYS_DAX) -> List[dict]:
    if not os.path.isdir(sys_dax):
        return []
    try:
        names = sorted(os.listdir(sys_dax))
    except OSError:
        return []
    out: List[dict] = []
    for n in names:
        d = os.path.join(sys_dax, n)
        if not os.path.isdir(d):
            continue
        out.append({
            "id": n,
            "size": _read_int(os.path.join(d, "size")),
            "target_node": _read_int(os.path.join(
                d, "target_node")),
            "align": _read_int(os.path.join(d, "align")),
        })
    return out


def list_nd_regions(sys_nd: str = _SYS_ND) -> List[dict]:
    if not os.path.isdir(sys_nd):
        return []
    try:
        names = sorted(os.listdir(sys_nd))
    except OSError:
        return []
    out: List[dict] = []
    for n in names:
        if not n.startswith("region"):
            continue
        d = os.path.join(sys_nd, n)
        if not os.path.isdir(d):
            continue
        out.append({
            "id": n,
            "size": _read_int(os.path.join(d, "size")),
            "set_cookie": _read(os.path.join(d, "set_cookie")),
        })
    return out


def classify(cxl: dict, dax: List[dict],
              nd_regions: List[dict],
              cxl_present: bool, dax_present: bool,
              nd_present: bool) -> dict:
    if not (cxl_present or dax_present or nd_present):
        return {"verdict": "unknown",
                "reason": ("None of /sys/bus/cxl, /sys/class/dax "
                          "or /sys/bus/nd/devices present — "
                          "tier-2 memory not configured."),
                "recommendation": ""}

    # 1) cxl_decoder_error
    bad_dec = [d for d in cxl.get("decoders", [])
                  if d.get("state")
                  and _ERROR_RE.search(d["state"])]
    if bad_dec:
        sample = ", ".join(
            f"{d['id']} state={d['state']}"
                for d in bad_dec[:3])
        return {"verdict": "cxl_decoder_error",
                "reason": (f"{len(bad_dec)} CXL decoder(s) report "
                          f"a fault state : {sample}."),
                "recommendation": _recipe_cxl_decoder()}

    # 2) dax_size_zero_misconfigured
    zero_dax = [d for d in dax if d.get("size") == 0]
    if zero_dax:
        sample = ", ".join(d["id"] for d in zero_dax[:3])
        return {"verdict": "dax_size_zero_misconfigured",
                "reason": (f"{len(zero_dax)} /sys/class/dax/dax* "
                          f"entries report size=0 : {sample}."),
                "recommendation": _recipe_dax_zero()}

    # 3) target_node_unbound
    unbound = [d for d in dax
                  if d.get("target_node") == -1]
    if unbound:
        sample = ", ".join(d["id"] for d in unbound[:3])
        return {"verdict": "target_node_unbound",
                "reason": (f"{len(unbound)} DAX device(s) have "
                          f"target_node=-1 (no NUMA placement) "
                          f": {sample}."),
                "recommendation": _recipe_target_node()}

    # 4) pmem_present_unused — nd regions but no dax devices.
    if nd_regions and not dax:
        sample = ", ".join(r["id"] for r in nd_regions[:3])
        return {"verdict": "pmem_present_unused",
                "reason": (f"{len(nd_regions)} nvdimm region(s) "
                          f"but no /sys/class/dax entries — "
                          f"PMEM hardware unused : {sample}."),
                "recommendation": _recipe_pmem_unused()}

    return {"verdict": "ok",
            "reason": (f"CXL decoders={len(cxl.get('decoders',[]))}, "
                      f"mems={len(cxl.get('mems',[]))}, "
                      f"ports={len(cxl.get('ports',[]))} ; "
                      f"dax={len(dax)} ; "
                      f"nd_regions={len(nd_regions)}."),
            "recommendation": ""}


def status(config=None,
            sys_cxl: str = _SYS_CXL,
            sys_dax: str = _SYS_DAX,
            sys_nd: str = _SYS_ND) -> dict:
    cxl_present = os.path.isdir(sys_cxl)
    dax_present = os.path.isdir(sys_dax)
    nd_present = os.path.isdir(sys_nd)
    cxl = list_cxl_devices(sys_cxl)
    dax = list_dax_devices(sys_dax)
    nd_regions = list_nd_regions(sys_nd)
    verdict = classify(cxl, dax, nd_regions,
                          cxl_present, dax_present, nd_present)
    return {"ok": cxl_present or dax_present or nd_present,
              "cxl_present": cxl_present,
              "dax_present": dax_present,
              "nd_present": nd_present,
              "cxl_decoder_count": len(cxl.get("decoders", [])),
              "cxl_mem_count": len(cxl.get("mems", [])),
              "cxl_port_count": len(cxl.get("ports", [])),
              "dax_device_count": len(dax),
              "nd_region_count": len(nd_regions),
              "cxl_decoders": cxl.get("decoders", []),
              "dax_devices": dax,
              "nd_regions": nd_regions,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_cxl_decoder() -> str:
    return ("# A CXL decoder is in an error state. Inspect :\n"
            "for d in /sys/bus/cxl/devices/decoder*; do\n"
            "  echo \"-- $d\"\n"
            "  sudo cat \"$d/mode\" \"$d/size\"\n"
            "done\n"
            "sudo dmesg | grep -i cxl | tail\n"
            "# cxl-cli :  cxl list ; cxl set-region ...\n")


def _recipe_dax_zero() -> str:
    return ("# DAX device sizing is zero. Check the underlying\n"
            "# nvdimm/cxl region binding :\n"
            "daxctl list\n"
            "daxctl create-device -r region0\n")


def _recipe_target_node() -> str:
    return ("# DAX target_node=-1 means no NUMA assignment. Use\n"
            "# daxctl to bind :\n"
            "daxctl reconfigure-device dax0.0 -m system-ram \\\n"
            "  --target-node 0 \\\n"
            "  --movable\n")


def _recipe_pmem_unused() -> str:
    return ("# Persistent-memory regions exist but no DAX devices\n"
            "# expose them. Create a dax device :\n"
            "ndctl list -R\n"
            "ndctl create-namespace -r region0 -m devdax -s 1G\n")
