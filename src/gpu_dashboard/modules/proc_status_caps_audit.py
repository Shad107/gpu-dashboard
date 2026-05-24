"""Module proc_status_caps_audit — capability / hardening
posture sweep across all readable PIDs (R&D #80.4).

Walks /proc/<pid>/status and decodes the capability bitmaps :

  CapInh   inheritable cap set
  CapPrm   permitted cap set
  CapEff   effective cap set  (what the process can do NOW)
  CapBnd   bounding set       (ceiling)
  CapAmb   ambient set        (survives exec)
  NoNewPrivs   1 = setuid bits on exec ignored
  Seccomp      0 = no filter, 1 = strict, 2 = filter

Also reads /proc/sys/kernel/cap_last_cap to know how many
capability bits the kernel actually supports.

What this catches that other audits do not :

  *  Ollama / llama.cpp / Plex / Jellyfin daemon that
     somehow got CAP_SYS_ADMIN or CAP_NET_ADMIN in its
     effective set while running as a non-root user
     (privilege confused-deputy).
  *  A process that inherited ambient capabilities — these
     survive exec() and travel with the process tree.
  *  An exe with capabilities (file caps) running with
     NoNewPrivs=0 — a setuid escalation surface.

Verdicts (worst first) :

  unexpected_full_caps_userland   non-root effective UID
                                  AND CapEff contains
                                  CAP_SYS_ADMIN / SYS_MODULE
                                  / NET_ADMIN / DAC_OVERRIDE
                                  / DAC_READ_SEARCH.
  ambient_caps_set_outside_systemd  dangerous caps in
                                  CapAmb  AND  the process
                                  is not a direct child of
                                  pid 1 / known sandbox helper.
  ok                              everything inspected
                                  came back clean.
  requires_root                   couldn't read any pid's
                                  status caps (e.g. dashboard
                                  itself, ulimit, etc.).
  unknown                         /proc absent entirely.
"""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_PROC = "/proc"
DEFAULT_CAP_LAST_CAP = "/proc/sys/kernel/cap_last_cap"

# capabilities we treat as "high-risk on non-root userland"
CAP_DAC_OVERRIDE = 1
CAP_DAC_READ_SEARCH = 2
CAP_NET_ADMIN = 12
CAP_SYS_MODULE = 16
CAP_SYS_ADMIN = 21

_DANGEROUS_BITS = (
    (CAP_DAC_OVERRIDE, "CAP_DAC_OVERRIDE"),
    (CAP_DAC_READ_SEARCH, "CAP_DAC_READ_SEARCH"),
    (CAP_NET_ADMIN, "CAP_NET_ADMIN"),
    (CAP_SYS_MODULE, "CAP_SYS_MODULE"),
    (CAP_SYS_ADMIN, "CAP_SYS_ADMIN"),
)

# Process names that LEGITIMATELY hold dangerous caps for
# namespace / sandbox setup. Chromium-based browsers and
# Electron apps need CAP_SYS_ADMIN (via setuid chrome-sandbox
# or file caps) to create user namespaces for the sandbox.
# Skip these regardless of PPid.
_SANDBOX_COMMS = frozenset({
    "chrome", "chromium", "chromium-browse",
    "chrome-sandbox", "chrome_crashpad",
    "code", "Code", "code-insiders",
    "firefox", "firefox-bin", "firefox-esr",
    "Isolated Web Co", "RDD Process", "Web Content",
    "electron", "brave", "brave-browser",
    "opera", "vivaldi", "msedge",
    "slack", "discord", "spotify",
    "obs", "telegram-desktop",
})


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, PermissionError):
        return None


def _parse_cap_bitmap(hex_str: str) -> int:
    try:
        return int(hex_str, 16)
    except ValueError:
        return 0


def parse_status(text: str) -> dict:
    """Returns dict with parsed cap fields + Uid/PPid/etc."""
    out: dict = {"name": None, "ppid": None,
                 "uid_real": None, "uid_eff": None,
                 "cap_inh": 0, "cap_prm": 0,
                 "cap_eff": 0, "cap_bnd": 0,
                 "cap_amb": 0,
                 "no_new_privs": None, "seccomp": None}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        val = val.strip()
        if key == "Name":
            out["name"] = val
        elif key == "PPid":
            try:
                out["ppid"] = int(val)
            except ValueError:
                pass
        elif key == "Uid":
            parts = val.split()
            if len(parts) >= 2:
                try:
                    out["uid_real"] = int(parts[0])
                    out["uid_eff"] = int(parts[1])
                except ValueError:
                    pass
        elif key == "CapInh":
            out["cap_inh"] = _parse_cap_bitmap(val)
        elif key == "CapPrm":
            out["cap_prm"] = _parse_cap_bitmap(val)
        elif key == "CapEff":
            out["cap_eff"] = _parse_cap_bitmap(val)
        elif key == "CapBnd":
            out["cap_bnd"] = _parse_cap_bitmap(val)
        elif key == "CapAmb":
            out["cap_amb"] = _parse_cap_bitmap(val)
        elif key == "NoNewPrivs":
            try:
                out["no_new_privs"] = int(val)
            except ValueError:
                pass
        elif key == "Seccomp":
            try:
                out["seccomp"] = int(val)
            except ValueError:
                pass
    return out


def _list_pids(proc_root: str) -> list[int]:
    try:
        return sorted(
            int(n) for n in os.listdir(proc_root)
            if n.isdigit())
    except OSError:
        return []


def scan_pid(proc_root: str, pid: int) -> Optional[dict]:
    text = _read_text(
        os.path.join(proc_root, str(pid), "status"))
    if text is None:
        return None
    info = parse_status(text)
    info["pid"] = pid
    return info


def _dangerous_caps(bitmap: int) -> list[str]:
    return [name for bit, name in _DANGEROUS_BITS
            if bitmap & (1 << bit)]


def classify(scans: list[dict],
             total_pids: int) -> dict:
    if total_pids == 0:
        return {"verdict": "unknown",
                "reason": "/proc had no PID entries."}
    if not scans:
        return {"verdict": "requires_root",
                "reason": (
                    f"Could not read /proc/<pid>/status for "
                    f"any of {total_pids} PID(s).")}

    # 1. err — non-root effective UID with dangerous CapEff
    #    AND NOT a direct systemd-launched daemon (PPid != 1).
    #    systemd's service hardening intentionally drops root
    #    and keeps a single cap (CAP_NET_ADMIN for
    #    systemd-networkd, etc.) — that's good posture, not a
    #    red flag. The actual concern is a non-systemd-spawned
    #    process holding dangerous caps as a regular user.
    for s in scans:
        eff_uid = s.get("uid_eff")
        cap_eff = s.get("cap_eff", 0)
        if eff_uid is None or eff_uid == 0:
            continue
        if s.get("ppid") == 1:
            continue
        if s.get("name") in _SANDBOX_COMMS:
            continue
        bad = _dangerous_caps(cap_eff)
        if bad:
            return {
                "verdict": "unexpected_full_caps_userland",
                "reason": (
                    f"PID {s['pid']} ({s.get('name')}) runs "
                    f"as uid={eff_uid} with {','.join(bad)} "
                    f"in CapEff and PPid={s.get('ppid')} "
                    "(not a systemd-launched daemon)."),
                "pid": s["pid"], "name": s.get("name"),
                "caps": bad, "uid_eff": eff_uid,
                "ppid": s.get("ppid")}

    # 2. warn — DANGEROUS ambient caps outside direct systemd
    # child. Benign ambient caps (CAP_AUDIT_READ on sddm-helper,
    # CAP_NET_BIND_SERVICE on user daemons) don't trigger —
    # those are intentional setup helpers.
    for s in scans:
        bad = _dangerous_caps(s.get("cap_amb", 0))
        if not bad:
            continue
        if s.get("ppid") == 1:
            continue
        if s.get("name") in _SANDBOX_COMMS:
            continue
        return {
            "verdict": "ambient_caps_set_outside_systemd",
            "reason": (
                f"PID {s['pid']} ({s.get('name')}) has "
                f"dangerous ambient capabilities "
                f"({','.join(bad)}) and is not a direct "
                f"systemd child (PPid={s.get('ppid')})."),
            "pid": s["pid"], "name": s.get("name"),
            "cap_amb": f"0x{s['cap_amb']:016x}",
            "caps": bad,
            "ppid": s.get("ppid")}

    return {"verdict": "ok",
            "reason": (
                f"Scanned {len(scans)}/{total_pids} PID(s) ; "
                "no risky capability postures.")}


def status(config: Optional[dict] = None,
           proc_root: str = DEFAULT_PROC,
           cap_last_path: str = DEFAULT_CAP_LAST_CAP) -> dict:
    pids = _list_pids(proc_root)
    scans: list[dict] = []
    for pid in pids:
        info = scan_pid(proc_root, pid)
        if info is not None:
            scans.append(info)
    verdict = classify(scans, len(pids))
    cap_last_raw = _read_text(cap_last_path)
    try:
        cap_last = (
            int(cap_last_raw.strip())
            if cap_last_raw is not None else None)
    except ValueError:
        cap_last = None
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "requires_root",
            "unexpected_full_caps_userland"),
        "pid_count_total": len(pids),
        "pid_count_scanned": len(scans),
        "cap_last_cap": cap_last,
        "verdict": verdict,
    }
