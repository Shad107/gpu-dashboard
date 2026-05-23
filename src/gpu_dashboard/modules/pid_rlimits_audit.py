"""Module pid_rlimits_audit — daemon + LLM process rlimits (R&D #59.4).

Reads /proc/self/limits (the dashboard daemon) plus /proc/<pid>/
limits for any discoverable llama-server / vllm / ollama / mlc-llm
process. Distinct from the existing vfs_limits_audit (which only
reads global /proc/sys/fs/* sysctls) — this targets *per-process*
rlimits inherited from systemd.

Why this matters :

* `LimitMEMLOCK=64K` (systemd default) silently turns
  llama.cpp / vLLM `mmap + mlock(2)` into pageable IO → first-
  token latency on 30 B+ quants jumps from 200 ms to 4-8 s.
* `LimitNOFILE=1024` (the historical default) runs out when many
  client sockets + tensor-parallel shards open simultaneously.
* `LimitAS` (address-space cap) bites when a worker tries to
  mmap a 70 B model.

Reads :
  /proc/self/limits
  /proc/<pid>/{comm, limits} for any candidate matching
    llama* / vllm* / ollama* / mlc* / sglang* / aphrodite*

Verdicts (priority-ordered) :
  memlock_too_low_for_mmap_lock  ≥1 candidate has Max locked
                                  memory < 1 GiB.
  nofile_lt_4096                  ≥1 candidate has Max open
                                  files < 4096.
  as_capped                       ≥1 candidate has Max address
                                  space != unlimited.
  nproc_capped                    ≥1 candidate has Max processes
                                  < 1024.
  ok                              limits sane for LLM workloads.
  unknown                         /proc/self/limits unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple


NAME = "pid_rlimits_audit"


_PROC = "/proc"

_LLM_PROC_PREFIXES = (
    "llama", "vllm", "ollama", "mlc-llm", "mlc_llm",
    "sglang", "aphrodite", "text-generation",
)

# Thresholds.
_MEMLOCK_OK = 1 * 1024 * 1024 * 1024     # 1 GiB
_NOFILE_OK = 4096
_NPROC_OK = 1024


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def parse_limits(text: Optional[str]) -> Dict[str, Tuple[str, str]]:
    """Parse /proc/<pid>/limits.

    Returns {name: (soft, hard)} as strings. 'unlimited' is kept
    as the literal token — callers normalize.
    """
    out: Dict[str, Tuple[str, str]] = {}
    if not text:
        return out
    lines = text.splitlines()
    if not lines:
        return out
    # First line is header ; columns are fixed-width but values are
    # space-separated. Tokens : Limit name (may contain spaces),
    # Soft, Hard, Units. The kernel formats it consistently with
    # padded fields ; we use rsplit to recover :
    for line in lines[1:]:
        if not line.strip():
            continue
        # The "Units" column is optional. Rsplit on 2+ runs of
        # whitespace.
        parts = line.split()
        # Soft / Hard are last 2 or last 3 tokens. If the trailing
        # token is non-numeric AND not "unlimited" we treat it as
        # the unit and strip it.
        if not parts:
            continue
        # Heuristic : the units column has alphabetic-only words
        # (seconds, bytes, files, processes, signals, locks, us).
        if (parts[-1].isalpha() and
                parts[-1] not in ("unlimited",)):
            unit = parts[-1]
            parts = parts[:-1]
        # Now last two are soft, hard.
        if len(parts) < 3:
            continue
        soft = parts[-2]
        hard = parts[-1]
        name = " ".join(parts[:-2])
        out[name] = (soft, hard)
    return out


def _soft_int(limits: Dict[str, Tuple[str, str]],
                 key: str) -> Optional[int]:
    if key not in limits:
        return None
    soft, _ = limits[key]
    if soft == "unlimited":
        return None
    try:
        return int(soft)
    except ValueError:
        return None


def find_llm_processes(proc: str = _PROC) -> List[dict]:
    """Walk /proc/*/comm — return processes matching LLM-runtime
    prefixes."""
    out: List[dict] = []
    if not os.path.isdir(proc):
        return out
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        comm = _read(os.path.join(proc, name, "comm"))
        if not comm:
            continue
        c = comm.strip().lower()
        if any(c.startswith(p) for p in _LLM_PROC_PREFIXES):
            out.append({"pid": int(name), "comm": comm.strip()})
    return sorted(out, key=lambda x: x["pid"])


def classify(candidates: List[dict]) -> dict:
    if not candidates:
        return {"verdict": "unknown",
                "reason": ("No /proc/<pid>/limits readable — even "
                          "/proc/self failed."),
                "recommendation": ""}

    # 1) memlock_too_low_for_mmap_lock
    bad = []
    for c in candidates:
        ml = _soft_int(c["limits"], "Max locked memory")
        if ml is not None and ml < _MEMLOCK_OK:
            bad.append(f"{c['comm']}(pid={c['pid']})={ml}")
    if bad:
        return {"verdict": "memlock_too_low_for_mmap_lock",
                "reason": (f"{len(bad)} LLM-candidate process(es) "
                          f"have Max locked memory < 1 GiB : "
                          f"{bad[0]}. mmap+mlock fallbacks → "
                          f"first-token latency 10× worse."),
                "recommendation": _recipe_memlock()}

    # 2) nofile_lt_4096
    bad = []
    for c in candidates:
        nf = _soft_int(c["limits"], "Max open files")
        if nf is not None and nf < _NOFILE_OK:
            bad.append(f"{c['comm']}(pid={c['pid']})={nf}")
    if bad:
        return {"verdict": "nofile_lt_4096",
                "reason": (f"{len(bad)} candidate(s) with Max open "
                          f"files < 4096 : {bad[0]}. Client sockets "
                          f"+ tensor shards exhaust the fd pool."),
                "recommendation": _recipe_nofile()}

    # 3) as_capped — Max address space not unlimited
    capped = []
    for c in candidates:
        soft, _ = c["limits"].get("Max address space",
                                       ("unlimited", "unlimited"))
        if soft != "unlimited":
            capped.append(
                f"{c['comm']}(pid={c['pid']})={soft}")
    if capped:
        return {"verdict": "as_capped",
                "reason": (f"{len(capped)} candidate(s) with Max "
                          f"address space capped : {capped[0]}. "
                          f"Mmap of large quants will ENOMEM."),
                "recommendation": _recipe_as()}

    # 4) nproc_capped
    bad = []
    for c in candidates:
        np_ = _soft_int(c["limits"], "Max processes")
        if np_ is not None and np_ < _NPROC_OK:
            bad.append(f"{c['comm']}(pid={c['pid']})={np_}")
    if bad:
        return {"verdict": "nproc_capped",
                "reason": (f"{len(bad)} candidate(s) with Max "
                          f"processes < {_NPROC_OK} : {bad[0]}."),
                "recommendation": _recipe_nproc()}

    return {"verdict": "ok",
            "reason": (f"{len(candidates)} candidate(s) — rlimits "
                      f"sane for LLM workloads."),
            "recommendation": ""}


def status(config=None, proc: str = _PROC) -> dict:
    candidates: List[dict] = []
    self_text = _read(os.path.join(proc, "self", "limits"))
    self_limits = parse_limits(self_text)
    if self_limits:
        candidates.append({"pid": os.getpid(),
                              "comm": "self",
                              "limits": self_limits})

    for p in find_llm_processes(proc):
        lim_text = _read(os.path.join(proc, str(p["pid"]),
                                            "limits"))
        if not lim_text:
            continue
        candidates.append({**p, "limits": parse_limits(lim_text)})

    ok = bool(candidates)
    verdict = classify(candidates)
    return {"ok": ok,
              "candidate_count": len(candidates),
              "candidates": [
                  {"pid": c["pid"], "comm": c["comm"],
                   "Max locked memory": c["limits"].get(
                       "Max locked memory"),
                   "Max open files": c["limits"].get(
                       "Max open files"),
                   "Max processes": c["limits"].get(
                       "Max processes"),
                   "Max address space": c["limits"].get(
                       "Max address space")}
                  for c in candidates],
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_memlock() -> str:
    return ("# Raise the locked-memory limit for the LLM service :\n"
            "sudo systemctl edit llama-server.service  # or vllm.service\n"
            "#   [Service]\n"
            "#   LimitMEMLOCK=infinity\n"
            "#   LimitNOFILE=65536\n"
            "sudo systemctl daemon-reload\n"
            "sudo systemctl restart llama-server.service\n"
            "# Verify : grep Max\\ locked /proc/$(pidof llama-server)/limits\n")


def _recipe_nofile() -> str:
    return ("# Raise the open-file limit (default 1024 is far too\n"
            "# low for tensor-parallel inference) :\n"
            "sudo systemctl edit <your-llm>.service\n"
            "#   [Service]\n"
            "#   LimitNOFILE=65536\n")


def _recipe_as() -> str:
    return ("# Address-space cap is rare ; usually inherited from a\n"
            "# shell ulimit -v that hangs around in the service.\n"
            "# Drop the cap :\n"
            "sudo systemctl edit <your-llm>.service\n"
            "#   [Service]\n"
            "#   LimitAS=infinity\n")


def _recipe_nproc() -> str:
    return ("# Raise the process / thread limit :\n"
            "sudo systemctl edit <your-llm>.service\n"
            "#   [Service]\n"
            "#   LimitNPROC=8192\n")
