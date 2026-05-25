"""Module bpf_jit_xdp_busy_poll_audit — BPF JIT + XDP + busy
poll posture (R&D #93.4).

Existing modules touch related surfaces but never these :

  * btf_bpf_audit          — checks BTF / loaded-prog presence
  * bpf_program_inventory  — lists BPF progs only
  * softnet_stat_audit     — softnet counters
  * tcp_congestion_control_audit — L4, not the BPF/XDP plane
  * nic_queue_affinity     — owns rps/xps/napi_defer_hard_irqs

This audit owns the three remaining knobs :

  /proc/sys/net/core/bpf_jit_enable      0 = interpreter only
                                          (10× slower)
  /proc/sys/net/core/busy_poll           usecs ; > 0 burns
                                          CPU on rx path
  /sys/class/net/<iface>/xdp             XDP program attached
                                          to this NIC.

Verdicts (worst-first) :

  jit_disabled        err   bpf_jit_enable = 0 — every BPF
                            program runs in the kernel
                            interpreter. ~10× slower for
                            seccomp / tracing / networking
                            filters.
  xdp_attached        warn  ≥ 1 net iface has an XDP program
                            attached. Unusual on a desktop ;
                            often a Docker / Cilium / bpftrace
                            leftover from a prior experiment.
  busy_poll_active    accent net.core.busy_poll > 0 — kernel
                            busy-polls the rx ring per
                            packet, burning CPU. Useful for
                            HFT / RT, wasteful on a homelab.
  ok                  none of the above.
  requires_root       /proc/sys/net/core/bpf_jit_enable
                      unreadable (mode-600).
  unknown             /proc/sys/net/core absent (non-Linux).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "bpf_jit_xdp_busy_poll_audit"

DEFAULT_PROC_SYS_NET_CORE = "/proc/sys/net/core"
DEFAULT_SYS_CLASS_NET = "/sys/class/net"

# Names to skip when scanning /sys/class/net.
_SKIP_IFACES = ("lo", "bonding_masters")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, PermissionError):
        return None


def _read_int(path: str) -> Optional[int]:
    t = _read_text(path)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def read_core_state(
        proc_sys_net_core: str = DEFAULT_PROC_SYS_NET_CORE
        ) -> dict:
    return {
        "bpf_jit_enable": _read_int(
            os.path.join(
                proc_sys_net_core, "bpf_jit_enable")),
        "busy_poll": _read_int(
            os.path.join(proc_sys_net_core, "busy_poll")),
        "busy_read": _read_int(
            os.path.join(proc_sys_net_core, "busy_read")),
    }


def _iface_has_xdp(iface_dir: str) -> bool:
    """Detect attached XDP program at the iface level.

    Recent kernels expose either:
      /sys/class/net/<iface>/xdp        single file (older)
      /sys/class/net/<iface>/xdp/<keys> directory (newer)
    Either presence + non-zero content signals attachment.
    """
    xdp_path = os.path.join(iface_dir, "xdp")
    if not os.path.exists(xdp_path):
        return False
    # If it's a directory, look for any non-zero leaf.
    if os.path.isdir(xdp_path):
        try:
            entries = os.listdir(xdp_path)
        except OSError:
            return False
        for e in entries:
            v = _read_int(os.path.join(xdp_path, e))
            if v is not None and v != 0:
                return True
        return False
    # File case.
    v = _read_int(xdp_path)
    return v is not None and v != 0


def find_xdp_ifaces(
        sys_class_net: str = DEFAULT_SYS_CLASS_NET
        ) -> list:
    if not os.path.isdir(sys_class_net):
        return []
    try:
        names = os.listdir(sys_class_net)
    except OSError:
        return []
    out: list = []
    for name in names:
        if name in _SKIP_IFACES:
            continue
        path = os.path.join(sys_class_net, name)
        if _iface_has_xdp(path):
            out.append(name)
    return out


def classify(core: dict, xdp_ifaces: list,
             core_present: bool) -> dict:
    if not core_present:
        return {"verdict": "unknown",
                "reason": (
                    "/proc/sys/net/core absent — non-Linux "
                    "or procfs unavailable.")}
    jit = core.get("bpf_jit_enable")
    busy = core.get("busy_poll")

    if jit is None:
        return {"verdict": "requires_root",
                "reason": (
                    "/proc/sys/net/core/bpf_jit_enable "
                    "unreadable — re-run as root.")}

    # err — BPF JIT disabled
    if jit == 0:
        return {
            "verdict": "jit_disabled",
            "reason": (
                "net.core.bpf_jit_enable = 0 — every BPF "
                "program (seccomp / tracing / netfilter) "
                "runs in the kernel interpreter. ~10× slower "
                "and easier for unprivileged exploits.")}

    # warn — XDP attached on any iface
    if xdp_ifaces:
        return {
            "verdict": "xdp_attached",
            "reason": (
                f"XDP program attached on: {xdp_ifaces}. "
                "Unusual on a desktop ; often a Docker / "
                "Cilium / bpftrace leftover from a prior "
                "experiment."),
            "ifaces": xdp_ifaces}

    # accent — busy_poll > 0
    if busy is not None and busy > 0:
        return {
            "verdict": "busy_poll_active",
            "reason": (
                f"net.core.busy_poll = {busy} µs — kernel "
                "busy-polls the rx ring per packet, burning "
                "CPU. Useful for HFT / RT, wasteful on a "
                "homelab unless a userspace app is using "
                "SO_BUSY_POLL.")}

    return {"verdict": "ok",
            "reason": (
                f"bpf_jit_enable = {jit} ; busy_poll = "
                f"{busy} ; no XDP attached on inspected "
                "ifaces.")}


def status(config: Optional[dict] = None,
           proc_sys_net_core: str = DEFAULT_PROC_SYS_NET_CORE,
           sys_class_net: str = DEFAULT_SYS_CLASS_NET) -> dict:
    core_present = os.path.isdir(proc_sys_net_core)
    core = (read_core_state(proc_sys_net_core)
            if core_present else {})
    xdp_ifaces = find_xdp_ifaces(sys_class_net)
    verdict = classify(core, xdp_ifaces, core_present)
    return {
        "ok": verdict["verdict"] == "ok",
        "bpf_jit_enable": core.get("bpf_jit_enable"),
        "busy_poll": core.get("busy_poll"),
        "xdp_attached_ifaces": xdp_ifaces,
        "verdict": verdict,
    }
