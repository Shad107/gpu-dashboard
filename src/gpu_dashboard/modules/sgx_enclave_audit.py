"""Module sgx_enclave_audit — Intel SGX enclave readiness audit
(R&D #75.3).

Intel Software Guard eXtensions (SGX) exposes :

  /proc/cpuinfo flags                "sgx", "sgx_lc"
  /sys/firmware/sgx_*                EPC region sysfs (older
                                       Linux ≤ 6.0)
  /sys/devices/system/cpu/sgx_*       newer placement
                                       (≥ Linux 6.1)
  /dev/sgx_enclave                   enclave creation
  /dev/sgx_provision                 quote / attestation
                                       provisioning
  /dev/sgx_vepc                      virtualized EPC (KVM guests
                                       passed through)

Common findings on a homelab :

* CPU supports SGX but BIOS leaves it Disabled → /dev/sgx_* nodes
  absent ; remote-attestation workloads fail with no useful
  error.
* /dev/sgx_provision world-writable → any local user can request
  remote attestation quotes (privilege concern).
* FLC (Flexible Launch Control) bit missing — required by every
  modern SGX SDK ; without it the launch enclave is hard-coded
  to Intel's signing key and locked-out clients can't run.

Verdicts (priority order) :
  sgx_disabled_in_bios            cpuinfo lists 'sgx' flag but
                                    /dev/sgx_enclave is absent.
  sgx_unavailable                  no sgx flag at all (non-Intel
                                    or generation predating SGX).
  provision_node_world_writable    /dev/sgx_provision has
                                    mode & 0o002.
  flc_missing                      sgx flag present, sgx_lc
                                    missing — locked launch
                                    control.
  ok                               SGX usable, perms sane.
  unknown                          /proc/cpuinfo absent (test).

stdlib only.
"""
from __future__ import annotations

import os
import re
import stat
from typing import List, Optional, Set


NAME = "sgx_enclave_audit"


_PROC_CPUINFO = "/proc/cpuinfo"
_SYS_SGX_OLD = "/sys/firmware"
_SYS_SGX_NEW = "/sys/devices/system/cpu"
_DEV_ROOT = "/dev"


_SGX_FLAG_RE = re.compile(r"\bsgx(?:_lc)?\b")


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def parse_cpu_flags(text: Optional[str]) -> Set[str]:
    """Returns the set of CPU feature flags from the first
    "flags" line in /proc/cpuinfo."""
    if not text:
        return set()
    for ln in text.splitlines():
        if ln.startswith("flags") and ":" in ln:
            _, _, rhs = ln.partition(":")
            return set(rhs.split())
    return set()


def list_sgx_sysfs(sys_old: str = _SYS_SGX_OLD,
                       sys_new: str = _SYS_SGX_NEW
                       ) -> List[str]:
    """Returns SGX-related sysfs entries (both old + new
    locations)."""
    out: List[str] = []
    for root in (sys_old, sys_new):
        if not os.path.isdir(root):
            continue
        try:
            for n in os.listdir(root):
                if "sgx" in n.lower():
                    out.append(os.path.join(root, n))
        except OSError:
            continue
    return sorted(out)


def list_dev_nodes(dev_root: str = _DEV_ROOT) -> List[dict]:
    """Returns mode for /dev/sgx_enclave, sgx_provision, sgx_vepc."""
    out: List[dict] = []
    for name in ("sgx_enclave", "sgx_provision", "sgx_vepc"):
        full = os.path.join(dev_root, name)
        try:
            st = os.stat(full)
            out.append({"name": name, "present": True,
                          "mode": stat.S_IMODE(st.st_mode)})
        except OSError:
            out.append({"name": name, "present": False,
                          "mode": None})
    return out


def classify(cpu_flags: Set[str],
              sysfs_entries: List[str],
              dev_nodes: List[dict],
              cpuinfo_present: bool) -> dict:
    if not cpuinfo_present:
        return {"verdict": "unknown",
                "reason": ("/proc/cpuinfo absent — cannot detect "
                          "SGX support."),
                "recommendation": ""}

    has_sgx = "sgx" in cpu_flags
    has_sgx_lc = "sgx_lc" in cpu_flags
    enclave_present = any(d["name"] == "sgx_enclave"
                                 and d["present"]
                                 for d in dev_nodes)

    # 1) sgx_disabled_in_bios — CPU advertises but no /dev/sgx*
    if has_sgx and not enclave_present:
        return {"verdict": "sgx_disabled_in_bios",
                "reason": ("CPU exposes 'sgx' flag but "
                          "/dev/sgx_enclave is absent — SGX "
                          "disabled in BIOS / UEFI."),
                "recommendation": _recipe_bios()}

    # 2) sgx_unavailable
    if not has_sgx:
        return {"verdict": "sgx_unavailable",
                "reason": ("CPU has no 'sgx' feature flag — "
                          "non-Intel processor or generation "
                          "predating SGX. Remote-attestation "
                          "workloads will fail."),
                "recommendation": _recipe_unavailable()}

    # 3) provision_node_world_writable
    prov = next((d for d in dev_nodes
                       if d["name"] == "sgx_provision"), None)
    if (prov and prov.get("present")
            and prov.get("mode") is not None
            and (prov["mode"] & 0o002)):
        return {"verdict": "provision_node_world_writable",
                "reason": (f"/dev/sgx_provision mode = "
                          f"0o{prov['mode']:03o} (world-"
                          f"writable). Any local user can "
                          f"request attestation quotes."),
                "recommendation": _recipe_provision_ww()}

    # 4) flc_missing — SGX without Flexible Launch Control
    if not has_sgx_lc:
        return {"verdict": "flc_missing",
                "reason": ("SGX flag present but 'sgx_lc' "
                          "(Flexible Launch Control) is "
                          "absent. Modern SGX SDK requires "
                          "FLC."),
                "recommendation": _recipe_flc()}

    return {"verdict": "ok",
            "reason": (f"SGX usable ; sgx_lc=yes ; "
                      f"{len(sysfs_entries)} sysfs entries ; "
                      f"{sum(1 for d in dev_nodes if d['present'])}"
                      f" /dev/sgx* nodes present."),
            "recommendation": ""}


def status(config=None,
            proc_cpuinfo: str = _PROC_CPUINFO,
            sys_old: str = _SYS_SGX_OLD,
            sys_new: str = _SYS_SGX_NEW,
            dev_root: str = _DEV_ROOT) -> dict:
    cpuinfo_text = _read(proc_cpuinfo)
    cpuinfo_present = cpuinfo_text is not None
    cpu_flags = parse_cpu_flags(cpuinfo_text)
    sysfs_entries = list_sgx_sysfs(sys_old, sys_new)
    dev_nodes = list_dev_nodes(dev_root)
    verdict = classify(cpu_flags, sysfs_entries, dev_nodes,
                          cpuinfo_present)
    return {"ok": cpuinfo_present,
              "cpu_has_sgx": "sgx" in cpu_flags,
              "cpu_has_sgx_lc": "sgx_lc" in cpu_flags,
              "sgx_sysfs_entries": sysfs_entries,
              "dev_nodes": dev_nodes,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_bios() -> str:
    return ("# SGX disabled in BIOS / UEFI. Common knobs :\n"
            "#  - 'Software Guard Extensions (SGX)' = Enabled\n"
            "#  - 'PRMRR Size' set to ≥ 32 MiB (or auto)\n"
            "# Reboot to BIOS setup and toggle, then verify :\n"
            "ls /dev/sgx*\n"
            "grep -ow sgx /proc/cpuinfo | head -1\n")


def _recipe_unavailable() -> str:
    return ("# CPU does not support SGX. Use a Xeon-D, Xeon-E,\n"
            "# 6th-gen+ Core (with vendor-flashed SGX-friendly\n"
            "# firmware), or a virtualized SGX backend (sgx_vepc\n"
            "# / KVM passthrough).\n")


def _recipe_provision_ww() -> str:
    return ("# /dev/sgx_provision world-writable. Lock via udev:\n"
            "echo 'KERNEL==\"sgx_provision\", MODE=\"0660\", "
            "GROUP=\"sgx\"' \\\n"
            "  | sudo tee /etc/udev/rules.d/99-sgx.rules\n"
            "sudo udevadm trigger\n")


def _recipe_flc() -> str:
    return ("# FLC missing. On old kernels you may need to load\n"
            "# 'intel_sgx' with launch_enclave_id specified, OR\n"
            "# the CPU may genuinely predate FLC support.\n"
            "# Verify with :\n"
            "cat /proc/cpuinfo | grep -ow sgx_lc | head -1\n")
