"""Module thunderbolt_usb4_audit — Thunderbolt / USB4 DMA
posture audit (R&D #86.2).

Reads /sys/bus/thunderbolt/devices/ for connected
domain (controller) + device entries.

A homelab eGPU / JHL7440 dock running with
``security=none`` re-enables pre-IOMMU-era DMA attacks ;
a never-authorized device (``authorized=0`` forever)
explains "my dock doesn't show up" without any error in
``dmesg``.

Per-domain : /sys/bus/thunderbolt/devices/domain<N>/
  security                    none | user | secure |
                              dponly | usbonly
  iommu_dma_protection        1 = kernel-side IOMMU DMA
                              shield active

Per-device : /sys/bus/thunderbolt/devices/<id>/
  authorized                  0 = denied / unauthorized
                              1 = user-approved
                              2 = boot-only allow
  device_name, vendor_name    informational
  iommu_dma_protection        per-device read

Security modes :

  ``none``    DMA wide open (legacy pre-IOMMU machines or
              BIOS-disabled).
  ``user``    devices require user approval (boltctl /
              GNOME prompt).
  ``secure``  devices need cryptographic key exchange.
  ``dponly``  DP-alt-mode only, no PCIe DMA tunnel.
  ``usbonly`` USB-tunnel only, no PCIe DMA tunnel.

Verdicts (worst first) :

  unauthenticated_device      ≥1 device with authorized = 0
                              while domain security is
                              ``user`` or ``secure`` — the
                              device tried to connect and
                              never got approved.
  security_none               any domain has security =
                              none AND has attached
                              devices — DMA shield off.
  no_iommu_dma_protection     domain has devices attached
                              but iommu_dma_protection = 0
                              — IOMMU shield bypassed.
  ok                          domains in secure/user mode,
                              devices authorized, IOMMU
                              shield on.
  n/a                         /sys/bus/thunderbolt absent
                              — no TB / USB4 controller.
  unknown                     bus present but unreadable.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_TB_ROOT = "/sys/bus/thunderbolt/devices"

# Security modes considered "unsafe" — the lack of any
# DMA gate.
_UNSAFE_SECURITY = {"none"}

# Modes where authorized=0 is treated as "device pending /
# never approved". For dp/usb-only the lack of approval is
# expected (no PCIe to approve).
_AUTH_REQUIRED_MODES = {"user", "secure"}

_DOMAIN_RE = re.compile(r"^domain\d+$")
_DEVICE_RE = re.compile(r"^\d+-\d+(:\d+\.\d+)?$")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    s = _read_text(path)
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def list_devices(root: str = DEFAULT_TB_ROOT
                  ) -> tuple[list[str], list[str]]:
    """Returns (domain_names, device_names)."""
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return ([], [])
    domains = [e for e in entries if _DOMAIN_RE.match(e)]
    devices = [e for e in entries if _DEVICE_RE.match(e)]
    return (domains, devices)


def read_domain(root: str, name: str) -> dict:
    d = os.path.join(root, name)
    return {
        "name": name,
        "security": _read_text(
            os.path.join(d, "security")) or "",
        "iommu_dma_protection": _read_int(
            os.path.join(d, "iommu_dma_protection")),
    }


def read_device(root: str, name: str) -> dict:
    d = os.path.join(root, name)
    return {
        "name": name,
        "authorized": _read_int(
            os.path.join(d, "authorized")),
        "vendor_name": _read_text(
            os.path.join(d, "vendor_name")) or "",
        "device_name": _read_text(
            os.path.join(d, "device_name")) or "",
        "iommu_dma_protection": _read_int(
            os.path.join(d, "iommu_dma_protection")),
    }


def classify(domains: list[dict],
             devices: list[dict],
             bus_present: bool) -> dict:
    if not bus_present:
        return {"verdict": "n/a",
                "reason": (
                    "/sys/bus/thunderbolt absent — no "
                    "Thunderbolt / USB4 controller on "
                    "this host.")}
    if not domains and not devices:
        return {"verdict": "n/a",
                "reason": (
                    "Thunderbolt bus present but no domains "
                    "or devices enumerated.")}

    # 1. err — unauthenticated device on secure/user domain
    for dev in devices:
        if dev["authorized"] != 0:
            continue
        # Match device to its domain by name prefix:
        # "1-0" → domain1
        m = re.match(r"^(\d+)-", dev["name"])
        if m is None:
            continue
        dom_name = f"domain{m.group(1)}"
        dom = next(
            (d for d in domains if d["name"] == dom_name),
            None)
        if (dom is not None
                and dom["security"] in _AUTH_REQUIRED_MODES):
            return {
                "verdict": "unauthenticated_device",
                "reason": (
                    f"Device {dev['name']} "
                    f"({dev['vendor_name']} / "
                    f"{dev['device_name']}) has "
                    "authorized = 0 on domain with "
                    f"security={dom['security']} — "
                    "device tried to connect and was "
                    "never approved."),
                "device": dev["name"],
                "vendor": dev["vendor_name"],
                "domain_security": dom["security"]}

    # 2. err — security=none on a domain WITH attached devices
    for dom in domains:
        if dom["security"] not in _UNSAFE_SECURITY:
            continue
        # Check if any devices belong to this domain
        dom_num = dom["name"].replace("domain", "")
        dom_devs = [
            d for d in devices
            if d["name"].startswith(f"{dom_num}-")]
        if dom_devs:
            return {
                "verdict": "security_none",
                "reason": (
                    f"Domain {dom['name']} has "
                    "security = none with "
                    f"{len(dom_devs)} device(s) attached "
                    "— DMA shield wide open."),
                "domain": dom["name"],
                "device_count": len(dom_devs)}

    # 3. warn — IOMMU DMA protection off (domain-level)
    for dom in domains:
        # Skip if no devices attached to this domain
        dom_num = dom["name"].replace("domain", "")
        dom_devs = [
            d for d in devices
            if d["name"].startswith(f"{dom_num}-")]
        if not dom_devs:
            continue
        if dom.get("iommu_dma_protection") == 0:
            return {
                "verdict": "no_iommu_dma_protection",
                "reason": (
                    f"Domain {dom['name']} has "
                    "iommu_dma_protection = 0 with "
                    f"{len(dom_devs)} device(s) attached "
                    "— IOMMU shield bypassed."),
                "domain": dom["name"]}

    return {"verdict": "ok",
            "reason": (
                f"{len(domains)} domain(s), "
                f"{len(devices)} device(s) ; all in "
                "secure / user mode with authorized = 1.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_TB_ROOT) -> dict:
    bus_present = os.path.isdir(root)
    domain_names, device_names = list_devices(root)
    domains = [read_domain(root, n) for n in domain_names]
    devices = [read_device(root, n) for n in device_names]
    verdict = classify(domains, devices, bus_present)
    return {
        "ok": verdict["verdict"] not in (
            "unauthenticated_device", "security_none",
            "unknown"),
        "bus_present": bus_present,
        "domain_count": len(domains),
        "device_count": len(devices),
        "domains": domains,
        "devices": devices,
        "verdict": verdict,
    }
