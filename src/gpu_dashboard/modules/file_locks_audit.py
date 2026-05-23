"""Module file_locks_audit — /proc/locks contention auditor (R&D #42.1).

Inference daemons (ollama, llama-server, vllm, comfyui) take advisory
flock()/fcntl() locks on the GGUF / safetensors / model-blob files
they mmap. When two of them race on the *same* file path (most
common : the user starts `ollama serve` and then runs
`llama-server -m /home/user/.ollama/models/blobs/...` on the same
quant, or a manual `cp` overlaps with a model load), the kernel
serialises the writers and inference prefill stalls 3-30 s with no
visible error.

The kernel exposes /proc/locks — 8 fields per line :
  N: TYPE   KIND      ACCESS  PID  MAJ:MIN:INODE  START  END

Where TYPE is POSIX/FLOCK/OFDLCK, KIND is ADVISORY/MANDATORY,
ACCESS is READ/WRITE. Inode → path resolution requires walking
/proc/<pid>/fd/* and matching st_dev + st_ino — this module does
that with stdlib only.

Verdicts (priority-ordered) :
  contention_on_model    ≥ 2 distinct PIDs hold WRITE / WRITE-vs-
                         WRITE locks on the same inode, AND that
                         inode resolves to an LLM-pattern path
                         (.gguf, .safetensors, models/, ollama/) —
                         the actual ollama-vs-llama-server stall.
  contention_general     same write-vs-write contention on a non-
                         LLM file — surface for awareness.
  orphan_lock            a /proc/locks entry references a PID that
                         no longer exists in /proc — kernel will
                         clean these up on the next file release
                         but they linger meanwhile.
  ok                     no contention.
  unknown                /proc/locks unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "file_locks_audit"


_PROC_LOCKS = "/proc/locks"
_PROC = "/proc"


_LOCK_RE = re.compile(
    r"^\s*\d+:\s+(?P<type>\S+)\s+(?P<kind>\S+)\s+(?P<access>\S+)\s+"
    r"(?P<pid>-?\d+)\s+(?P<maj>[0-9a-f]+):(?P<minor>[0-9a-f]+):"
    r"(?P<inode>\d+)\s+(?P<start>\S+)\s+(?P<end>\S+)"
)


LLM_PATH_HINTS = (
    ".gguf", ".safetensors", ".bin", ".pt", ".onnx",
    "/.ollama/models/", "/models/", "/blobs/sha256",
    "huggingface", "vllm", "exllama", "comfyui",
)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def parse_proc_locks(text: str) -> list:
    out: list = []
    for line in text.splitlines():
        m = _LOCK_RE.match(line)
        if not m:
            continue
        try:
            pid = int(m.group("pid"))
        except ValueError:
            continue
        try:
            maj = int(m.group("maj"), 16)
            minor = int(m.group("minor"), 16)
            inode = int(m.group("inode"))
        except ValueError:
            continue
        out.append({
            "type": m.group("type"),
            "kind": m.group("kind"),
            "access": m.group("access"),
            "pid": pid,
            "major": maj,
            "minor": minor,
            "inode": inode,
            "start": m.group("start"),
            "end": m.group("end"),
        })
    return out


def _read_comm(pid: int, proc_root: str = _PROC) -> Optional[str]:
    t = _read(os.path.join(proc_root, str(pid), "comm"))
    return t.strip() if t else None


def _pid_exists(pid: int, proc_root: str = _PROC) -> bool:
    return os.path.isdir(os.path.join(proc_root, str(pid)))


def resolve_inode_to_path(pid: int, dev_major: int, dev_minor: int,
                            inode: int,
                            proc_root: str = _PROC) -> Optional[str]:
    """Walk /proc/<pid>/fd looking for a fd whose stat matches."""
    fd_dir = os.path.join(proc_root, str(pid), "fd")
    try:
        fds = os.listdir(fd_dir)
    except OSError:
        return None
    for fd in fds:
        link = os.path.join(fd_dir, fd)
        try:
            st = os.stat(link)
        except OSError:
            continue
        if st.st_ino != inode:
            continue
        # st_dev encodes maj/min ; compare via os.major / os.minor.
        if os.major(st.st_dev) != dev_major:
            continue
        if os.minor(st.st_dev) != dev_minor:
            continue
        try:
            return os.readlink(link)
        except OSError:
            return None
    return None


def is_llm_path(path: Optional[str]) -> bool:
    if not path:
        return False
    low = path.lower()
    for h in LLM_PATH_HINTS:
        if h in low:
            return True
    return False


def enrich(locks: list, proc_root: str = _PROC) -> list:
    """Add comm + path (best-effort) to each lock entry."""
    out: list = []
    for L in locks:
        pid = L["pid"]
        comm = _read_comm(pid, proc_root) if pid > 0 else None
        path = (resolve_inode_to_path(pid, L["major"], L["minor"],
                                          L["inode"], proc_root)
                if pid > 0 else None)
        new = dict(L)
        new["comm"] = comm
        new["path"] = path
        new["pid_alive"] = (_pid_exists(pid, proc_root)
                              if pid > 0 else True)
        new["is_llm"] = is_llm_path(path)
        out.append(new)
    return out


def detect_contention(locks: list) -> list:
    """Find inodes held by ≥ 2 distinct PIDs with WRITE access."""
    by_inode: dict = {}
    for L in locks:
        key = (L["major"], L["minor"], L["inode"])
        by_inode.setdefault(key, []).append(L)
    out: list = []
    for key, entries in by_inode.items():
        # Distinct PIDs holding WRITE on the same inode.
        writers = [e for e in entries if e["access"] == "WRITE"]
        pid_set = {e["pid"] for e in writers}
        if len(pid_set) >= 2:
            paths = sorted({e.get("path") for e in entries
                              if e.get("path")} or {None})
            is_llm = any(e.get("is_llm") for e in entries)
            out.append({
                "inode_key": list(key),
                "writers": writers,
                "all_entries": entries,
                "paths": [p for p in paths if p],
                "is_llm": is_llm,
            })
    return out


_RECIPE_KILL_DUP = (
    "# Two daemons hold WRITE locks on the same model file ; the\n"
    "# second one's prefill is stalled. Identify both PIDs in the\n"
    "# UI list below, then stop the duplicate :\n"
    "#   sudo systemctl stop llama-server.service\n"
    "# or kill the manually-launched process. The most common\n"
    "# offender is `ollama serve` (default daemon) racing with a\n"
    "# foreground `llama-server` on the same blob — pick one\n"
    "# daemon as the source of truth.\n"
    "ps -fp <PID-LIST-FROM-CARD>"
)

_RECIPE_GENERAL_CONTENTION = (
    "# Two processes hold WRITE locks on the same file ; one of them\n"
    "# is waiting. Inspect with :\n"
    "#   ls -l /proc/<PID>/fd/ | grep <INODE>\n"
    "#   lsof <PATH>"
)

_RECIPE_ORPHAN = (
    "# Orphan locks reference a PID that no longer exists. The\n"
    "# kernel clears these on the next file release — typically\n"
    "# self-resolves within minutes. If stuck > 1 h on the same\n"
    "# entry, the underlying file descriptor leaked into a zombie\n"
    "# task. Reboot to clear if it blocks production work."
)


def classify(enriched_locks: list, conflicts: list,
              orphans: list) -> dict:
    if conflicts:
        llm_conflicts = [c for c in conflicts if c.get("is_llm")]
        if llm_conflicts:
            paths = sorted({p for c in llm_conflicts
                              for p in c.get("paths") or []})
            pids = sorted({e["pid"] for c in llm_conflicts
                             for e in c["writers"]})
            return {"verdict": "contention_on_model",
                    "reason": (f"{len(llm_conflicts)} LLM-model "
                               f"file(s) under WRITE-lock "
                               f"contention from {len(pids)} "
                               f"distinct PID(s) — inference "
                               f"prefill stalls until one releases. "
                               f"Path(s) : "
                               f"{', '.join(paths) or '<unresolved>'}"),
                    "recommendation": _RECIPE_KILL_DUP}
        return {"verdict": "contention_general",
                "reason": (f"{len(conflicts)} file(s) under "
                           f"WRITE-vs-WRITE lock contention from "
                           f"distinct PIDs (no LLM-pattern path "
                           f"matched). Surface for awareness."),
                "recommendation": _RECIPE_GENERAL_CONTENTION}
    if orphans:
        return {"verdict": "orphan_lock",
                "reason": (f"{len(orphans)} lock entry(s) reference "
                           f"PID(s) that no longer exist in /proc. "
                           f"Kernel will clean these up on the "
                           f"next file release."),
                "recommendation": _RECIPE_ORPHAN}
    return {"verdict": "ok",
            "reason": (f"{len(enriched_locks)} active lock(s), "
                       f"no contention."),
            "recommendation": ""}


def status(cfg=None) -> dict:
    text = _read(_PROC_LOCKS)
    if text is None:
        return {
            "ok": False,
            "verdict": {"verdict": "unknown",
                         "reason": "/proc/locks unreadable.",
                         "recommendation": ""},
            "lock_count": 0, "locks": [], "conflicts": [],
            "orphan_count": 0,
        }
    raw = parse_proc_locks(text)
    enriched = enrich(raw, _PROC)
    conflicts = detect_contention(enriched)
    orphans = [L for L in enriched if not L["pid_alive"]]
    verdict = classify(enriched, conflicts, orphans)
    # Return a trimmed top-N to keep payload small ; full list
    # would explode on busy hosts.
    return {
        "ok": True,
        "lock_count": len(enriched),
        "conflict_count": len(conflicts),
        "orphan_count": len(orphans),
        "llm_lock_count": sum(1 for L in enriched if L["is_llm"]),
        "conflicts": conflicts[:20],
        "llm_locks": [L for L in enriched if L["is_llm"]][:20],
        "verdict": verdict,
    }
