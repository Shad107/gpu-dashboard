"""Module btf_bpf_audit — kernel BTF + BPF pin audit (R&D #66.2).

CO-RE BPF programs (libbpf, bpftrace, Cilium, Tetragon, recent
versions of perf, …) rely on **BTF — BPF Type Format** metadata
shipped by the kernel at /sys/kernel/btf/. When that metadata is
missing or much smaller than expected, every CO-RE program on
the host degrades silently :

* `bpftrace one-liners` start refusing to attach with
  "no BTF found for kernel".
* libbpf-tools (in bcc-tools) print "BTF is required" and exit.
* eBPF observability stacks (Tetragon, Pixie) crash-loop.

What this audit covers :

  /sys/kernel/btf/vmlinux    full kernel BTF blob (≥3 MB on a
                              modern Ubuntu kernel ; tiny means
                              kernel was built without
                              CONFIG_DEBUG_INFO_BTF).
  /sys/kernel/btf/*          one entry per loadable module that
                              shipped BTF. Compared to the count
                              of loaded modules to detect a
                              kernel that's missing per-module
                              BTF for a meaningful fraction.
  /sys/fs/bpf/               BPF pin filesystem. Presence is
                              informational ; the directory is
                              typically 0700-root, so EACCES is
                              the normal outcome from an
                              unprivileged daemon and is NOT a
                              fault.

Verdicts (priority order) :
  vmlinux_btf_missing     /sys/kernel/btf/vmlinux absent OR
                          zero-byte (kernel built without BTF).
  module_btf_missing      Loaded-module count is ≥4 *and* fewer
                          than 50 % of loaded modules have a
                          BTF entry — kernel was likely built
                          without MODULES_BTF.
  stale_btf               /sys/kernel/btf/vmlinux exists but is
                          smaller than 100 KiB ; almost certainly
                          a stub.
  ok                      BTF blob present, healthy size, BTF
                          coverage for modules is reasonable.
  unknown                 /sys/kernel/btf directory absent.

stdlib only.
"""
from __future__ import annotations

import os
from typing import List, Optional


NAME = "btf_bpf_audit"


_SYS_BTF = "/sys/kernel/btf"
_SYS_MODULE = "/sys/module"
_SYS_BPF = "/sys/fs/bpf"

_VMLINUX_MIN_BYTES = 100 * 1024     # 100 KiB
_MOD_COUNT_FLOOR = 4
_MOD_COVERAGE_MIN = 0.50


def _exists(p: str) -> bool:
    return os.path.exists(p)


def _file_size(p: str) -> Optional[int]:
    try:
        return os.path.getsize(p)
    except OSError:
        return None


def list_btf_entries(sys_btf: str = _SYS_BTF) -> List[str]:
    """All non-vmlinux entries are per-module BTF files."""
    if not os.path.isdir(sys_btf):
        return []
    try:
        names = os.listdir(sys_btf)
    except OSError:
        return []
    return sorted(n for n in names if n != "vmlinux")


def list_loaded_modules(sys_module: str = _SYS_MODULE) -> List[str]:
    """A loaded *kernel* module always has a refcnt file. Built-
    in pseudo-modules (printk, etc.) live under /sys/module too
    but lack refcnt, so we exclude them."""
    if not os.path.isdir(sys_module):
        return []
    out: List[str] = []
    try:
        names = os.listdir(sys_module)
    except OSError:
        return []
    for n in names:
        if os.path.isfile(os.path.join(sys_module, n, "refcnt")):
            out.append(n)
    return sorted(out)


def vmlinux_btf_size(sys_btf: str = _SYS_BTF) -> Optional[int]:
    return _file_size(os.path.join(sys_btf, "vmlinux"))


def bpf_pinfs_present(sys_bpf: str = _SYS_BPF) -> dict:
    """Returns presence + readability without complaining about
    EACCES — the BPF pin fs is normally root-only."""
    if not os.path.isdir(sys_bpf):
        return {"present": False, "readable": False, "entries": 0}
    try:
        n = len(os.listdir(sys_bpf))
        return {"present": True, "readable": True, "entries": n}
    except OSError:
        return {"present": True, "readable": False, "entries": None}


def classify(vmlinux_size: Optional[int],
              btf_entries: List[str],
              loaded_modules: List[str],
              btf_dir_present: bool) -> dict:
    if not btf_dir_present:
        return {"verdict": "unknown",
                "reason": ("/sys/kernel/btf directory absent — "
                          "kernel was built without "
                          "CONFIG_DEBUG_INFO_BTF support."),
                "recommendation": _recipe_no_btf_dir()}

    # 1) vmlinux_btf_missing
    if vmlinux_size is None or vmlinux_size == 0:
        return {"verdict": "vmlinux_btf_missing",
                "reason": ("/sys/kernel/btf/vmlinux is absent or "
                          "zero-byte — CO-RE BPF programs cannot "
                          "attach."),
                "recommendation": _recipe_vmlinux_missing()}

    # 3) stale_btf — vmlinux blob is suspiciously small.
    #    (Checked before module coverage because a stub vmlinux
    #    BTF guarantees coverage is also broken — and stub size
    #    is a more actionable signal.)
    if vmlinux_size < _VMLINUX_MIN_BYTES:
        return {"verdict": "stale_btf",
                "reason": (f"/sys/kernel/btf/vmlinux is only "
                          f"{vmlinux_size} bytes (< 100 KiB) — "
                          f"likely a build-without-BTF stub."),
                "recommendation": _recipe_stale_btf()}

    # 2) module_btf_missing — coverage low.
    nmod = len(loaded_modules)
    nbtf = len(btf_entries)
    if nmod >= _MOD_COUNT_FLOOR:
        coverage = nbtf / nmod if nmod else 1.0
        if coverage < _MOD_COVERAGE_MIN:
            return {"verdict": "module_btf_missing",
                    "reason": (f"Only {nbtf} of {nmod} loaded "
                              f"modules expose BTF "
                              f"({coverage:.0%}). Kernel was "
                              f"likely built without "
                              f"MODULES_BTF=y."),
                    "recommendation": _recipe_module_btf()}

    return {"verdict": "ok",
            "reason": (f"vmlinux BTF {vmlinux_size:,} bytes ; "
                      f"{len(btf_entries)} module BTF entries "
                      f"for {len(loaded_modules)} loaded "
                      f"modules."),
            "recommendation": ""}


def status(config=None,
            sys_btf: str = _SYS_BTF,
            sys_module: str = _SYS_MODULE,
            sys_bpf: str = _SYS_BPF) -> dict:
    btf_dir = os.path.isdir(sys_btf)
    vmlinux_size = vmlinux_btf_size(sys_btf) if btf_dir else None
    btf_entries = list_btf_entries(sys_btf)
    loaded_modules = list_loaded_modules(sys_module)
    bpf = bpf_pinfs_present(sys_bpf)

    verdict = classify(vmlinux_size, btf_entries,
                          loaded_modules, btf_dir)

    return {"ok": btf_dir,
              "vmlinux_btf_bytes": vmlinux_size,
              "module_btf_count": len(btf_entries),
              "loaded_module_count": len(loaded_modules),
              "module_btf_coverage": (
                  round(len(btf_entries) / len(loaded_modules), 3)
                      if loaded_modules else None),
              "bpf_pinfs": bpf,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_no_btf_dir() -> str:
    return ("# Kernel was built without CONFIG_DEBUG_INFO_BTF.\n"
            "# Install the distro -dbg kernel or rebuild with :\n"
            "#   CONFIG_DEBUG_INFO=y\n"
            "#   CONFIG_DEBUG_INFO_BTF=y\n"
            "#   CONFIG_DEBUG_INFO_BTF_MODULES=y\n"
            "# Ubuntu/Debian : apt install linux-image-$(uname -r)\n")


def _recipe_vmlinux_missing() -> str:
    return ("# /sys/kernel/btf/vmlinux missing → CO-RE BPF broken.\n"
            "# Reinstall the matching kernel image :\n"
            "uname -r\n"
            "sudo apt install --reinstall linux-image-$(uname -r)\n")


def _recipe_stale_btf() -> str:
    return ("# vmlinux BTF blob is tiny — most likely a stub.\n"
            "ls -l /sys/kernel/btf/vmlinux\n"
            "# Reinstall the kernel package and reboot.\n")


def _recipe_module_btf() -> str:
    return ("# Many loaded modules lack BTF — bpftrace probes on\n"
            "# them will fail. Rebuild kernel with :\n"
            "#   CONFIG_DEBUG_INFO_BTF_MODULES=y\n"
            "# (Or install the matching distro kernel-modules-btf\n"
            "# package if your distro splits it out.)\n")
