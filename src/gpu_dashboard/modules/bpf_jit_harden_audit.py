"""Module bpf_jit_harden_audit — BPF JIT hardening posture
(R&D #102.4).

Three sysctls control eBPF JIT hardening and visibility on
the host. The existing bpf_jit_xdp_busy_poll_audit only reads
bpf_jit_enable + busy-poll ; security_posture only reads
unprivileged_bpf_disabled. None touch the harden/kallsyms/limit
triplet.

  net.core.bpf_jit_harden   0 = off, 1 = privileged-only,
                              2 = all (spectre-v1/v2 mitigations
                              on JITed code)
  net.core.bpf_jit_kallsyms 0/1 — JIT symbols leaked into
                              /proc/kallsyms (KASLR weakening)
  net.core.bpf_jit_limit    bytes — total JIT allocation cap
                              (default ~264 MiB)

Reads :

  /proc/sys/net/core/bpf_jit_harden
  /proc/sys/net/core/bpf_jit_kallsyms
  /proc/sys/net/core/bpf_jit_limit
  /proc/sys/kernel/unprivileged_bpf_disabled  (cross-check)

Verdicts (worst-first) :

  bpf_jit_unhardened_unpriv   warn    bpf_jit_harden=0 AND
                                      unprivileged_bpf_disabled
                                      != 2 — unhardened JIT
                                      reachable by unpriv users.
  bpf_jit_kallsyms_leak       accent  bpf_jit_kallsyms=1 — JIT
                                      symbols in /proc/kallsyms.
  ok                                  bpf_jit_harden>=1 and
                                      kallsyms=0.
  requires_root                       sysctls unreadable.
  unknown                             bpf_jit_* knobs absent
                                      (CONFIG_BPF_JIT=n).

stdlib only.
"""
from __future__ import annotations

import os
from typing import Optional

NAME = "bpf_jit_harden_audit"

DEFAULT_NET_CORE = "/proc/sys/net/core"
DEFAULT_KERNEL_SYSCTL = "/proc/sys/kernel"


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, PermissionError, ValueError):
        return None


def classify(harden: Optional[int],
             kallsyms: Optional[int],
             limit: Optional[int],
             unpriv_disabled: Optional[int],
             knobs_present: bool) -> dict:
    if not knobs_present:
        return {"verdict": "unknown",
                "reason": (
                    "net.core.bpf_jit_* sysctls absent — "
                    "CONFIG_BPF_JIT=n.")}
    if (harden is None and kallsyms is None
            and limit is None):
        return {"verdict": "requires_root",
                "reason": (
                    "bpf_jit_* sysctls unreadable — "
                    "re-run as root.")}

    # warn — unhardened JIT reachable by unprivileged
    if (harden == 0
            and unpriv_disabled is not None
            and unpriv_disabled != 2):
        return {
            "verdict": "bpf_jit_unhardened_unpriv",
            "reason": (
                f"bpf_jit_harden=0 AND "
                f"unprivileged_bpf_disabled={unpriv_disabled} "
                "— unhardened JIT reachable by unprivileged "
                "users. Spectre-v1/v2 mitigations off in "
                "JITed code.")}

    # accent — kallsyms leak
    if kallsyms == 1:
        return {
            "verdict": "bpf_jit_kallsyms_leak",
            "reason": (
                "bpf_jit_kallsyms=1 — JIT symbols leaked "
                "into /proc/kallsyms. Useful for debugging, "
                "reduces KASLR effectiveness.")}

    return {"verdict": "ok",
            "reason": (
                f"bpf_jit_harden={harden} ; "
                f"kallsyms={kallsyms} ; "
                f"limit={limit} bytes ; "
                f"unpriv_disabled={unpriv_disabled}.")}


def status(config: Optional[dict] = None,
           net_core: str = DEFAULT_NET_CORE,
           kernel_sysctl: str = DEFAULT_KERNEL_SYSCTL) -> dict:
    knobs_present = os.path.isfile(
        os.path.join(net_core, "bpf_jit_harden"))
    harden = (
        _read_int(os.path.join(net_core, "bpf_jit_harden"))
        if knobs_present else None)
    kallsyms = (
        _read_int(os.path.join(net_core, "bpf_jit_kallsyms"))
        if knobs_present else None)
    limit = (
        _read_int(os.path.join(net_core, "bpf_jit_limit"))
        if knobs_present else None)
    unpriv_disabled = _read_int(
        os.path.join(
            kernel_sysctl, "unprivileged_bpf_disabled"))
    verdict = classify(harden, kallsyms, limit,
                       unpriv_disabled, knobs_present)
    return {
        "ok": verdict["verdict"] == "ok",
        "bpf_jit_harden": harden,
        "bpf_jit_kallsyms": kallsyms,
        "bpf_jit_limit": limit,
        "unprivileged_bpf_disabled": unpriv_disabled,
        "verdict": verdict,
    }
