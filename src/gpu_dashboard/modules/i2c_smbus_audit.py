"""Module i2c_smbus_audit — /sys/bus/i2c + /dev/i2c-* (R&D #52.2).

Linux I²C / SMBus surface :

* `/sys/bus/i2c/devices/i2c-<N>/name` lists the adapter — typically a
  CPU-internal SMBus controller (I801, AMD PIIX4) plus per-GPU DDC
  nubs registered by the NVIDIA driver for EDID + DDC-CI.
* `/dev/i2c-<N>` is the user-space access node, mode 0600 root by
  default. Some "fan-control" how-to guides chmod this 0666 ; that
  lets any unprivileged process flash EDID, scrape SPD, or hit
  embedded controllers on the bus.
* When the NVIDIA kernel module is rebuilt / reloaded without
  `nvidia-modeset`, the DDC nubs vanish — `nvidia-settings` then
  silently fails to read display brightness / refresh rate.

Reads :
  /sys/bus/i2c/devices/i2c-*/{name, device/driver/module}
  /dev/i2c-*               (stat only — mode + ownership)
  /sys/bus/pci/devices/*/class  (to detect NVIDIA GPU presence)

Verdicts (priority-ordered) :
  ddc_bus_world_writable       ≥1 /dev/i2c-* has world-write
                               permission (0?2 / 0?6).
  i2c_dev_module_absent        /sys/class/i2c-dev empty AND
                               /sys/bus/i2c/devices populated → the
                               i2c-dev module isn't loaded and
                               nothing can talk to the bus.
  nvidia_ddc_missing           an NVIDIA-display PCI device exists
                               but no i2c adapter is named
                               'NVIDIA i2c'.
  smbus_orphan_adapter         an i2c adapter has no driver bound.
  ok                           bus state matches the workload.
  unknown                      /sys/bus/i2c unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
import stat
from typing import List, Optional


NAME = "i2c_smbus_audit"


_SYS_I2C_DEVICES = "/sys/bus/i2c/devices"
_SYS_I2C_DEV_CLASS = "/sys/class/i2c-dev"
_SYS_PCI_DEVICES = "/sys/bus/pci/devices"
_DEV = "/dev"

_NVIDIA_VENDOR = "0x10de"
# PCI base class 0x03 = display controller
_DISPLAY_BASE_CLASS = 0x03


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def list_adapters(sys_i2c: str = _SYS_I2C_DEVICES) -> List[dict]:
    """Enumerate /sys/bus/i2c/devices/i2c-N adapters.

    Driver discovery : i2c adapter nodes themselves don't carry a
    driver — they're virtual children of a PCI / platform device.
    We resolve the symlink target and walk up to the first parent
    that has a 'driver' symlink (the controlling driver).
    """
    if not os.path.isdir(sys_i2c):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(sys_i2c)):
        if not name.startswith("i2c-"):
            continue
        d = os.path.join(sys_i2c, name)
        adapter_name = _read(os.path.join(d, "name"))
        driver = _resolve_parent_driver(d)
        out.append({"id": name, "name": adapter_name,
                      "driver": driver})
    return out


def _resolve_parent_driver(adapter_link: str) -> Optional[str]:
    """Walk up from an i2c-N symlink target until we find a parent
    with a 'driver' symlink and return that driver's basename."""
    try:
        real = os.path.realpath(adapter_link)
    except OSError:
        return None
    # Walk up at most 8 levels — i2c adapter is typically 1-2 up.
    for _ in range(8):
        cand = os.path.join(real, "driver")
        try:
            return os.path.basename(os.readlink(cand))
        except OSError:
            pass
        parent = os.path.dirname(real)
        if not parent or parent == real:
            return None
        real = parent
    return None


def list_dev_nodes(dev: str = _DEV) -> List[dict]:
    """Stat /dev/i2c-N nodes — return mode + owners."""
    if not os.path.isdir(dev):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(dev)):
        if not name.startswith("i2c-"):
            continue
        p = os.path.join(dev, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({"name": name,
                      "mode": stat.S_IMODE(st.st_mode),
                      "uid": st.st_uid, "gid": st.st_gid})
    return out


def list_nvidia_display(sys_pci: str = _SYS_PCI_DEVICES) -> List[str]:
    """BDFs of NVIDIA display-class PCI devices."""
    if not os.path.isdir(sys_pci):
        return []
    out: List[str] = []
    for bdf in sorted(os.listdir(sys_pci)):
        ddir = os.path.join(sys_pci, bdf)
        vendor = _read(os.path.join(ddir, "vendor"))
        klass = _read(os.path.join(ddir, "class"))
        if vendor != _NVIDIA_VENDOR or not klass:
            continue
        try:
            base = (int(klass, 16) >> 16) & 0xff
        except ValueError:
            continue
        if base == _DISPLAY_BASE_CLASS:
            out.append(bdf)
    return out


def list_i2c_dev_class(sys_i2c_dev: str = _SYS_I2C_DEV_CLASS) -> List[str]:
    if not os.path.isdir(sys_i2c_dev):
        return []
    return sorted(os.listdir(sys_i2c_dev))


def _is_world_writable(mode: int) -> bool:
    return bool(mode & 0o002)


def classify(adapters: List[dict], dev_nodes: List[dict],
              dev_class: List[str], nvidia_display: List[str]) -> dict:
    if not adapters and not dev_nodes:
        return {"verdict": "unknown",
                "reason": "/sys/bus/i2c/devices unreadable.",
                "recommendation": ""}

    # 1) ddc_bus_world_writable
    bad_nodes = [n for n in dev_nodes
                    if _is_world_writable(n["mode"])]
    if bad_nodes:
        sample = ", ".join(f"{n['name']}=0o{n['mode']:o}"
                              for n in bad_nodes[:3])
        return {"verdict": "ddc_bus_world_writable",
                "reason": (f"{len(bad_nodes)} /dev/i2c-* node(s) "
                          f"are world-writable ({sample}). Any "
                          f"unprivileged user can flash EDID / "
                          f"scrape SPD / hit ECs."),
                "recommendation": _recipe_chmod_i2c()}

    # 2) i2c_dev_module_absent
    if adapters and not dev_class and not dev_nodes:
        return {"verdict": "i2c_dev_module_absent",
                "reason": (f"{len(adapters)} i2c adapter(s) present "
                          f"but /sys/class/i2c-dev is empty — the "
                          f"i2c-dev module is not loaded, no "
                          f"user-space access possible."),
                "recommendation": _recipe_modprobe_i2c_dev()}

    # 3) nvidia_ddc_missing
    if nvidia_display:
        has_nvidia_adapter = any(
            (a.get("name") or "").lower().startswith("nvidia")
            for a in adapters)
        if not has_nvidia_adapter:
            return {"verdict": "nvidia_ddc_missing",
                    "reason": (f"NVIDIA display GPU present "
                              f"({nvidia_display[0]}) but no "
                              f"'NVIDIA i2c' adapter on the bus. "
                              f"nvidia-settings DDC/brightness "
                              f"will fail."),
                    "recommendation": _recipe_nvidia_modeset()}

    # 4) smbus_orphan_adapter
    orphans = [a for a in adapters if a["driver"] is None]
    if orphans:
        sample = ", ".join(a["id"] for a in orphans[:3])
        return {"verdict": "smbus_orphan_adapter",
                "reason": (f"{len(orphans)} i2c adapter(s) without "
                          f"a driver bound : {sample}."),
                "recommendation": _recipe_inspect_orphan()}

    return {"verdict": "ok",
            "reason": (f"{len(adapters)} adapter(s), "
                      f"{len(dev_nodes)} dev node(s). Permissions "
                      f"and bindings look healthy."),
            "recommendation": ""}


def status(config=None,
            sys_i2c: str = _SYS_I2C_DEVICES,
            sys_i2c_dev: str = _SYS_I2C_DEV_CLASS,
            sys_pci: str = _SYS_PCI_DEVICES,
            dev: str = _DEV) -> dict:
    adapters = list_adapters(sys_i2c)
    dev_nodes = list_dev_nodes(dev)
    dev_class = list_i2c_dev_class(sys_i2c_dev)
    nvidia_display = list_nvidia_display(sys_pci)
    ok = bool(adapters or dev_nodes)
    verdict = classify(adapters, dev_nodes, dev_class, nvidia_display)
    return {"ok": ok,
              "adapter_count": len(adapters),
              "adapters": adapters,
              "dev_node_count": len(dev_nodes),
              "dev_nodes": dev_nodes,
              "i2c_dev_class": dev_class,
              "nvidia_display": nvidia_display,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_chmod_i2c() -> str:
    return ("# Restore restrictive permissions on /dev/i2c-* :\n"
            "sudo chgrp i2c /dev/i2c-* 2>/dev/null || true\n"
            "sudo chmod 660 /dev/i2c-*\n"
            "# Persist via /etc/udev/rules.d/99-i2c.rules :\n"
            "#   KERNEL==\"i2c-[0-9]*\", GROUP=\"i2c\", MODE=\"0660\"\n")


def _recipe_modprobe_i2c_dev() -> str:
    return ("# Load the i2c-dev kernel module so userspace can\n"
            "# open /dev/i2c-* :\n"
            "sudo modprobe i2c-dev\n"
            "# Persist by adding 'i2c-dev' to /etc/modules-load.d/\n")


def _recipe_nvidia_modeset() -> str:
    return ("# Reload the NVIDIA stack with nvidia-modeset so DDC\n"
            "# nubs come back :\n"
            "sudo modprobe -r nvidia_drm nvidia_modeset nvidia_uvm nvidia\n"
            "sudo modprobe nvidia nvidia_uvm nvidia_modeset nvidia_drm\n"
            "# Then verify : ls /sys/bus/i2c/devices/i2c-*/name | grep -i nvidia\n")


def _recipe_inspect_orphan() -> str:
    return ("# Inspect an orphan adapter to find which driver should\n"
            "# bind it. Most commonly this is the SMBus controller\n"
            "# missing i2c_piix4 / i2c_i801 :\n"
            "for d in /sys/bus/i2c/devices/i2c-*; do\n"
            "  [ -e \"$d/driver\" ] || echo \"$d: $(cat $d/name)\"\n"
            "done\n"
            "# Look up the matching module : modinfo i2c_i801 ; modinfo i2c_piix4\n")
