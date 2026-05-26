#!/usr/bin/env python3
"""F5 — Generate the integrations taxonomy + inject data-cat attributes.

This script does two things:
  1. Reads every `i18n.t("integrations.<PREFIX>.title")` from
     SettingsModal.svelte, classifies each PREFIX into one of the
     13 categories below, and writes the result to
     frontend/src/lib/integrations-taxonomy.ts.
  2. Adds `data-cat="<category>"` and `data-prefix="<prefix>"`
     attributes on every `<div class="card-form" ...>` so the
     Svelte filter can hide non-matching cards.

Re-run after adding new cards. Safe to re-run — already-tagged
divs are skipped.

Usage:
  python3 scripts/build-integrations-taxonomy.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "frontend/src/components/SettingsModal.svelte"
TAXONOMY = ROOT / "frontend/src/lib/integrations-taxonomy.ts"


# Order matters — earlier patterns win. Categories are intentionally
# permissive: prefer over-categorising a prefix to leaving it in
# "misc". When in doubt, look at the actual module under
# src/gpu_dashboard/modules/<name>.py.
PATTERNS = [
    ("gpu_driver", r"^(vbios|dkms|nvidia|nv$|nv[a-rt-z]|nvr|nvram|nvdrm|cuda|vram|drm|dri|mig|nvenc|nvdec|nvlink|gsp|bwgauge|bestgpu|fanc|coolb|warm|wmi|gpu|driver|flavor|fbvtcon|dridebugfs|gpuaff|acc|ttmpool|xid|persistence|hf)"),
    ("pcie_bus", r"^(pcie|aer|iommu|vfio|pcierec|busres|aspm|ari|bdf|vmd|busreset|usb|tbt|thunder|xhci|hotswap|iomempci|iio|msi|pcinuma|rebar|sriov|typec)"),
    ("memory_swap", r"^(vm|mem|swap|zram|ksm|thp|numa|buddy|page|smaps|huge|zsw|zswap|zone|zi|cgmem|hugetlb|khugepaged|kpageflags|dirtybytes|dmabuf|dmaheap|dma$|dtmem|cmem|cxldax|damoncma|kvmmmu|edac|ecc|dedup|leak|ctxleak|oom|maps|mglru|retire|slab|sdcache)"),
    ("storage_fs", r"^(xfs|fs|mount|blk|btrfs|ext|nvme|md$|md[a-z]|raid|disk|aiof|bdi|backing|fanot|bdev|hdparm|blockstack|blockint|fuse|dio|ino|iodelay|iouring|dmmod|eddmmc|dmabufbuf|atapor?t?|hpa|fdinfokinds|loopdev|mtdflash|nfsd|nfsmount|nfsmounts|pipemq|satapm|scsi|trim|vfs|zfsarc)"),
    ("network", r"^(net|bpfjit|busy|qdisc|bbr|af|arp|wol|bond|bridge|wifi|ipv|sock|nic|tcp|udp|bq|bql|nft|conntrack|ct$|unixsock|ephport|devlink|gpb|softnet|noc|peers|routes)"),
    ("power_thermal", r"^(thermal|cpufreq|hwp|cool|tdp|batt|fan|pwm|cpuidle|cstate|powercap|epp|epb|boost|carbon|wall|ups|reg|blpwm|cdev|hwmon|cppc|devfreq|devfreqevt|freqres|idleres|clksummary|wakeup|wakup|pdwakeup|pmasync|pstate|pwrenv|rapl|rpm|setspeed|suspend|suspendsel|tariff|thermzones|throttle|tslow|umwait|psu)"),
    ("security_lsm", r"^(lsm|ima|lockdown|key|aslr|kptr|sec|auth|yama|airgap|caps|ambient|seccomp|abicompat|userspacehard|veil|harden|cpuvuln|cpvuln|crypto|ent$|entropy|binfmt|discardcaps|kfence|kr$|kr[a-z]|pam|proccaps|rlim|rlimit|sgx|sigenforce|smt|splitlock|tbits|tpm)"),
    ("boot_firmware", r"^(efi|kexec|pstore|microcode|bgrt|acpi|fw|bootstatus|boot|smbios|dmi|cmdline|kcfg|kmod|esrt|wd|wdog|watchdog|devcd|mce|modint|modparams|modprobedrift|modrefcnt|remoteproc|spifw|sysctldev|sysd|lockupwatchdog)"),
    ("irq_sched", r"^(irq|rcu|sched|kthread|ksoftirq|autogroup|nohz|preempt|softirq|wq|workqueue|wqpe|clock|tickless|tsc|hpet|jiffies|timer|batch|core|cpu$|cpud|cpui|cpuis|cput|hcpu|jr|jiff|cmd|loadavg|lock$|lockdep|ptp|rseq|rtc|taskaff|uclamp)"),
    ("tracing_bpf", r"^(ftrace|bpf|btf|perf|kprobe|ebpf|trace|live|tracepoint|uprobe|kfunc|dyndbg|kmsg|pmu|tracingbuf|tracingevt|uncore)"),
    ("containers", r"^(container|cgroup|namespace|ns|cg|delegate|iso|userns|pidns|mntns|chroot|ipc|kvm|overlay|pidlimits|resctrl|sysvipc)"),
    ("input_misc", r"^(input|hid|kbd|leds|extcon|rfkill|bt$|bt[a-z]|usbhid|alsa|audio|v4l|media|sound|backlight|codec|mei|miscchar|ttyserial|uiogpio)"),
    ("meta_diag", r"^(collprof|pcierec|fleet|telegram|alert|service|llm|lmstudio|discord|diag|usage|profile|bugrep|crash|tunctl|history|setup|wizard|stat|update|version|virt|cache|bench|cost|hungtask|host|dr$|dr[a-z]|notif|oopswarn|printkpace|procdeep|procio|proclocks|procmaps|procns|procregistry|procsysauxv|psi|psiirq|reset|ring|sysrq|sysrqcad|taint|uevent|wchan|mprb|mps|npc|pan|pwidth|ftr|uc$|uc[a-z])"),
]
DEFAULT = "misc"
LABELS = [
    ("gpu_driver", "🎮", "GPU & driver"),
    ("pcie_bus", "🚍", "PCIe & bus"),
    ("memory_swap", "🧠", "Memory & swap"),
    ("storage_fs", "💾", "Storage & FS"),
    ("network", "🌐", "Network"),
    ("power_thermal", "⚡", "Power & thermal"),
    ("security_lsm", "🛡️", "Security & LSM"),
    ("boot_firmware", "🔧", "Boot & firmware"),
    ("irq_sched", "⏰", "IRQ & scheduling"),
    ("tracing_bpf", "🔬", "Tracing & BPF"),
    ("containers", "📦", "Containers"),
    ("input_misc", "🎛️", "I/O peripherals"),
    ("meta_diag", "📊", "Diagnostics & meta"),
    ("misc", "📚", "Other"),
]


def extract_prefixes(text: str) -> list:
    pattern = re.compile(r'i18n\.t\("integrations\.([a-z_]+)\.title"\)')
    return sorted(set(pattern.findall(text)))


def classify(prefix: str) -> str:
    for cat, pat in PATTERNS:
        if re.match(pat, prefix):
            return cat
    return DEFAULT


def write_taxonomy(prefixes: list, classification: dict) -> None:
    counts = {}
    for p in prefixes:
        c = classification[p]
        counts[c] = counts.get(c, 0) + 1
    out = "// AUTO-GENERATED by scripts/build-integrations-taxonomy.py — DO NOT EDIT BY HAND\n"
    out += "// F5 — Integrations refactor: maps each card's i18n prefix to a category.\n\n"
    out += "export const CATEGORIES = [\n"
    for slug, emoji, label in LABELS:
        n = counts.get(slug, 0)
        out += f'  {{ id: "{slug}", emoji: "{emoji}", label: "{label}", count: {n} }},\n'
    out += "] as const;\n\n"
    out += "export type CategoryId = (typeof CATEGORIES)[number]['id'] | 'all';\n\n"
    out += "export const PREFIX_TO_CATEGORY: Record<string, Exclude<CategoryId, 'all'>> = {\n"
    for p in sorted(classification.keys()):
        out += f'  "{p}": "{classification[p]}",\n'
    out += "};\n\n"
    out += "export function categoryOf(prefix: string): Exclude<CategoryId, 'all'> {\n"
    out += '  return PREFIX_TO_CATEGORY[prefix] ?? "misc";\n'
    out += "}\n"
    TAXONOMY.write_text(out)


def inject_data_cat(prefix_map: dict) -> int:
    """Add data-cat + data-prefix attributes to each card-form div
    whose h4 contains an integrations i18n title. Skip already-tagged."""
    text = SETTINGS.read_text()
    lines = text.split("\n")
    modified = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if ('class="card-form"' in line
                and "modal.section" in line
                and "integrations" in line
                and "data-cat=" not in line):
            prefix = None
            for j in range(i + 1, min(i + 9, len(lines))):
                m = re.search(
                    r'i18n\.t\("integrations\.([a-z_]+)\.title"\)',
                    lines[j])
                if m:
                    prefix = m.group(1)
                    break
            if prefix:
                cat = prefix_map.get(prefix, "misc")
                lines[i] = line.replace(
                    'class="card-form"',
                    f'class="card-form" data-cat="{cat}" data-prefix="{prefix}"')
                modified += 1
        i += 1
    SETTINGS.write_text("\n".join(lines))
    return modified


def main() -> int:
    text = SETTINGS.read_text()
    prefixes = extract_prefixes(text)
    classification = {p: classify(p) for p in prefixes}
    misc_count = sum(1 for c in classification.values() if c == DEFAULT)
    write_taxonomy(prefixes, classification)
    modified = inject_data_cat(classification)
    print(f"Found {len(prefixes)} unique prefixes")
    if misc_count:
        misc_list = [p for p, c in classification.items() if c == DEFAULT]
        print(f"WARNING: {misc_count} prefixes fell back to 'misc':")
        print("  " + " ".join(sorted(misc_list)))
    print(f"Wrote {TAXONOMY.relative_to(ROOT)}")
    print(f"Injected data-cat into {modified} card div(s) in {SETTINGS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
