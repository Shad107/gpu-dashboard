"""Module msi_inventory — MSI-X vector inventory + interrupt mode (R&D #30.1).

NVIDIA GPUs can allocate up to 64 MSI-X interrupt vectors. With proper
MSI-X enabled, each CUDA stream / DMA queue gets its own vector and
scales smoothly across CPU cores. When MSI-X is *not* allocated —
because `pci=nomsi` is on the kernel cmdline (Anaconda installers
sometimes add it), because `NVreg_EnableMSI=0` is in modprobe.d, or
because the chipset / firmware refuses MSI-X for the bridge — the
driver falls back to a single MSI vector (or worse, a legacy IRQ
line shared with other devices). That fallback costs ~10 % on CUDA
host→device copy latency and never appears in nvidia-smi.

This module reads:
  /sys/bus/pci/devices/<bdf>/msi_irqs/        list of MSI-X vectors
  /sys/bus/pci/devices/<bdf>/irq              legacy IRQ if applicable
  /proc/interrupts                            per-CPU hits + controller
                                              (PCI-MSI-X vs PCI-MSI)

Verdicts:
  msix_active   >= 4 vectors, controller is "PCI-MSI-X" → optimal
  msi_active    1-3 vectors OR controller is "PCI-MSI" → fallback,
                surface NVreg_EnableMSI=1 + cmdline check
  legacy_irq    no msi_irqs entries → IO-APIC fallback, ~10 % tax
  unknown       cannot read sysfs

stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Optional


NAME = "msi_inventory"


_PCI_ROOT = "/sys/bus/pci/devices"
_PROC_INTERRUPTS = "/proc/interrupts"


def find_nvidia_bdfs(pci_root: str = _PCI_ROOT) -> list[str]:
    """NVIDIA VGA-class devices only (filter out the GPU's onboard HDA)."""
    out: list[str] = []
    try:
        for n in sorted(os.listdir(pci_root)):
            vp = os.path.join(pci_root, n, "vendor")
            cp = os.path.join(pci_root, n, "class")
            try:
                with open(vp) as f:
                    if f.read().strip().lower() != "0x10de":
                        continue
                with open(cp) as f:
                    klass = f.read().strip().lower()
                if klass.startswith("0x03"):
                    out.append(n)
            except OSError:
                continue
    except OSError:
        return []
    return out


def list_msi_vectors(pci_root: str, bdf: str) -> list[int]:
    p = os.path.join(pci_root, bdf, "msi_irqs")
    try:
        names = os.listdir(p)
    except OSError:
        return []
    out: list[int] = []
    for n in names:
        try:
            out.append(int(n))
        except ValueError:
            continue
    return sorted(out)


def read_irq(pci_root: str, bdf: str) -> Optional[int]:
    p = os.path.join(pci_root, bdf, "irq")
    try:
        with open(p) as f:
            v = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return v if v > 0 else None


_IRQ_LINE_RE = re.compile(
    r"^\s*(\d+):\s+((?:\d+\s+)+)([\w:.\-]+)(?:\s+(.+))?$"
)


def parse_interrupts(text: str) -> dict:
    """Return {irq_num: {"count": total, "controller": str, "device": str}}.

    Format per row:  IRQ:  CPU0 CPU1 ... CPUN  CONTROLLER  DEV1 DEV2 ...
    We sum the CPU columns into `count`, take the first non-numeric token
    as `controller`, and the remainder as `device`.
    """
    out: dict = {}
    for line in text.splitlines():
        # Quick skip of the header line ("CPU0  CPU1  ...") and lines
        # that don't start with a digit-only IRQ id.
        stripped = line.strip()
        if not stripped or not stripped.split(":", 1)[0].strip().isdigit():
            continue
        head, _, rest = stripped.partition(":")
        try:
            irq = int(head.strip())
        except ValueError:
            continue
        toks = rest.split()
        # Walk until we hit a non-numeric token — that's the controller.
        count = 0
        i = 0
        while i < len(toks) and toks[i].isdigit():
            count += int(toks[i])
            i += 1
        controller = toks[i] if i < len(toks) else ""
        device = " ".join(toks[i + 1:]) if i + 1 < len(toks) else ""
        # `device` may start with "<n>-edge" / "<n>-fasteoi" — strip
        # that leading word so the action name (e.g. "nvidia") is
        # actually the last column the user reads.
        if device:
            parts = device.split(None, 1)
            if parts and re.match(r"^\d+-", parts[0]):
                device = parts[1] if len(parts) > 1 else ""
        out[irq] = {"count": count, "controller": controller,
                    "device": device}
    return out


def detect_mode(controller: Optional[str]) -> str:
    if not controller:
        return "unknown"
    c = controller.upper()
    if "MSI-X" in c:
        return "MSI-X"
    if "MSI" in c:
        return "MSI"
    if "IO-APIC" in c or "APIC" in c:
        return "legacy"
    return "unknown"


_MIN_MSIX_VECTORS = 4


def classify(vectors: list, controllers: list) -> dict:
    if not vectors:
        return {
            "verdict": "legacy_irq",
            "reason": ("GPU has no MSI/MSI-X vectors allocated — falling "
                       "back to a legacy IO-APIC IRQ line. CUDA host→"
                       "device copies pay ~10 % latency tax and the "
                       "single line may be shared with other devices."),
            "recommendation": (
                "# Check /proc/cmdline for `pci=nomsi` and remove it:\n"
                "grep pci=nomsi /proc/cmdline\n"
                "# Then enable MSI for the nvidia driver:\n"
                "echo 'options nvidia NVreg_EnableMSI=1' | "
                "sudo tee /etc/modprobe.d/nvidia-msi.conf\n"
                "sudo update-initramfs -u && reboot"
            ),
        }
    is_msix = any(detect_mode(c) == "MSI-X" for c in controllers)
    if is_msix and len(vectors) >= _MIN_MSIX_VECTORS:
        return {
            "verdict": "msix_active",
            "reason": (f"GPU has {len(vectors)} MSI-X vectors active — "
                       f"each CUDA stream / DMA queue gets its own "
                       f"interrupt, scaling smoothly across CPU cores."),
            "recommendation": "",
        }
    # Either MSI mode, or few-vector fallback
    return {
        "verdict": "msi_active",
        "reason": (f"GPU has only {len(vectors)} MSI vector(s), "
                   f"controller(s)={list(set(controllers))} — driver "
                   f"fell back from MSI-X. CUDA host→device copies "
                   f"pay ~10 % latency tax that nvidia-smi never "
                   f"surfaces."),
        "recommendation": (
            "# Check whether NVreg_EnableMSI is explicitly disabled:\n"
            "cat /sys/module/nvidia/parameters/NVreg_EnableMSI\n"
            "# If 0, enable it and rebuild initramfs:\n"
            "echo 'options nvidia NVreg_EnableMSI=1' | "
            "sudo tee /etc/modprobe.d/nvidia-msi.conf\n"
            "sudo update-initramfs -u && reboot\n"
            "# Also verify chipset PCIe ports support MSI-X "
            "(some boards' BIOS toggles disable it)."
        ),
    }


_RANK = {"msix_active": 0, "unknown": 1, "msi_active": 2, "legacy_irq": 3}


def status(cfg=None) -> dict:
    gpus = find_nvidia_bdfs(_PCI_ROOT)
    if not gpus:
        return {"ok": True, "device_count": 0,
                "cards": [], "worst_verdict": "no_gpus"}
    try:
        with open(_PROC_INTERRUPTS) as f:
            interrupts = parse_interrupts(f.read())
    except OSError:
        interrupts = {}
    cards: list = []
    worst = "msix_active"
    for gpu in gpus:
        vectors = list_msi_vectors(_PCI_ROOT, gpu)
        legacy_irq = read_irq(_PCI_ROOT, gpu)
        irqs_to_check = vectors if vectors else (
            [legacy_irq] if legacy_irq is not None else []
        )
        controllers = [interrupts.get(v, {}).get("controller", "")
                        for v in irqs_to_check]
        total = sum(interrupts.get(v, {}).get("count", 0)
                     for v in irqs_to_check)
        v = classify(vectors, controllers)
        modes = [detect_mode(c) for c in controllers if c]
        mode = "unknown"
        if "MSI-X" in modes:
            mode = "MSI-X"
        elif "MSI" in modes:
            mode = "MSI"
        elif "legacy" in modes:
            mode = "legacy"
        elif not vectors:
            mode = "legacy"
        cards.append({
            "gpu_bdf": gpu,
            "vector_count": len(vectors),
            "vectors": vectors,
            "legacy_irq": legacy_irq,
            "mode": mode,
            "controllers": list(dict.fromkeys(controllers)),  # dedup
            "total_interrupts": total,
            "verdict": v,
        })
        if _RANK.get(v["verdict"], 0) > _RANK.get(worst, 0):
            worst = v["verdict"]
    return {"ok": True, "device_count": len(cards),
            "cards": cards, "worst_verdict": worst}
