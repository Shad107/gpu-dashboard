"""Module ksm_advisor — Kernel Same-page Merging detector (R&D #40.2).

KSM (Kernel Same-page Merging) is a Linux feature that scans
anonymous memory for identical pages and dedups them. It's a
clear win on KVM hosts running many identical guest images, and
a clear *loss* on a single-host LLM rig:

  - GGUF / safetensors tensor pages have effectively zero
    inter-page duplication (random-looking quantized blobs).
  - The `ksmd` kernel thread scans them futilely at CPU cost
    while never finding merges (3-8 % kernel CPU even idle).
  - The scan triggers TLB flushes that invalidate the inference
    worker's hot working set.
  - When KSM *does* merge a page (rare, but happens with all-zero
    padding), the next write triggers a copy-on-write fault and
    stalls the inference worker for a few hundred microseconds.
  - merge_across_nodes=1 (default) defeats numa_placement (#35.3).

Yet KSM is enabled by default on Fedora 37+ (via ksmtuned.service),
Proxmox VE hosts (always on for VM density), openSUSE/Tumbleweed,
and increasingly on Ubuntu Server LTS via systemd-ksm.service.

Verdicts:
  not_running              run=0 (or 2 = unmerged-and-stopped)
                           ; nothing to do.
  hurting_inference        run=1 + ≥1 LLM daemon has
                           ksm_merging_pages > 0 → ksmd is
                           actively touching the worker's tensors.
  running_no_dedup         run=1 but pages_sharing=0 system-wide
                           after uptime ; pure scan cost, no
                           benefit on this host (no VMs).
  justified_on_kvm_host    run=1 + pages_sharing > 0 + host_class
                           shipped form-factor suggests KVM/server
                           context → leave on, but maybe set
                           merge_across_nodes=0.
  unknown                  /sys/kernel/mm/ksm unreadable.

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional


NAME = "ksm_advisor"


_SYS_KSM = "/sys/kernel/mm/ksm"
_PROC = "/proc"


LLM_COMM_PATTERNS = (
    "ollama", "llama-server", "llama_server", "llama.cpp", "llamacpp",
    "vllm", "sglang", "exllamav2", "exllama", "comfyui",
)
LLM_CMDLINE_HINTS = (
    "llama_cpp", "vllm.entrypoints", "ollama", "exllama",
    "text-generation-webui", "comfyui",
)


_FIELDS_INT = (
    "run", "pages_shared", "pages_sharing", "pages_to_scan",
    "sleep_millisecs", "merge_across_nodes", "general_profit",
    "full_scans", "pages_scanned", "pages_skipped", "pages_unshared",
    "pages_volatile", "max_page_sharing", "stable_node_chains",
    "stable_node_dups", "use_zero_pages", "ksm_zero_pages",
    "smart_scan",
)


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t.strip())
    except ValueError:
        return None


def read_ksm_state(sys_ksm: str = _SYS_KSM) -> dict:
    if not os.path.isdir(sys_ksm):
        return {"available": False}
    state: dict = {"available": True}
    for f in _FIELDS_INT:
        v = _read_int(os.path.join(sys_ksm, f))
        if v is not None:
            state[f] = v
    # advisor_mode is a string ("none", "scan-time", "scan-pages")
    mode = _read(os.path.join(sys_ksm, "advisor_mode"))
    if mode is not None:
        state["advisor_mode"] = mode.strip()
    return state


def parse_ksm_stat(text: str) -> dict:
    """Parse /proc/<pid>/ksm_stat — newline-delimited `key value`."""
    out: dict = {}
    if not text:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # Some keys use trailing colon, some don't (kernel version
        # variance) — split on whitespace and trim.
        if ":" in s:
            k, _, v = s.partition(":")
            k = k.strip()
            v = v.strip()
        else:
            parts = s.split(None, 1)
            if len(parts) != 2:
                continue
            k, v = parts
        if v.lower() in ("yes", "no"):
            out[k] = v.lower() == "yes"
            continue
        try:
            out[k] = int(v)
        except ValueError:
            out[k] = v
    return out


def read_comm(pid: int, proc_root: str = _PROC) -> str:
    t = _read(os.path.join(proc_root, str(pid), "comm"))
    return t.strip() if t else ""


def read_cmdline(pid: int, proc_root: str = _PROC) -> str:
    try:
        with open(os.path.join(proc_root, str(pid), "cmdline"), "rb") as f:
            return f.read().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace")
    except OSError:
        return ""


def is_llm_proc(comm: str, cmdline: str) -> bool:
    low = comm.lower()
    for pat in LLM_COMM_PATTERNS:
        if pat in low:
            return True
    if low.startswith("python") or low.startswith("uvicorn"):
        for h in LLM_CMDLINE_HINTS:
            if h in cmdline:
                return True
    return False


def scan_llm_procs(proc_root: str = _PROC) -> list:
    out: list = []
    try:
        names = os.listdir(proc_root)
    except OSError:
        return out
    for n in names:
        if not n.isdigit():
            continue
        pid = int(n)
        comm = read_comm(pid, proc_root)
        cmdline = read_cmdline(pid, proc_root)
        if not is_llm_proc(comm, cmdline):
            continue
        ksm_stat_text = _read(os.path.join(proc_root, str(pid),
                                            "ksm_stat")) or ""
        ksm_merging_legacy = _read_int(os.path.join(
            proc_root, str(pid), "ksm_merging_pages"))
        stat = parse_ksm_stat(ksm_stat_text)
        merging = stat.get("ksm_merging_pages")
        if not isinstance(merging, int):
            merging = ksm_merging_legacy
        out.append({
            "pid": pid,
            "comm": comm,
            "cmdline_short": cmdline[:140],
            "ksm_merging_pages": merging,
            "ksm_rmap_items": stat.get("ksm_rmap_items"),
            "ksm_zero_pages": stat.get("ksm_zero_pages"),
            "ksm_merge_any": stat.get("ksm_merge_any"),
            "ksm_mergeable": stat.get("ksm_mergeable"),
        })
    return out


_RECIPE_DISABLE_FEDORA = (
    "# Fedora / RHEL : ksmtuned manages ksmd. Disable both :\n"
    "sudo systemctl disable --now ksmtuned ksm\n"
    "# Persist over kernel updates :\n"
    "sudo systemctl mask ksmtuned ksm"
)

_RECIPE_DISABLE_GENERIC = (
    "# Stop ksmd + unmerge already-merged pages :\n"
    "echo 2 | sudo tee /sys/kernel/mm/ksm/run\n"
    "# Persistent disable (Ubuntu/Debian/Arch) :\n"
    "sudo systemctl mask ksm.service\n"
    "# Or via tmpfiles.d :\n"
    "echo 'w /sys/kernel/mm/ksm/run - - - - 0' | \\\n"
    "  sudo tee /etc/tmpfiles.d/ksm-off.conf"
)

_RECIPE_NUMA_FENCE = (
    "# KSM is justified (VM host), but disable cross-NUMA merging\n"
    "# so it doesn't break numa_placement (R&D #35.3) :\n"
    "echo 0 | sudo tee /sys/kernel/mm/ksm/merge_across_nodes\n"
    "# Persist :\n"
    "echo 'w /sys/kernel/mm/ksm/merge_across_nodes - - - - 0' | \\\n"
    "  sudo tee /etc/tmpfiles.d/ksm-numa.conf"
)


_KVM_FORM_FACTORS = ("kvm_host", "server", "vm")


def classify(state: dict, procs: list,
              host_form_factor: Optional[str] = None) -> dict:
    if not state.get("available"):
        return {"verdict": "unknown",
                "reason": "/sys/kernel/mm/ksm not present.",
                "recommendation": ""}
    run = state.get("run", 0)
    if run in (0, 2):
        return {"verdict": "not_running",
                "reason": ("KSM is disabled (run=" + str(run) +
                           "). Nothing to do."),
                "recommendation": ""}
    pages_sharing = state.get("pages_sharing", 0) or 0
    pages_shared = state.get("pages_shared", 0) or 0
    merging_procs = [p for p in procs
                       if isinstance(p.get("ksm_merging_pages"), int)
                       and p["ksm_merging_pages"] > 0]
    if merging_procs:
        names = ", ".join(
            f"{p['comm']}(pid {p['pid']}, "
            f"{p['ksm_merging_pages']} merged)"
            for p in merging_procs)
        return {"verdict": "hurting_inference",
                "reason": (f"KSM is actively merging pages inside "
                           f"{len(merging_procs)} LLM daemon(s) — "
                           f"{names}. Tensor-page dedup is futile "
                           f"and the COW faults on writes stall "
                           f"inference."),
                "recommendation": _RECIPE_DISABLE_GENERIC}
    if pages_sharing == 0 and pages_shared == 0:
        return {"verdict": "running_no_dedup",
                "reason": ("KSM is running but pages_sharing=0 — "
                           "no duplication candidates on this "
                           "host. ksmd is burning CPU scanning "
                           "for matches that don't exist."),
                "recommendation": _RECIPE_DISABLE_FEDORA}
    # KSM has found duplicates ; only justified if we're on a
    # KVM host / server / VM context.
    if host_form_factor and host_form_factor in _KVM_FORM_FACTORS:
        rec = (_RECIPE_NUMA_FENCE
                if state.get("merge_across_nodes") == 1 else "")
        return {"verdict": "justified_on_kvm_host",
                "reason": (f"KSM is running and has merged "
                           f"{pages_sharing} pages — likely "
                           f"justified on this VM/server host. "
                           f"Leave on."),
                "recommendation": rec}
    # Standalone desktop with KSM finding matches — rare but
    # possible (Chromium tabs share zero-pages). Still net cost.
    return {"verdict": "running_no_dedup",
            "reason": (f"KSM has merged {pages_sharing} pages on "
                       f"a non-VM host — typically system noise "
                       f"(Chromium zero-pages), not LLM benefit. "
                       f"The scan cost still applies to the "
                       f"inference worker."),
            "recommendation": _RECIPE_DISABLE_GENERIC}


def status(cfg=None) -> dict:
    state = read_ksm_state(_SYS_KSM)
    procs = scan_llm_procs(_PROC)
    host_form_factor: Optional[str] = None
    try:
        from . import host_class
        hc = host_class.status(cfg)
        if hc.get("ok"):
            host_form_factor = (hc.get("verdict", {}) or {}).get("verdict")
    except Exception:
        pass
    verdict = classify(state, procs, host_form_factor)
    return {
        "ok": state.get("available", False),
        "state": state,
        "process_count": len(procs),
        "processes": procs,
        "host_form_factor": host_form_factor,
        "verdict": verdict,
    }
