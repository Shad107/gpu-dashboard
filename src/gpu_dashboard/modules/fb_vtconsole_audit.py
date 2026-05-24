"""Module fb_vtconsole_audit — framebuffer / vtconsole
ownership handoff check (R&D #79.3).

Walks /proc/fb, /sys/class/graphics/fb*/ and
/sys/class/vtconsole/vtcon*/ to detect a top cause of
"black-screen-after-resume" on Ampere desktops :

  efifb (or simpledrm) still owning the framebuffer device
  AFTER nvidia-drm has been loaded.  Two drivers fighting
  for the same DRM master = silent crashes, garbled output
  after suspend/resume, X failing to start.

The healthy state is :
  /proc/fb              shows exactly one ``*drmfb`` entry
                        (nvidia-drmfb, i915drmfb, amdgpudrmfb,
                        virtio_gpudrmfb …)
  /sys/.../vtcon<N>     for the active console has
                        name = "(M) frame buffer device"
                        bind = 1

The unhealthy states this catches :

  efifb_owns_console    efifb / simpledrm still registered
                        alongside the real *drmfb — handoff
                        didn't clean up.
  vesafb_fallback       vesafb registered  OR  only firmware
                        fb (efifb / simpledrm) and no GPU drm
                        fb — graphics stack incomplete.
  wrong_fb_bound        ≥2 fb devices and the active vtcon
                        is bound to the non-DRM one.

Verdicts (worst first) :
  efifb_owns_console  err     handoff failed (both present)
  vesafb_fallback     warn    no real drm fb at all
  wrong_fb_bound      warn    console on non-DRM fb
  multi_fb_ok         accent  ≥2 GPU drm fbs (multi-GPU)
  ok                          single drm fb + bound console
  unknown                     /proc/fb missing
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_PROC_FB = "/proc/fb"
DEFAULT_GRAPHICS = "/sys/class/graphics"
DEFAULT_VTCONSOLE = "/sys/class/vtconsole"

# Framebuffer driver names that indicate firmware fallback
# rather than a real GPU DRM driver.
_FIRMWARE_FB = frozenset({"efifb", "simpledrm"})
_LEGACY_FB = frozenset({"vesafb", "uvesafb"})


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def read_proc_fb(path: str = DEFAULT_PROC_FB
                  ) -> Optional[list[dict]]:
    """Returns [{id: int, name: str}, …] or None if missing.

    /proc/fb format is `<id> <driver_name>` per line."""
    text = _read_text(path)
    if text is None:
        return None
    rows: list[dict] = []
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            fb_id = int(parts[0])
        except ValueError:
            continue
        rows.append({"id": fb_id, "name": parts[1].strip()})
    return rows


def read_graphics_devs(root: str = DEFAULT_GRAPHICS
                        ) -> list[dict]:
    out: list[dict] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    for name in entries:
        if not name.startswith("fb"):
            continue
        d = os.path.join(root, name)
        out.append({
            "node": name,
            "name": _read_text(os.path.join(d, "name")),
        })
    return out


def read_vtcons(root: str = DEFAULT_VTCONSOLE) -> list[dict]:
    out: list[dict] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    for name in entries:
        if not name.startswith("vtcon"):
            continue
        d = os.path.join(root, name)
        bind_s = _read_text(os.path.join(d, "bind"))
        try:
            bind = int(bind_s) if bind_s is not None else None
        except ValueError:
            bind = None
        out.append({
            "node": name,
            "name": _read_text(os.path.join(d, "name")),
            "bind": bind,
        })
    return out


def _is_drm_fb(name: Optional[str]) -> bool:
    return bool(name) and name.endswith("drmfb")


def _is_firmware_fb(name: Optional[str]) -> bool:
    return name in _FIRMWARE_FB


def _is_legacy_fb(name: Optional[str]) -> bool:
    return name in _LEGACY_FB


def classify(fbs: Optional[list[dict]],
             vtcons: list[dict]) -> dict:
    if fbs is None:
        return {"verdict": "unknown",
                "reason": "/proc/fb unreadable."}
    if not fbs:
        return {"verdict": "ok",
                "reason": "No framebuffer devices — headless."}

    drm_fbs = [f for f in fbs if _is_drm_fb(f["name"])]
    fw_fbs = [f for f in fbs if _is_firmware_fb(f["name"])]
    legacy_fbs = [f for f in fbs if _is_legacy_fb(f["name"])]

    # 1. err — both firmware-fb and real DRM-fb present
    if fw_fbs and drm_fbs:
        return {"verdict": "efifb_owns_console",
                "reason": (
                    f"Both firmware fb ({fw_fbs[0]['name']}) "
                    f"and real DRM fb ({drm_fbs[0]['name']}) "
                    "registered — handoff failed."),
                "firmware_fb": fw_fbs[0]["name"],
                "drm_fb": drm_fbs[0]["name"]}

    # 2. warn — legacy vesafb
    if legacy_fbs:
        return {"verdict": "vesafb_fallback",
                "reason": (
                    f"Legacy fb driver {legacy_fbs[0]['name']} "
                    "registered — GPU driver never took over."),
                "legacy_fb": legacy_fbs[0]["name"]}

    # 2b. warn — only firmware fb, no drm fb
    if fw_fbs and not drm_fbs:
        return {"verdict": "vesafb_fallback",
                "reason": (
                    f"Only firmware fb {fw_fbs[0]['name']} "
                    "registered — no GPU DRM driver loaded."),
                "firmware_fb": fw_fbs[0]["name"]}

    # 3. warn — multiple fbs and console bound to non-DRM one
    fb_vtcons = [v for v in vtcons
                  if v.get("name")
                  and "frame buffer" in v["name"]]
    bound_fb_vtcons = [v for v in fb_vtcons
                        if v.get("bind") == 1]
    # If multiple fbs and no fb-vtcon is bound, that's
    # also wrong_fb_bound territory.
    if len(fbs) >= 2 and not bound_fb_vtcons:
        return {"verdict": "wrong_fb_bound",
                "reason": (
                    f"{len(fbs)} fb devices but no vtcon is "
                    "bound to a frame-buffer device."),
                "fb_count": len(fbs)}

    # 4. accent — multiple drm fbs (multi-GPU)
    if len(drm_fbs) >= 2:
        return {"verdict": "multi_fb_ok",
                "reason": (
                    f"{len(drm_fbs)} GPU DRM fbs "
                    f"({','.join(f['name'] for f in drm_fbs)})"
                    " — multi-GPU setup."),
                "drm_fb_count": len(drm_fbs)}

    # 5. ok
    primary = drm_fbs[0] if drm_fbs else fbs[0]
    bound = (
        "bound" if bound_fb_vtcons else
        "no console binding" if fb_vtcons else
        "no fb vtcon")
    return {"verdict": "ok",
            "reason": (
                f"Single fb {primary['name']} ; "
                f"console {bound}."),
            "fb_name": primary["name"]}


def status(config: Optional[dict] = None,
           proc_fb: str = DEFAULT_PROC_FB,
           graphics_root: str = DEFAULT_GRAPHICS,
           vtcon_root: str = DEFAULT_VTCONSOLE) -> dict:
    fbs = read_proc_fb(proc_fb)
    fb_devs = read_graphics_devs(graphics_root)
    vtcons = read_vtcons(vtcon_root)
    verdict = classify(fbs, vtcons)
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "efifb_owns_console"),
        "fb_count": len(fbs) if fbs else 0,
        "fbs": fbs or [],
        "graphics_devs": fb_devs,
        "vtcons": vtcons,
        "verdict": verdict,
    }
