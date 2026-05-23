"""Module kvm_misc_audit — KVM runtime + nested + perms (R&D #54.3).

Reads :
  /sys/devices/virtual/misc/kvm/        existence + dev node id
  /sys/module/kvm/{taint,parameters/*}  global KVM knobs (halt_poll
                                         and friends)
  /sys/module/kvm_intel/parameters/*    Intel-VMX-specific
  /sys/module/kvm_amd/parameters/*      AMD-SVM-specific
  /sys/module/vfio_pci                  detect GPU/VFIO passthrough
  /dev/kvm                              perms (root:kvm 660 = ok)
  /proc/misc                            KVM minor number

On a homelab LLM rig the foot-guns we want to surface :

* Nested virtualization left ON while a VFIO/GPU passthrough is
  active : breaks IOMMU isolation guarantees and wastes CPU on
  shadow EPT.
* halt_poll_ns cranked high (≥ 500 µs) costs ~10 % idle power on
  hosts with bursty inference VMs.
* /dev/kvm world-readable / wrong-group is a real-world miss
  after a manual `chmod` for a single tool to work.

Verdicts (priority-ordered) :
  kvm_disabled                  /sys/module/kvm absent OR
                                /dev/kvm missing.
  nested_on_with_passthrough    kvm_intel.nested = Y / kvm_amd.
                                nested = 1 AND vfio_pci module
                                present.
  halt_poll_excessive           kvm.halt_poll_ns > 300_000.
  group_perm_missing            /dev/kvm world-writable, OR mode
                                not 0660 group-rw, OR group != kvm.
  ok                            kvm available, gates reasonable.
  unknown                       neither /sys/module/kvm nor
                                /dev/kvm present.

stdlib only.
"""
from __future__ import annotations

import grp
import os
import stat
from typing import Optional


NAME = "kvm_misc_audit"


_SYS_MODULE = "/sys/module"
_DEV_KVM = "/dev/kvm"

_HALT_POLL_NS_MAX_OK = 300_000  # 300 µs


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(p: str) -> Optional[int]:
    t = _read(p)
    if t is None:
        return None
    try:
        return int(t)
    except ValueError:
        return None


def kvm_module_present(sys_module: str = _SYS_MODULE) -> bool:
    return os.path.isdir(os.path.join(sys_module, "kvm"))


def detect_vmx_or_svm(sys_module: str = _SYS_MODULE) -> Optional[str]:
    for name in ("kvm_intel", "kvm_amd"):
        if os.path.isdir(os.path.join(sys_module, name)):
            return name
    return None


def vfio_pci_present(sys_module: str = _SYS_MODULE) -> bool:
    return os.path.isdir(os.path.join(sys_module, "vfio_pci"))


def read_kvm_params(sys_module: str = _SYS_MODULE) -> dict:
    out: dict = {}
    kvm = os.path.join(sys_module, "kvm", "parameters")
    if os.path.isdir(kvm):
        out["halt_poll_ns"] = _read_int(
            os.path.join(kvm, "halt_poll_ns"))
        out["kvmclock_periodic_sync"] = _read(
            os.path.join(kvm, "kvmclock_periodic_sync"))
        out["tdp_mmu"] = _read(os.path.join(kvm, "tdp_mmu"))
    return out


def read_kvm_intel_amd_params(sys_module: str = _SYS_MODULE) -> dict:
    out: dict = {"variant": None, "nested": None}
    for name in ("kvm_intel", "kvm_amd"):
        d = os.path.join(sys_module, name, "parameters")
        if os.path.isdir(d):
            out["variant"] = name
            out["nested"] = _read(os.path.join(d, "nested"))
            return out
    return out


def stat_dev_kvm(dev_kvm: str = _DEV_KVM) -> dict:
    out: dict = {"present": False}
    try:
        st = os.stat(dev_kvm)
    except OSError:
        return out
    out["present"] = True
    out["mode"] = stat.S_IMODE(st.st_mode)
    out["uid"] = st.st_uid
    out["gid"] = st.st_gid
    try:
        out["group_name"] = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        out["group_name"] = None
    return out


def _nested_active(value: Optional[str]) -> bool:
    if value is None:
        return False
    v = value.strip().lower()
    return v in ("y", "1", "true")


def classify(kvm_present: bool, intel_amd: dict,
              kvm_params: dict, vfio_present: bool,
              dev_kvm: dict) -> dict:
    # 1) kvm_disabled
    if not kvm_present or not dev_kvm.get("present"):
        if not kvm_present and not dev_kvm.get("present"):
            return {"verdict": "unknown",
                    "reason": ("Neither /sys/module/kvm nor "
                              "/dev/kvm is present — host kernel "
                              "compiled without KVM, or running in "
                              "a container."),
                    "recommendation": ""}
        return {"verdict": "kvm_disabled",
                "reason": ("KVM is partially configured : "
                          f"/sys/module/kvm present={kvm_present}, "
                          f"/dev/kvm present={dev_kvm.get('present')}. "
                          f"Guests will fail to start."),
                "recommendation": _recipe_kvm_disabled()}

    # 2) nested_on_with_passthrough
    if _nested_active(intel_amd.get("nested")) and vfio_present:
        return {"verdict": "nested_on_with_passthrough",
                "reason": (f"{intel_amd.get('variant')} has nested "
                          f"= {intel_amd.get('nested')} AND "
                          f"vfio_pci is loaded — IOMMU isolation "
                          f"is weakened, shadow EPT burns CPU."),
                "recommendation": _recipe_disable_nested(
                    intel_amd.get("variant"))}

    # 3) halt_poll_excessive
    hp = kvm_params.get("halt_poll_ns")
    if hp is not None and hp > _HALT_POLL_NS_MAX_OK:
        return {"verdict": "halt_poll_excessive",
                "reason": (f"kvm.halt_poll_ns = {hp} (> "
                          f"{_HALT_POLL_NS_MAX_OK}). Bursty "
                          f"inference VMs burn ~10 % idle power."),
                "recommendation": _recipe_halt_poll()}

    # 4) group_perm_missing
    mode = dev_kvm.get("mode")
    if mode is not None:
        world_write = bool(mode & 0o002)
        group_rw = (mode & 0o060) == 0o060
        wrong_group = dev_kvm.get("group_name") not in (
            "kvm", "wheel", "root")
        if world_write or not group_rw or wrong_group:
            return {"verdict": "group_perm_missing",
                    "reason": (f"/dev/kvm mode 0{mode:o} "
                              f"group={dev_kvm.get('group_name')}. "
                              f"Expected 0660 root:kvm."),
                    "recommendation": _recipe_dev_perms()}

    return {"verdict": "ok",
            "reason": ("KVM available, gates look reasonable."),
            "recommendation": ""}


def status(config=None,
            sys_module: str = _SYS_MODULE,
            dev_kvm: str = _DEV_KVM) -> dict:
    kvm_present = kvm_module_present(sys_module)
    intel_amd = read_kvm_intel_amd_params(sys_module)
    kvm_params = read_kvm_params(sys_module)
    vfio_present = vfio_pci_present(sys_module)
    dk = stat_dev_kvm(dev_kvm)
    ok = bool(kvm_present or dk.get("present"))
    verdict = classify(kvm_present, intel_amd, kvm_params,
                          vfio_present, dk)
    return {"ok": ok,
              "kvm_module_present": kvm_present,
              "kvm_variant": intel_amd.get("variant"),
              "nested": intel_amd.get("nested"),
              "kvm_params": kvm_params,
              "vfio_pci_loaded": vfio_present,
              "dev_kvm": dk,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_kvm_disabled() -> str:
    return ("# Check BIOS for Intel VT-x / AMD-V :\n"
            "egrep -c '(vmx|svm)' /proc/cpuinfo\n"
            "# Load the right KVM module :\n"
            "sudo modprobe kvm-intel  # or kvm-amd\n"
            "ls -l /dev/kvm\n")


def _recipe_disable_nested(variant: Optional[str]) -> str:
    var = variant or "kvm_intel"
    return (f"# Disable nested virt before VFIO/GPU passthrough :\n"
            f"echo N | sudo tee /sys/module/{var}/parameters/nested 2>/dev/null || true\n"
            f"sudo modprobe -r {var}\n"
            f"sudo modprobe {var} nested=0\n"
            f"# Persist via /etc/modprobe.d/{var}.conf :\n"
            f"#   options {var} nested=0\n")


def _recipe_halt_poll() -> str:
    return ("# Lower the halt-poll window — reduces idle power on\n"
            "# bursty inference workloads :\n"
            "echo 50000 | sudo tee /sys/module/kvm/parameters/halt_poll_ns\n"
            "# Persist via /etc/modprobe.d/kvm.conf :\n"
            "#   options kvm halt_poll_ns=50000\n")


def _recipe_dev_perms() -> str:
    return ("# Restore the standard /dev/kvm ownership :\n"
            "sudo chgrp kvm /dev/kvm\n"
            "sudo chmod 0660 /dev/kvm\n"
            "# Persist via /etc/udev/rules.d/65-kvm.rules :\n"
            "#   KERNEL==\"kvm\", GROUP=\"kvm\", MODE=\"0660\"\n")
