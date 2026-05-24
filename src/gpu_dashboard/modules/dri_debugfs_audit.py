"""Module dri_debugfs_audit — DRM /debug/dri/* client +
GEM-handle audit (R&D #83.4).

Catches GEM / framebuffer leaks and stuck DRM clients —
the slow VRAM bleed that complements vram_leak.py at the
kernel-handle layer.  Relevant for RTX 3090 + Wayland +
CUDA users where renderer crashes leave handles allocated.

Reads per /sys/kernel/debug/dri/<minor>/ :

  name            driver string (amdgpu, i915, nouveau,
                  virtio_gpu, nvidia-drm, …)
  clients         current open DRM clients with command,
                  pid (tgid), master flag, uid
  gem_names       per-driver GEM-handle inventory ; format
                  varies but always lists open handle ids
  framebuffer     active framebuffers
  state           atomic modeset state (large blob)

What we flag :

  * zombie_drm_clients     a client entry references a
                           PID that no longer exists in
                           /proc — the kernel context lock
                           is held by a corpse.
  * multiple_master_clients > 1 client has master flag set
                           — two compositors / video
                           backends fighting for KMS.
  * orphaned_gem_handles    drivers grew their GEM handle
                           count significantly vs the
                           open-client count (very rough
                           "lots of handles, few clients"
                           signal of a leak).

debugfs is mode-700 on most distros, so the dominant
verdict for a user-mode dashboard is requires_root.

Verdicts (worst first) :

  orphaned_gem_handles       GEM-to-client ratio looks
                             abnormally large.
  zombie_drm_clients         ≥ 1 client PID not in /proc.
  multiple_master_clients    > 1 client with master=y on
                             the same minor.
  ok                         consistent state, no zombies.
  requires_root              /sys/kernel/debug/dri
                             unreadable.
  unknown                    no DRM minors (headless box,
                             VM without DRM).
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_DEBUG_DRI = "/sys/kernel/debug/dri"
DEFAULT_PROC = "/proc"

# Heuristic threshold : GEM-handle-per-client ratio. Real
# desktop sessions sit around 200–500 handles per client ;
# > 2000 with only 1–2 clients is a strong leak signal.
_GEM_PER_CLIENT_LEAK = 2000


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _pid_alive(proc_root: str, pid: int) -> bool:
    return os.path.isdir(os.path.join(proc_root, str(pid)))


def list_dri_minors(root: str = DEFAULT_DEBUG_DRI
                     ) -> list[str]:
    try:
        return sorted(
            n for n in os.listdir(root)
            if n.isdigit())
    except OSError:
        return []


_CLIENT_LINE_RE = re.compile(
    r"^\s*(\S+)\s+(\d+)\s+\d+\s+([yn])\s+\d+", re.IGNORECASE)


def parse_clients(text: str) -> list[dict]:
    """Parses /sys/kernel/debug/dri/<n>/clients lines.

    Format (kernel drivers/gpu/drm/drm_debugfs.c) :
      command   tgid drm-minor master   uid    magic
      gnome-shell  1234         0      y    1000  0
    """
    out: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("command"):
            continue
        m = _CLIENT_LINE_RE.match(line)
        if m is None:
            continue
        out.append({
            "command": m.group(1),
            "tgid": int(m.group(2)),
            "master": m.group(3).lower() == "y",
        })
    return out


def count_gem_handles(text: str) -> int:
    """Roughly counts GEM-handle lines.  Driver-specific
    formats vary, but each handle starts on its own line
    and we just count non-empty / non-header lines."""
    count = 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # Skip obvious header lines that some drivers prepend
        if s.startswith(("name", "handle", "id",
                            "-----", "===")):
            continue
        count += 1
    return count


def read_dri_state(root: str = DEFAULT_DEBUG_DRI,
                    proc_root: str = DEFAULT_PROC) -> dict:
    """Returns nested dict per minor."""
    out: dict = {"minors": {}, "read_state": "ok"}
    if not os.path.isdir(root):
        # Distinguish unknown vs requires_root via parent
        parent = os.path.dirname(root) or "/"
        try:
            os.listdir(parent)
            out["read_state"] = "unknown"
        except (OSError, PermissionError):
            out["read_state"] = "requires_root"
        return out
    try:
        minors = sorted(
            n for n in os.listdir(root)
            if n.isdigit())
    except (OSError, PermissionError):
        out["read_state"] = "requires_root"
        return out
    if not minors:
        out["read_state"] = "unknown"
        return out

    for m in minors:
        d = os.path.join(root, m)
        name = (_read_text(os.path.join(d, "name")) or "").strip()
        clients_text = _read_text(os.path.join(d, "clients"))
        gem_text = _read_text(os.path.join(d, "gem_names"))
        if clients_text is None and gem_text is None:
            out["read_state"] = "requires_root"
            continue
        clients = parse_clients(clients_text or "")
        gem_count = count_gem_handles(gem_text or "")
        out["minors"][m] = {
            "name": name,
            "clients": clients,
            "gem_count": gem_count,
        }
    return out


def classify(state: dict, proc_root: str = DEFAULT_PROC) -> dict:
    rs = state.get("read_state", "ok")
    if rs == "unknown":
        return {"verdict": "unknown",
                "reason": "/sys/kernel/debug/dri absent or "
                          "has no minors — headless or no "
                          "DRM."}
    if rs == "requires_root":
        return {"verdict": "requires_root",
                "reason": (
                    "/sys/kernel/debug/dri is mode-700 — "
                    "re-run dashboard as root for the DRM "
                    "client / GEM inventory.")}

    minors = state.get("minors", {})

    # 1. err — orphaned GEM handles
    for minor, info in minors.items():
        clients = info["clients"]
        gem = info["gem_count"]
        if (len(clients) > 0
                and gem / max(len(clients), 1)
                > _GEM_PER_CLIENT_LEAK):
            return {
                "verdict": "orphaned_gem_handles",
                "reason": (
                    f"DRI {minor} ({info['name']}) has "
                    f"{gem} GEM handles for "
                    f"{len(clients)} client(s) — "
                    f"ratio {gem // max(len(clients), 1)} "
                    "looks abnormal."),
                "minor": minor,
                "name": info["name"],
                "gem_count": gem,
                "client_count": len(clients)}

    # 2. warn — zombie clients
    for minor, info in minors.items():
        zombies = [c for c in info["clients"]
                    if not _pid_alive(proc_root, c["tgid"])]
        if zombies:
            first = zombies[0]
            return {
                "verdict": "zombie_drm_clients",
                "reason": (
                    f"DRI {minor} ({info['name']}) has "
                    f"{len(zombies)} client(s) with dead "
                    f"PID(s) (first: {first['command']} "
                    f"pid={first['tgid']})."),
                "minor": minor,
                "zombie_count": len(zombies)}

    # 3. accent — multiple master clients
    for minor, info in minors.items():
        masters = [c for c in info["clients"] if c["master"]]
        if len(masters) > 1:
            return {
                "verdict": "multiple_master_clients",
                "reason": (
                    f"DRI {minor} ({info['name']}) has "
                    f"{len(masters)} clients with master=y "
                    "— two compositors / video backends "
                    "fighting for KMS."),
                "minor": minor,
                "master_count": len(masters)}

    return {"verdict": "ok",
            "reason": (
                f"{len(minors)} DRM minor(s) audited ; no "
                "zombies, no master conflicts.")}


def status(config: Optional[dict] = None,
           root: str = DEFAULT_DEBUG_DRI,
           proc_root: str = DEFAULT_PROC) -> dict:
    state = read_dri_state(root, proc_root)
    verdict = classify(state, proc_root)
    return {
        "ok": verdict["verdict"] not in (
            "orphaned_gem_handles",
            "requires_root", "unknown"),
        "minor_count": len(state.get("minors", {})),
        "read_state": state.get("read_state", "ok"),
        "minors": [
            {"id": m, "name": info["name"],
             "client_count": len(info["clients"]),
             "gem_count": info["gem_count"]}
            for m, info in state.get("minors", {}).items()],
        "verdict": verdict,
    }
