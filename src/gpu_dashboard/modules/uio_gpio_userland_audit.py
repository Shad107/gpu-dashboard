"""Module uio_gpio_userland_audit — UIO + GPIO userland audit
(R&D #70.3).

Two legacy "userspace I/O" interfaces that occasionally appear on
homelab / industrial boxes :

  /sys/class/uio/uio<N>/        Userspace I/O drivers (UIO).
                                  Each uioN device exposes its
                                  memory regions to userspace via
                                  /dev/uio<N> — a powerful but
                                  dangerous interface when mode-
                                  bits are loose.

  /sys/class/gpio/{export,
                    unexport,
                    gpiochip*/}  Two GPIO interfaces co-exist :
                                  * Legacy : `/sys/class/gpio/export`
                                    writes a pin number → a
                                    `gpioN/` directory appears.
                                    Deprecated since 4.8, expected
                                    to be removed.
                                  * Modern : `gpiochip<N>/` always
                                    present per chip ; userspace
                                    should use the character-device
                                    /dev/gpiochip<N> + libgpiod.

Why on a homelab :

* Some industrial / SBC distributions ship UIO devices for
  custom FPGAs and forget to lock /dev/uio*. World-writable
  uio is "instant ring-0 with userspace memcpy" for any local
  user.
* Apps that still write to /sys/class/gpio/export pollute the
  global namespace ; libgpiod-aware kernel will warn but proceed.
  Surfacing the active set helps migrate before the kernel
  removes the legacy interface.

Verdicts (priority order) :
  uio_world_writable             ≥1 /dev/uio* node is world-
                                   writable (mode & 0o002).
  orphan_gpio_exported           A pin was exported via the
                                   legacy interface but no
                                   matching gpio<N> directory
                                   appeared (driver returned
                                   failure but pin stayed dirty).
  legacy_gpio_sysfs_in_use       ≥1 `gpio<N>` directory present
                                   under /sys/class/gpio
                                   (deprecated interface in use).
  uio_present_unowned            ≥1 UIO device exists but has
                                   no readable name (driver did
                                   not finish registration).
  ok                             clean.
  unknown                        /sys/class/uio AND
                                   /sys/class/gpio both absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
import stat
from typing import List, Optional


NAME = "uio_gpio_userland_audit"


_SYS_UIO = "/sys/class/uio"
_SYS_GPIO = "/sys/class/gpio"
_DEV_UIO_GLOB = "/dev/uio"

_GPIO_LEGACY_RE = re.compile(r"^gpio(\d+)$")
_GPIO_CHIP_RE = re.compile(r"^gpiochip(\d+)$")


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def list_uio_devices(sys_uio: str = _SYS_UIO,
                       dev_prefix: str = _DEV_UIO_GLOB
                       ) -> List[dict]:
    if not os.path.isdir(sys_uio):
        return []
    out: List[dict] = []
    try:
        names = sorted(os.listdir(sys_uio))
    except OSError:
        return []
    for n in names:
        d = os.path.join(sys_uio, n)
        if not os.path.isdir(d):
            continue
        entry = {"id": n,
                    "name": _read(os.path.join(d, "name")),
                    "version": _read(os.path.join(d, "version")),
                    "dev_node_present": False,
                    "dev_node_mode": None}
        # Map sysfs name to /dev/uioN
        dev_path = dev_prefix + n.replace("uio", "")
        try:
            st = os.stat(dev_path)
            entry["dev_node_present"] = True
            entry["dev_node_mode"] = stat.S_IMODE(st.st_mode)
        except OSError:
            pass
        out.append(entry)
    return out


def list_gpio_state(sys_gpio: str = _SYS_GPIO) -> dict:
    """Returns {legacy_pins:list, chips:list, export_present:bool}.
    legacy_pins : the deprecated /sys/class/gpio/gpio<N> dirs.
    chips      : modern /sys/class/gpio/gpiochip<N> dirs."""
    out = {"legacy_pins": [], "chips": [],
              "export_present": False}
    if not os.path.isdir(sys_gpio):
        return out
    try:
        names = sorted(os.listdir(sys_gpio))
    except OSError:
        return out
    if "export" in names:
        out["export_present"] = True
    for n in names:
        m = _GPIO_LEGACY_RE.match(n)
        if m:
            out["legacy_pins"].append({
                "pin": int(m.group(1)),
                "value": _read(os.path.join(sys_gpio, n,
                                                  "value")),
                "direction": _read(os.path.join(sys_gpio, n,
                                                      "direction")),
            })
            continue
        m = _GPIO_CHIP_RE.match(n)
        if m:
            d = os.path.join(sys_gpio, n)
            out["chips"].append({
                "id": n,
                "label": _read(os.path.join(d, "label")),
                "base": _read(os.path.join(d, "base")),
                "ngpio": _read(os.path.join(d, "ngpio")),
            })
    return out


def classify(uios: List[dict], gpio: dict,
              uio_present: bool, gpio_present: bool) -> dict:
    if not uio_present and not gpio_present:
        return {"verdict": "unknown",
                "reason": ("/sys/class/uio AND /sys/class/gpio "
                          "both absent — kernel built without "
                          "either subsystem."),
                "recommendation": ""}

    # 1) uio_world_writable
    ww = [u for u in uios
            if u.get("dev_node_mode") is not None
              and (u["dev_node_mode"] & 0o002)]
    if ww:
        sample = ", ".join(
            f"{u['id']} mode=0o{u['dev_node_mode']:03o}"
                for u in ww[:3])
        return {"verdict": "uio_world_writable",
                "reason": (f"{len(ww)} /dev/uio* node(s) "
                          f"world-writable : {sample}."),
                "recommendation": _recipe_uio_ww()}

    # 2) orphan_gpio_exported
    #   Pins listed but with no value/direction file = driver
    #   declined the export but didn't clean up.
    orphans = [p for p in gpio.get("legacy_pins", [])
                  if p.get("value") is None
                  and p.get("direction") is None]
    if orphans:
        nums = ", ".join(str(p["pin"]) for p in orphans[:5])
        return {"verdict": "orphan_gpio_exported",
                "reason": (f"{len(orphans)} legacy GPIO pin(s) "
                          f"exported but missing "
                          f"value/direction files : {nums}."),
                "recommendation": _recipe_orphan_gpio()}

    # 3) legacy_gpio_sysfs_in_use
    pins = gpio.get("legacy_pins", [])
    if pins:
        nums = ", ".join(str(p["pin"]) for p in pins[:5])
        return {"verdict": "legacy_gpio_sysfs_in_use",
                "reason": (f"{len(pins)} pin(s) using the "
                          f"deprecated /sys/class/gpio/gpio<N> "
                          f"interface : {nums}."),
                "recommendation": _recipe_legacy_gpio()}

    # 4) uio_present_unowned
    unowned = [u for u in uios
                  if not u.get("name")]
    if unowned:
        sample = ", ".join(u["id"] for u in unowned[:3])
        return {"verdict": "uio_present_unowned",
                "reason": (f"{len(unowned)} UIO device(s) without "
                          f"a name (driver registration "
                          f"incomplete) : {sample}."),
                "recommendation": _recipe_uio_unowned()}

    return {"verdict": "ok",
            "reason": (f"UIO devs = {len(uios)} ; legacy GPIO pins "
                      f"= {len(gpio.get('legacy_pins', []))} ; "
                      f"GPIO chips = "
                      f"{len(gpio.get('chips', []))}."),
            "recommendation": ""}


def status(config=None,
            sys_uio: str = _SYS_UIO,
            sys_gpio: str = _SYS_GPIO,
            dev_uio_prefix: str = _DEV_UIO_GLOB) -> dict:
    uio_present = os.path.isdir(sys_uio)
    gpio_present = os.path.isdir(sys_gpio)
    uios = list_uio_devices(sys_uio, dev_uio_prefix)
    gpio = list_gpio_state(sys_gpio)
    verdict = classify(uios, gpio, uio_present, gpio_present)
    return {"ok": uio_present or gpio_present,
              "uio_present": uio_present,
              "gpio_present": gpio_present,
              "uio_count": len(uios),
              "uios": uios,
              "gpio_chip_count": len(gpio.get("chips", [])),
              "gpio_chips": gpio.get("chips", []),
              "legacy_gpio_pin_count": len(
                  gpio.get("legacy_pins", [])),
              "legacy_gpio_pins": gpio.get("legacy_pins", []),
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_uio_ww() -> str:
    return ("# World-writable /dev/uio* = local privilege\n"
            "# escalation surface. Lock with udev :\n"
            "echo 'KERNEL==\"uio*\", MODE=\"0600\"' \\\n"
            "  | sudo tee /etc/udev/rules.d/99-uio.rules\n"
            "sudo udevadm trigger\n")


def _recipe_orphan_gpio() -> str:
    return ("# Stale GPIO exports. Force-unexport :\n"
            "for n in $(ls /sys/class/gpio/ | grep '^gpio[0-9]'); do\n"
            "  echo \"${n#gpio}\" | sudo tee /sys/class/gpio/unexport\n"
            "done\n")


def _recipe_legacy_gpio() -> str:
    return ("# Migrate to libgpiod (modern character-device API) :\n"
            "sudo apt install gpiod libgpiod-dev\n"
            "gpiodetect ; gpioinfo\n"
            "# Then unexport legacy pins to free them :\n"
            "echo <pin> | sudo tee /sys/class/gpio/unexport\n")


def _recipe_uio_unowned() -> str:
    return ("# UIO device present without a name means the driver\n"
            "# registered late or partial. Identify it :\n"
            "for u in /sys/class/uio/*; do\n"
            "  echo \"$u : name=$(cat $u/name 2>/dev/null)\"\n"
            "done\n"
            "sudo dmesg | grep -i uio | tail\n")
