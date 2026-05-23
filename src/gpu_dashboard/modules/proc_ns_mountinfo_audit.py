"""Module proc_ns_mountinfo_audit — per-PID NS + mountinfo (R&D #64.3).

Reads /proc/<pid>/ns/{mnt, pid, net, user, uts, ipc, cgroup,
time} (the inode IDs are stable namespace identifiers) and
/proc/<pid>/mountinfo (the mount view inside that namespace),
for the dashboard daemon plus every LLM-runtime PID discoverable
via comm.

Distinct from R&D #59.4 pid_rlimits_audit (rlimits only) and
fs_mount_audit (reads system-wide /proc/mounts, NOT per-PID
mountinfo). container_audit may use Docker-side metadata but
doesn't read /proc/<pid>/ns/.

Why this matters on an LLM rig running containers / systemd-
nspawn :

* A CUDA / inference PID lives in a different mount namespace
  than the dashboard — model paths reported by fs_mount_audit
  don't actually apply inside that namespace.
* A process in a private mount-ns hiding /dev/nvidia* bind mounts
  → silent driver-version mismatch between host and container.
* PID isolated in a different network namespace → NCCL between
  sibling containers will fail.

Reads :
  /proc/self/ns/{mnt, pid, net, user, uts, ipc, cgroup, time}
  /proc/<pid>/ns/* for LLM-runtime PIDs
  /proc/<pid>/mountinfo (count of nvidia* entries)

Verdicts (priority-ordered) :
  cuda_pid_in_different_mnt_ns  ≥1 LLM-runtime PID with mnt
                                namespace inode != daemon's.
  netns_split_for_nccl          ≥1 LLM PID with net namespace
                                != daemon's.
  nvidia_uvm_hidden_by_bind     LLM PID's mountinfo lacks any
                                /dev/nvidia* entry while host
                                has one.
  ok                            all candidates share daemon's
                                namespaces.
  unknown                       /proc/self/ns absent or no
                                candidates discoverable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "proc_ns_mountinfo_audit"


_PROC = "/proc"

_LLM_PROC_PREFIXES = (
    "llama", "vllm", "ollama", "mlc-llm", "mlc_llm",
    "sglang", "aphrodite", "text-generation",
)

_NS_KEYS = ("mnt", "pid", "net", "user", "uts", "ipc",
              "cgroup", "time")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def read_ns(proc: str, pid: str) -> Dict[str, Optional[str]]:
    """Returns {ns_kind: inode_string|None}. The /proc/<pid>/ns/<kind>
    symlink target looks like 'mnt:[4026531832]' ; we keep just the
    bracketed inode."""
    out: Dict[str, Optional[str]] = {k: None for k in _NS_KEYS}
    for k in _NS_KEYS:
        link = os.path.join(proc, pid, "ns", k)
        try:
            target = os.readlink(link)
        except OSError:
            continue
        m = re.search(r"\[(\d+)\]", target)
        if m:
            out[k] = m.group(1)
    return out


def has_nvidia_in_mountinfo(proc: str, pid: str) -> bool:
    text = _read(os.path.join(proc, pid, "mountinfo"))
    if not text:
        return False
    return "/dev/nvidia" in text or "/proc/driver/nvidia" in text


def find_llm_processes(proc: str = _PROC) -> List[dict]:
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
        if not any(c.startswith(p) for p in _LLM_PROC_PREFIXES):
            continue
        out.append({"pid": int(name),
                      "comm": comm.strip(),
                      "ns": read_ns(proc, name),
                      "has_nvidia": has_nvidia_in_mountinfo(
                          proc, name)})
    return sorted(out, key=lambda x: x["pid"])


def host_has_nvidia(proc: str = _PROC) -> bool:
    """Check the daemon's own mountinfo for nvidia."""
    return has_nvidia_in_mountinfo(proc, "self")


def classify(self_ns: Dict[str, Optional[str]],
              candidates: List[dict],
              host_has_nv: bool) -> dict:
    if not self_ns or not candidates:
        return {"verdict": "unknown",
                "reason": ("No LLM-candidate PIDs discoverable or "
                          "/proc/self/ns unreadable."),
                "recommendation": ""}

    # 1) cuda_pid_in_different_mnt_ns
    diff_mnt = [c for c in candidates
                   if c["ns"].get("mnt") and
                      c["ns"]["mnt"] != self_ns.get("mnt")]
    if diff_mnt:
        sample = ", ".join(
            f"{c['comm']}(pid={c['pid']})"
            for c in diff_mnt[:3])
        return {"verdict": "cuda_pid_in_different_mnt_ns",
                "reason": (f"{len(diff_mnt)} LLM PID(s) in a "
                          f"different mount namespace : {sample}. "
                          f"fs_mount_audit metrics don't apply "
                          f"inside that ns."),
                "recommendation": _recipe_mnt_ns()}

    # 2) netns_split_for_nccl
    diff_net = [c for c in candidates
                   if c["ns"].get("net") and
                      c["ns"]["net"] != self_ns.get("net")]
    if diff_net:
        sample = ", ".join(
            f"{c['comm']}(pid={c['pid']})"
            for c in diff_net[:3])
        return {"verdict": "netns_split_for_nccl",
                "reason": (f"{len(diff_net)} LLM PID(s) in a "
                          f"different network namespace : "
                          f"{sample}. NCCL inter-container will "
                          f"need explicit IPC setup."),
                "recommendation": _recipe_net_ns()}

    # 3) nvidia_uvm_hidden_by_bind
    if host_has_nv:
        hidden = [c for c in candidates if not c["has_nvidia"]]
        if hidden:
            sample = ", ".join(
                f"{c['comm']}(pid={c['pid']})"
                for c in hidden[:3])
            return {"verdict": "nvidia_uvm_hidden_by_bind",
                    "reason": (f"Host has /dev/nvidia* but "
                              f"{len(hidden)} LLM PID(s) don't see "
                              f"it in their mountinfo : {sample}. "
                              f"Driver-version mismatch risk."),
                    "recommendation": _recipe_uvm_hidden()}

    return {"verdict": "ok",
            "reason": (f"{len(candidates)} LLM candidate(s) share "
                      f"the daemon's namespaces."),
            "recommendation": ""}


def status(config=None, proc: str = _PROC) -> dict:
    self_ns = read_ns(proc, "self")
    candidates = find_llm_processes(proc)
    host_has_nv = host_has_nvidia(proc)
    ok = bool(self_ns.get("mnt"))
    verdict = classify(self_ns, candidates, host_has_nv)
    return {"ok": ok,
              "self_ns": self_ns,
              "candidate_count": len(candidates),
              "candidates": [
                  {"pid": c["pid"], "comm": c["comm"],
                   "ns": c["ns"], "has_nvidia": c["has_nvidia"]}
                  for c in candidates],
              "host_has_nvidia": host_has_nv,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_mnt_ns() -> str:
    return ("# Inspect the offending PID's mount view :\n"
            "sudo nsenter -t <pid> -m -- cat /proc/self/mountinfo | grep -E 'nvidia|huggingface|/srv'\n"
            "# If it's a container, the model path needs an\n"
            "# explicit bind mount :\n"
            "#   docker run -v /srv/models:/srv/models ...\n")


def _recipe_net_ns() -> str:
    return ("# NCCL across mount-ns siblings needs explicit IPC :\n"
            "#   docker run --ipc=host --net=host ...\n"
            "# Or use NCCL over a shared bridge / overlay network.\n"
            "# Verify the netns inode :\n"
            "ls -la /proc/<pid>/ns/net\n")


def _recipe_uvm_hidden() -> str:
    return ("# Container doesn't see /dev/nvidia* — likely missing\n"
            "# nvidia-container-toolkit hook. Verify :\n"
            "docker run --rm --gpus all nvidia/cuda:12.4.1-base nvidia-smi\n"
            "# Or for Podman :\n"
            "podman run --rm --device nvidia.com/gpu=all ...\n")
