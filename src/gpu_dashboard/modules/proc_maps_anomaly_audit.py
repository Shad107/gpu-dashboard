"""Module proc_maps_anomaly_audit — process memory-map
anomaly scan (R&D #78.4).

Walks ``/proc/<pid>/maps`` for every readable PID and flags
unusual *executable* mappings — the kind that show up when
something has injected code, dynamically generated a payload,
or kept an outdated binary alive after the disk file was
swapped under it.

Anomaly classes (worst first) :

* **rwx_mapping_found** — a single VMA is simultaneously
  writable AND executable. The classic W⊕X violation ;
  modern JITs use separate mappings + ``mprotect``.
* **anon_exec_segment** — executable VMA with no backing
  file at all. Legitimate for some JITs (V8, JVM, Mono,
  CRuby) so we whitelist common JIT process names.
* **deleted_exec_backing** — executable VMA whose backing
  file shows `` (deleted)`` in the maps line. Means the
  binary was replaced/removed on disk while still running.
  Skips NVIDIA libs (already covered by proc_maps_libs) and
  snap mounts (snap re-mounts trigger this routinely).
* **memfd_exec_present** — executable VMA backed by
  ``/memfd:…``. Used by some legit tooling (systemd
  fexecve) but also by the Symbiote-class malware family,
  so worth surfacing.
* **requires_root** — could not read maps for any PID.
* **ok** — scanned ≥1 PID, nothing unusual.
* **unknown** — ``/proc`` itself unreadable.

This audit explicitly does *not* try to be a security
scanner — it flags drift / oddities for a homelab user to
investigate, not signatures.
"""
from __future__ import annotations

import os
import re
from typing import Optional

DEFAULT_PROC_ROOT = "/proc"

# Process names that are *expected* to have anon-exec / rwx
# JIT mappings. Lowercased exact-match against /proc/<pid>/comm.
_JIT_COMMS = frozenset({
    "node", "nodejs",
    "chrome", "chromium", "chromium-browse", "chrome_crashpad",
    "firefox", "firefox-bin", "firefox-esr", "Web Content",
    "electron", "code", "Code", "code-insiders",
    "java", "javac", "jshell",
    "mono", "mono-sgen", "dotnet",
    "ruby", "python3", "pypy", "pypy3",
    "guile", "racket", "sbcl",
    "discord", "slack", "spotify", "obs",
    "Isolated Web Co",  # firefox content process
    "RDD Process",      # firefox renderer
    "claude",           # Claude Code CLI (Node.js)
    "bun",              # Bun runtime
    "deno",
})

# Shared-library substrings whose presence in /proc/<pid>/maps
# indicates the process embeds a known JIT engine — treat it
# as JIT regardless of comm. Catches Qt-QML (KDE Plasma, KDE
# apps, Telegram, Qt Creator, etc.), Mono Wine, MS V8, JavaScriptCore,
# and plain Qt-Core apps (which sometimes mmap one rwx page for
# Qt's resource hook table).
_JIT_LIB_MARKERS = (
    "libQt5Core", "libQt6Core",
    "libQt5Qml", "libQt6Qml",
    "libQt5Quick", "libQt6Quick",
    "libv8", "libnode",
    "libmono", "libmonosgen",
    "libjvm.so", "libjvm-server",
    "libwebkit", "libjavascriptcoregtk",
    # Mesa GL stack — libgallium uses LLVM as a JIT codegen
    # for shaders, libLLVM is the JIT itself
    "libLLVM", "libgallium",
)

# Anonymous-mapping kernel labels (the `[anon:NAME]` tail
# attached by prctl PR_SET_VMA_ANON_NAME) that flag JIT
# code regions explicitly. We always treat these mappings
# as JIT regardless of comm or other libs. Matched
# case-insensitively against the label tail.
_JIT_ANON_LABELS = (
    "jsjitcode", "javascriptcore", "jit",
    "v8 coderange", "v8", "mmtk", "wasm-code",
    "swiftshader",  # Chromium / Electron software renderer
)

# Backing-file path patterns that are *expected* to show
# (deleted) markers and should NOT trigger the verdict.
# - NVIDIA driver upgrade replaces the on-disk lib
# - snap re-mounts move the dentry
# - libnvidia-* of any flavour from /usr/lib or /run
_EXPECTED_DELETED_RE = re.compile(
    r"(libnvidia-.+\.so|libcuda.*\.so|libcudart.*\.so|"
    r"libcudnn.*\.so|libcublas.*\.so|libnvrtc.*\.so|"
    r"^/snap/|^/var/lib/snapd/|^/tmp/\.snap)"
)

# Maps line format (5 or 6 fields after offset/dev/inode):
#   start-end perms offset dev inode  [path...]
_MAPS_RE = re.compile(
    r"^([0-9a-f]+)-([0-9a-f]+)\s+"
    r"([rwxps-]{4})\s+"
    r"[0-9a-f]+\s+[0-9a-f:]+\s+\d+\s*(.*)$"
)


def _list_pids(proc_root: str) -> list[int]:
    try:
        return sorted(
            int(name) for name in os.listdir(proc_root)
            if name.isdigit())
    except OSError:
        return []


def _read_comm(proc_root: str, pid: int) -> Optional[str]:
    try:
        with open(os.path.join(proc_root, str(pid), "comm"),
                  "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _classify_path(perms: str, path: str) -> Optional[str]:
    """Returns anomaly tag for one map line, or None.

    Tags : rwx, anon_exec, memfd_exec, deleted_exec
    """
    if "x" not in perms:
        return None
    path_lc = path.lower()
    # Kernel-labeled JIT anonymous regions — always benign.
    if path.startswith("[anon:") and any(
            label in path_lc for label in _JIT_ANON_LABELS):
        return None
    is_rw = ("r" in perms and "w" in perms)
    if is_rw and "x" in perms:
        return "rwx"
    # Anonymous executable mapping. The kernel attaches a
    # label as `[anon:NAME]`; anything else (incl. empty
    # path or `[heap]`/`[stack]`) is genuine anon.
    if path == "" or path == "[heap]" or path == "[stack]":
        return "anon_exec"
    if path.startswith("[anon:"):
        # Labeled but not in our JIT allow-list above —
        # still suspicious enough to surface as anon_exec.
        return "anon_exec"
    if path.startswith("/memfd:"):
        # Strip trailing "(deleted)" tag for label match
        clean = path_lc
        if clean.endswith("(deleted)"):
            clean = clean[:-len("(deleted)")].strip()
        # JIT engines stash codegen via memfd_create("JIT…")
        # — Qt-QML, JavaScriptCore, SwiftShader, etc.
        if any(label in clean for label in _JIT_ANON_LABELS):
            return None
        return "memfd_exec"
    if path.endswith("(deleted)"):
        clean = path[:-len("(deleted)")].strip()
        if _EXPECTED_DELETED_RE.search(clean):
            return None
        return "deleted_exec"
    return None


def scan_pid(proc_root: str, pid: int) -> Optional[dict]:
    """Returns dict of anomaly counts for one PID, or None
    if maps is unreadable."""
    maps_path = os.path.join(proc_root, str(pid), "maps")
    try:
        fh = open(maps_path, "r", encoding="utf-8",
                  errors="replace")
    except (OSError, PermissionError):
        return None
    comm = _read_comm(proc_root, pid) or ""
    out = {"pid": pid, "comm": comm,
           "rwx": [], "anon_exec": [],
           "memfd_exec": [], "deleted_exec": []}
    is_jit = comm in _JIT_COMMS
    # Two-pass to detect JIT via embedded library marker.
    try:
        lines = fh.readlines()
    finally:
        fh.close()
    if not is_jit:
        for line in lines:
            if any(marker in line for marker in _JIT_LIB_MARKERS):
                is_jit = True
                break
    for line in lines:
        m = _MAPS_RE.match(line)
        if m is None:
            continue
        _start, _end, perms, path = m.groups()
        path = path.strip()
        tag = _classify_path(perms, path)
        if tag is None:
            continue
        # Suppress rwx + anon_exec for known JIT processes
        if is_jit and tag in ("rwx", "anon_exec"):
            continue
        out[tag].append(path or "<anon>")
    return out


def classify(proc_present: bool, scans: list[dict],
             total_pids: int) -> dict:
    if not proc_present:
        return {"verdict": "unknown",
                "reason": "/proc unreadable."}
    if total_pids == 0:
        return {"verdict": "unknown",
                "reason": "/proc had no PID entries."}
    if not scans:
        return {"verdict": "requires_root",
                "reason": (
                    f"Could not read maps for any of "
                    f"{total_pids} PID(s). "
                    "Re-run with sudo for full scan.")}

    rwx_hits = [s for s in scans if s["rwx"]]
    anon_hits = [s for s in scans if s["anon_exec"]]
    deleted_hits = [s for s in scans if s["deleted_exec"]]
    memfd_hits = [s for s in scans if s["memfd_exec"]]

    if rwx_hits:
        return {"verdict": "rwx_mapping_found",
                "reason": (
                    f"{len(rwx_hits)} process(es) have "
                    "writable+executable VMAs: "
                    + ",".join(
                        f"{s['comm']}({s['pid']})"
                        for s in rwx_hits[:3])
                    + "."),
                "hits": [
                    {"pid": s["pid"], "comm": s["comm"],
                     "count": len(s["rwx"])}
                    for s in rwx_hits]}
    if anon_hits:
        return {"verdict": "anon_exec_segment",
                "reason": (
                    f"{len(anon_hits)} non-JIT process(es) "
                    "have anonymous executable segments: "
                    + ",".join(
                        f"{s['comm']}({s['pid']})"
                        for s in anon_hits[:3]) + "."),
                "hits": [
                    {"pid": s["pid"], "comm": s["comm"],
                     "count": len(s["anon_exec"])}
                    for s in anon_hits]}
    if deleted_hits:
        return {"verdict": "deleted_exec_backing",
                "reason": (
                    f"{len(deleted_hits)} process(es) "
                    "have exec mappings to deleted files: "
                    + ",".join(
                        f"{s['comm']}({s['pid']})"
                        for s in deleted_hits[:3]) + "."),
                "hits": [
                    {"pid": s["pid"], "comm": s["comm"],
                     "paths": s["deleted_exec"][:5]}
                    for s in deleted_hits]}
    if memfd_hits:
        return {"verdict": "memfd_exec_present",
                "reason": (
                    f"{len(memfd_hits)} process(es) have "
                    "memfd executable mappings: "
                    + ",".join(
                        f"{s['comm']}({s['pid']})"
                        for s in memfd_hits[:3]) + "."),
                "hits": [
                    {"pid": s["pid"], "comm": s["comm"],
                     "count": len(s["memfd_exec"])}
                    for s in memfd_hits]}
    return {"verdict": "ok",
            "reason": (
                f"Scanned {len(scans)}/{total_pids} PID(s) ; "
                "no anomalies.")}


def status(config: Optional[dict] = None,
           proc_root: str = DEFAULT_PROC_ROOT) -> dict:
    present = os.path.isdir(proc_root)
    pids = _list_pids(proc_root) if present else []
    scans = []
    for pid in pids:
        res = scan_pid(proc_root, pid)
        if res is not None:
            scans.append(res)
    verdict = classify(present, scans, len(pids))
    return {
        "ok": verdict["verdict"] not in (
            "unknown", "requires_root"),
        "pid_count_total": len(pids),
        "pid_count_scanned": len(scans),
        "verdict": verdict,
    }
