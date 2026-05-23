"""Module cpu_vulnerabilities_audit — CPU side-channel mitigations (R&D #53.2).

Reads /sys/devices/system/cpu/vulnerabilities/* — the canonical
kernel view of each known speculative-execution / side-channel
vulnerability and the current mitigation state.

Why this matters on a multi-user / homelab LLM rig :

* Some users boot with `mitigations=off` to squeeze a few percent of
  throughput, then forget — the host stays vulnerable to cross-
  tenant leaks (containers, browser sessions, hosted code).
* New vulnerabilities are continuously published — `Vulnerable: No
  microcode` typically means the user's microcode is out of date.
* SMT enabled together with a TAA/L1TF/MDS vulnerability is the
  classic guest-to-host leak path on shared hosts.

Reads :
  /sys/devices/system/cpu/vulnerabilities/*
  /sys/devices/system/cpu/smt/{active,control}
  /proc/cmdline                           # detect mitigations=off

Verdicts (priority-ordered) :
  vulnerable_unmitigated         ≥1 vuln string starts with
                                 'Vulnerable' (no mitigation
                                 deployed by kernel).
  mitigation_disabled_via_cmdline /proc/cmdline contains
                                 mitigations=off / nopti / l1tf=off
                                 / mds=off / etc.
  smt_forced_on_with_vuln        smt/active = 1 AND one of l1tf /
                                 mds / tsx_async_abort / mmio_stale
                                 is in a 'SMT vulnerable' state.
  partial_mitigation             ≥1 vuln string contains 'STIBP:
                                 disabled' / 'no microcode' / 'no
                                 IBPB' / similar partial-only marker.
  ok                             all vulns 'Not affected' or fully
                                 mitigated.
  unknown                        /sys/devices/system/cpu/vulnerabilities
                                 absent.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional


NAME = "cpu_vulnerabilities_audit"


_SYS_CPU_VULN = "/sys/devices/system/cpu/vulnerabilities"
_SYS_CPU_SMT = "/sys/devices/system/cpu/smt"
_PROC_CMDLINE = "/proc/cmdline"


_OFF_TOKENS_RE = re.compile(
    r"\b(?:mitigations=off|nopti|nospectre_v[12]|nospec|"
    r"spectre_v2=off|spec_store_bypass_disable=off|"
    r"l1tf=off|mds=off|tsx_async_abort=off|"
    r"mmio_stale_data=off|retbleed=off)\b")

_SMT_RELEVANT_VULNS = ("l1tf", "mds", "tsx_async_abort",
                        "mmio_stale_data")

_PARTIAL_MARKERS = ("STIBP: disabled", "no microcode",
                     "no IBPB", "vulnerable, no microcode",
                     "Vulnerable: No microcode")


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def read_vulns(sys_vuln: str = _SYS_CPU_VULN) -> Dict[str, str]:
    if not os.path.isdir(sys_vuln):
        return {}
    out: Dict[str, str] = {}
    for name in sorted(os.listdir(sys_vuln)):
        val = _read(os.path.join(sys_vuln, name))
        if val is None:
            continue
        out[name] = val
    return out


def read_smt(sys_smt: str = _SYS_CPU_SMT) -> dict:
    return {"active": _read(os.path.join(sys_smt, "active")),
              "control": _read(os.path.join(sys_smt, "control"))}


def read_cmdline_off_tokens(proc_cmdline: str = _PROC_CMDLINE
                              ) -> List[str]:
    text = _read(proc_cmdline)
    if not text:
        return []
    return _OFF_TOKENS_RE.findall(text)


def classify(vulns: Dict[str, str], smt: dict,
              off_tokens: List[str]) -> dict:
    if not vulns:
        return {"verdict": "unknown",
                "reason": ("/sys/devices/system/cpu/vulnerabilities "
                          "is absent."),
                "recommendation": ""}

    # 1) vulnerable_unmitigated (excluding "Vulnerable: No microcode"
    #    which is a partial state — kernel mitigation is in place,
    #    only microcode update missing).
    full_vuln = [n for n, v in vulns.items()
                    if v.startswith("Vulnerable")
                    and "no microcode" not in v.lower()]
    if full_vuln:
        sample = ", ".join(full_vuln[:3])
        return {"verdict": "vulnerable_unmitigated",
                "reason": (f"{len(full_vuln)} vulnerability(ies) "
                          f"have no kernel mitigation : {sample}."),
                "recommendation": _recipe_check_kernel()}

    # 2) mitigation_disabled_via_cmdline
    if off_tokens:
        return {"verdict": "mitigation_disabled_via_cmdline",
                "reason": (f"/proc/cmdline contains explicit "
                          f"mitigation-off tokens : "
                          f"{' '.join(off_tokens)}."),
                "recommendation": _recipe_remove_off_tokens()}

    # 3) smt_forced_on_with_vuln
    smt_active = (smt or {}).get("active") == "1"
    if smt_active:
        smt_vuln = [n for n in _SMT_RELEVANT_VULNS
                      if n in vulns and "SMT" in vulns[n]
                      and "vulnerable" in vulns[n].lower()]
        if smt_vuln:
            return {"verdict": "smt_forced_on_with_vuln",
                    "reason": (f"SMT is active and "
                              f"{', '.join(smt_vuln)} "
                              f"flags SMT-related vulnerability."),
                    "recommendation": _recipe_smt_off()}

    # 4) partial_mitigation — any partial markers (incl. no microcode)
    partial = [n for n, v in vulns.items()
                  if any(m.lower() in v.lower()
                            for m in _PARTIAL_MARKERS)]
    if partial:
        sample = ", ".join(partial[:3])
        return {"verdict": "partial_mitigation",
                "reason": (f"{len(partial)} vulnerability(ies) "
                          f"have partial mitigation (often missing "
                          f"microcode) : {sample}."),
                "recommendation": _recipe_microcode_update()}

    return {"verdict": "ok",
            "reason": (f"{len(vulns)} vulnerabilities tracked, "
                      f"all 'Not affected' or fully mitigated."),
            "recommendation": ""}


def status(config=None,
            sys_vuln: str = _SYS_CPU_VULN,
            sys_smt: str = _SYS_CPU_SMT,
            proc_cmdline: str = _PROC_CMDLINE) -> dict:
    vulns = read_vulns(sys_vuln)
    smt = read_smt(sys_smt)
    off_tokens = read_cmdline_off_tokens(proc_cmdline)
    ok = bool(vulns)
    verdict = classify(vulns, smt, off_tokens)
    return {"ok": ok,
              "vuln_count": len(vulns),
              "vulnerabilities": vulns,
              "smt": smt,
              "cmdline_off_tokens": off_tokens,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_check_kernel() -> str:
    return ("# A vulnerability has no kernel mitigation. Either the\n"
            "# kernel is too old or the mitigation was explicitly\n"
            "# turned off. Verify each entry :\n"
            "for f in /sys/devices/system/cpu/vulnerabilities/*; do\n"
            "  echo \"$(basename $f): $(cat $f)\"\n"
            "done\n"
            "# Then : sudo apt update && sudo apt full-upgrade  (or distro equiv.)\n")


def _recipe_remove_off_tokens() -> str:
    return ("# Remove the offending tokens from GRUB_CMDLINE_LINUX :\n"
            "sudo grep '^GRUB_CMDLINE_LINUX' /etc/default/grub\n"
            "sudo sed -i 's/\\<mitigations=off\\>//' /etc/default/grub\n"
            "sudo update-grub  # debian/ubuntu\n"
            "# Then reboot. Verify with: cat /proc/cmdline\n")


def _recipe_smt_off() -> str:
    return ("# Disable SMT to close the side-channel :\n"
            "echo off | sudo tee /sys/devices/system/cpu/smt/control\n"
            "# Persist by adding 'nosmt' to GRUB_CMDLINE_LINUX.\n"
            "# Trade-off : single-thread perf improves, total threads halved.\n")


def _recipe_microcode_update() -> str:
    return ("# Install / update CPU microcode :\n"
            "sudo apt install intel-microcode amd64-microcode  # Debian/Ubuntu\n"
            "# … or  sudo dnf install microcode_ctl  on RHEL family.\n"
            "# Then reboot and re-check /sys/devices/system/cpu/vulnerabilities/.\n")
