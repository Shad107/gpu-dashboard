"""Module virt_guest_detect_audit — VM guest detection (R&D #60.4).

Distinct from R&D #59.1 dmi_smbios_audit (which only reports the
DMI/SMBIOS board strings). This module checks the *live*
hypervisor surface :

  /sys/firmware/qemu_fw_cfg/        QEMU/KVM guest signal
  /sys/hypervisor/{type, uuid}      Xen guest signal
  /proc/cpuinfo flags hypervisor     generic hypervisor signal
  /sys/bus/virtio/devices/          virtio-bus devices present

Why this matters on an LLM rig :

* User assumes bare-metal GPU access — actually running inside a
  Proxmox/QEMU guest. Explains mysterious VFIO reset failures,
  missing MSR access for power capping (RAPL/intel_pstate
  surface absent), and `nvidia-smi` ECC gaps.
* Nested virt + GPU passthrough is a rare-but-real config that
  needs different /dev/kvm tuning.

Verdicts (priority-ordered) :
  running_as_kvm_guest_with_nvidia_passthrough_quirk
                                  qemu_fw_cfg present AND NVIDIA
                                  display GPU bound on PCI bus.
  nested_virt_detected            hypervisor CPU flag set AND
                                  /sys/module/kvm present AND
                                  /sys/devices/virtual/misc/kvm
                                  present (we're a guest *and* we
                                  run KVM).
  running_as_guest_generic        any guest signal (qemu_fw_cfg /
                                  Xen hypervisor / cpuinfo flag /
                                  virtio).
  bare_metal                      no guest signals.
  unknown                         /proc/cpuinfo unreadable.

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional


NAME = "virt_guest_detect_audit"


_QEMU_FW_CFG = "/sys/firmware/qemu_fw_cfg"
_SYS_HYPERVISOR = "/sys/hypervisor"
_PROC_CPUINFO = "/proc/cpuinfo"
_SYS_BUS_VIRTIO = "/sys/bus/virtio/devices"
_SYS_MODULE_KVM = "/sys/module/kvm"
_SYS_KVM_MISC = "/sys/devices/virtual/misc/kvm"
_SYS_BUS_PCI = "/sys/bus/pci/devices"

_NVIDIA_VENDOR = "0x10de"
_DISPLAY_BASE_CLASS = 0x03


def _read(p: str) -> Optional[str]:
    try:
        with open(p) as f:
            return f.read()
    except OSError:
        return None


def has_qemu_fw_cfg(path: str = _QEMU_FW_CFG) -> bool:
    return os.path.isdir(path)


def has_xen_hypervisor(path: str = _SYS_HYPERVISOR) -> Optional[str]:
    t = _read(os.path.join(path, "type"))
    return t.strip() if t else None


def has_hypervisor_cpu_flag(proc_cpuinfo: str = _PROC_CPUINFO
                               ) -> bool:
    text = _read(proc_cpuinfo)
    if not text:
        return False
    for line in text.splitlines():
        if line.startswith("flags"):
            return "hypervisor" in line.split()
    return False


def list_virtio_devices(sys_bus_virtio: str = _SYS_BUS_VIRTIO
                          ) -> List[str]:
    if not os.path.isdir(sys_bus_virtio):
        return []
    return sorted(os.listdir(sys_bus_virtio))


def has_kvm_module(sys_module_kvm: str = _SYS_MODULE_KVM,
                      sys_kvm_misc: str = _SYS_KVM_MISC) -> bool:
    return (os.path.isdir(sys_module_kvm) and
              os.path.isdir(sys_kvm_misc))


def has_nvidia_display_gpu(sys_bus_pci: str = _SYS_BUS_PCI
                              ) -> List[str]:
    if not os.path.isdir(sys_bus_pci):
        return []
    out: List[str] = []
    for bdf in sorted(os.listdir(sys_bus_pci)):
        ddir = os.path.join(sys_bus_pci, bdf)
        try:
            with open(os.path.join(ddir, "vendor")) as f:
                vendor = f.read().strip()
            with open(os.path.join(ddir, "class")) as f:
                klass = f.read().strip()
        except OSError:
            continue
        if vendor != _NVIDIA_VENDOR:
            continue
        try:
            base = (int(klass, 16) >> 16) & 0xff
        except ValueError:
            continue
        if base == _DISPLAY_BASE_CLASS:
            out.append(bdf)
    return out


def classify(qemu_fw: bool, xen_type: Optional[str],
              hyp_flag: bool, virtio_devs: List[str],
              kvm_loaded: bool,
              nvidia_gpus: List[str],
              proc_cpuinfo_ok: bool) -> dict:
    if not proc_cpuinfo_ok:
        return {"verdict": "unknown",
                "reason": "/proc/cpuinfo unreadable.",
                "recommendation": ""}

    is_guest = bool(qemu_fw or xen_type or hyp_flag or
                       virtio_devs)

    # 1) running_as_kvm_guest_with_nvidia_passthrough_quirk
    if qemu_fw and nvidia_gpus:
        sample = ", ".join(nvidia_gpus[:3])
        return {"verdict": ("running_as_kvm_guest_with_"
                              "nvidia_passthrough_quirk"),
                "reason": (f"QEMU/KVM guest detected (qemu_fw_cfg "
                          f"present) AND NVIDIA display GPU(s) on "
                          f"PCI bus : {sample}. This is a GPU-"
                          f"passthrough VM — MSR / RAPL / ECC "
                          f"surfaces may be missing."),
                "recommendation": _recipe_passthrough_guest()}

    # 2) nested_virt_detected
    if is_guest and kvm_loaded:
        return {"verdict": "nested_virt_detected",
                "reason": ("Hypervisor flag is set AND /dev/kvm is "
                          "available — this host is a guest *and* "
                          "runs nested KVM."),
                "recommendation": _recipe_nested()}

    # 3) running_as_guest_generic
    if is_guest:
        signals: List[str] = []
        if qemu_fw:
            signals.append("qemu_fw_cfg")
        if xen_type:
            signals.append(f"xen({xen_type})")
        if hyp_flag:
            signals.append("cpuinfo:hypervisor")
        if virtio_devs:
            signals.append(f"virtio×{len(virtio_devs)}")
        return {"verdict": "running_as_guest_generic",
                "reason": (f"Guest signals : {', '.join(signals)}. "
                          f"Bare-metal-only sysfs paths legitimately "
                          f"absent."),
                "recommendation": _recipe_guest_acknowledge()}

    return {"verdict": "bare_metal",
            "reason": ("No guest signals — running on bare metal."),
            "recommendation": ""}


def status(config=None,
            qemu_fw_cfg: str = _QEMU_FW_CFG,
            sys_hypervisor: str = _SYS_HYPERVISOR,
            proc_cpuinfo: str = _PROC_CPUINFO,
            sys_bus_virtio: str = _SYS_BUS_VIRTIO,
            sys_module_kvm: str = _SYS_MODULE_KVM,
            sys_kvm_misc: str = _SYS_KVM_MISC,
            sys_bus_pci: str = _SYS_BUS_PCI) -> dict:
    qemu_fw = has_qemu_fw_cfg(qemu_fw_cfg)
    xen_type = has_xen_hypervisor(sys_hypervisor)
    hyp_flag = has_hypervisor_cpu_flag(proc_cpuinfo)
    virtio_devs = list_virtio_devices(sys_bus_virtio)
    kvm_loaded = has_kvm_module(sys_module_kvm, sys_kvm_misc)
    nvidia_gpus = has_nvidia_display_gpu(sys_bus_pci)
    cpuinfo_ok = _read(proc_cpuinfo) is not None
    verdict = classify(qemu_fw, xen_type, hyp_flag,
                          virtio_devs, kvm_loaded, nvidia_gpus,
                          cpuinfo_ok)
    return {"ok": cpuinfo_ok,
              "qemu_fw_cfg_present": qemu_fw,
              "xen_type": xen_type,
              "cpu_hypervisor_flag": hyp_flag,
              "virtio_device_count": len(virtio_devs),
              "virtio_devices": virtio_devs,
              "kvm_loaded": kvm_loaded,
              "nvidia_gpus": nvidia_gpus,
              "verdict": verdict}


# ── recovery recipes ────────────────────────────────────────────

def _recipe_passthrough_guest() -> str:
    return ("# Confirm GPU passthrough is healthy :\n"
            "lspci -nnk -d 10de:  # in the guest\n"
            "# Verify vfio-pci binding on the host (Proxmox) :\n"
            "#   lspci -nnk -d 10de:  | grep -A2 VGA\n"
            "# Recommend host VM config :\n"
            "#   cpu: host\n"
            "#   args: -object memory-backend-memfd,size=N\n"
            "# Some sysfs paths (RAPL / ECC / MCE banks) are\n"
            "# legitimately absent in the guest — flag them as\n"
            "# 'expected' rather than real issues.\n")


def _recipe_nested() -> str:
    return ("# Nested virt confirmed. If your inner VMs need\n"
            "# performance, ensure nested EPT is enabled :\n"
            "cat /sys/module/kvm_intel/parameters/nested  # or kvm_amd\n"
            "# Trade-off : nested EPT costs CPU vs flat hypervisor.\n")


def _recipe_guest_acknowledge() -> str:
    return ("# Running as a VM guest. The following modules will\n"
            "# legitimately surface 'unknown' or empty :\n"
            "#   RAPL (powercap), intel_pstate, machinecheck,\n"
            "#   EDAC, hwmon (most chips), IOMMU groups, DMI\n"
            "#   (sys_vendor matches the VM ; not the physical box).\n"
            "# Use the host's sysfs / IPMI for those metrics.\n")
