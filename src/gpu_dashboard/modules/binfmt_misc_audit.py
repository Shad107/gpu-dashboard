"""Module binfmt_misc_audit — /proc/sys/fs/binfmt_misc (R&D #63.4).

Reads /proc/sys/fs/binfmt_misc/{status, *} — the global enabled
flag plus each registered interpreter.

Why this matters on an LLM rig that runs `docker buildx build
--platform linux/arm64` or qemu-user emulation :

* A `qemu-aarch64-static` interpreter with the `F` flag (preload
  binary at registration) silently breaks every foreign exec
  with ENOENT when the static binary is moved / removed during
  package upgrade.
* Duplicate registrations for the same magic from
  systemd-binfmt + manual register + Docker buildx → which
  interpreter wins is unpredictable.
* `/proc/sys/fs/binfmt_misc/status` = `disabled` while the user
  expects qemu-user to work — docker buildx fails opaquely.

Reads :
  /proc/sys/fs/binfmt_misc/status
  /proc/sys/fs/binfmt_misc/<name>
  (each per-registration file: enabled, interpreter, flags, magic)

Verdicts (priority-ordered) :
  qemu_user_interp_stale          ≥1 qemu-* interpreter with
                                  F flag AND interpreter path
                                  missing on disk.
  duplicate_registration          same magic value across ≥2
                                  registrations.
  globally_disabled_with_buildx   /status = disabled AND a
                                  qemu-* registration exists.
  ok                              binfmt_misc consistent.
  unknown                         /proc/sys/fs/binfmt_misc absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "binfmt_misc_audit"


_PROC_BINFMT = "/proc/sys/fs/binfmt_misc"


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def parse_registration(text: Optional[str]) -> dict:
    """Each registration file has lines like :
       enabled
       interpreter /usr/bin/qemu-aarch64-static
       flags: OCF
       offset 0
       magic 7f454c460201010000000000000000000200b700"""
    out: dict = {"enabled": None, "interpreter": None,
                   "flags": None, "offset": None, "magic": None}
    if not text:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if line in ("enabled", "disabled"):
            out["enabled"] = (line == "enabled")
            continue
        if line.startswith("interpreter "):
            out["interpreter"] = line.split(" ", 1)[1].strip()
            continue
        if line.startswith("flags:"):
            out["flags"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("offset"):
            try:
                out["offset"] = int(line.split()[1])
            except (ValueError, IndexError):
                out["offset"] = None
            continue
        if line.startswith("magic "):
            out["magic"] = line.split(" ", 1)[1].strip()
            continue
    return out


def list_registrations(proc_binfmt: str = _PROC_BINFMT
                         ) -> List[dict]:
    if not os.path.isdir(proc_binfmt):
        return []
    out: List[dict] = []
    for name in sorted(os.listdir(proc_binfmt)):
        if name in ("status", "register"):
            continue
        text = _read(os.path.join(proc_binfmt, name))
        info = parse_registration(text)
        info["name"] = name
        out.append(info)
    return out


def is_qemu_user(name: str, interpreter: Optional[str]) -> bool:
    lname = (name or "").lower()
    linterp = (interpreter or "").lower()
    if "qemu" in lname:
        return True
    if "qemu" in linterp:
        return True
    return False


def classify(status_text: Optional[str],
              registrations: List[dict]) -> dict:
    if status_text is None and not registrations:
        return {"verdict": "unknown",
                "reason": ("/proc/sys/fs/binfmt_misc absent — "
                          "kernel built without CONFIG_BINFMT_"
                          "MISC."),
                "recommendation": ""}

    enabled_global = (status_text or "").strip() == "enabled"
    qemu_regs = [r for r in registrations
                    if is_qemu_user(r["name"], r.get("interpreter"))]

    # 1) qemu_user_interp_stale
    stale = []
    for r in qemu_regs:
        flags = r.get("flags") or ""
        interp = r.get("interpreter")
        if "F" in flags.upper() and interp and \
                not os.path.exists(interp):
            stale.append(f"{r['name']}->{interp}")
    if stale:
        return {"verdict": "qemu_user_interp_stale",
                "reason": (f"{len(stale)} qemu-user binfmt entry "
                          f"with F flag points to a missing "
                          f"interpreter : {stale[0]}. Foreign-arch "
                          f"exec fails ENOENT."),
                "recommendation": _recipe_qemu_restore()}

    # 2) duplicate_registration
    by_magic: Dict[str, List[str]] = {}
    for r in registrations:
        mag = r.get("magic")
        if not mag:
            continue
        by_magic.setdefault(mag, []).append(r["name"])
    dups = [(mag, names) for mag, names in by_magic.items()
              if len(names) > 1]
    if dups:
        mag0, names0 = dups[0]
        return {"verdict": "duplicate_registration",
                "reason": (f"{len(dups)} magic value(s) shared by "
                          f"multiple registrations ; example : "
                          f"magic={mag0[:20]}... → "
                          f"{', '.join(names0)}."),
                "recommendation": _recipe_dedup()}

    # 3) globally_disabled_with_buildx
    if not enabled_global and qemu_regs:
        return {"verdict": "globally_disabled_with_buildx",
                "reason": ("binfmt_misc is globally disabled but "
                          f"{len(qemu_regs)} qemu-* registration(s) "
                          f"present. `docker buildx build "
                          f"--platform`-style multi-arch will fail."),
                "recommendation": _recipe_enable()}

    return {"verdict": "ok",
            "reason": (f"{len(registrations)} binfmt registration(s) "
                      f"; status={'enabled' if enabled_global else 'disabled'}."),
            "recommendation": ""}


def status(config=None, proc_binfmt: str = _PROC_BINFMT) -> dict:
    if not os.path.isdir(proc_binfmt):
        return {"ok": False,
                "binfmt_present": False,
                "verdict": classify(None, [])}
    status_text = _read(os.path.join(proc_binfmt, "status"))
    regs = list_registrations(proc_binfmt)
    verdict = classify(status_text, regs)
    return {"ok": True,
              "binfmt_present": True,
              "status_text": (status_text or "").strip(),
              "registration_count": len(regs),
              "registrations": regs,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_qemu_restore() -> str:
    return ("# Re-install qemu-user-static :\n"
            "sudo apt install --reinstall qemu-user-static\n"
            "# Or via docker (re-registers binfmt with current\n"
            "# interpreter paths) :\n"
            "docker run --rm --privileged tonistiigi/binfmt --install all\n")


def _recipe_dedup() -> str:
    return ("# Inspect each duplicate registration :\n"
            "for f in /proc/sys/fs/binfmt_misc/*; do\n"
            "  [ -f $f ] && case \"$f\" in *register|*status) ;; *)\n"
            "    echo \"=== $f ===\" ; cat $f\n"
            "  esac\n"
            "done\n"
            "# Delete the redundant entry :\n"
            "echo -1 | sudo tee /proc/sys/fs/binfmt_misc/<entry>\n"
            "# Then restart systemd-binfmt to re-register from\n"
            "# /usr/lib/binfmt.d/ only :\n"
            "sudo systemctl restart systemd-binfmt\n")


def _recipe_enable() -> str:
    return ("# Re-enable binfmt_misc :\n"
            "echo 1 | sudo tee /proc/sys/fs/binfmt_misc/status\n"
            "# Persist via systemd-binfmt :\n"
            "sudo systemctl enable --now systemd-binfmt\n")
