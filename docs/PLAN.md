# gpu-dashboard — Living Plan

Plan vivant. Mis à jour à chaque cycle du loop autonome.
Source de vérité pour : ce qui est fait, en cours, à venir.

**Last updated** : 2026-05-22 13:46 (R&D #5 COMPLETE — 3/3 priority shipped)
**Latest commit** : `<head>`
**Tests** : 704+ passing · **CI** : ✅ green

## ✅ R&D iteration #4 complete (5/5 priority)
4.1 Prometheus /metrics · 4.2 clocks-event-reasons decoder · 4.3 ECC health · 4.4 fan curve hysteresis · 4.5 idle-state audit.
Backlog still queued (4.6-4.11) : per-process tab, MangoHud bridge, workload tagger, PCIe probe, allow/block list, undervolt auto-tuner.

## R&D #39 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-38), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8 + 29.2/29.4/29.5/29.6 + 30.4/30.6/30.7/30.8 + 31.5/31.6/31.7/31.8 + 32.2/32.6/32.7/32.8 + 33.3/33.5/33.7/33.8 + 34.5/34.6/34.7/34.8 + 35.5/35.6/35.7/35.8 + 36.5/36.6/36.7/36.8 + 37.5/37.6/37.7/37.8 + 38.5/38.6/38.7/38.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 39.1 | **`/proc/cmdline` unusual kernel boot-parameter parser + LLM-rig "should be there but isn't" + "shouldn't be there but is" advisor** | XS | 5 | The single line at `/proc/cmdline` (e.g. `BOOT_IMAGE=/boot/vmlinuz-6.8.0-49-generic root=UUID=… ro quiet splash nvidia-drm.modeset=1 nvidia.NVreg_PreserveVideoMemoryAllocations=1`) is the highest-leverage "what is this box actually doing on every boot" string in all of Linux — and today nothing in the shipped stack parses it. The recurring homelab foot-guns it surfaces: (1) `mitigations=off` *missing* on a single-user LAN-only inference rig (shipped #37.1 cpu_vulns flags the cost, this flags the missing fix), (2) `nvidia-drm.modeset=1` *missing* on Wayland + nvidia 555+ setups → Sway/Hyprland tearing, (3) `nvidia.NVreg_PreserveVideoMemoryAllocations=1` *missing* → suspend/resume blanks VRAM on shipped #suspend_guard's watchlist, (4) `iommu=pt` + `intel_iommu=on` / `amd_iommu=on` *missing* on a vfio-passthrough rig (shipped #vfio_sentinel can't help if IOMMU is off at boot), (5) `pcie_aspm=off` *present* on a homelab box (defeats shipped #28.6 pcie_aspm wake-error mitigations and wastes 5-10 W idle), (6) `isolcpus=` / `nohz_full=` / `rcu_nocbs=` *misconfigured* (intended for low-latency inference, often left as a copy-paste residue from a guide that didn't match the user's core layout — silently strands cores from the scheduler), (7) `transparent_hugepage=never` *present* (defeats shipped #31.1 thp_audit's khugepaged story), (8) `numa_balancing=disable` *present* on a multi-socket box → cross-NUMA penalty bypasses shipped #35.3 numa_placement advice, (9) `acpi=off` / `noapic` / `pci=noaer` *present* → emergency-mode boot leftover that disables IRQ MSI-X (defeats shipped #38.4 gpu_irq_affinity), AER (defeats shipped #28.5 + #38.1 pcie_aer_trend), and modern ACPI thermal (defeats shipped #thermal_zones). Parse `/proc/cmdline` once (one read), tokenise into key=value pairs (handles quoted values, escaped chars), build a curated allowlist of ~40 "expected on a tuned inference rig" params + a denylist of ~25 "you probably didn't mean to leave this on" params, classify each token into 4 verdicts: (a) expected-and-present = OK, (b) expected-but-missing = recommend addition with paste-ready GRUB_CMDLINE_LINUX_DEFAULT snippet, (c) unexpected-but-harmless = info, (d) unexpected-and-degrading = flag with explanation + removal recipe. Cross-references shipped #29.1 kmod_params (`nvidia.*` cmdline params override modprobe.d), shipped #37.1 cpu_vulns (mitigations interaction), shipped #38.2 modprobe_audit (where the `nvidia.NVreg_*` lives long-term), shipped #28.6 pcie_aspm, shipped #38.4 gpu_irq_affinity, shipped #31.1 thp_audit, shipped #35.3 numa_placement, shipped #suspend_guard. Emit a paste-ready `sudo sed -i 's|GRUB_CMDLINE_LINUX_DEFAULT=".*"|GRUB_CMDLINE_LINUX_DEFAULT="… mitigations=off nvidia-drm.modeset=1 nvidia.NVreg_PreserveVideoMemoryAllocations=1 …"|' /etc/default/grub && sudo update-grub` snippet for the union of fixes. The most "explains a dozen forum threads in one diff view" feature per kB of code possible — one read, ~40 lookup-table entries, 4 output cards. First : `modules/cmdline_audit.py` + GET `/api/cmdline-audit`. |
| 39.2 | **`/etc/sysctl.d/*.conf` + `/usr/lib/sysctl.d/*.conf` + `/run/sysctl.d/*.conf` on-disk vs runtime sysctl drift detector** (parallel to shipped #38.2 modprobe_audit, for sysctl knobs) | S | 5 | Shipped `net_sysctl_audit.py` + `vm_sysctl_audit.py` audit the *runtime* values of `/proc/sys/net/*` and `/proc/sys/vm/*` and emit `sysctl.conf` snippets — but neither walks the on-disk `sysctl.d/*.conf` config-merge tree to detect "I pasted the snippet 3 weeks ago, will it actually survive a reboot, and is something else silently overriding it?" Linux's sysctl-merge order is (lowest to highest precedence): `/usr/lib/sysctl.d/` < `/run/sysctl.d/` < `/etc/sysctl.d/` < `/etc/sysctl.conf`, alphabetical within each dir, applied by `systemd-sysctl.service` on boot. The recurring foot-guns: (1) user pastes `vm.swappiness=10` into `/etc/sysctl.conf` but `/etc/sysctl.d/10-cloudimg-settings.conf` (Ubuntu cloud-init residue) re-sets it to 60 at boot because last-applied wins, (2) two snippets in `/etc/sysctl.d/` (`50-default.conf` + `99-llm.conf`) both touch `net.core.rmem_max` with conflicting values, alphabetical-last `99-` wins but the user thinks `50-` is authoritative, (3) `procps`/`systemd-sysctl` package upgrade rewrites `/usr/lib/sysctl.d/99-protect-links.conf` and silently re-enables `fs.protected_hardlinks=1` that defeats a user's container-data-symlink workflow, (4) a sysctl in `/etc/sysctl.d/` syntactically valid but the parameter no longer exists in the running kernel (renamed/removed between 5.x and 6.x) — applied silently with no error, the user thinks it's working, (5) `/run/sysctl.d/` overrides everything in `/etc/` and is recreated on every boot from a tmpfiles snippet the user forgot about. Walk `/usr/lib/sysctl.d/*.conf` + `/run/sysctl.d/*.conf` + `/etc/sysctl.d/*.conf` + `/etc/sysctl.conf` in merge order (alphabetical within each tier), parse `key = value` directives (handle comments, line continuations, `-key` ignore-error syntax), compute the effective "if I rebooted right now" union per key, snapshot the *current* runtime `/proc/sys/<key>` value for every key found, classify into 5 drift verdicts: (a) on-disk = runtime = OK, (b) on-disk says X but runtime says Y → `sysctl --system` not applied since edit, run it, (c) two `.conf` files set the same key with conflicting values, last-alphabetical wins → flag the precedence trap with the resolved value + the silently-overridden ones, (d) `.conf` references a key that doesn't exist in `/proc/sys/` → typo or kernel removed the knob, flag with the `find /proc/sys -name "<basename>"` resolver hint, (e) `/run/sysctl.d/` content present but no corresponding tmpfiles `.conf` in `/etc/tmpfiles.d/` → mystery override, flag for investigation. Cross-references shipped `net_sysctl_audit.py` + `vm_sysctl_audit.py` (runtime view per subtree) for a complete "what sysctl values will this box use on every boot, and are they future-proofed against package upgrades?" answer — the sysctl-side equivalent of shipped #38.2's modprobe-side answer. Emit "your `/etc/sysctl.d/99-llm.conf` sets `vm.swappiness=10` but `/etc/sysctl.d/10-cloudimg-settings.conf` re-applies `vm.swappiness=60` last (alphabetical-last wins, but `99-` should win — check that the file actually exists with `ls -la /etc/sysctl.d/`), runtime is 60". First : `modules/sysctl_d_audit.py` + GET `/api/sysctl-d-audit`. |
| 39.3 | **`/proc/<pid>/coredump_filter` + `/proc/sys/kernel/core_pattern` + `/proc/sys/kernel/core_uses_pid` coredump-readiness auditor for crash-debugging inference workers** | XS | 4 | When a llama-server / ollama / vllm process segfaults on a CUDA error 700 (illegal memory access) or hits a GGML assertion deep in a quantised matmul, the user's *only* post-mortem signal is a core dump — and on every modern distro it silently doesn't get written. The pipeline has six independent gates the user must pass: (1) `ulimit -c unlimited` (shipped #rlimit_audit catches this), (2) `/proc/sys/kernel/core_pattern` must not be `|/usr/share/apport/apport …` (Ubuntu) or `|/usr/lib/systemd/systemd-coredump …` with a `Storage=none` in `/etc/systemd/coredump.conf` (Fedora minimal), or `|/dev/null` (some servers), (3) `/proc/sys/fs/suid_dumpable=0` (default) blocks dumps for setuid binaries — usually irrelevant, but llama-server with `CAP_SYS_NICE` ambient capabilities counts, (4) the per-PID `/proc/<pid>/coredump_filter` bitmask (default `0x33`) must include the *anonymous private* + *anonymous shared* + *huge private* bits for a useful dump of an inference worker whose tensors live in private anon mappings (default 0x33 happens to cover this, but containers and `prctl(PR_SET_DUMPABLE)` calls override it), (5) destination filesystem must have free space (a 64 GB VRAM model with a full host RAM dump is ~150 GB, and `systemd-coredump` truncates by default at `ProcessSizeMax=2G`), (6) compression / format / journal storage policy in `/etc/systemd/coredump.conf` (`Storage=external`, `Compress=yes`, `ProcessSizeMax=`, `ExternalSizeMax=`). Read `/proc/sys/kernel/core_pattern`, `/proc/sys/kernel/core_uses_pid`, `/proc/sys/fs/suid_dumpable`, walk shipped service-discovery PIDs and read each `/proc/<pid>/coredump_filter`, decode the bitmask into named flags (anon_private/anon_shared/file_private/file_shared/elf_headers/huge_private/huge_shared/dax_private/dax_shared), parse `/etc/systemd/coredump.conf` + `/etc/systemd/coredump.conf.d/*.conf` for `Storage`/`Compress`/`ProcessSizeMax`/`ExternalSizeMax`, classify into 4 readiness states: (a) `systemd-coredump` active + `Storage=external` + filter includes anon bits + ulimit unlimited → ready, expect dumps in `/var/lib/systemd/coredump/`, (b) `apport` pattern but apport service disabled or unconfigured for non-apt processes → silent black hole, paste-ready disable+`systemd-coredump` install recipe, (c) `core_pattern=core` (legacy) + cwd at `/` (systemd unit) → dumps land in `/` and silently fail, (d) `Storage=none` Fedora-minimal style → flag, emit `Storage=external` Drop-In. Surface "core_pattern=|/usr/share/apport/apport but apport.service is disabled → your llama-server crashes will leave no trace; either re-enable apport or switch to systemd-coredump with the Drop-In below + bump `ProcessSizeMax=10G` because your model's RSS is 8.2 GB". Niche but high-payoff "I have no idea why ollama died last Tuesday" diagnostic; pairs with shipped `nvrm_tail` (XID / nvrm dmesg logs) + shipped #xid_decoder (CUDA error decode) + shipped #rlimit_audit (RLIMIT_CORE) for a complete post-mortem-readiness story. First : `modules/coredump_ready.py` + GET `/api/coredump-ready`. |
| 39.4 | **`/sys/devices/virtual/dmi/id/chassis_type` + `/sys/firmware/qemu_fw_cfg/` + `/sys/class/dmi/id/{sys_vendor,product_name,board_name,bios_vendor}` virtualization + form-factor classifier for adaptive Settings UI gating** | XS | 4 | The dashboard today shows the *same* Settings → Integrations panel to every user regardless of whether they're running on bare-metal Threadripper desktop, a Proxmox VM with vfio-passthrough GPU, a LXC container on a Synology NAS, a WSL2 environment with `nvidia-container-toolkit`, or a laptop with an eGPU dock — and many cards are irrelevant or actively misleading in some contexts (BIOS update card on a VM, fan-curve card on a passthrough GPU where the host owns the fan, suspend/resume card on a server with no S3 path, UPS card on a laptop with a battery, watchdog card on a container that can't access `/dev/watchdog0`). Six sysfs reads classify the host with high confidence: `/sys/devices/virtual/dmi/id/chassis_type` (3=Desktop, 4=LowProfileDesktop, 6=MiniTower, 7=Tower, 9=Laptop, 10=Notebook, 14=SubNotebook, 17=MainServerChassis, 23=RackMount, 28=BladeEnclosure, 31=Convertible, 32=Detachable), `/sys/firmware/qemu_fw_cfg/` (present-or-not = QEMU guest, can read `/etc/system-status` etc. for richer detection), `/sys/class/dmi/id/sys_vendor` (`QEMU` / `Microsoft Corporation` for Hyper-V / WSL / `VMware, Inc.` / `innotek GmbH` for VirtualBox / `Xen` / `Parallels` / `Bochs` / actual mobo OEM), `/sys/class/dmi/id/product_name` (Synology / TrueNAS / Proxmox VE leaves fingerprints), `/sys/class/dmi/id/bios_vendor` (`SeaBIOS` / `EDK II` / `Phoenix` / `AMI`), `/proc/cpuinfo` `hypervisor` flag (kvm/vmware/microsoft/xen/qemu), plus `/proc/1/cgroup` (containerd / docker / lxc / systemd-nspawn signatures). Classify into 6 form-factors: (a) bare-metal desktop, (b) bare-metal laptop (eGPU likely), (c) bare-metal server / rack, (d) KVM/QEMU guest with passthrough GPU (read `/sys/firmware/qemu_fw_cfg/` to confirm), (e) WSL2 / Hyper-V guest (sys_vendor=Microsoft + chassis_type=0 + `WSL_INTEROP` env), (f) LXC/Docker container (cgroup signatures, `/proc/1/sched` shows non-init pid), each with a *gating profile* that maps to {hide, show, show-with-warning} per Settings card. Push the classification once at startup into shipped Settings → Integrations registry, auto-hide irrelevant cards (BIOS update on VMs, fan-curve on containers, suspend on rack servers, watchdog on containers), auto-warn on lossy cards (e.g. "you're in WSL2, `/proc/<pid>/stat` field 7 (TTY) is 0 for everything, shipped #37.5 sched_autogroup advice may be inaccurate"). Pairs with parked-bench 38.8 (chassis_type + battery, narrower scope) by widening it to a full virtualization-and-form-factor classifier that gates the *entire* UI surface. Niche but very-high-satisfaction-per-feature: cleans up the new-user "what is half this stuff and why is it shown to me on my Proxmox VM" complaint. First : `modules/host_class.py` + GET `/api/host-class`. |
| 39.5 | **`/sys/class/block/*/queue/{discard_max_bytes,discard_granularity,discard_zeroes_data}` + `/etc/fstab` discard / `fstrim.timer` TRIM-pipeline auditor for NVMe model storage + swap longevity** | S | 4 | Modern homelab inference rigs store 200-2000 GB of GGUF / safetensors weights on a single consumer-grade NVMe (Samsung 980 Pro, WD SN850X, Crucial T705) — and the SSD's sustained read performance + endurance both degrade catastrophically without working TRIM, which has *four* independent moving parts that all must align: (1) the SSD itself must report `discard_max_bytes > 0` (some cheap PCIe Gen3 NVMe and almost all USB-NVMe enclosures with cheap JMS583/RTL9210 bridges advertise 0 = no TRIM), (2) the mount option chain must propagate discard intent (`discard` in `/etc/fstab` for inline-TRIM, or `noatime,nodiratime` + nothing for batched-TRIM-via-`fstrim`), (3) `fstrim.timer` must be enabled in systemd (Debian/Ubuntu default ON since 19.04, Fedora ON, but Arch + many minimal/headless setups OFF), (4) for swap on NVMe (a homelab pattern when running 70B-quant models with a 32 GB RAM mobo), `/etc/fstab` swap line must include `discard=once` or `discard=pages` or the SSD's swap-area sectors silently never get TRIMmed and the SSD's FTL endurance burns 3-5× faster. Foot-guns: (a) `/etc/fstab` has `discard` mount option on a kernel ≥ 5.4 where inline `discard` is *worse* than `fstrim.timer` (every file delete triggers a TRIM, stalls the queue) — shipped `nvme_iosched` doesn't surface this, (b) `fstrim.timer` enabled but the SSD reports `discard_max_bytes=0` (USB-NVMe enclosure or fake-NVMe DRAM-less drive) → timer fires weekly with zero effect, user thinks they're protected, (c) ZFS / btrfs RAID on top of NVMe with discard not propagated through the filesystem layer (btrfs needs `discard=async` on kernel ≥ 5.6, ZFS needs `zpool autotrim=on`), (d) swap on NVMe with no `discard=` flag — most-common silent foot-gun. Walk `/sys/class/block/nvme*n*/queue/{discard_max_bytes,discard_granularity,discard_zeroes_data,rotational}` for every NVMe namespace + `/sys/class/block/sd*/queue/discard_max_bytes` for SATA SSDs, parse `/etc/fstab` for mount-option + swap-line discard intent, parse `/proc/swaps` for active swap devices, query systemd for `fstrim.timer` state + `OnCalendar` schedule + last activation (`systemctl show fstrim.timer --property=LastTriggerUSec`), cross-reference shipped `nvme_iosched` (I/O scheduler choice impacts TRIM coalescing), `nvme_swap` (the swap-on-NVMe sentinel), `disk_health` (SMART endurance % left), classify into 4 audit states: (a) NVMe with `discard_max_bytes>0` + `fstrim.timer enabled` + swap-line has `discard=once` → optimal, (b) USB-NVMe with `discard_max_bytes=0` → flag, advise SATA-style mount instead, (c) inline `discard` in `/etc/fstab` on kernel ≥ 5.4 → recommend switch to `fstrim.timer`, (d) swap on NVMe with no discard flag → flag with paste-ready `/etc/fstab` swap-line patch. Emit "your `/etc/fstab` swap line `UUID=… none swap sw 0 0` is missing `discard=once` — every page swapped out and freed leaves dead blocks the SSD never reclaims, ~3-5× endurance penalty over time; change to `UUID=… none swap sw,discard=once 0 0`". Niche but high-leverage longevity audit for the 70B-on-32-GB-host pattern. First : `modules/trim_pipeline.py` + GET `/api/trim-pipeline`. |
| 39.6 | **`/sys/kernel/debug/tracing/available_tracers` + `/sys/kernel/tracing/current_tracer` + `/sys/kernel/debug/tracing/events/` ftrace / eBPF tracing readiness auditor for CUDA / NVRM / GPU-IRQ kernel-side debugging** | S | 3 | When the user's GPU mysteriously XIDs once a week, the *correct* answer is to leave an ftrace `irq:irq_handler_entry` + `nvidia:*` events filter armed in the background and inspect after the fact — but every distro ships with a maze of ftrace toggles in 4 different parent directories (`/sys/kernel/debug/tracing/`, `/sys/kernel/tracing/`, `/sys/kernel/debug/dynamic_debug/`, `/proc/dynamic_debug/`), some of which require `CONFIG_FTRACE=y`, `CONFIG_FUNCTION_TRACER=y`, `CONFIG_KPROBE_EVENTS=y`, `CONFIG_UPROBE_EVENTS=y`, `CONFIG_BPF_SYSCALL=y` in the running kernel — and `/proc/sys/kernel/perf_event_paranoid` (parked #37.7) ALSO gates some of them. Today shipped #nvrm_tail tails the kernel ring buffer for nvidia XID messages after-the-fact, shipped #xid_decoder decodes the codes — but neither tells the user "you have ftrace available right now, here's the one-liner that captures the next XID's preceding kernel events so you can actually root-cause it". Read `/sys/kernel/debug/tracing/available_tracers` (lists `function`, `function_graph`, `nop`, `mmiotrace`, `irqsoff`, `preemptoff`, `wakeup`, `wakeup_rt` — presence tells you which CONFIG_* features the running kernel supports), `current_tracer` (is anything tracing right now? — high CPU cost if accidentally left on), enumerate `/sys/kernel/debug/tracing/events/` subdirs (irq/, sched/, nvidia/, pci/, power/, mce/) + per-event `enable` files (1=enabled), classify into 4 readiness verdicts: (a) full ftrace + nvidia events available + nothing currently tracing → ready, emit ready-to-arm one-liner for XID capture, (b) ftrace available but `nvidia/` events subdir missing → nvidia.ko built without TRACE_EVENT hooks (older driver), suggest 580+ upgrade, (c) `current_tracer != nop` → something is tracing in the background (forgotten? high CPU? — surface what), (d) `tracefs` not mounted or debugfs locked-down → CONFIG_DEBUG_FS=n or `lockdown=integrity` kernel cmdline (parked #37.6 secureboot_tpm angle), flag with workaround. Cross-references shipped `nvrm_tail` (where the after-the-fact trace lands), shipped `xid_decoder` (decoder for what the trace captured), and parked #37.7 perf_event_paranoid (sibling toggle). Surface "your kernel has 47 nvidia/ trace events available, none currently armed; the next time GPU 0 XID-69's, you'll get only the dmesg line — arm `echo 1 > /sys/kernel/debug/tracing/events/nvidia/enable && echo 1 > /sys/kernel/debug/tracing/tracing_on` now to capture context for the next XID, output rotates into `/sys/kernel/debug/tracing/trace_pipe` — here's a paste-ready 5-line systemd-service that captures + rotates daily for the next ftrace-curious investigator". Niche audience (~5 % of homelab — ftrace-curious users with recurring XIDs) but the *single* highest-payoff "next time it breaks I'll have data" reliability feature for that slice. First : `modules/ftrace_ready.py` + GET `/api/ftrace-ready`. |
| 39.7 | **`/proc/sys/kernel/kexec_load_disabled` + `/proc/sys/kernel/dmesg_restrict` + `/sys/kernel/security/lockdown` + `/proc/sys/kernel/kptr_restrict` kernel-hardening posture auditor for "what does this user actually have access to debug with"** | XS | 3 | A recurring frustration on Ubuntu 22.04+ / Fedora 39+ / Pop!_OS 22.04+: the user tries to read `dmesg` to investigate a GPU XID, hits `dmesg: read kernel buffer failed: Operation not permitted` because `/proc/sys/kernel/dmesg_restrict=1` (default since Ubuntu 12.04 but more aggressively enforced since 22.04), tries `cat /proc/kallsyms` for a stack trace, gets all-zero addresses because `kptr_restrict=1`, tries to `kexec -l` a debug kernel to capture a crash dump, fails silently because `kexec_load_disabled=1` (set automatically when Secure Boot is on or `lockdown=integrity` is in `/proc/cmdline`), tries to load a debug module, fails because `lockdown=confidentiality` mode forbids `init_module` syscall entirely. Shipped #30.8 reads `/sys/kernel/security/lockdown` and emits a one-line "lockdown is `<value>`" — but doesn't surface the *consequences* (what specifically can the user no longer do?) or the related four sibling toggles. The dashboard itself runs as a regular user via systemd-system or systemd-user, and a clear-eyed "here's what debugging surface this user has access to right now" card pre-empts the "the dashboard says XID 69 happened but `dmesg` gave me nothing" complaint. Read `/proc/sys/kernel/dmesg_restrict` (0=anyone can read kernel ringbuffer, 1=root-only), `/proc/sys/kernel/kexec_load_disabled` (1=irreversibly disabled this boot — once on, only a reboot clears it), `/proc/sys/kernel/kptr_restrict` (0=show ptrs to everyone, 1=root-only, 2=hide always), `/sys/kernel/security/lockdown` (`none` / `integrity` / `confidentiality`), `/proc/sys/kernel/yama/ptrace_scope` (0=any process, 1=child-only Ubuntu default, 2=admin-only, 3=disabled — blocks `gdb -p` + `py-spy` + `strace -p`), `/proc/sys/kernel/modules_disabled` (1=no more module loading this boot), classify into 5 posture levels: (a) Wide Open (all 0, no lockdown) = single-user dev box, full debug access, (b) Ubuntu Desktop Default (dmesg_restrict=1, ptrace_scope=1, kptr_restrict=1, no lockdown) = `sudo dmesg` works, `gdb -p` only on child processes, kallsyms shows 0s, (c) Secure Boot On (lockdown=integrity, kexec_load_disabled=1, modules_disabled=0) = no kexec, signed modules only, (d) Hardened (lockdown=confidentiality, kptr_restrict=2) = no kallsyms at all, (e) Locked Tight (modules_disabled=1 + lockdown=confidentiality) = no new modules until reboot. Cross-references shipped #30.8 lockdown (one-axis), parked #37.7 perf_event_paranoid (profiling subset), shipped #36.3 kernel_taint (taint history). Surface "your dashboard runs as user `gpud`, posture is Ubuntu Desktop Default: `dmesg -k` will fail with EPERM, kallsyms is masked, `gdb -p $(pidof ollama)` will only work for processes you launched yourself; if this is a single-user trusted box, drop `kernel.dmesg_restrict=0` + `kernel.kptr_restrict=0` + `kernel.yama.ptrace_scope=0` into `/etc/sysctl.d/99-llm-debug.conf` — paste-ready snippet". Niche but high-payoff "I now know what tools work on this box" eye-opener. First : `modules/kernel_posture.py` + GET `/api/kernel-posture`. |
| 39.8 | **`/sys/devices/system/cpu/*/topology/thread_siblings_list` + `/sys/devices/system/cpu/*/online` per-pair hyper-thread siblings map for surgical SMT disable on inference cores** (extends shipped #35.4 smt_audit with per-pair granularity) | S | 3 | Shipped `smt_audit.py` covers the *global* SMT state (`/sys/devices/system/cpu/smt/control` = `on`/`off`/`forceoff`/`notsupported`, `/sys/devices/system/cpu/smt/active` = 0/1) and emits binary "disable SMT for inference" advice — but on multi-CCD Zen4 (7950X/7900X), multi-tile Sapphire-Rapids, Threadripper, and EPYC, the *right* answer is rarely "all-or-nothing SMT off". The optimal pattern is: keep SMT on globally for the OS / background workers / E-cores, but *surgically* offline the hyper-thread sibling of every P-core that's pinned to llama-server via shipped #31.3's `taskset` advice, so the inference cores get *exclusive* L1/L2 + ALU bandwidth without the 12-15 % SMT-contention penalty, while the rest of the box keeps its parallelism. Today nothing in the OSS Linux ecosystem surfaces *per-core-pair* SMT disable advice (every existing tool is global-only). Walk `/sys/devices/system/cpu/cpu*/topology/thread_siblings_list` (each file lists the small set of logical CPUs that share a physical core, e.g. `0,16` for cores 0 and 16 on a 7950X = same physical core) + `/sys/devices/system/cpu/cpu*/online` (1=enabled, 0=offlined via `echo 0 >`), build a graph "physical-core-id → {sibling_lcpus} → online_state", cross-reference shipped #31.3 cpu_topology (which cores does the user's `taskset` recipe pin?) + shipped #37.4 cpu_cache_topology (which cores share L3 with the pinned set?) + shipped #37.2 gpu_cpu_affinity (which cores are PCIe-local?), emit a per-pair "disable lcpu 16-31 only (the high-numbered siblings of P-cores 0-15) for inference workloads, keep them offline while llama-server runs, re-enable after with the paired `echo 1 >` snippet" recipe + a systemd Drop-In oneshot that offlines the sibling set on `llama-server.service` start and re-enables on stop (`ExecStartPre=/usr/local/bin/llm-smt-pair off`, `ExecStopPost=/usr/local/bin/llm-smt-pair on`). Classify into 4 verdicts: (a) SMT globally off (shipped #35.4 already handled) → no advice, (b) SMT on + user is *not* taskset-pinning → suggest #31.3's pin advice first, (c) SMT on + user IS taskset-pinning to some core range + the sibling range is online → here's the surgical offline recipe, (d) SMT on + user has *already* offlined the sibling range → optimal, congratulate. Pairs with shipped #35.4 (the global view), #37.4 (cache topology), #37.2 (GPU↔CPU placement) for the *fifth dimension* (per-core SMT) of the CPU placement multidimensional cube. Niche audience (the SMT-curious slice of multi-CCD homelab — overlaps heavily with shipped #34/35/37 audience), but the cleanest "+12-15 % inference t/s for 20 lines of systemd" hyper-tactical win in the cycle. First : `modules/smt_pair_pin.py` + GET `/api/smt-pair-pin`. |

**Top 4 (fit × urgency)** :
1. 39.1 `/proc/cmdline` unusual kernel boot-parameter parser — XS, fit 5, the lowest-effort + highest-leverage pick of the cycle: one read of `/proc/cmdline` + a curated ~40-entry "expected on a tuned inference rig" allowlist + ~25-entry "you probably didn't mean this" denylist surfaces a dozen recurring homelab foot-guns at once (`mitigations=off` missing, `nvidia-drm.modeset=1` missing on Wayland, `nvidia.NVreg_PreserveVideoMemoryAllocations=1` missing on suspend-prone setups, `pcie_aspm=off` accidentally on, leftover `isolcpus=` from a guide that didn't match the user's core layout, `transparent_hugepage=never` defeating shipped #thp_audit, `numa_balancing=disable` defeating shipped #numa_placement, emergency `acpi=off`/`pci=noaer` residue) — each one a forum thread that today nothing in the OSS Linux ecosystem cross-references against the shipped audit suite in one place. Cross-references shipped #29.1 kmod_params, #37.1 cpu_vulns, #38.2 modprobe_audit, #28.6 pcie_aspm, #38.4 gpu_irq_affinity, #31.1 thp_audit, #35.3 numa_placement, #suspend_guard for a complete "what is this box actually doing on every boot vs what it should be doing" verdict, with a paste-ready `sed -i … && update-grub` snippet for the union of fixes. Biggest "explains a dozen forum threads in one diff view" feature per kB of code possible. First : `modules/cmdline_audit.py`.
2. 39.2 `/etc/sysctl.d/*.conf` on-disk vs runtime drift detector — S, fit 5, the sysctl-side equivalent of shipped #38.2 modprobe_audit, the missing "I pasted `vm.swappiness=10` into `/etc/sysctl.d/99-llm.conf` three weeks ago, will it survive my next `apt upgrade procps`, and is `/etc/sysctl.d/10-cloudimg-settings.conf` silently re-overriding it on every boot?" diagnostic that today shipped `net_sysctl_audit.py` + `vm_sysctl_audit.py` (runtime view only) cannot answer. Walks the four-tier sysctl-config-merge order (`/usr/lib` < `/run` < `/etc/sysctl.d/` < `/etc/sysctl.conf`) in alphabetical precedence, parses `key = value` directives (handles comments, line continuations, `-key` ignore-error syntax), computes the effective union per key, diffs against the runtime `/proc/sys/<key>` value, and catches the five recurring foot-guns (cloud-init residue overrides, two-snippets-conflicting-precedence, package-upgrade rewrites, kernel-removed keys silently no-op, `/run/sysctl.d/` mystery overrides). Pairs naturally with shipped `net_sysctl_audit.py` + `vm_sysctl_audit.py` (runtime view per subtree) and shipped #38.2 modprobe_audit (parallel modprobe-side persistence story) for the complete "what sysctl + nvidia.ko parameter values will this box actually use on every boot, and are they future-proofed against package upgrades" answer. Highest-payoff "set it once and forget it" persistence-correctness win for sysctl tuning. First : `modules/sysctl_d_audit.py`.
3. 39.3 `/proc/<pid>/coredump_filter` + `core_pattern` coredump-readiness auditor — XS, fit 4, the missing post-mortem-readiness leg on top of shipped `nvrm_tail` (XID / nvrm log capture), shipped `xid_decoder` (CUDA error decode), and shipped `rlimit_audit` (RLIMIT_CORE PAM-side) — catches the silent "your llama-server segfaulted last Tuesday and you have nothing to inspect" failure mode that hits every Ubuntu user whose `core_pattern=|/usr/share/apport/apport …` pipe silently sends crashes into a disabled apport service, and every Fedora-minimal user whose `Storage=none` in `/etc/systemd/coredump.conf` makes `systemd-coredump` a black hole. Reads six independent gates (`core_pattern`, `core_uses_pid`, `suid_dumpable`, per-PID `coredump_filter` bitmask decoded into named flags, `coredump.conf` Storage/Compress/ProcessSizeMax limits, destination FS free space), classifies into 4 readiness states with paste-ready Drop-In recipes (switch from apport to systemd-coredump, bump `ProcessSizeMax=10G` for 8.2 GB-RSS inference workers). Niche but high-payoff "next time it crashes I'll actually have data" reliability feature; cross-references shipped #nvrm_tail + #xid_decoder + #rlimit_audit for the complete post-mortem story. First : `modules/coredump_ready.py`.
4. 39.4 virtualization + form-factor classifier for adaptive Settings UI gating — XS, fit 4, six sysfs/dmi reads (`chassis_type`, `qemu_fw_cfg/`, `sys_vendor`, `product_name`, `bios_vendor`, `/proc/cpuinfo` hypervisor flag + `/proc/1/cgroup` container signatures) classify the host into 6 form-factors (bare-metal desktop / laptop / server, KVM-QEMU guest with passthrough GPU, WSL2 / Hyper-V, LXC/Docker container) at startup and push the classification into shipped Settings → Integrations registry to auto-hide/show/warn-on cards per host context. Cleans up the new-user "what is half this stuff and why is it shown to me on my Proxmox VM with passthrough GPU" complaint, gates the BIOS / fan-curve / suspend / watchdog / UPS cards on form-factor reality. Wider scope than parked-bench 38.8 (chassis_type + battery only) by extending into virt detection. Pairs with shipped Settings → Integrations registry to deliver the cleanest "the dashboard now knows where it's running" win in the cycle. First : `modules/host_class.py`.

Bench (39.5 NVMe TRIM-pipeline + fstab discard audit / 39.6 ftrace + nvidia tracing readiness / 39.7 kernel-hardening posture auditor / 39.8 per-pair hyper-thread siblings surgical SMT disable) for cycles after top 4 lands.

---

## R&D #38 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-37), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8 + 29.2/29.4/29.5/29.6 + 30.4/30.6/30.7/30.8 + 31.5/31.6/31.7/31.8 + 32.2/32.6/32.7/32.8 + 33.3/33.5/33.7/33.8 + 34.5/34.6/34.7/34.8 + 35.5/35.6/35.7/35.8 + 36.5/36.6/36.7/36.8 + 37.5/37.6/37.7/37.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 38.1 | **`/sys/bus/pci/devices/<gpu>/aer_dev_{correctable,fatal,nonfatal}` per-counter trend tracker + correctable-error rate alarm — companion to shipped #28.5 pcie_aer** | XS | 5 | Shipped `pcie_aer.py` reads the *current* AER status flags (RxErr/BadTLP/BadDLLP/Rollover/Timeout/NonFatalErr/FatalErr/CorrIntErr/HeaderLog) once and surfaces a binary "AER errors present yes/no". What it does NOT do is *count* and *trend* the correctable-error subcounters at `/sys/bus/pci/devices/<gpu>/aer_dev_correctable` (12 named lines: `RxErr`, `BadTLP`, `BadDLLP`, `Rollover`, `Timeout`, `NonFatalErr`, `CorrIntErr`, `HeaderLog`, `TOTAL_ERR_COR`, etc., each followed by a monotonic counter that increments on every PCIe link replay event). A clean PCIe Gen4 x16 GPU link on a quality riser should show counter deltas of 0 per minute; a borderline link (cheap riser cable, oxidised slot fingers, a marginal PSU dropping the 12 V rail during transients) will show 5-50 correctable errors per minute, *each one triggering a TLP retransmit* that silently steals 1-3 % of usable PCIe bandwidth. The user sees "tokens/s feels jittery and 8-12 % lower than benchmarks but `nvidia-smi` looks fine, pcie_aer says no fatal errors" — the smoking gun is the *delta rate*, not the absolute presence, of correctable errors. Sample `aer_dev_correctable` + `aer_dev_fatal` + `aer_dev_nonfatal` per GPU at 0.1 Hz, compute per-counter rate over the last 60 s rolling window, classify into 4 link-health verdicts: (a) all rates = 0 → pristine, (b) BadTLP/BadDLLP rate 1-5/min → marginal riser, suggest cable reseating, (c) Rollover or Timeout > 0 → severe link degradation, force `lspci -vv` snippet + `setpci` LnkCtl link retrain recipe, (d) sudden jump > 100 events in 10 s → power transient (correlate with shipped #35.2 wall_meter spike + #28.7 throttle_cause `Power_BR`). Cross-reference shipped #28.3 pcie_width_watcher (link width/speed downgrade) and #28.6 pcie_aspm (ASPM-induced wake errors) to disambiguate cause. Surface "GPU 0 aer_dev_correctable.BadTLP +3 /min last 5 min (was 0 /min for 4 h prior) → marginal PCIe link, expect ~2 % t/s degradation; recommend reseat riser cable, then `setpci -s 01:00.0 CAP_EXP+10.w=0x60` to force link retrain" + paste-ready `journalctl -k -g aer` snippet. The missing *trend* dimension on top of shipped #28.5's *snapshot* view, completes the PCIe-link health story with a 0.1 Hz sampler that today nothing in the OSS Linux ecosystem surfaces for inference rigs. First : `modules/pcie_aer_trend.py` + GET `/api/pcie-aer-trend`. |
| 38.2 | **`/etc/modprobe.d/*.conf` + `/etc/modules-load.d/*.conf` on-disk vs runtime nvidia.ko params drift detector — extends shipped #29.1 kmod_params** | S | 5 | Shipped `kmod_params.py` reads the *runtime* nvidia.ko parameter state from `/sys/module/nvidia/parameters/*` (NVreg_OpenRmEnableUnsupportedGpus, NVreg_EnableMSI, NVreg_PreserveVideoMemoryAllocations, NVreg_RegistryDwords, etc.) and emits paste-ready `options nvidia <param>=<value>` snippets — but it has *zero* visibility into what's actually in `/etc/modprobe.d/` already, leading to the silent failure mode where the user pastes a `nvidia.conf` snippet, reboots, and gets confused because `/etc/modprobe.d/nvidia-graphics-drivers.conf` (Ubuntu's apt-installed file) overrides their hand-edited `/etc/modprobe.d/zz-nvidia-llm.conf` due to alphabetical `modprobe` config-merge order. Worse, on `apt upgrade nvidia-driver-XXX` Ubuntu *rewrites* `nvidia-graphics-drivers.conf`, silently reverting the user's tuning. Walk `/etc/modprobe.d/*.conf` + `/etc/modules-load.d/*.conf` + `/usr/lib/modprobe.d/*.conf` + `/run/modprobe.d/*.conf` in modprobe-config-merge order (alphabetical within each directory, lowest-to-highest precedence: `/lib` < `/usr/lib` < `/run` < `/etc`), parse `options nvidia <key>=<value>` + `blacklist <module>` + `install <module>` + `softdep` directives, compute the effective "if I rebooted right now" union per parameter, compare against shipped #29.1's runtime `/sys/module/nvidia/parameters/*` view, surface 4 drift verdicts: (a) on-disk = runtime = OK, (b) on-disk says `NVreg_OpenRmEnableUnsupportedGpus=1` but runtime `/sys/module/nvidia/parameters/NVreg_OpenRmEnableUnsupportedGpus=0` → modprobe config was edited after last boot, reboot to apply, (c) two `.conf` files both set `NVreg_EnableMSI` with conflicting values, last-alphabetical wins → flag the precedence trap with the resolved value, (d) `nouveau` not blacklisted in any `/etc/modprobe.d/` file → silent fallback risk on next initramfs rebuild. Also detect the Ubuntu/Debian `nvidia-graphics-drivers.conf` rewrite-on-upgrade landmine: if the file's mtime is < 7 d but `dpkg -V nvidia-driver-XXX` shows it as package-owned, warn "your tuning may be reverted on next apt upgrade, move tuning to `/etc/modprobe.d/zz-llm-nvidia.conf` (alphabetical-last, survives apt upgrades)". Pairs with shipped #29.1 kmod_params (runtime), #6 dkms_status, and #29.3 driver_flavor for a complete "what nvidia.ko parameters does this box actually use on every boot" answer. First : `modules/modprobe_audit.py` + GET `/api/modprobe-audit`. |
| 38.3 | **`/proc/<pid>/maps` shared-library version drift detector for inference workers (libcuda.so / libcublas.so / libnvidia-ml.so / libllama.so mismatch)** | S | 5 | A recurring "this used to work, now it's slow / segfaults" pattern on long-uptime homelab boxes: the user `apt upgrade`d nvidia-driver-575 → 580, the new `libcuda.so.580.82.07` landed at `/usr/lib/x86_64-linux-gnu/libcuda.so.1` (symlink updated), but the *already-running* llama-server / ollama / vllm process still has the *old* `libcuda.so.575.64.03` mmap'd because the kernel keeps the inode alive for the open file descriptor; the user then sees "tokens/s dropped 8 % after the apt upgrade" or "ollama API works but CUDA error 999 on llama-cpp-python loaded fresh against the new lib". Today shipped #25.x cuda_inventory enumerates which CUDA versions are *installed*, but nothing surfaces which library *each running inference process is actually using right now* — a critical gap because the answer can differ from the on-disk symlink target. Walk shipped service-discovery PIDs (llama-server, ollama, vllm, koboldcpp, text-generation-webui, jupyter), read `/proc/<pid>/maps` (one line per mmap'd region: address range, perms, offset, dev:inode, **pathname including `(deleted)` marker if the file was unlinked since mmap**), filter for `libcuda.so*`, `libcublas.so*`, `libcublasLt.so*`, `libcudart.so*`, `libnvidia-ml.so*`, `libnccl.so*`, `libnvrtc.so*`, `libllama.so*`, `libggml*.so`, parse each pathname's version suffix, compare against `/usr/lib/x86_64-linux-gnu/libcuda.so.1`'s current readlink target, classify into 5 verdicts: (a) all libs match on-disk current → pristine, (b) one or more libs show `(deleted)` suffix → driver was upgraded since process start, restart the process to pick up the new lib, (c) two inference processes hold different libcuda versions (e.g. ollama on 575, llama-server on 580) → version split risk on multi-GPU coordination, (d) libcuda version older than libcudart by major → ABI risk, (e) libnvidia-ml.so doesn't match the running nvidia kernel module version (read from `/sys/module/nvidia/version`) → `nvidia-smi` may misreport, restart `nvidia-persistenced`. Surface "ollama PID 12834 holds libcuda.so.575.64.03 (deleted) — driver upgrade to 580.82.07 happened 3 d ago, restart ollama with `systemctl restart ollama` to pick up the new lib for +2-4 % t/s and to clear the CUDA error 999 risk". Niche but high-value diagnostic — explains a top-10 "what changed?" homelab forum complaint that no tool today surfaces. First : `modules/proc_maps_libs.py` + GET `/api/proc-maps-libs`. |
| 38.4 | **`/proc/irq/<n>/smp_affinity` + `/proc/interrupts` GPU MSI/MSI-X IRQ-affinity advisor for cross-NUMA PCIe interrupt placement** | S | 5 | When the GPU's MSI-X interrupts (one per CUDA stream / NVLink port / PCIe transaction class — modern nvidia.ko allocates 4-32 vectors per GPU) fire on a CPU core that is *not* in `/sys/bus/pci/devices/<gpu>/local_cpulist` (the local NUMA node from shipped #37.2), every interrupt handler does a cross-socket UPI/Infinity-Fabric round-trip just to acknowledge the IRQ — adding ~200 ns to each CUDA H2D/D2H completion notification on top of the data-path penalty that shipped #37.2 already catches. On a busy llama-server during prompt-eval (tens of thousands of small CUDA launches/sec), this IRQ-side cross-socket tax stacks to 1-3 % t/s. Worse, Ubuntu's `irqbalance` daemon defaults to spreading interrupts round-robin across *all* online CPUs, ignoring PCIe topology, so a Threadripper / dual-Xeon box silently runs GPU IRQs on the wrong socket *most of the time*. Read `/proc/interrupts` (header line gives per-CPU columns, then one row per IRQ with counts per CPU; lines ending in `nvidia` / `nvidia-pci` are the GPU's MSI-X vectors), correlate IRQ number → `/proc/irq/<n>/smp_affinity_list` (which CPUs are *allowed* to handle this IRQ) + `effective_affinity_list` (which CPU(s) *actually* take it), cross-reference shipped #37.2 gpu_cpu_affinity's `local_cpulist`, classify into 4 verdicts: (a) all GPU IRQs have `smp_affinity_list ⊆ local_cpulist` → optimal, (b) `irqbalance` running and spreading IRQs cross-socket → suggest `systemctl stop irqbalance` + paste-ready `for i in $(grep nvidia /proc/interrupts | cut -d: -f1); do echo <hex-mask> > /proc/irq/$i/smp_affinity; done` snippet, (c) IRQ pinned to a single CPU outside `local_cpulist` (manual misconfig) → fix, (d) GPU has only 1 MSI vector (legacy INTx, indicates `pci=nomsi` cmdline or driver fallback) → upgrade investigation. Surface "GPU 0 has 16 MSI-X vectors (IRQs 124-139) but irqbalance scattered them across CPUs 0-31 including remote node 0 (5 of 16 IRQs); pin all 16 to `local_cpulist=8-15` with the snippet below for +1-2 % t/s and ~200 ns lower CUDA-completion latency" + emit a systemd Drop-In oneshot service that pins on boot. Completes the #37.2 (data path) + #37.4 (cache placement) story with the *interrupt path* third leg. First : `modules/gpu_irq_affinity.py` + GET `/api/gpu-irq-affinity`. |
| 38.5 | **`/proc/sys/fs/file-max` + `nr_open` + `epoll_max_user_watches` + `inotify/max_user_watches` kernel-side FD limit auditor for high-concurrency llama-server / OpenWebUI fan-out** (extends shipped #37.x limits_audit with kernel ceilings) | XS | 4 | Shipped `limits_audit.py` audits PAM `/etc/security/limits.conf` + `limits.d/*.conf` (user/process-level RLIMIT_NOFILE, RLIMIT_NPROC, RLIMIT_STACK) — but does NOT cover the *kernel-wide* ceilings that sit above those PAM limits: `/proc/sys/fs/file-max` (system-wide maximum open files, default ~9-13 M on modern Linux but historically 1 M on older distros), `/proc/sys/fs/nr_open` (per-process hard ceiling that bounds the `RLIMIT_NOFILE` even if PAM allows higher, default 1048576), `/proc/sys/fs/epoll/max_user_watches` (default ~123 K, dominates Python `asyncio` / `uvicorn` / `httpx` worker headroom for many-LAN-client serving), `/proc/sys/fs/inotify/max_user_watches` (default 8192 on Ubuntu, default 524288 on Fedora — explains why OpenWebUI's hot-reload watcher works on Fedora but silently misses changes on Ubuntu when serving >50 simultaneous chats). When a Home Assistant + OpenWebUI + 5 Discord bots + a llama-server-side metrics scraper all hit the same ollama instance, the typical 1024-default `RLIMIT_NOFILE` ceiling gets hit at ~100 concurrent SSE streams and ollama starts dropping connections silently. PAM-side fix is shipped #limits_audit; kernel-side ceiling needs this. Four file reads + parse + cross-reference with shipped service-discovery PIDs' `/proc/<pid>/limits` to compute "effective FD ceiling = min(PAM, fs.file-max, fs.nr_open)", classify into 4 states: (a) all kernel ceilings ≥ 1 M and PAM aligned → unlimited headroom, (b) fs.nr_open = 1024 (rare, but seen on busybox-based containers) → hardcaps every process, (c) fs.epoll.max_user_watches = 8192 (Ubuntu default) on a multi-user inference box → bump to 1 M, (d) fs.inotify.max_user_watches = 8192 on Ubuntu + OpenWebUI hot-reload broken → fix with sysctl.d snippet. Surface "fs.file-max=9 223 372, fs.nr_open=1 048 576, fs.epoll.max_user_watches=123 285 (Ubuntu default) → you'll hit epoll ceiling at ~120 K concurrent SSE streams; if planning multi-tenant LAN serving, bump to 1 M via `/etc/sysctl.d/99-llm-fd.conf` snippet" + paste-ready sysctl recipe. Pairs with shipped limits_audit (PAM/user-level) + shipped #34.4 proc_sched (per-thread scheduling) for a complete "what are the ceilings on this box for concurrent inference serving" answer. First : `modules/kernel_fd_limits.py` + GET `/api/kernel-fd-limits`. |
| 38.6 | **`/sys/class/thermal/cooling_device*/{type,cur_state,max_state}` + `/sys/class/leds/*/{brightness,trigger}` cooling-device inventory + LED-trigger auditor for chassis fans / pump signalling** | S | 3 | Linux exposes every cooling device (CPU fan, GPU fan when not nvidia-proprietary, chassis fans wired to ITE/Nuvoton EC chips, AIO pumps, NVMe thermal pads with active cooling) at `/sys/class/thermal/cooling_device*/` with `type` (`Processor`, `Fan`, `intel_powerclamp`, `intel_pstate`, `cpufreq`, `TFN1`, `TCPU`, etc.) + `cur_state` + `max_state` — and shipped #15.x fan_curve + #16.x hwmon_inventory cover the PWM-controllable nvidia GPU fan + the hwmon temperature sensors, but neither inventories the *full* set of cooling actors (chassis fans, motherboard EC fans, pumps) or surfaces which ones are *idle* (`cur_state=0` while CPU is at 85 °C → fan curve misconfigured in BIOS, the chassis fan is wired but not ramping). Similarly `/sys/class/leds/` exposes every motherboard / chassis / keyboard / drive-activity LED with a `trigger` file (`none` / `disk-activity` / `nvme0-pcie-0` / `mmc0` / `phy0tx` / `cpu0`) — the homelab use case is "use the chassis power LED as a `cpu0-trigger` so a glance at the box tells me whether it's actually working without alt-tabbing to the dashboard", a small but delightful 5-minute customisation no OSS tool surfaces today. Walk `/sys/class/thermal/cooling_device*/`, classify by `type`, cross-reference with shipped #16.x hwmon temperature readings to flag "cooling_device3 (Fan, max_state=255) sits at cur_state=0 while CPU package temp is 87 °C → BIOS fan curve disabled this chassis fan; check BIOS or set `cur_state` manually" + walk `/sys/class/leds/`, list available triggers, propose one-click "wire chassis power LED to `cpu0-trigger` for visual CPU activity feedback" with the `echo cpu0 > /sys/class/leds/input2::scrolllock/trigger` (or the box's relevant LED path) snippet + paste-ready udev rule for persistence. Niche but charming: completes shipped #fan_curve (GPU-only) into a whole-box thermal-actor inventory + opens the small "chassis LEDs as ambient monitoring" surface area that homelab/desktop builders love (and no OSS tool ships today). First : `modules/cooling_leds.py` + GET `/api/cooling-leds`. |
| 38.7 | **`/proc/sys/kernel/perf_event_paranoid` + `perf_event_mlock_kb` + `kptr_restrict` profiling-readiness auditor for nsys / ncu / perf record / py-spy workflows** (promoting from bench #37.7) | XS | 3 | Promoting from bench because a vocal slice of homelab LLM-rig owners profile their inference workloads with `perf record` / `nsys profile` / `ncu` / `py-spy record` to chase the last few % of tokens/s — and every modern distro silently locks these tools out by default with `kernel.perf_event_paranoid=2` (forbids unprivileged CPU event observation, hardware counter access, and kernel symbols) and `perf_event_mlock_kb=516` (caps per-user mmap'd ring-buffer to 516 KB, causing nsys to drop events on busy traces). The fix is one sysctl line, but the symptom is opaque: nsys reports "Could not enable IBT for the current user", `py-spy` reports `Permission denied`, and the user has no idea where to look. Today nothing in the shipped stack surfaces profiling-readiness; the closest analogues (#37.x limits_audit for PAM ceilings) don't touch perf-side knobs at all. Read `/proc/sys/kernel/perf_event_paranoid` (-1=allow everything / 0=allow kernel profiling / 1=disallow CPU events / 2=disallow kernel access / 3=disallow user access, Ubuntu/Debian default), `perf_event_mlock_kb` (per-user mmap cap, default 516), `/proc/sys/kernel/kptr_restrict` (0=show kernel ptrs / 1=show for root / 2=hide), `/proc/sys/kernel/yama/ptrace_scope` (0=any process / 1=child only Ubuntu default, blocks py-spy attach), classify into 4 readiness levels: (a) fully open (paranoid≤0, mlock≥8192, kptr=0, ptrace_scope=0) → all profilers work, (b) typical desktop (paranoid=2, mlock=516, ptrace_scope=1) → only sudo'd `perf record` works, no nsys / ncu / py-spy attach, (c) hardened (paranoid=3) → no profiling at all, (d) some-but-not-all (paranoid=1 + mlock=8192) → CPU profiling works, GPU CUDA tracing partly works. Surface "perf_event_paranoid=2 + perf_event_mlock_kb=516 + ptrace_scope=1 (Ubuntu default) → nsys / ncu / py-spy record will fail or drop events for unprivileged users; if this is a single-user homelab box, drop to paranoid=1, bump mlock_kb to 8192, set ptrace_scope=0 with `/etc/sysctl.d/99-llm-profiling.conf` — paste-ready snippet" + a clear "only do this on a trusted single-user host" warning. Niche audience (profiler-curious users, ~10-15 % of homelab) but a clean answer to the "nsys says permission denied even though I'm in the group" recurring complaint. First : `modules/perf_paranoid.py` + GET `/api/perf-paranoid`. |
| 38.8 | **`/sys/class/power_supply/{BAT*,AC,ADP*}/` + `/sys/class/dmi/id/chassis_type` laptop/eGPU battery-aware GPU clamp + chassis classifier combo** (promoting from bench #35.8 + #31.5) | XS | 3 | Promoting both bench items together because they're each other's gating mechanism: `chassis_type` (3=Desktop, 9=Laptop, 10=Notebook, 14=SubNotebook, 23=RackMount, 31=Convertible) is the single byte that decides whether to even surface battery cards in the UI, and the battery cards themselves are the foot-gun that justifies the chassis classifier. Today every Settings panel shows the same content regardless of form-factor; a rack-server user sees an irrelevant "battery low" mock-up and a laptop+eGPU user gets no warning when their 250 W eGPU drains a 90 Wh battery in 20 min. One read of `/sys/class/dmi/id/chassis_type` (gate), enumerate `/sys/class/power_supply/BAT*/{capacity,status,cycle_count,energy_now,energy_full,charge_behaviour}` + `AC/online`, compute time-to-empty from `energy_now / power_now`, classify into 4 form-factors, push the classification into shipped Settings → Integrations to auto-hide irrelevant cards on rack servers and auto-show them on laptops, surface "chassis_type=10 (Notebook) + on battery + GPU at 250 W → 22 min runtime, consider `nvidia-smi -pl 120` while unplugged" with one-click auto-clamp toggle that integrates with shipped #power_limit. Bonus: read `/sys/class/power_supply/BAT*/charge_behaviour` (`auto` / `inhibit-charge` / `force-discharge` on supported ThinkPad / ASUS / MSI laptops) and surface a "stop charging at 80 % for battery longevity on a desk-bound eGPU laptop" toggle if the silicon supports it. Smaller audience (laptop+eGPU rigs are ~15 % of users) but high satisfaction-per-feature for that slice. First : `modules/chassis_battery.py` + GET `/api/chassis-battery`. |

**Top 4 (fit × urgency)** :
1. 38.1 PCIe AER per-counter trend tracker — XS, fit 5, the lowest-effort + highest-leverage pick of the cycle: three sysfs reads per GPU at 0.1 Hz + a rolling-window delta computation turns shipped #28.5 pcie_aer's *snapshot* "errors present yes/no" view into a *trend* "BadTLP +3 /min last 5 min, was 0 /min for 4 h prior → marginal riser, expect ~2 % t/s degradation" diagnostic that catches the silent borderline-PCIe-link foot-gun (cheap riser cable, oxidised slot fingers, marginal PSU during transients) which today nothing in the OSS Linux ecosystem surfaces for inference rigs. Pairs naturally with shipped #28.3 pcie_width_watcher (link width/speed downgrade), #28.6 pcie_aspm (ASPM-induced wake errors), and #35.2 wall_meter (power-transient correlation) to disambiguate cause. Biggest "explains a recurring 'my t/s feels jittery but nvidia-smi looks fine' weekly forum complaint" per kB of code in this cycle, with a paste-ready `setpci` LnkCtl link-retrain recipe. First : `modules/pcie_aer_trend.py`.
2. 38.2 modprobe.d + modules-load.d on-disk vs runtime nvidia.ko params drift detector — S, fit 5, the missing "is the nvidia.ko parameter tuning I pasted last week actually going to survive the next `apt upgrade nvidia-driver-XXX`?" diagnostic that today shipped #29.1 kmod_params (runtime view only) cannot answer. Walks the four-tier modprobe-config-merge order (`/lib` < `/usr/lib` < `/run` < `/etc`) in alphabetical precedence, parses `options` / `blacklist` / `install` / `softdep` directives, computes the effective union per parameter, diffs against the runtime `/sys/module/nvidia/parameters/*` view, and catches the Ubuntu/Debian `nvidia-graphics-drivers.conf` rewrite-on-upgrade landmine (suggest `/etc/modprobe.d/zz-llm-nvidia.conf` as alphabetical-last, survives apt upgrades). Pairs with shipped #29.1 (runtime), #6 dkms_status, and #29.3 driver_flavor for the complete "what nvidia.ko parameters will this box actually use on every boot, and are they future-proofed against apt?" answer. Highest-payoff "set it once and forget it" persistence-correctness win for nvidia-tuning that today nothing surfaces cleanly. First : `modules/modprobe_audit.py`.
3. 38.3 /proc/<pid>/maps shared-library version drift detector for inference workers — S, fit 5, the missing "your ollama is still running against the old libcuda.so.575 (deleted) because nobody restarted it after the apt upgrade to 580 three days ago" diagnostic that today shipped #25.x cuda_inventory (installed-versions view only) cannot answer — explains the recurring "tokens/s dropped 8 % after I upgraded the driver" or "ollama works but llama-cpp-python loaded fresh against the new lib gives CUDA error 999" homelab complaint with a hard, paste-ready "restart ollama with `systemctl restart ollama` to pick up the new lib" verdict. Walks shipped service-discovery PIDs, parses `/proc/<pid>/maps` for the eight load-bearing CUDA / NCCL / llama libraries, detects the `(deleted)` inode-still-alive marker that signals stale mappings, cross-checks against current on-disk symlink targets and the running nvidia kernel-module version. Niche audience (anyone who ever runs `apt upgrade` on a long-running inference rig — i.e. everyone) but a top-10 "what changed?" forum complaint with no current OSS surface. First : `modules/proc_maps_libs.py`.
4. 38.4 GPU MSI-X IRQ affinity advisor — S, fit 5, the third leg (interrupt path) on top of shipped #37.2 gpu_cpu_affinity (data path) and shipped #37.4 cpu_cache_topology (cache placement) that today nothing in the OSS Linux ecosystem surfaces cleanly for inference rigs — catches the silent ~1-3 % t/s tax on every Threadripper / dual-Xeon / EPYC box where Ubuntu's `irqbalance` defaults scatter the GPU's 16-32 MSI-X vectors across all CPUs including the remote NUMA node, adding ~200 ns per CUDA H2D/D2H completion notification on top of the data-path penalty. Reads `/proc/interrupts` + `/proc/irq/<n>/smp_affinity_list` per nvidia IRQ, cross-references shipped #37.2's `local_cpulist`, classifies into 4 verdicts with a paste-ready `for i in $(grep nvidia /proc/interrupts | cut -d: -f1); do echo <mask> > /proc/irq/$i/smp_affinity; done` snippet plus a systemd Drop-In oneshot for boot-time persistence. Completes the CPU placement quartet (socket via #35.3 → NUMA via #37.2 → cache via #37.4 → IRQ via #38.4) for the multi-socket homelab audience. First : `modules/gpu_irq_affinity.py`.

Bench (38.5 kernel-side FD limits / 38.6 cooling-device + LED inventory / 38.7 perf_event_paranoid profiling-readiness / 38.8 chassis_type + battery awareness) for cycles after top 4 lands.

---

## R&D #37 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-36), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8 + 29.2/29.4/29.5/29.6 + 30.4/30.6/30.7/30.8 + 31.5/31.6/31.7/31.8 + 32.2/32.6/32.7/32.8 + 33.3/33.5/33.7/33.8 + 34.5/34.6/34.7/34.8 + 35.5/35.6/35.7/35.8 + 36.5/36.6/36.7/36.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 37.1 | **`/sys/devices/system/cpu/vulnerabilities/*` per-CPU Spectre/MDS/L1TF/Retbleed/SRSO mitigation cost auditor for inference throughput** | XS | 5 | Linux exposes every speculative-execution mitigation status as a one-line text file under `/sys/devices/system/cpu/vulnerabilities/` — `spectre_v1`, `spectre_v2`, `meltdown`, `mds`, `l1tf`, `retbleed`, `srso`, `gather_data_sampling`, `mmio_stale_data`, `spec_store_bypass`, `tsx_async_abort`, etc. The contents tell you exactly which mitigation is active (`Mitigation: Retpolines, IBPB: conditional, IBRS_FW, STIBP: conditional, RSB filling`) and whether the silicon is `Not affected` / `Vulnerable` / `Mitigation: <something>`. Each active mitigation costs measurable throughput: Retbleed IBRS on Zen2 burns 6-12 % single-thread, Intel's GDS mitigation on Skylake-derived parts costs 3-5 %, SRSO `safe-ret` on Zen3/4 costs 2-4 % — and the *combination* on a 5-year-old kernel can stack to 15-20 % of llama.cpp prompt-eval throughput silently. Today shipped #36.3 kernel_taint catches MCE/oops history and shipped #35.1 cpu_boost catches the turbo gate, but neither surfaces "your CPU is running at 84 % of its silicon potential because the kernel chose to mitigate 8 CVEs you may or may not care about on a single-user homelab box". Read every file in `/sys/devices/system/cpu/vulnerabilities/`, classify into (a) `Not affected` (free), (b) `Mitigation: <fast>` (e.g. microcode-fix, near-zero cost), (c) `Mitigation: <expensive>` (Retpolines+IBRS, SRSO safe-ret, GDS), (d) `Vulnerable` (no cost but risky on shared hosts), cross-reference a tiny embedded "known LLM-cost" table (Retbleed IBRS Zen2 ≈ -8 %, SRSO safe-ret Zen3 ≈ -3 %, GDS Skylake ≈ -4 %), compute an aggregate "mitigation tax %" estimate, surface "8 mitigations active, ~13 % aggregate inference cost on this 5950X; if this is a single-user homelab box behind a LAN-only firewall, `mitigations=off` in GRUB cmdline buys back ~13 % t/s — paste-ready snippet attached, with a clear 'do not do this on a multi-tenant host' warning". Pairs with shipped #36.1 cpu_microcode (silicon-level fixes that *replace* expensive software mitigations) for a complete "what is your CPU actually leaving on the table" verdict. First : `modules/cpu_vulns.py` + GET `/api/cpu-vulns`. |
| 37.2 | **`/sys/bus/pci/devices/<gpu>/{local_cpulist,local_cpus,numa_node}` GPU↔CPU affinity advisor for `taskset` / `CUDA_VISIBLE_DEVICES` / llama-server pinning on multi-socket boxes** | S | 5 | Shipped #35.3 numa_placement reads `/sys/devices/system/node/node*/` + per-PID `/proc/<pid>/numa_maps` to detect *memory* split across NUMA nodes — but it only emits node-level advice ("membind=1") and doesn't tell the user *which specific cores* the GPU is electrically closest to. The PCIe sysfs path exposes `local_cpulist` (e.g. `0-7,16-23` for the half of the cores wired to the same socket as the GPU's root complex) + `local_cpus` (the matching bitmask hex) + `numa_node`, which is the single most actionable piece of placement information on a Threadripper / EPYC / dual-Xeon: any llama-server thread that lands on a CPU outside `local_cpulist` pays an Infinity-Fabric / UPI hop on every host→device transfer. Worse, on AMD Bergamo / Genoa with NPS4 BIOS setting, a single socket has 4 NUMA nodes and the GPU is local to *only one* of them — `numa_node=1` and `local_cpulist=8-15` while the user's `numactl --cpunodebind=0` recipe (the natural first guess) is *wrong*. Walk `/sys/bus/pci/devices/` for nvidia VID 0x10de, read `local_cpulist` + `local_cpus` + `numa_node` per GPU, cross-reference shipped #31.3 cpu_topology (P-core / E-core / SMT siblings) + #35.3 numa_placement (where memory actually lives), surface "GPU 0 (PCI 0000:01:00.0) `local_cpulist=8-15` (node 1) but your llama-server is pinned via `taskset -c 0-7` (node 0) → every CUDA H2D transfer crosses the socket interconnect for ~25 % prompt-eval penalty, fix: `taskset -c 8-15 numactl --cpunodebind=1 --membind=1 llama-server …`" + emit a paste-ready systemd Drop-In with `CPUAffinity=` matching `local_cpulist`. Adds the missing "which exact cores" granularity that #35.3 (node-level) and #31.3 (governor + SMT) don't cover. First : `modules/gpu_cpu_affinity.py` + GET `/api/gpu-cpu-affinity`. |
| 37.3 | **`/sys/class/watchdog/watchdog*/{identity,timeout,state,bootstatus,nowayout}` hardware watchdog + `/dev/watchdog0` ping daemon detector for unattended homelab reliability** | XS | 5 | Every modern motherboard ships an IPMI / SP5100-TCO / iTCO / AMD-SBI hardware watchdog at `/dev/watchdog0`, exposed via `/sys/class/watchdog/watchdog*/`. When configured properly (a userland daemon pings it every < timeout seconds), a wedged kernel — the classic "ollama hangs and the box becomes unreachable, but the GPU LED is still on" homelab nightmare — triggers a hard reset within 10-60 s. When *misconfigured* (the common case: distros ship the `watchdog` package's `wd_keepalive` enabled but don't load the hardware driver, or load `softdog` which only catches usermode hangs), the user thinks they have crash-protection and they don't. Today shipped `watchdog_setup.py` is the dashboard's *own* `/readyz` self-watchdog (systemd user-timer poking the HTTP endpoint), which has zero overlap with the kernel hardware watchdog story. Read `/sys/class/watchdog/watchdog*/identity` (driver name: `iTCO_wdt` / `sp5100_tco` / `softdog` / `ipmi_watchdog` / `hpwdt`), `timeout` (current configured seconds), `state` (active/inactive), `bootstatus` (was the *last* boot caused by a watchdog reset? a smoking gun for "my box just rebooted itself at 3 AM"), `nowayout` (0=daemon can release, 1=hardwired), check if any process holds `/dev/watchdog0` open (via `lsof` → fallback `/proc/*/fd/`), classify into 4 states: (a) hardware watchdog present + userland pinger active = OK, (b) `softdog` only = pseudo-protection, (c) hardware present + no pinger = open file descriptor will silently arm-and-fire on next boot if `nowayout=1`, (d) `bootstatus=1` = your last reboot WAS a watchdog reset (correlate with shipped #36.3 kernel_taint MCE history). Surface "iTCO_wdt detected, timeout=60s, bootstatus=1 (last boot was a watchdog reset 3 d ago, see kernel_taint MCE log) → install `watchdog` package + enable systemd `watchdog.service` for proper kernel-hang detection, here's the snippet" + emit `RuntimeWatchdogSec=30s` systemd-system.conf Drop-In. The single highest-payoff "set it once and forget it" reliability win for unattended homelab inference rigs that today nothing in the OSS Linux ecosystem surfaces cleanly. First : `modules/hw_watchdog.py` + GET `/api/hw-watchdog`. |
| 37.4 | **`/sys/devices/system/cpu/cpu*/cache/index*/{shared_cpu_list,size,type,level}` L3 cache topology + per-core sibling map for inference thread placement on Alder/Raptor P+E, Zen4 CCDs, Bergamo dies** (promoting from bench #36.7) | S | 5 | Promoting from bench because shipped #36 landed the CPU-perf audit quintet (microcode revision, cpuidle exit latency, kernel taint, HWP EPP) and the now-obvious missing leg is *cache-aware* thread placement, which dominates inference performance on every modern heterogenous chip: Alder/Raptor Lake P-cores have a private L3 island separate from E-cores (8-15 MB on 12700K, 30 MB on 12900K), AMD Zen4 CCDs each carry their own 32 MB L3 with no sharing across CCDs (so a `taskset -c 0-15` on a 7950X straddles both CCDs and every cross-CCD weight tensor access becomes a DRAM round-trip), Bergamo/Genoa-X have wildly different L3 sizes per die. Shipped #31.3 cpu_topology emits `taskset` advice based on SMT siblings only, shipped #35.4 smt_audit recomputes it for runtime SMT state, shipped #35.3 numa_placement covers socket-level memory affinity — but *none* of them know about L3 islands, so a Ryzen 7950X user gets "pin to cores 0-15" advice that costs ~9 % t/s vs the correct "pin to cores 0-7 (one CCD)" advice. Walk `/sys/devices/system/cpu/cpu*/cache/index*/` for each cache level (index0=L1d, index1=L1i, index2=L2, index3=L3, on some AMD index4=L4), read `shared_cpu_list` (e.g. `0-7` = cores 0-7 share this L3) + `size` (MB) + `type` (Data/Instruction/Unified) + `level`, build a graph "core → L3 island → island size", classify into 4 placement verdicts: (a) all-cores-same-L3 (typical Zen2/Zen3 monolithic) = use shipped #31.3 advice unchanged, (b) multi-L3-island AMD CCD (Zen4 7950X/7900X) → emit `taskset -c <one-CCD>` for prompt-eval-bound workloads, (c) hybrid P+E with separate L3 islands (Alder/Raptor) → emit `taskset -c <P-core-range>`, (d) Bergamo-style many-CCD with tiny per-CCD L3 → emit `numactl --cpunodebind=<one-CCD-node>`. Cross-check with shipped #35.3 (NUMA) + #31.3 (governor + SMT siblings) + #35.4 (SMT runtime). Surface "Ryzen 7950X: 2 L3 islands (32 MB each, cores 0-7 + 8-15) → pinning all 16 worker threads scattered across both CCDs costs ~9 % t/s vs `taskset -c 0-7`, paste-ready snippet" or "12900K: P-cores 0-15 share 30 MB L3, E-cores 16-23 share 4 MB L3 → pin llama.cpp to P-cores only (`taskset -c 0-15`) for inference, leave E-cores for the OS". Adds the third dimension (L3 island) on top of NUMA (socket) and SMT (sibling) to the CPU placement picture, completes shipped #31.3 + #35.3 + #35.4 into a coherent placement quartet. First : `modules/cpu_cache_topology.py` + GET `/api/cpu-cache-topology`. |
| 37.5 | **`/proc/sys/kernel/sched_autogroup_enabled` + `/proc/<pid>/autogroup` autogroup TTY-share scheduler-fairness auditor for tmux/SSH-launched inference** (promoting from bench #36.5) | XS | 4 | Promoting from bench because the Ubuntu / Fedora / Pop!_OS default `sched_autogroup_enabled=1` silently buckets every process launched from the same TTY into a single scheduler-fairness group: a llama-server launched in a tmux pane that *also* holds a `htop`, a `journalctl -f`, a `tail -f`, and a half-broken `apt update` shares ONE CPU slice across all those processes instead of getting its own per-thread shares. Symptom: "tokens/s halves when I open a second tmux pane in the same SSH session" — a recurring forum complaint that shipped #34.4 proc_sched (per-thread `nr_involuntary_switches`) catches the *what* of but never surfaces autogroup as the *why*. Worse, autogroup is invisible in `htop` / `top` / `ps` (no column for it), so users have nowhere to look. Read `/proc/sys/kernel/sched_autogroup_enabled` (global toggle, 0/1) + per-PID `/proc/<pid>/autogroup` (autogroup id + nice level, settable per-group: write `echo -10 > /proc/<pid>/autogroup` to nice the group up), enumerate shipped service-discovery inference PIDs, for each read `/proc/<pid>/stat` field 7 (TTY) to find sibling processes sharing the same controlling terminal, build a graph "autogroup id → {PIDs}", surface "ollama PID 12834 shares autogroup #847 with 3 other busy processes (`journalctl -f` PID 9912, `tail -f /var/log/syslog` PID 9913, `apt update` PID 9914) — four processes splitting one CPU share, you're losing ~30-40 % of llama-server's CPU budget to the other three; nice the inference group up with `echo -10 > /proc/12834/autogroup` or disable autogroup entirely with `sysctl kernel.sched_autogroup_enabled=0`". Pairs naturally with shipped #34.4 proc_sched (the symptom) and shipped #31.3 cpu_topology (the placement) for a complete "where is my llama-server losing CPU?" diagnostic. First : `modules/sched_autogroup.py` + GET `/api/sched-autogroup`. |
| 37.6 | **`/sys/firmware/efi/efivars/SecureBoot-*` + `/sys/class/tpm/tpm*/{tpm_version_major,vendor,active_pcr_banks}` + `mokutil --list-enrolled` Secure Boot + TPM context auditor for DKMS / nvidia-installer signing** (promoting from bench #36.6) | S | 4 | Promoting from bench because Ubuntu 24.04 LTS + Fedora 40 + Pop!_OS 22.04+ + openSUSE Tumbleweed all ship Secure Boot enabled by default on UEFI machines, and the silent failure mode is the most-reported nvidia-Linux issue on every distro tracker: "next kernel update leaves you without nvidia.ko on boot, fall back to llvmpipe, can't even launch the dashboard's webview". Shipped #30.8 reads `/sys/kernel/security/lockdown` but doesn't disambiguate *why* lockdown is on (Secure Boot enabled vs `lockdown=integrity` kernel cmdline vs `lockdown=confidentiality`) and doesn't surface TPM presence (relevant for the AMD fTPM stutter bug on Ryzen 5000/7000 that mimics GPU hangs, fixed by AGESA 1.2.0.7+ but many users never updated BIOS) or DKMS MOK enrolment status. Read `/sys/firmware/efi/efivars/SecureBoot-*` (raw 4-byte EFI variable, last byte 0=disabled / 1=enabled), `/sys/firmware/efi/efivars/SetupMode-*` (1=user can enrol keys without a password), `/sys/class/tpm/tpm0/{tpm_version_major,vendor,active_pcr_banks}` (TPM 1.2 vs 2.0, vendor = AMD-fTPM / Intel-PTT / Infineon / STMicro / Nuvoton / dTPM), invoke `mokutil --list-enrolled` (subprocess, stdlib) to enumerate enrolled MOK keys, cross-check with shipped #6 dkms_status + #29.3 driver_flavor, classify into 5 states: (a) Secure Boot OFF = OK no signing needed, (b) Secure Boot ON + DKMS+MOK enrolled = OK, (c) Secure Boot ON + apt/dnf signed shim = OK (Ubuntu's `nvidia-driver-XXX-signed`), (d) Secure Boot ON + DKMS + NO MOK enrolled = LANDMINE, next kernel update breaks nvidia.ko, (e) fTPM AMD detected on old BIOS = stutter risk. Surface "Secure Boot ON + DKMS-built nvidia.ko + no MOK enrolled (`mokutil --list-enrolled` returns nothing) → next kernel update will fail to load nvidia.ko on boot, here's the `mokutil --import /var/lib/dkms/mok.pub` + reboot enrolment recipe" + "fTPM 2.0 on Ryzen detected, BIOS predates AGESA 1.2.0.7 — known stutter bug, see vendor BIOS update list". First : `modules/secureboot_tpm.py` + GET `/api/secureboot-tpm`. |
| 37.7 | **`/proc/sys/kernel/perf_event_paranoid` + `perf_event_mlock_kb` + `/proc/sys/kernel/kptr_restrict` profiling-readiness auditor for nsys / ncu / perf record / py-spy workflows** | XS | 3 | A small but vocal slice of homelab LLM-rig owners profile their inference workloads with `perf record` / `nsys profile` / `ncu` / `py-spy record` to chase the last few % of tokens/s — and every modern distro silently locks these tools out by default with `kernel.perf_event_paranoid=2` (which forbids unprivileged users from observing CPU events, hardware counters, and kernel symbols) and `perf_event_mlock_kb=516` (which caps the per-user mmap'd ring-buffer to 516 KB, causing nsys to drop events on busy traces). The fix is one sysctl line, but the symptom is opaque: nsys reports "Could not enable IBT for the current user" / `py-spy` reports `Permission denied`, and the user has no idea where to look. Today nothing in the shipped stack surfaces profiling-readiness. Read `/proc/sys/kernel/perf_event_paranoid` (-1=allow everything / 0=allow kernel profiling / 1=disallow CPU events / 2=disallow kernel access / 3=disallow user access, Ubuntu/Debian default), `perf_event_mlock_kb` (per-user mmap cap, default 516), `/proc/sys/kernel/kptr_restrict` (0=show kernel ptrs / 1=show for root / 2=hide), classify into 4 readiness levels: (a) fully open (paranoid≤0, mlock≥8192, kptr=0) → all profilers work, (b) typical desktop (paranoid=2, mlock=516) → only sudo'd `perf record` works, no nsys/ncu, (c) hardened (paranoid=3) → no profiling at all, (d) some-but-not-all (paranoid=1 + mlock=8192) → CPU profiling works, GPU CUDA tracing partly works. Surface "perf_event_paranoid=2 + perf_event_mlock_kb=516 (Ubuntu default) → nsys / ncu / py-spy record will fail or drop events for unprivileged users; if this is a single-user homelab box, drop to paranoid=1 and bump mlock_kb to 8192 with `/etc/sysctl.d/99-llm-profiling.conf` — paste-ready snippet" + a clear "only do this on a trusted single-user host" warning. Niche audience (profiler-curious users, ~10-15 % of homelab) but a clean answer to the "nsys says permission denied even though I'm in the group" recurring complaint. First : `modules/perf_paranoid.py` + GET `/api/perf-paranoid`. |
| 37.8 | **`/proc/sys/kernel/random/{poolsize,entropy_avail,write_wakeup_threshold}` + `/sys/class/misc/hw_random/{rng_current,rng_available}` CSPRNG / hardware-RNG readiness for HTTPS auth-token signing + bcrypt + boot-time token generation** (promoting from bench #36.8) | XS | 2 | Promoting from bench because the dashboard's own HTTPS / token-auth flow (shipped `auth_tokens.py`) calls `getrandom(2)` and on minimal headless installs (no audio entropy, no HID input, no `haveged`, no `rng-tools` package) `/proc/sys/kernel/random/entropy_avail` can sit below 256 bits for the first 30-90 s after boot — causing the dashboard's first POST to block long enough that the user thinks the service is hung. Even past boot, on cheap embedded NVMe controllers with no `rdrand` (rare but exists on AMD GX, some Atom-based ITX boards) and FIPS-mode kernels that reject the CRNG until a re-seed, the issue lingers. Today nothing in shipped catches it. Read `/proc/sys/kernel/random/poolsize` (max bits the entropy pool can hold, typically 256 on modern kernels), `entropy_avail` (current bits available), `write_wakeup_threshold` (when writers stop blocking), `/sys/class/misc/hw_random/rng_current` (is `tpm-rng` / `intel-rng` / `amd-rng` / `virtio-rng` feeding the pool?), `rng_available` (list of available HW RNGs), classify into 4 states: (a) entropy_avail≥256 + hw_rng_current=non-empty = OK, (b) entropy_avail<256 + no hw_rng = boot-time block risk, (c) rng_available has entries but rng_current is empty = HW RNG present but not selected (one `echo` fix), (d) virtualized guest with no virtio-rng = recurring problem. Surface "entropy_avail=89 bits + no HW RNG attached + uptime=12 s → dashboard's auth_tokens module will block on first signing call, attach `tpm-rng` via `modprobe tpm-rng` or install `rng-tools` (jitter-based seeding) — paste-ready systemd-enable snippet" + emit `rngd.service` enable line. Smaller audience (most modern x86 boxes have `rdrand`-fed CRNG which is fine) but a clean answer to the "my dashboard takes 30 s to start serving on this minimal Debian VM / Proxmox container" recurring homelab complaint. First : `modules/kernel_entropy.py` + GET `/api/kernel-entropy`. |

**Top 4 (fit × urgency)** :
1. 37.1 CPU vulnerabilities mitigation cost auditor — XS, fit 5, the lowest-effort + highest-leverage pick of the cycle: ~12 file reads under `/sys/devices/system/cpu/vulnerabilities/` + one embedded "known LLM-cost" lookup table catches the silent 10-20 % aggregate inference tax that Spectre/Retbleed/SRSO/GDS mitigations stack onto every modern CPU since 2018, an entirely invisible foot-gun on a single-user LAN-only homelab box where the threat model that justifies those mitigations does not apply. Sits underneath shipped #36.1 cpu_microcode (which silicon-fixes some of these for free) as the *aggregate "how much CPU is the kernel actually leaving on the table for security you may not need"* gate, completes the CPU-perf audit story. Biggest "I had no idea I was losing 13 % of my CPU to mitigations" eye-opener per kB of code in this cycle, with a clear-eyed `mitigations=off` GRUB cmdline snippet that is gated on a "this is a single-user trusted host" user confirmation. First : `modules/cpu_vulns.py`.
2. 37.2 GPU↔CPU PCIe local_cpulist affinity advisor — S, fit 5, the missing "*which exact cores* are electrically closest to your GPU" granularity that shipped #35.3 numa_placement (node-level only) and shipped #31.3 cpu_topology (governor + SMT siblings only) both stop short of; reads three sysfs files per GPU under `/sys/bus/pci/devices/`, cross-references shipped #31.3 + #35.3 + #35.4 (SMT runtime), surfaces the silent ~25 % cross-socket PCIe traffic penalty on Threadripper / EPYC / dual-Xeon / NPS4-Bergamo rigs where the user's natural `numactl --cpunodebind=0` guess is *wrong* because `numa_node=1`. Pairs with shipped #37.4 (L3 cache islands, also this cycle) to give the *complete* multi-dimensional CPU placement picture (socket → NUMA node → L3 island → SMT sibling → P/E core class) for inference threads. First : `modules/gpu_cpu_affinity.py`.
3. 37.3 hardware watchdog + `/dev/watchdog0` ping daemon detector — XS, fit 5, four sysfs reads under `/sys/class/watchdog/watchdog*/` + one `/proc/*/fd/` glob catch the silent "you think you have crash-protection and you don't" foot-gun (softdog-only, no daemon pinging, `nowayout=1` arm-and-fire trap) plus the smoking-gun `bootstatus=1` "your last reboot WAS a watchdog reset 3 d ago" diagnostic that pairs naturally with shipped #36.3 kernel_taint (MCE / oops history) to give a complete "why does my homelab box mysteriously reboot itself at 3 AM" answer. Highest-payoff "set it once and forget it" reliability win for unattended LLM-inference rigs, with a paste-ready `RuntimeWatchdogSec=30s` systemd-system.conf Drop-In. Zero overlap with shipped `watchdog_setup.py` (which is the dashboard's own `/readyz` self-poker via systemd user-timer, a completely different layer). First : `modules/hw_watchdog.py`.
4. 37.4 L3 cache topology + per-core sibling map for inference thread placement (promoted from bench #36.7) — S, fit 5, the third dimension (L3 island) on top of NUMA (socket, shipped #35.3) and SMT (sibling, shipped #31.3 + #35.4) that today nothing in the OSS Linux ecosystem surfaces cleanly for inference workloads — catches the silent ~9 % t/s penalty on every Ryzen 7950X / 7900X user who pins `taskset -c 0-15` across both CCDs without realising the 32 MB L3 doesn't span them, and the analogous Alder/Raptor P+E L3-island story. Walks `/sys/devices/system/cpu/cpu*/cache/index*/`, builds a "core → L3 island → island size" graph, classifies into 4 placement verdicts with paste-ready `taskset` snippets, and sits naturally next to shipped #37.2 (this cycle, GPU↔CPU PCI affinity) to complete the full CPU placement quartet. The audience is everyone on multi-CCD Zen4/Zen5 (a growing slice of homelab) plus Alder/Raptor hybrid (the bulk of new Intel desktop builds). First : `modules/cpu_cache_topology.py`.

Bench (37.5 sched_autogroup_enabled / 37.6 Secure Boot + TPM context / 37.7 perf_event_paranoid profiling-readiness / 37.8 kernel entropy + CSPRNG readiness) for cycles after top 4 lands.

---

## R&D #36 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-35), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8 + 29.2/29.4/29.5/29.6 + 30.4/30.6/30.7/30.8 + 31.5/31.6/31.7/31.8 + 32.2/32.6/32.7/32.8 + 33.3/33.5/33.7/33.8 + 34.5/34.6/34.7/34.8 + 35.5/35.6/35.7/35.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 36.1 | **`/sys/devices/system/cpu/microcode/{version,reload}` + `/proc/cpuinfo` per-core microcode revision drift detector + on-disk vs runtime divergence** (promoting from bench #35.5) | XS | 5 | Promoting from bench because shipped #35 cycle landed the CPU-perf audit triplet (cpu_boost turbo gate + smt_audit runtime SMT + net_sysctl_audit LAN buffers) and the now-obvious missing leg is "is your CPU even running the microcode revision the vendor intended for this workload?". Intel's 0x129 update (Raptor Lake voltage instability fix) shaved 2-7 % multithread throughput in many workloads; AMD's `cpu_amd_inv_lbr` microcode for Zen4 fixed a 12 % single-thread regression that an earlier patch had introduced; on long-uptime homelab boxes the running revision lags the package-manager-installed one by months because `intel-microcode` / `amd-ucode` package updates land the new file under `/lib/firmware/` but the kernel keeps the boot-time revision until next reboot or an explicit `echo 1 > /sys/devices/system/cpu/microcode/reload`. Read `/sys/devices/system/cpu/microcode/version` (runtime) + parse the header of `/lib/firmware/intel-ucode/<sig>` or `/lib/firmware/amd-ucode/microcode_amd*.bin` (on-disk), enumerate per-core via `/proc/cpuinfo`'s `microcode` field (catches the rare "core 7 stuck on the old revision after hotplug" Skylake edge case), cross-reference a tiny embedded "known LLM-affecting" revision list (Intel 0x129/0x12B for 14900K, AMD 0x0a601206 for Zen4, Intel 0x2f for Sapphire Rapids AMX), surface "runtime microcode 0x129 (known −5 % multi-thread on 14900K) — on-disk has 0x12B which restores it, reboot to apply" or "core 7 stuck at 0x121, others at 0x129 → hotplug bug, run `echo 1 > /sys/devices/system/cpu/microcode/reload`". Pairs with shipped #35.1 cpu_boost (turbo gate) for a complete "is your CPU running at the throughput it should" verdict. First : `modules/cpu_microcode.py` + GET `/api/cpu-microcode`. |
| 36.2 | **`/sys/devices/system/cpu/cpuidle/{current_driver,current_governor_ro}` + `/sys/devices/system/cpu/cpu*/cpuidle/state*/{name,latency,disable,usage,time}` C-state exit-latency tax auditor for first-token wakeup cost** | S | 5 | When a llama-server / ollama process is idle between requests (the common LAN-served homelab pattern: a phone or Home Assistant hits the API every 30-90 s), the CPU drops into deep C-states (C6, C7, C8, C10) with exit latencies of 100-1000 µs each — and the wakeup tax of *every* core that has to spin up for the first prompt token adds up to a measurable 5-15 ms ttft penalty on top of GPU launch latency. Today shipped #31.3 cpu_topology + #35.1 cpu_boost cover the *running* CPU's frequency story but say nothing about the *idle* CPU's wakeup story; users notice "the first token after a 60 s pause is slow but subsequent ones are fine" and have nowhere to look. Read `/sys/devices/system/cpu/cpuidle/current_driver` (`intel_idle` / `acpi_idle` / `none`), `current_governor_ro` (`menu` / `teo` / `ladder` / `haltpoll`), enumerate per-CPU `state*/{name,latency,disable,usage,time}` (latency in µs, usage = entries counter, time = ns spent in state), compute a per-core "deepest C-state seen × exit-latency" tax in the last sampling window, classify into 4 verdicts: (a) governor=`menu` (Ubuntu default) + deep C7+ available → typical wakeup tax for sporadic LAN traffic, (b) `intel_idle.max_cstate=1` cmdline → no idle wakeup penalty but burning ~30 W more at idle, (c) `cpuidle.off=1` → governor disabled entirely (rare, perf-mode), (d) `processor.max_cstate=N` quirk. Surface "your CPU sleeps in C8 (520 µs exit) 70 % of the time, governor=`menu` — if first-token latency after idle bothers you, try `cpupower idle-set -D 100` to cap exit latency at 100 µs (loses ~12 W idle, gains 4-8 ms ttft)". Emits a `cpupower idle-set` snippet + systemd Drop-In. Sibling to shipped #33.4 clocksource (timing reads) but for the wakeup-from-sleep side. First : migration `cpuidle_sample` + `modules/cpuidle_audit.py` + GET `/api/cpuidle-audit`. |
| 36.3 | **`/proc/sys/kernel/tainted` bitmask decoder + `/proc/uptime` long-uptime correlator (oom-killer pruner kthread degradation after 47+ days)** | XS | 5 | Linux's `/proc/sys/kernel/tainted` is a 32-bit bitmask that records every "I am no longer a clean vanilla kernel" event since boot: bit 0 = proprietary module loaded (nvidia.ko sets this universally), bit 4 = machine check exception happened, bit 7 = warning produced, bit 9 = kernel died and was restarted (`oops`), bit 12 = out-of-tree module loaded, bit 18 = livepatch applied, bit 28 = unsigned module on a Secure-Boot host. Today nothing in the shipped stack tells the user "your kernel has thrown 3 MCEs and a soft-lockup since boot 47 days ago" — a top hidden cause of degraded performance / random crashes that mimics GPU issues. Pair with `/proc/uptime` (long-uptime correlation: kernel kthreads like `khugepaged`, `kswapd`, the per-cgroup oomd pruner all accumulate drift / leak slab cache after 30+ days uptime, fixed only by reboot) + `dmesg` grep for the actual oops/MCE/warn lines that *set* each tainted bit, classify into 4 severities, surface "tainted=0x10081 (proprietary nvidia.ko + warning thrown 14d ago + MCE bit 4 — see dmesg line 12894 `mce: CPU 3: Machine Check Exception`) on a 53-day uptime → consider a reboot before chasing GPU instability; the MCE is non-fatal but degrades cache reliability". Tiny scope (two file reads + one bitfield decoder + a single dmesg grep), explains the recurring "my homelab box gets weirder over time" complaint that today's shipped #6 nvrm_tail (GPU side only) misses entirely. First : `modules/kernel_taint.py` + GET `/api/kernel-taint`. |
| 36.4 | **`/sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference` + `energy_performance_available_preferences` HWP EPP string-mode auditor for modern Intel (Tiger Lake+ / Sapphire Rapids+ / Alder Lake+)** (promoting from bench #35.7) | XS | 4 | Promoting from bench because shipped #32.7 EPB (legacy 0-15 byte) gives a *wrong* answer on every Intel CPU from 2020 onward (Tiger Lake+, Sapphire Rapids+, Alder Lake+) where the HWP-aware kernel preempts EPB with the richer string-mode interface at `policy*/energy_performance_preference` (`default` / `performance` / `balance_performance` / `balance_power` / `power`). Ubuntu Server ships `balance_performance` by default, GNOME desktops with `power-profiles-daemon` ship `balance_power`, and both silently cap llama.cpp prompt-eval single-thread turbo by 200-400 MHz versus `performance`. Read `energy_performance_preference` per policy (per-core or grouped depending on CPU), `energy_performance_available_preferences` (the whitelist of values the silicon supports — Sapphire Rapids has 5, Alder Lake P-cores have 5, E-cores have 3), branch on `/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver` (`intel_pstate` HWP-active = use this interface, `acpi_cpufreq` = fall through to shipped #32.7 EPB), surface "16 policies at `balance_performance` (GNOME `power-profiles-daemon` default on Ubuntu desktop) → losing ~12 % prompt-eval single-thread turbo, `echo performance | sudo tee /sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference` (one-shot) + `tuned-adm profile throughput-performance` (persistent across reboots)". Tiny scope, fixes the silent-wrong-answer on modern Intel, pairs with shipped #35.1 (turbo gate) + #35.4 (SMT runtime) for a complete CPU-perf audit quartet. First : `modules/hwp_epp.py` + GET `/api/hwp-epp`. |
| 36.5 | **`/proc/sys/kernel/sched_autogroup_enabled` + `/proc/<pid>/autogroup` autogroup TTY-share scheduler-fairness auditor for tmux/SSH-launched inference** | XS | 4 | Linux's `sched_autogroup_enabled=1` (Ubuntu default) silently buckets every process launched from the same TTY into a single scheduler-fairness group, so the entire group gets one share of CPU rather than per-thread shares — meaning a llama-server launched in a tmux pane that *also* holds a `htop`, a `journalctl -f`, a `tail -f`, and a half-broken `apt update` shares one CPU slice across all those processes. Symptom: "tokens/s halves when I open a second tmux pane in the same session". Shipped #34.4 proc_sched catches the per-thread `nr_involuntary_switches` (the *what*) but does not surface autogroup as the *why* — a missing-link diagnosis. Read `/proc/sys/kernel/sched_autogroup_enabled` (global toggle, 0/1) + per-PID `/proc/<pid>/autogroup` (autogroup id + nice level, settable per-group), enumerate shipped service-discovery inference PIDs and check if they share an autogroup with other busy processes (correlate via `/proc/<pid>/stat`'s TTY field), surface "ollama PID 12834 shares autogroup #847 with `journalctl -f` PID 9912 and `tail -f /var/log/syslog` PID 9913 — three processes splitting one CPU share, nice the autogroup down: `echo -10 > /proc/12834/autogroup` or disable autogroup entirely with `sysctl kernel.sched_autogroup_enabled=0`". Tiny scope (one global toggle + per-PID parse), explains the "tmux pane shenanigans halve my tokens/s" complaint that shipped #34.4 alone leaves unrooted. First : `modules/sched_autogroup.py` + GET `/api/sched-autogroup`. |
| 36.6 | **`/sys/firmware/efi/efivars/SecureBoot-*` + `/sys/class/tpm/tpm*/{tpm_version_major,vendor,active_pcr_banks}` + `mokutil --list-enrolled` Secure Boot + TPM context auditor for DKMS / nvidia-installer signing** (promoting from bench #35.6) | S | 4 | Promoting from bench because Ubuntu 24.04 LTS + Fedora 40 + Pop!_OS 22.04+ all ship Secure Boot enabled by default on UEFI machines, and the silent failure mode is "next kernel update leaves you without nvidia.ko on boot" — a top-5 GitHub issue in every Linux nvidia-driver tracker. Shipped #30.8 reads `/sys/kernel/security/lockdown` but does not disambiguate *why* lockdown is on (Secure Boot enabled vs `lockdown=integrity` kernel cmdline vs `lockdown=confidentiality`) and does not surface TPM presence (relevant for the AMD fTPM stutter bug that mimics GPU hangs on Ryzen 5000/7000, fixed by AGESA 1.2.0.7+ but many users never updated BIOS). Read `/sys/firmware/efi/efivars/SecureBoot-*` (raw 4-byte EFI variable, last byte 0=disabled / 1=enabled), `/sys/firmware/efi/efivars/SetupMode-*` (1=user can enrol keys without password), `/sys/class/tpm/tpm0/{tpm_version_major,vendor,active_pcr_banks}` (TPM 1.2 vs 2.0, vendor = AMD-fTPM / Intel-PTT / Infineon / STMicro / dTPM), invoke `mokutil --list-enrolled` (subprocess, stdlib) to enumerate enrolled MOK keys, cross-check with shipped #6 dkms_status and #29.3 driver_flavor, surface "Secure Boot ON + no MOK enrolled (`mokutil --list-enrolled` returns nothing) → next kernel update will fail to load nvidia.ko on boot, here's the `mokutil --import /var/lib/dkms/mok.pub` + reboot enrolment recipe" + "fTPM 2.0 on Ryzen (AMD vendor) detected without recent BIOS — known stutter bug, see vendor BIOS update". First : `modules/secureboot_tpm.py` + GET `/api/secureboot-tpm`. |
| 36.7 | **`/sys/devices/system/cpu/cpu*/cache/index*/{shared_cpu_list,size,type,level}` L3 cache topology + per-core sibling map for inference thread placement on heterogenous CPUs (Alder/Raptor P+E, Bergamo CCDs)** | S | 3 | Shipped #31.3 cpu_topology emits `taskset -c 0,2,4,…` advice based on SMT siblings, and shipped #35.4 smt_audit recomputes it for runtime SMT state — but neither knows about *L3 cache* sharing, which is the dominant factor for inference performance on heterogenous chips (Alder Lake P-cores have their own L3 island separate from E-cores; AMD Zen4/5 CCDs each have a 32 MB L3 that *does not* share across CCDs; Bergamo / Genoa-X have wildly different L3 sizes per die). When llama.cpp's worker threads land on cores in different L3 islands, every cross-island memory access is an L3 miss → DRAM round-trip → ~80 ns latency that destroys prompt-eval throughput silently. Walk `/sys/devices/system/cpu/cpu*/cache/index*/` for each cache level (typically index0=L1d, index1=L1i, index2=L2, index3=L3), read `shared_cpu_list` (e.g. `0-7` = cores 0-7 share this L3) + `size` + `type` + `level`, build a graph "core → L3 island → island size", cross-check with shipped #31.3 NUMA + #35.3 numa_placement, surface "Ryzen 7950X has 2 CCDs (cores 0-7 + 8-15) each with 32 MB L3 — pin all 16 llama-server worker threads to one CCD with `taskset -c 0-7` for +9 % t/s vs scattered, here's the snippet" or "Alder Lake 12900K has P-cores 0-15 sharing 30 MB L3 and E-cores 16-23 sharing 4 MB L3 — pin to P-cores only for llama.cpp inference". Adds the third dimension (L3 island) on top of NUMA (socket) and SMT (sibling) to the CPU placement picture. First : `modules/cpu_cache_topology.py` + GET `/api/cpu-cache-topology`. |
| 36.8 | **`/proc/sys/kernel/random/{poolsize,entropy_avail,write_wakeup_threshold}` + `/dev/random` getrandom readiness for HTTPS auth-token signing + inference signing** | XS | 2 | The dashboard's own HTTPS / token-auth flow (shipped `auth_tokens.py`) depends on `getrandom(2)` blocking briefly at boot if the kernel CSPRNG isn't seeded; on minimal headless installs (no audio entropy, no HID input, no `haveged`, no `rng-tools`), `/proc/sys/kernel/random/entropy_avail` can sit below 256 bits for the first 30-90 s after boot, causing `auth_tokens` to *block* its first request and the dashboard to look "hung" until the kernel finds enough entropy from disk / network interrupts. Even past boot, on cheap embedded NVMe controllers with no hardware RNG and `random.fips_enabled=1` (FIPS-mode kernels reject the CRNG until a re-seed), the issue lingers. Read `/proc/sys/kernel/random/poolsize` (max bits the entropy pool can hold, typically 256 on modern kernels), `entropy_avail` (current bits available), `write_wakeup_threshold` (when writers stop blocking), check `/sys/class/misc/hw_random/{rng_current,rng_available}` (is `tpm-rng` or `intel-rng` feeding the pool?), surface "entropy_avail=89 bits + no hardware RNG attached + boot+12s → your dashboard's auth_tokens module will block on first signing call, attach `tpm-rng` via `modprobe tpm-rng` or install `rng-tools` for jitter-based seeding" + emit a one-line `rngd` systemd-enable snippet. Smaller audience (most modern x86 boxes have rdrand-fed CRNG) but a clean answer to the "my dashboard takes 30 s to start serving on this minimal Debian VM" homelab complaint. First : `modules/kernel_entropy.py` + GET `/api/kernel-entropy`. |

**Top 4 (fit × urgency)** :
1. 36.1 CPU microcode revision drift detector (promoted from bench #35.5) — XS, fit 5, the lowest-effort + highest-leverage pick of the cycle: one runtime read + one on-disk header parse + one per-core `/proc/cpuinfo` walk catches the silent "your apt-installed microcode update never actually loaded because nobody rebooted" case that hides 5-12 % multi-thread regressions on 14900K (Intel 0x129) and Zen4 boxes (AMD 0x0a601206) — and also catches the rare per-core divergence that breaks shipped #31.3's pinning advice. Sits underneath shipped #35.1 (turbo gate) and #34.4 proc_sched (per-thread accounting) as the *binary "is your CPU even running the silicon's intended throughput"* gate, completing the CPU-perf audit story. Biggest "explains a recurring weekly complaint with one trivial read" of the cycle.
2. 36.2 cpuidle C-state exit-latency tax auditor — S, fit 5, the missing "why is my first token after a 60 s pause slow but subsequent ones are fine" diagnostic that today has zero surface in the shipped stack; reads cpuidle governor + driver + per-CPU per-state usage/time/latency at 1 Hz and turns the recurring "ttft is jittery between requests" LAN-served-ollama complaint into a concrete "your CPU sleeps in C8 (520 µs exit) 70 % of the idle window, here's the `cpupower idle-set -D 100` snippet for +4-8 ms ttft at the cost of ~12 W idle". Pairs naturally with shipped #33.4 clocksource (timing reads) + #35.1 cpu_boost (running frequency) for a complete idle→active→running CPU story.
3. 36.3 kernel taint flags + uptime correlator — XS, fit 5, two file reads + a bitmask decoder + one dmesg grep catch the silent "my homelab box gets weirder over time, MCEs piled up since the last reboot, kernel oopsed once 14 d ago" case that today shipped #6 nvrm_tail (GPU side only) misses entirely; explains the recurring "uptime=53 days and tokens/s feels off but the GPU looks fine" complaint with a hard verdict ("tainted=0x10081 + 53 d uptime + MCE on CPU 3 → reboot before chasing further") and a single user-facing severity classification. Tiniest effort, biggest "stops the user blaming the GPU when the kernel is sick" payoff in the cycle.
4. 36.4 HWP EPP string-mode auditor (promoted from bench #35.7) — XS, fit 4, fixes the silent-wrong-answer in shipped #32.7 EPB on every Intel CPU from 2020 onward (Tiger Lake+ / Sapphire Rapids+ / Alder Lake+ — the bulk of the homelab audience's hardware), where the HWP-aware kernel preempts EPB with the `energy_performance_preference` string-mode interface at `policy*`. Two reads per policy, surfaces the GNOME `power-profiles-daemon` `balance_performance` default that costs ~12 % prompt-eval single-thread turbo, emits a paste-ready `tuned-adm profile throughput-performance` snippet. Completes the CPU-perf audit quintet with shipped #35.1 (turbo gate) + #35.4 (SMT runtime) + #36.1 (microcode revision) + #36.2 (cpuidle exit latency) into one coherent "what your CPU is actually doing right now" Diagnostics card.

Bench (36.5 sched_autogroup_enabled / 36.6 Secure Boot + TPM context / 36.7 L3 cache topology / 36.8 kernel entropy + CSPRNG readiness) for cycles after top 4 lands.

---

## R&D #35 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-34), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8 + 29.2/29.4/29.5/29.6 + 30.4/30.6/30.7/30.8 + 31.5/31.6/31.7/31.8 + 32.2/32.6/32.7/32.8 + 33.3/33.5/33.7/33.8 + 34.5/34.6/34.7/34.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 35.1 | **`/sys/devices/system/cpu/cpufreq/boost` + `/sys/devices/system/cpu/intel_pstate/no_turbo` Intel Turbo Boost / AMD CPB runtime-toggle auditor** | XS | 5 | The single byte that decides whether the box's CPU is actually allowed to clock above base frequency lives in one of two well-known files: AMD exposes `/sys/devices/system/cpu/cpufreq/boost` (0/1, Core Performance Boost), Intel exposes `/sys/devices/system/cpu/intel_pstate/no_turbo` (inverted: 1=disabled). A startling slice of homelab rigs ships with these off because the user once toggled "powersave" in GNOME tuned, set `cpupower frequency-set --max` during a thermal scare, or `tuned-adm profile powersave` left them stuck — and llama.cpp prompt-eval then runs ~25-40 % slower with no visible cause (governor still reads `performance`, frequency cap looks fine, but turbo bins are simply unreachable). Distinct from shipped #31.3 cpu_topology (governor only) and #34.8-bench HWP-EPP (string-mode prefs): this is the binary "is turbo even available?" gate that those settings sit on top of. Two reads, branch on vendor (`/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver` tells you which file applies), surface "AMD CPB is disabled → your 7950X is capped at 4.5 GHz instead of 5.7 GHz boost, lose ~26 % single-thread prompt-eval, `echo 1 | sudo tee /sys/devices/system/cpu/cpufreq/boost` + add to `tuned`/`systemd-tmpfiles` to persist". First : `modules/cpu_boost.py` + GET `/api/cpu-boost`. |
| 35.2 | **`/proc/sys/net/core/{somaxconn,rmem_max,wmem_max,netdev_max_backlog}` + `/proc/sys/net/ipv4/tcp_{rmem,wmem,fastopen}` LAN-served ollama / OpenWebUI socket-buffer auditor** | XS | 5 | Shipped #33.1 nic_health caught the autoneg-fallback / cable foot-gun, but the *next* invisible cap on LAN-served ollama is the per-socket buffer + accept-queue tuning: Ubuntu defaults `somaxconn=4096` (fine) but `tcp_rmem` ceiling = 6 MiB and `wmem_max` = 208 KiB, which on a 1 GbE LAN with 0.3 ms RTT can hold the BDP yet still throttle a streaming generation when the OS scheduler ticks while the buffer is full (visible as 50-80 ms "micro-stalls" between tokens served to OpenWebUI clients). Worse, when the user installs `tuned-adm profile network-latency` it silently *shrinks* these to optimise for low-latency request/reply, hurting bulk streaming. Eight stdlib reads, diff against an "LLM-LAN baseline" JSON, correlate any post-#33.1 per-window NIC stall against current buffer ceilings, surface "wmem_max=208 KiB + tcp_wmem=4M default → your ollama API streams to OpenWebUI cap at ~80 KB/s per connection during scheduler pressure, raise to 16 MiB via `sysctl.d/99-llm-lan.conf` (snippet attached)" — completes the LAN-serving triangle with #33.1 (link health) + #33.6 (cgroup IOWeight). First : `modules/net_sysctl_audit.py` + GET `/api/net-sysctl-audit`. |
| 35.3 | **`/sys/devices/system/node/node*/{meminfo,cpulist,distance}` + `/sys/bus/pci/devices/<gpu>/numa_node` NUMA placement auditor for GGUF mmap on Threadripper / EPYC / dual-Xeon** (promoting from bench #33.3) | S | 5 | Promoting from bench because shipped #34 cycle landed three memory-side modules (#34.1 THP, #34.2 buddyinfo frag, #34.3 oomd-correlator) that *all* assume single-NUMA-node placement and silently mis-advise on multi-socket / Threadripper boxes — a small but loud audience slice where the symptom is exactly "I followed your THP advice and still lose 15 % t/s". On a 2-node Threadripper, llama-server's mmap'd GGUF lands wherever the first-touching thread runs, typically opposite the GPU's PCIe root complex; every host→device DMA then traverses the Infinity-Fabric link, costing ~15-25 % prompt-eval throughput silently. Today nothing surfaces it: shipped #31.3 cpu_topology emits a `taskset` snippet but ignores memory affinity. Enumerate `/sys/devices/system/node/node*/meminfo` (MemFree/MemUsed/FilePages per node), `cpulist`, `distance` matrix, cross-check `/sys/bus/pci/devices/<gpu>/numa_node`, walk shipped service-discovery inference PIDs and read `/proc/<pid>/numa_maps` to see *where* their mmap'd pages live, surface "GPU on node 1 but 22/24 GiB of llama-server's mmap sits on node 0 → cross-socket traffic on every prompt, run `numactl --cpunodebind=1 --membind=1 llama-server …` for +18 % t/s" + ready-to-paste `numactl` + systemd Drop-In snippet. First : `modules/numa_placement.py` + GET `/api/numa-placement`. |
| 35.4 | **`/sys/devices/system/cpu/smt/{control,active}` + `/sys/devices/system/cpu/cpu*/online` SMT toggle + offline-core audit** (promoting from bench #33.8) | XS | 4 | Promoting from bench because shipped #31.3 cpu_topology emits `taskset -c 0,2,4,…` advice that assumes SMT-on and all cores online — when reality diverges (SMT was disabled in BIOS for security, a Spectre L1TF mitigation hot-unplugged half the cores, a thermal event triggered a hot-CPU-offline) the user gets confused advice ("pin to cpu 14? it's offline") and the dashboard's own correlation loses half its data without explanation. Three reads: `/sys/devices/system/cpu/smt/control` (`on`/`off`/`forceoff`/`notsupported`), `/sys/devices/system/cpu/smt/active` (0/1), per-cpu `online` (glob), classify into 4 states and *recompute* shipped #31.3's taskset advice on the fly to match runtime reality, surface "SMT=forceoff (kernel cmdline `nosmt=force` from a Spectre paranoid install) → only 8 logical CPUs available, here's the recomputed taskset snippet" or "cpu14 offline since boot+3h (thermal HW unplug, see `dmesg | grep -i cpu14`) → investigate VRM/cooling before re-pinning". Tiny scope, sharpens shipped #31.3, also catches the "I disabled SMT in BIOS to chase a CUDA bug and forgot" case. First : `modules/smt_audit.py` + GET `/api/smt-audit`. |
| 35.5 | **`/sys/devices/system/cpu/microcode/{version,reload}` + `/proc/cpuinfo` per-core microcode revision drift detector** | XS | 4 | CPU microcode silently fixes (and sometimes regresses) performance — Intel's 0x129 update to fight Raptor Lake voltage instability shaved 2-7 % of multithread throughput in many workloads, AMD's `cpu_amd_inv_lbr` microcode for Zen4 fixed a 12 % single-thread regression introduced by an earlier patch. Today the dashboard can't tell the user whether their host is running the up-to-date or the buggy revision. Worse: an `intel-microcode` / `amd-ucode` package update lands the new file under `/lib/firmware/` but the running kernel keeps the boot-time microcode until early-init reload at next boot — `/sys/devices/system/cpu/microcode/version` shows runtime, `/lib/firmware/intel-ucode/<model>` (parsed header) shows what *would* load. Detect divergence (runtime vs on-disk), enumerate per-core via `/proc/cpuinfo` (catches the rare "core 7 has older microcode because hotplug didn't reapply" Skylake edge case), cross-reference a tiny embedded "known LLM-regression" list (3-4 revisions: Intel 0x129, AMD Zen4 0x0a601206), surface "runtime microcode 0x129 (Intel known to lose ~5 % multi-thread on 14900K) — on-disk has 0x12b which restores it, reboot to apply" or "core 7 stuck at 0x121, others at 0x129 → hotplug bug, run `echo 1 > /sys/devices/system/cpu/microcode/reload` then re-pin". First : `modules/cpu_microcode.py` + GET `/api/cpu-microcode`. |
| 35.6 | **`/sys/firmware/efi/efivars/SecureBoot-*` + `/sys/class/tpm/tpm*/{tpm_version_major,vendor,active_pcr_banks}` Secure Boot + TPM context auditor for DKMS / nvidia-installer signing** (promoting from bench #34.6) | S | 4 | Promoting from bench because Ubuntu 24.04 LTS + Fedora 40 ship Secure Boot enabled by default on UEFI machines, and the silent failure mode is "next kernel update leaves you without nvidia.ko on boot" — a top-5 GitHub issue in every Linux nvidia-driver tracker. Shipped #30.8 reads `/sys/kernel/security/lockdown` but doesn't disambiguate *why* lockdown is on (Secure Boot vs `lockdown=integrity` cmdline) and doesn't surface TPM presence (relevant for the AMD fTPM stutter bug that mimics GPU hangs on Ryzen, fixed by AGESA but many users never updated BIOS). Read `/sys/firmware/efi/efivars/SecureBoot-*` (raw 4-byte EFI variable, last byte 0/1), `/sys/firmware/efi/efivars/SetupMode-*`, `/sys/class/tpm/tpm0/{tpm_version_major,vendor,active_pcr_banks}`, cross-check with shipped #6 dkms_status and #29.3 driver_flavor, surface "Secure Boot ON + no MOK enrolled (`mokutil --list-enrolled` returns nothing) → next kernel update will fail to load nvidia.ko, here's the `mokutil --import` + reboot enrolment recipe" + "fTPM 2.0 on Ryzen (AMD vendor) without recent BIOS → known Windows stutter, see vendor BIOS update". First : `modules/secureboot_tpm.py` + GET `/api/secureboot-tpm`. |
| 35.7 | **`/sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference` + `energy_performance_available_preferences` HWP EPP string-mode auditor** (promoting from bench #34.8) | XS | 4 | Promoting from bench because shipped #32.7 covered the legacy 0-15 `energy_perf_bias` byte but modern Intel HWP (Tiger Lake+, Sapphire Rapids+, Alder Lake+) preempts EPB with the richer string-mode interface at `policy*/energy_performance_preference` — meaning shipped #32.7 *gives a wrong answer* on every CPU from 2020 onward, the bulk of the homelab audience's hardware. Read `energy_performance_preference` per policy (values: `default`/`performance`/`balance_performance`/`balance_power`/`power`) + `energy_performance_available_preferences` (whitelist of what the CPU supports), branch on `/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver` (`intel_pstate` HWP-active = use this, `acpi_cpufreq` = fall through to shipped #32.7 EPB), surface "16 policies at `balance_performance` (GNOME power-profiles-daemon default) → losing ~12 % prompt-eval single-thread turbo, `echo performance | sudo tee /sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference` (one-shot) + `tuned-adm profile throughput-performance` (persistent)". Tiny scope, fixes the silent-wrong-answer on modern Intel, pairs with #35.1 (turbo gate) + #35.4 (SMT runtime) for a complete CPU-perf audit triplet. First : `modules/hwp_epp.py` + `GET /api/hwp-epp`. |
| 35.8 | **`/sys/class/power_supply/{BAT*,AC,ADP*}/` + `/sys/class/dmi/id/chassis_type` laptop/eGPU battery-aware GPU clamp + chassis classifier combo** (promoting from bench #33.5 + #31.5) | XS | 3 | Promoting both bench items together because they're each other's gating mechanism: chassis_type (3=Desktop, 9=Laptop, 10=Notebook, 14=SubNotebook, 23=RackMount, 31=Convertible) is the single byte that decides whether to even surface battery cards in the UI, and the battery cards themselves are the foot-gun that justifies the chassis classifier. Today every Settings panel shows the same content regardless of form-factor; a rack-server user sees an irrelevant "battery low" mock-up and a laptop+eGPU user gets no warning when their 250 W eGPU drains a 90 Wh battery in 20 min. One read of `/sys/class/dmi/id/chassis_type` (gate), enumerate `/sys/class/power_supply/BAT*/{capacity,status,cycle_count,energy_now,energy_full}` + `AC/online`, classify into 4 form-factors, push the classification into shipped Settings → Integrations to auto-hide irrelevant cards on rack servers and auto-show them on laptops, surface "chassis_type=10 (Notebook) + on battery + GPU at 250 W → 22 min runtime, consider `nvidia-smi -pl 120` while unplugged" with one-click auto-clamp toggle. Smaller audience (laptop+eGPU rigs are ~15 % of users) but high satisfaction-per-feature for that slice. First : `modules/chassis_battery.py` + GET `/api/chassis-battery`. |

**Top 4 (fit × urgency)** :
1. 35.1 CPU turbo / boost runtime-toggle auditor — XS, fit 5, the lowest-effort + highest-leverage pick of the cycle: two reads (one branched on vendor) catch the silent ~25-40 % single-thread prompt-eval penalty when AMD CPB or Intel Turbo is disabled and the user doesn't realise it (governor still says `performance`, frequency cap looks fine, but turbo bins are simply unreachable). Sits underneath shipped #31.3 cpu_topology (governor advice), #32.7 EPB, and bench-promoted #35.7 HWP-EPP as the *binary "is turbo even available?" gate* those settings depend on — answers the recurring "I set governor=performance but my t/s didn't move" complaint with a one-line `echo 1 > /sys/devices/system/cpu/cpufreq/boost` fix. Biggest "explains a recurring weekly forum post" per kB of code in this cycle.
2. 35.2 LAN socket-buffer auditor for ollama / OpenWebUI — XS, fit 5, completes the LAN-serving triangle that shipped #33.1 (NIC link health) + #33.6 (cgroup IOWeight) started: eight `/proc/sys/net/*` reads catch the Ubuntu-default `wmem_max=208 KiB` + `tuned-adm profile network-latency` foot-guns that turn streaming token responses to OpenWebUI / mobile clients into 50-80 ms micro-stalls during scheduler pressure. Emits a paste-ready `sysctl.d/99-llm-lan.conf` snippet and correlates per-window NIC stall (already collected by shipped #33.1) against buffer ceilings to prove causation — turns the "ollama feels laggy on my phone tonight" complaint into a one-line verdict. Lowest-effort of the four and tightly synergistic with two already-shipped modules.
3. 35.3 NUMA placement auditor (promoted from bench #33.3) — S, fit 5, the silent ~15-25 % cross-socket DMA penalty on Threadripper / EPYC / dual-Xeon rigs that shipped #34 cycle's memory triplet (#34.1 THP / #34.2 buddyinfo / #34.3 oomd) *all* mis-advise on by silently assuming single-NUMA-node placement. Reads node meminfo + cpulist + distance + GPU `numa_node` + per-PID `/proc/<pid>/numa_maps` for the inference daemon, surfaces "22/24 GiB of GGUF lives on the wrong node" with a paste-ready `numactl --cpunodebind=N --membind=N` snippet + systemd Drop-In. Smaller audience than picks #1+#2 (multi-socket is ~10-15 % of homelab) but biggest absolute t/s lift on the rigs it applies to, and the only thing in the OSS Linux ecosystem that surfaces NUMA-aware GGUF placement without manual `numastat` digging.
4. 35.4 SMT toggle + offline-core audit (promoted from bench #33.8) — XS, fit 4, three reads + per-cpu glob that *sharpens* shipped #31.3 cpu_topology by recomputing the `taskset` advice on the fly when reality diverges from "SMT-on, all cores online" — catches the BIOS-disabled-SMT case, the Spectre `nosmt=force` cmdline case, and the rare-but-confusing thermal-hotplug-offlined-core case where shipped #31.3's advice silently refers to a non-existent CPU. Trivial effort, completes the CPU-perf audit triplet with #35.1 (turbo gate) + bench-promoted #35.7 (HWP-EPP modern) into a coherent "what your CPU is actually doing right now" Diagnostics card.

Bench (35.5 microcode revision drift / 35.6 Secure Boot + TPM context / 35.7 HWP EPP string-mode auditor / 35.8 chassis_type + battery-aware GPU clamp combo) for cycles after top 4 lands.

---

## R&D #34 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-33), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8 + 29.2/29.4/29.5/29.6 + 30.4/30.6/30.7/30.8 + 31.5/31.6/31.7/31.8 + 32.2/32.6/32.7/32.8 + 33.3/33.5/33.7/33.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 34.1 | **`/sys/kernel/mm/transparent_hugepage/{enabled,defrag,khugepaged/defrag}` THP setting auditor for llama.cpp / ggml-cuda mmap regions** | XS | 5 | Transparent Huge Pages is one of the loudest invisible knobs for inference: on `always` defrag the kernel will stall a 24 GiB GGUF mmap mid-prompt-eval for hundreds of milliseconds while it tries to compact anonymous pages, producing the dreaded "first token took 6 s for no reason" complaint that today has zero diagnostic surface in the shipped stack. The LLM-rig recipe is `enabled=madvise` (let llama.cpp opt in via `MADV_HUGEPAGE`) + `defrag=defer+madvise` (never block the inference thread), but distros ship a mix (`always`/`always`, `madvise`/`defer`, etc.). Three trivial reads, diff against an "LLM-rig baseline" JSON, surface "transparent_hugepage=always + defrag=always → your llama-server can stall up to 500 ms during prompt-eval on a kernel compaction event; set madvise / defer+madvise, here's the sysfs + GRUB snippet" + correlate any past PSI memory.full spikes (shipped #32.1) with khugepaged activity. First : `modules/thp_audit.py` + GET `/api/thp-audit`. |
| 34.2 | **`/proc/buddyinfo` + `/proc/zoneinfo` external memory-fragmentation gauge for large mmap reservations** | S | 5 | When the host has been up for 30+ days and `free -h` shows "12 GiB available" but loading a new 20 GiB GGUF nevertheless triggers SIGBUS / `ENOMEM`, the cause is almost always external fragmentation: `/proc/buddyinfo` shows the free pages broken into order-0/order-1 slivers with nothing at order-9 (2 MiB) or order-10 (4 MiB) that THP and `mmap(MAP_HUGETLB)` need. Today nothing in the dashboard surfaces this — users blame "ollama memory leak" when the real issue is kernel zone fragmentation. Parse `/proc/buddyinfo` (per-zone free pages per order) + `/proc/zoneinfo` (`pages free`, `pages min/low/high`, `nr_free_pages`), compute an external-fragmentation index (Mel Gorman's formula: 1 − Σ(2^k · n_k) / total_free_pages), surface "Normal zone fragmentation index = 0.94 — only 3 order-9 blocks free, your next 24 GiB GGUF mmap will allocate base pages (no THP) and run ~8 % slower; consider `echo 1 > /proc/sys/vm/compact_memory` or reboot". First : migration `buddyinfo_sample` + `modules/buddyinfo_frag.py` + GET `/api/buddyinfo-frag`. |
| 34.3 | **`systemd-oomd` status + `/run/systemd/oom-killed` + journal `_TRANSPORT=kernel` OOM event correlator** | S | 5 | Ubuntu 22.04+ ships `systemd-oomd` enabled by default, which can kill ollama / llama-server *before* the kernel OOM killer ever fires (and with completely different rules — it watches PSI memory.full averaged over 20 s per cgroup). Users see "ollama died, no Xid, no kernel OOM in dmesg" and have nowhere to look because the kill is recorded only in `journalctl -u systemd-oomd.service` + `/run/systemd/oom-killed` (transient file). Detect oomd is active (`systemctl is-active systemd-oomd`), parse its journal lines via `journalctl --since="-24h" -u systemd-oomd -o json` (stdlib subprocess + JSON), correlate kills with shipped service-discovery inference PIDs, surface "systemd-oomd killed ollama.service 3× this week (PSI memory.full > 20 % for 20 s) — your default `ManagedOOMMemoryPressure=auto` is too aggressive for LLM rigs, set `ManagedOOMMemoryPressure=kill` only on browser cgroups via Drop-In" + complete the OOM-detection triangle (kernel OOM via dmesg/Xid + systemd-oomd via journal + cgroup memory.events via shipped #32.5). First : `modules/oomd_correlator.py` + GET `/api/oomd-correlator`. |
| 34.4 | **`/proc/<pid>/sched` per-inference-thread scheduler accounting (sum_exec_runtime, nr_voluntary_switches, nr_involuntary_switches, se.statistics.wait_sum)** | S | 5 | `/proc/<pid>/sched` is the single richest per-thread scheduler view in Linux — it exposes `sum_exec_runtime` (CPU ns actually executed), `nr_voluntary_switches` (yielded on I/O / sleep), `nr_involuntary_switches` (preempted by another runnable task), `se.statistics.wait_sum` (ns spent on runqueue waiting for CPU). Shipped #32.1 PSI tells the *system* it's CPU-stalled and #31.3 cpu_topology tells the user how to pin, but nobody surfaces "thread 7 of llama-server was preempted 14 200 times in the last minute and spent 4.2 s on the runqueue waiting" — the smoking-gun for "another runnable workload is eating my inference cores" that PSI only hints at as aggregate. Delta-sample per inference PID + per worker thread (read `/proc/<pid>/task/*/sched`) at 1 Hz, classify into voluntary-bound (I/O wait) vs involuntary-bound (CPU contention), surface "llama-server tid 12834 involuntary_switches/s = 240, wait_sum/s = 380 ms → another CPU-heavy task is preempting your inference thread, see what's running on cpu14". First : migration `sched_sample` + `modules/proc_sched.py` + GET `/api/proc-sched`. |
| 34.5 | **`/proc/<pid>/timerslack_ns` per-daemon NTP-class precision auditor for sampler loops + token timing** | XS | 4 | Linux defaults `timerslack_ns=50000` (50 µs) per process — for most daemons fine, but for the dashboard's own 1 Hz sampler loop *and* for llama.cpp's `clock_nanosleep`-based mini-batch pacing it adds quantised drift on top of HPET / clocksource jitter (shipped #33.4). Worse, `systemd-tmpfiles.d` on some distros sets `nice 19` + `timerslack_ns=500000000` (500 ms!) on background services that the user later repurposes as inference daemons via `systemctl edit`. Read `/proc/<pid>/timerslack_ns` for the dashboard's own PID + each shipped service-discovery inference PID, flag any > 100 µs, surface "ollama.service inherited timerslack_ns=500000000 from its systemd unit's `TimerSlackNSec=` → your token-streaming pacing has 500 ms jitter, add `TimerSlackNSec=50us` to the unit" + the Drop-In snippet. Tiny scope, but a precise complement to #33.4 clocksource. First : `modules/timerslack_audit.py` + GET `/api/timerslack-audit`. |
| 34.6 | **`/sys/firmware/efi/efivars/SecureBoot-*` + `/sys/class/tpm/tpm*/` Secure-Boot + TPM context auditor for DKMS / nvidia-installer signing** | S | 4 | Users hit DKMS / proprietary-nvidia rebuild failures after kernel updates *only* when Secure Boot is enabled (modules must be signed with a MOK enrolled key), and Ubuntu 24.04 + Fedora 40 ship Secure Boot on by default on UEFI machines. Today shipped #30.8 reads `/sys/kernel/security/lockdown` but doesn't tell the user *why* lockdown is on (Secure Boot vs `lockdown=integrity` cmdline) and doesn't surface TPM presence (relevant for fTPM stutter bugs that mimic GPU hangs on Ryzen). Read `/sys/firmware/efi/efivars/SecureBoot-*` (raw bytes 0/1), `/sys/firmware/efi/efivars/SetupMode-*`, `/sys/class/tpm/tpm0/{tpm_version_major,vendor,active_pcr_banks}`, surface "Secure Boot ON + no MOK enrolled → next kernel update will leave you without nvidia.ko on boot, here's the `mokutil --import` recipe" + "fTPM 2.0 detected on Ryzen → Windows-known stutter bug, see BIOS update". First : `modules/secureboot_tpm.py` + GET `/api/secureboot-tpm`. |
| 34.7 | **`/sys/block/<nvme>/integrity/*` T10 PI / DIF / DIX data-integrity auditor for enterprise NVMe hosting GGUFs** | XS | 2 | A small slice of homelab owners runs enterprise NVMe (Micron 7450, Samsung PM9A3, Intel D7-P5520) that exposes `/sys/block/<dev>/integrity/{format,read_verify,write_generate,protection_interval_bytes}` — T10 PI / DIF / DIX adds 8-byte CRCs per 512 B sector and silently halves sequential throughput when `write_generate=1` on consumer-grade controllers that emulate it in firmware. Sample is rare (probably 3-5 % of audience) but the *symptom* is "my Micron 7450 reads at 3 GB/s but writes at 800 MB/s" with no other clue — solely a `dmidecode` / sysfs read story. Enumerate per NVMe device, surface "nvme0 has T10 PI enabled (write_generate=1, format=Type1) → halved write throughput on this controller, disable with `nvme format /dev/nvme0n1 -p 0` if you don't need end-to-end CRC". First : `modules/nvme_integrity.py` + GET `/api/nvme-integrity`. |
| 34.8 | **`/sys/devices/system/cpu/cpufreq/policy*/energy_performance_available_preferences` + `energy_performance_preference` HWP EPP string-mode auditor (modern HWP, sibling to bench #32.7 EPB)** | XS | 4 | Bench #32.7 covered the legacy 0-15 `energy_perf_bias` byte; the modern Intel HWP-aware kernel exposes a richer string-mode interface at `policy*/energy_performance_preference` with values like `default` / `performance` / `balance_performance` / `balance_power` / `power`, and `energy_performance_available_preferences` enumerates what the CPU actually supports. Ubuntu Server ships `balance_performance` by default, GNOME desktops ship `balance_power`, and both silently cap llama.cpp prompt-eval single-thread turbo by 200-400 MHz vs `performance`. Distinct from #32.7 because (a) intel_pstate's modern interface preempts EPB on Tiger Lake+ / Sapphire Rapids+ — reading EPB alone gives a wrong answer there, (b) the string form is what `tuned`/`power-profiles-daemon` actually writes. Two reads per policy, surface "all 16 policies at `balance_performance` → losing ~12 % prompt-eval single-thread, `echo performance | sudo tee /sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference` + add to your `tuned-adm profile throughput-performance`". First : `modules/hwp_epp.py` + GET `/api/hwp-epp`. |

**Top 4 (fit × urgency)** :
1. 34.1 transparent_hugepage auditor — XS, fit 5, the lowest-effort + highest-leverage pick of the cycle: three sysfs reads catch the `always`/`always` default that turns into a 500 ms first-token stall on every 30+ day uptime, completes shipped #32.4 vm_sysctl_audit (the swappiness / vfs_cache_pressure tuple) with the THP triplet, and is a deterministic paste-ready GRUB cmdline + sysfs snippet — biggest "explains a recurring weekly complaint" per kB of code in this cycle, and pairs naturally with shipped #32.1 PSI as the *cause* side of memory-stall spikes.
2. 34.3 systemd-oomd correlator — S, fit 5, plugs the gaping hole in the OOM detection story: shipped #6 nvrm_tail catches kernel OOM via Xid, shipped #32.5 cgroup_memcap catches cgroup OOM via memory.events, but `systemd-oomd` (default-on in Ubuntu 22.04+) kills inference daemons *before* either of those triggers and currently produces silent "ollama just died" reports with nowhere to look; one `journalctl --since -u systemd-oomd -o json` parse + correlation with shipped service-discovery completes the OOM-killer triangle and turns a top recurring complaint into a one-line Drop-In fix.
3. 34.4 `/proc/<pid>/sched` per-thread scheduler accounting — S, fit 5, the missing high-resolution counterpart to shipped #32.1 PSI (aggregate stall %) + #31.3 cpu_topology (pinning advice) — `nr_involuntary_switches` + `wait_sum` per worker thread is the only kernel-native way to prove "another task preempted my inference thread 240×/s and stole 380 ms of CPU" without `perf sched` or BCC; turns vague "tokens/s dipped" complaints into a specific tid + cpu + contention metric that maps directly to the `taskset`/cgroup-weight remediation already shipped (#31.3 + #33.6).
4. 34.2 `/proc/buddyinfo` + `/proc/zoneinfo` fragmentation gauge — S, fit 5, the missing "why did my fresh mmap fail despite `free -h` showing GiB available?" answer that no other tool in the homelab audience's belt surfaces; Mel-Gorman fragmentation index per zone + order-9/order-10 free-block counts directly explains the "30-day uptime → GGUF reload fails / runs slower" pattern, complements shipped #34.1 THP (which can't help if there are no order-9 blocks to coalesce) and #32.4 vm_sysctl (`compact_memory` / `vm.min_free_kbytes` remediation is one click away).

Bench (34.5 timerslack_ns audit / 34.6 Secure-Boot + TPM context / 34.7 NVMe T10 PI integrity / 34.8 HWP EPP string-mode auditor) for cycles after top 4 lands.

---

## R&D #33 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-32), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8 + 29.2/29.4/29.5/29.6 + 30.4/30.6/30.7/30.8 + 31.5/31.6/31.7/31.8 + 32.2/32.6/32.7/32.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 33.1 | **`/sys/class/net/<dev>/{statistics,carrier,speed,duplex}` LAN-served ollama / OpenWebUI link-health correlator** | S | 5 | Homelab rigs increasingly serve ollama / open-webui over the LAN to phones, laptops, and Home Assistant. A flapping NIC (loose Cat5e, autoneg fell back to 100 Mb half-duplex, switch port renegotiating) silently caps streaming token responses at 12 MB/s and turns "ollama feels slow tonight" into hours of GPU-side debugging when the real culprit is `eth0 speed=100`. Walk `/sys/class/net/` for non-loopback ifaces, read `statistics/{rx_bytes,tx_bytes,rx_errors,tx_errors,rx_dropped,tx_dropped,carrier_changes}` + `carrier` + `speed` + `duplex` + `operstate`, delta-sample at 1 Hz, correlate `carrier_changes` / `rx_errors` deltas with shipped service-discovery inference windows, surface "eth0 negotiated 100 Mb half-duplex + 14 carrier changes during your last 5 ollama API calls — your token stream is NIC-throttled, not GPU-throttled, check the cable and switch port". First : migration `nic_sample` + `modules/nic_health.py` + GET `/api/nic-health`. |
| 33.2 | **`/proc/<pid>/io` read_bytes / write_bytes per-inference-daemon I/O accounting (pulled from bench #30.4)** | S | 5 | The bench #30.4 entry has aged into a top-tier need now that #31.2 smaps_rollup (resident bytes) and #32.1 PSI memory-pressure (waiting-on-RAM) have shipped — `/proc/<pid>/io` is the missing *cumulative throughput* leg of the triangle. read_bytes/write_bytes/rchar/wchar tell the user "your llama-server reread 320 GiB of GGUF this week" (page-cache evicted, cold reload tax) or "ComfyUI wrote 80 GiB of intermediate tensors to /tmp" (tmpfs vs ssd matters); paired with shipped #31.2 it tells the *full* "GGUF lives in cache (smaps_rollup Shared_Clean) and stays there (proc/io read_bytes flat across sessions)" story or the failure mode (Shared_Clean=2 GiB + read_bytes climbing every generation → eviction). Delta-sample per inference PID (via shipped service discovery) at sampler tick, persist, surface side-by-side with smaps_rollup. First : migration `proc_io_sample` + `modules/proc_io_accounting.py` + GET `/api/proc-io`. |
| 33.3 | **`/sys/devices/system/node/node*/{meminfo,cpulist,distance}` NUMA placement auditor for GGUF mmap on dual-socket / Threadripper rigs** | S | 4 | A small but loud slice of homelab owners runs Threadripper / EPYC / dual-Xeon with 2-8 NUMA nodes; llama-server mmap'd GGUF lands wherever the first-touching thread runs, and on a 2-node box that's typically the node *opposite* the GPU's PCIe root complex — every host→device copy now traverses the QPI/Infinity-Fabric link, costing ~15-25 % prompt-eval throughput silently. Today nobody surfaces it. Enumerate `/sys/devices/system/node/node*/meminfo` (MemFree / MemUsed / FilePages per node), `cpulist`, `distance`, cross-check `/sys/bus/pci/devices/<gpu>/numa_node`, surface "GPU is on NUMA node 1 but 90 % of your GGUF (24 GiB) is mmap'd on node 0 → cross-socket traffic, run `numactl --cpunodebind=1 --membind=1 llama-server …` for +18 % t/s" + emit ready-to-paste numactl snippet. First : `modules/numa_placement.py` + GET `/api/numa`. |
| 33.4 | **`/sys/devices/system/clocksource/clocksource0/{current_clocksource,available_clocksource}` TSC-vs-HPET sanity for nsec-resolution metric collection (pulled from bench #32.2)** | XS | 5 | The dashboard itself uses `time.monotonic_ns()` for token-timing graphs, sampler ticks, and PSI delta intervals — and on the silent minority of hosts that fell back to `hpet` (older Xeon, BIOS that disabled `tsc=reliable`, kernel watchdog fired and demoted TSC) those reads cost ~10× more (1-2 µs vs 100 ns), which shows up as jittery ttft graphs *and* steals sampler-loop CPU. Promoting from bench because shipped #31.3 cpu_topology + #32.1 PSI both rely on tight sampling intervals that HPET silently undermines. One read of `current_clocksource` + `available_clocksource` + parse `dmesg | grep -iE 'clocksource|tsc'` for the smoking gun ("Clocksource tsc unstable (delta = …)"), surface "current=hpet (TSC marked unstable by kernel watchdog at boot+47s) → your dashboard's own timing graphs jitter ±2 ms from the timer, not the GPU; investigate BIOS power-mgmt / `processor.max_cstate=1` cmdline". First : `modules/clocksource_audit.py` + GET `/api/clocksource`. |
| 33.5 | **`/sys/class/dmi/id/chassis_type` laptop-vs-desktop classifier driving battery-aware tuning split (pulled from bench #31.5, generalised)** | XS | 4 | DMI chassis_type (3=Desktop, 9=Laptop, 10=Notebook, 14=SubNotebook, 31=Convertible, 36=Tablet, 23=RackMount, 17=Server) is the single byte that tells the dashboard "this user is on battery sometimes" vs "this is a rack server, ignore battery cards entirely". Today every UI card shows the same content regardless; battery-aware GPU clamp (bench #31.5) was deferred because we had no clean way to *gate* it — chassis_type is that gate. One read of one file, classify into 4 categories (desktop / laptop / server / unknown), feed shipped Settings → Integrations to auto-hide battery+laptop cards on rack servers and auto-show them on laptops, surface "chassis_type=10 (Notebook) detected → enabling laptop tuning split (battery clamp + lid-close suspend guard + AC-only OC profiles)". First : `modules/chassis_class.py` + GET `/api/chassis-class`. |
| 33.6 | **`/sys/fs/cgroup/**/cpu.weight` + `io.weight` + `cpu.max` + `io.max` cgroup-v2 CPU/IO weight scanner (sibling to shipped #32.5 memcap)** | S | 5 | Shipped #32.5 cgroup_memcap caught the memory cap silent-kill, but the *same* distro-packaged systemd units that ship `MemoryMax=8G` also ship `CPUWeight=50` (half the default) and `IOWeight=50`, throttling llama-server's prompt-processing during any CPU contention and starving GGUF loads behind background `apt-daily.timer` / `tracker-miner` I/O — symptoms are "tokens/s halves at 06:00 every morning" or "first-token latency randomly doubles" with no visible cause in shipped #32.1 PSI (PSI shows "yes you're stalled" but not "because cgroup weight is 50"). Walk `/sys/fs/cgroup/` for cgroups owning shipped service-discovery PIDs, read `cpu.weight` / `io.weight` / `cpu.max` / `io.max` + `cpu.stat` (throttled_usec), surface "ollama.service cgroup has CPUWeight=50 + IOWeight=50 + 14.2 s throttled this hour → distro packaging foot-gun, raise to 200 via Drop-In". First : `modules/cgroup_cpuio.py` + GET `/api/cgroup-cpuio`. |
| 33.7 | **`/etc/security/limits.conf` + `/etc/security/limits.d/*.conf` + `/etc/systemd/system.conf` LimitMEMLOCK static-config auditor (system-wide baseline before per-service overrides)** | XS | 4 | Shipped #29.8 (rlimit_audit) reads the *runtime* `prlimit` of each inference PID and #32.5 cgroup_memcap reads the cgroup cap, but neither tells the user *where* the limit came from before a systemd unit's `LimitMEMLOCK=` override — the system-wide baseline lives in `/etc/security/limits.conf` (for PAM-launched processes) + `/etc/security/limits.d/*.conf` + `/etc/systemd/system.conf` (`DefaultLimitMEMLOCK=`). When a user adds `--mlock` to a brand-new tool (vllm, mlx-lm, koboldcpp) launched outside an existing systemd unit and it fails with "cannot allocate memory", the answer is "your distro ships DefaultLimitMEMLOCK=64K, fix at the system layer not per-service". Parse three glob trees with a tiny PAM-format reader, cross-check against shipped #29.8's runtime view, surface "system-wide DefaultLimitMEMLOCK=64K (Ubuntu 24.04 default) → any new mlock-using tool will fail until you add a systemd Drop-In or edit /etc/systemd/system.conf". First : `modules/limits_static.py` + GET `/api/limits-static`. |
| 33.8 | **`/sys/devices/system/cpu/smt/{control,active}` + `/sys/devices/system/cpu/cpu*/online` SMT toggle + offline-core audit** | XS | 3 | Shipped #31.3 cpu_topology emits the `taskset -c 0,2,4,…` snippet to avoid SMT siblings, but the actual *runtime* SMT state (was SMT disabled in BIOS? was a core hot-unplugged by a thermal event?) lives in `/sys/devices/system/cpu/smt/control` (`on`/`off`/`forceoff`/`notsupported`) + `/sys/devices/system/cpu/smt/active` + per-cpu `online`. When taskset advice doesn't match reality ("you told me to pin to cpu 14 but it's offline"), the user is confused; also catches the rare "Spectre L1TF mitigation hot-unplugged half my cores" Skylake edge case. Three globs + two reads, surface "SMT control=off (BIOS or mitigation), only 8 logical CPUs online — recomputed taskset advice attached" or "cpu14 offline (thermal HW unplug at boot+3h) — investigate `dmesg | grep -i 'cpu14'`". First : `modules/smt_audit.py` + GET `/api/smt-audit`. |

**Top 4 (fit × urgency)** :
1. 33.1 LAN NIC health correlator — S, fit 5, the missing "ollama feels slow but the GPU is idle" diagnosis layer that completes shipped #32.1 PSI (memory-stall) + #32.5 cgroup_memcap (mem-cap) + #31.3 cpu_topology (CPU contention) into a 4-source root-cause matrix; one `/sys/class/net/<dev>` walk catches the autoneg-fallback / flapping-cable foot-gun that costs LAN-served homelab rigs ~88 % of their available bandwidth silently, and bucketing carrier_changes per inference window turns "ollama is slow" into a one-line verdict — biggest user-visible win of the cycle, very high audience fit (every ollama-host user serves the LAN).
2. 33.2 `/proc/<pid>/io` accounting — S, fit 5, promoting from bench because shipped #31.2 smaps_rollup (resident) + #32.1 PSI (waiting) make the cumulative-throughput leg now an obvious gap; reveals page-cache eviction (read_bytes climbing per session) and tmpfs-vs-disk write hot-spots for ComfyUI / SD that nothing else in the shipped stack covers; pairs with #31.2 for the definitive "is my GGUF actually resident across sessions?" answer.
3. 33.4 clocksource TSC-vs-HPET audit — XS, fit 5, promoting from bench because shipped #32.1 PSI + #31.3 cpu_topology + sampler-loop tightness all silently degrade when the host fell back to HPET; one file read + one dmesg grep catches the rare-but-confusing "my token-timing graphs are jittery and so is the dashboard's own sampler" failure that costs literally zero CPU to detect — lowest-effort top-4 pick and the highest "fix the dashboard's own measurements" leverage.
4. 33.6 cgroup-v2 CPU/IO weight scanner — S, fit 5, the natural sibling to shipped #32.5 memcap that completes the cgroup-v2 audit triangle (memory + cpu + io); the "tokens/s halves at 06:00" + "first-token latency randomly doubles" complaints map directly to distro-packaged `CPUWeight=50` / `IOWeight=50` foot-guns plus `cpu.stat`'s `throttled_usec` for hard evidence — turns yet another invisible silent-kill into a Drop-In paste.

Bench (33.3 NUMA placement auditor / 33.5 chassis_type classifier / 33.7 LimitMEMLOCK static-config auditor / 33.8 SMT toggle + offline-core audit) for cycles after top 4 lands.

---

## R&D #32 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-31), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8 + 29.2/29.4/29.5/29.6 + 30.4/30.6/30.7/30.8 + 31.5/31.6/31.7/31.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 32.1 | **`/proc/pressure/{cpu,memory,io}` PSI pressure-stall correlator per inference window** | S | 5 | Linux 4.20+ exposes Pressure Stall Information — `some` / `full` time-fractions on `cpu`, `memory`, `io` — that quantify "is the workload waiting on a resource right now?". Today shipped #28.5 thermal-zone + #29.8 rlimit show *symptoms*, but PSI is the only kernel-native source of truth for "your llama-server prompt-processing was stalled 18 % of the last 60 s on memory.full → you're swapping despite mlock". Sample three files at 1 Hz, bucket per shipped service-discovery inference PID's active window, surface "PSI memory full=18 % during your last generation → cgroup or host RAM pressure, not GPU-bound". First : migration `psi_sample` + `modules/psi_pressure.py` + GET `/api/psi`. |
| 32.2 | **`/sys/devices/system/clocksource/clocksource0/current_clocksource` + `available_clocksource` TSC-vs-HPET sanity for nanosec timing** | XS | 4 | llama.cpp / ggml-cuda use `clock_gettime(CLOCK_MONOTONIC)` for token timing + nvidia-cuda-mps event records depend on a coherent TSC; a small but irritating subset of hosts (older Xeon E5, BIOS that disabled `tsc=reliable`, kernel that fell back to `hpet` after a clock-watchdog event) silently run on HPET — ~10× the read latency of TSC and the source of "ttft graph looks jittery for no reason" reports. One read of `current_clocksource` + `available_clocksource` + parse `dmesg | grep 'Clocksource'`, surface "current=hpet (TSC marked unstable by kernel watchdog) → your token-timing graphs jitter ±2 ms from the timer itself, not the GPU". First : `modules/clocksource.py` + GET `/api/clocksource`. |
| 32.3 | **`/proc/<pid>/wchan` + `/proc/<pid>/stack` "inference stuck where?" live debugger** | S | 5 | When llama-server / ComfyUI freezes mid-token, the user has no way to tell "is it in `nv_dma_map_pages` (driver bug), `__schedule` (CPU stall), `do_page_fault` (mlock failed → swapping), or `wait_for_completion` (CUDA stream hang)?" without attaching gdb. `/proc/<pid>/wchan` exposes the current kernel function the thread is sleeping on (one short string), `/proc/<pid>/stack` the full kernel stack (root-only but readable inside the dashboard's already-privileged service). Sample per inference PID + per worker thread at 1 Hz, classify into 6 known stall categories, surface "thread 3 stuck in `nv_dma_map_pages` for 8 s — DMA exhaustion, increase `NVreg_RmGuardModeSize`" or "stuck in `__schedule`, CPU starved — see PSI". First : `modules/proc_wchan.py` + GET `/api/proc-wchan` (SSE). |
| 32.4 | **`/proc/sys/vm/{swappiness,vfs_cache_pressure,overcommit_memory,overcommit_ratio,min_free_kbytes,zone_reclaim_mode}` LLM-rig VM-sysctl sanity auditor** | XS | 5 | Ubuntu defaults `swappiness=60` (aggressive swap), `vfs_cache_pressure=100` (evict GGUF page-cache as fast as anon RAM), `overcommit_memory=0` (heuristic — can refuse a legitimate llama-server mmap on tight hosts); the LLM-friendly recipe is `swappiness=1` (or 10 with zram), `vfs_cache_pressure=50` (keep GGUFs cached), `overcommit_memory=1` (let mmap succeed). Six reads, diff against an "LLM-rig baseline" JSON, surface "swappiness=60 + vfs_cache_pressure=100 → your GGUF gets paged out the second Firefox touches RAM, change to 10/50" + emit a `sysctl.d/99-llm-rig.conf` snippet. First : `modules/vm_sysctl_audit.py` + GET `/api/vm-sysctl`. |
| 32.5 | **`/sys/fs/cgroup/**/memory.{high,max,current,swap.max}` cgroup-v2 memory-cap scanner for inference daemons** | S | 4 | Distro-packaged systemd units for ollama / lm-studio / open-webui increasingly land with `MemoryHigh=` or `MemoryMax=` set conservatively (8 GiB) — the daemon then silently *throttles* (memory.high) or gets *killed* (memory.max) when a 14 B model crosses the cap, and the user sees "ollama crashed" with no clue why. Walk `/sys/fs/cgroup/` for cgroups owning shipped service-discovery PIDs, read `memory.high/max/current/swap.max` + `memory.events` (low/high/oom_kill counters), surface "ollama.service has MemoryMax=8 GiB but loaded a 9.4 GiB model → 3 oom_kill events this week, raise the cap in the unit". First : `modules/cgroup_memcap.py` + GET `/api/cgroup-memcap`. |
| 32.6 | **`/etc/modprobe.d/*.conf` + `/usr/lib/modprobe.d/*.conf` cross-checker (parsed-config vs runtime `/sys/module/nvidia/parameters/*`)** | S | 4 | Shipped #29.1 (kmod_params) reads the *runtime* parameter values, but doesn't tell the user *where* a value came from — was `NVreg_PreserveVideoMemoryAllocations=1` set by `/etc/modprobe.d/nvidia.conf`, baked into the initramfs, or just the driver's compiled-in default? When a fix doesn't stick after reboot (initramfs not regenerated), users have no way to tell. Glob both modprobe.d trees, parse `options nvidia NVreg_X=Y`, cross-check against shipped #29.1 runtime values, surface "modprobe.d says NVreg_EnableMSI=1 but runtime is 0 → initramfs out of date, run `update-initramfs -u`". First : `modules/modprobe_xcheck.py` + GET `/api/modprobe-xcheck`. |
| 32.7 | **`/sys/devices/system/cpu/cpu*/power/energy_perf_bias` + `/sys/devices/system/cpu/intel_pstate/{no_turbo,hwp_dynamic_boost}` HWP energy-vs-perf bias auditor** | XS | 4 | Intel HWP exposes `energy_perf_bias` (0=performance, 15=powersave; Ubuntu Server defaults to 6-8 = "balance_performance", desktops often 11 = "balance_power") that silently caps single-thread turbo by 200-400 MHz during llama.cpp prompt-eval — same symptom as a powersave governor but invisible to `cpupower frequency-info`. Read EPB per CPU, intel_pstate's `no_turbo` + `hwp_dynamic_boost`, surface "EPB=11 across all cores → HWP biased toward power-save, llama.cpp prompt-eval losing ~12 % single-thread perf, set EPB=0 via `x86_energy_perf_policy performance`". First : `modules/cpu_epb.py` + GET `/api/cpu-epb`. |
| 32.8 | **`/proc/loadavg` + `/proc/stat` `procs_running` / `procs_blocked` contention monitor** | XS | 3 | loadavg + `procs_running` from `/proc/stat` give a free-with-no-extra-deps "is anything else fighting llama-server for cores right now?" gauge — when `procs_running` jumps from 3 to 17 mid-inference (a cron-launched `updatedb` / `tracker-miner-fs` / nightly snap refresh), the user sees a tokens/s dip and blames the GPU. Sample at 1 Hz, correlate spikes with shipped service-discovery inference windows, surface "tokens/s dropped 22 % at 03:14 — `procs_running` spiked to 14, likely `updatedb` ran (mlocate.timer fires at 03:13)". First : migration `loadavg_sample` + `modules/loadavg_contention.py` + GET `/api/loadavg`. |

**Top 4 (fit × urgency)** :
1. 32.1 PSI pressure-stall correlator — S, fit 5, the missing kernel-native "why is my inference stalled" signal that #29.8 rlimit and #28.5 thermal-zone only *hint* at; three `/proc/pressure/*` reads at 1 Hz reveal whether the last token burst was memory-stalled, CPU-stalled, or I/O-stalled with hard percentage numbers, plugging straight into shipped service discovery to bucket PSI per inference window — biggest "actually answers the recurring slow-token mystery" signal-to-effort ratio of the cycle.
2. 32.3 `/proc/<pid>/wchan` + `stack` inference-stuck debugger — S, fit 5, fills the gaping "llama-server froze, what is it waiting on?" diagnostic gap that today forces users into gdb; one-file kernel-function string + stack classification turns "stuck" into a 6-category verdict (DMA exhaustion / CPU stall / page fault / CUDA wait / lock / driver retry), pairs with #32.1 PSI (waiting-on-what + how-long) for the full picture.
3. 32.4 VM sysctl LLM-rig sanity auditor — XS, fit 5, six trivial reads catch the Ubuntu-default `swappiness=60` + `vfs_cache_pressure=100` foot-gun that systematically evicts mmap'd GGUFs the moment any other app touches RAM (the disk-cache counterpart to shipped #29.8 mlock auditor); emits a paste-ready `sysctl.d/99-llm-rig.conf` snippet, lowest effort of the four for one of the most visible "cold reload tax" wins.
4. 32.5 cgroup-v2 memory-cap scanner — S, fit 4, surgical solution to the rising "distro packaged ollama with MemoryMax=8 GiB and it crashes on a 14 B model" complaint that today shows up as a mysterious crash with no log; walks the unified hierarchy for shipped service-discovery PIDs, reads `memory.events` for hard counts of low/high/oom_kill, and turns "ollama crashed" into "your unit's MemoryMax killed it, here's the override".

Bench (32.2 clocksource TSC/HPET / 32.6 modprobe.d cross-checker / 32.7 HWP EPB auditor / 32.8 loadavg contention) for cycles after top 4 lands.

---

## R&D #31 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-30), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8 + 29.2/29.4/29.5/29.6 + 30.4/30.6/30.7/30.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 31.1 | **`/sys/class/hwmon/hwmon*/` NVMe + chipset hwmon parity (temps + fans outside `/sys/class/thermal`)** | S | 4 | Shipped #28.5 thermal-zone correlator catches `/sys/class/thermal/thermal_zone*` but the *richer* hwmon tree (nvme composite + sensor1/sensor2 per-die, chipset `it87`/`nct6798` fan tachs, `k10temp`/`coretemp`) lives elsewhere and is the only place chassis fan RPMs are exposed on most boards. Enumerate `/sys/class/hwmon/hwmon*/name` + `tempN_input` + `fanN_input` + `pwmN`, cross-correlate with shipped GPU-throttle and thermal-zone events, surface "chassis fan2 stuck at 0 RPM during GPU 81 °C throttle — bearing dead or PWM curve clipped". First : `modules/hwmon_inventory.py` + GET `/api/hwmon`. |
| 31.2 | **`/proc/<pid>/smaps_rollup` inference-daemon residence breakdown (Anonymous vs File-backed vs Shared)** | S | 5 | Shipped #30.4 (parked) reads `/proc/<pid>/io` byte counters but the *resident* picture lives in `smaps_rollup`: `Anonymous` (KV cache + activations), `Shared_Clean` (mmap'd GGUF still page-cached), `Pss` (proportional share when 2× llama-server share weights). Single read per inference PID (via shipped service discovery), surface "llama-server has 18 GiB Anonymous + only 2 GiB Shared_Clean → your GGUF was evicted from page cache, cold reload next session". First : `modules/proc_smaps.py` + GET `/api/proc-smaps`. |
| 31.3 | **`/sys/devices/system/cpu/cpu*/topology` + `cpufreq/scaling_governor` inference-pinning advisor** | S | 5 | llama-server prompt-processing uses `OMP_NUM_THREADS` but defaults to *all* cores including SMT siblings — on 7950X / 13900K this halves throughput because two threads contend on the same physical core's FP unit. Read `core_id` + `thread_siblings_list` + `physical_package_id` per cpu, classify P/E cores (Intel hybrid via `cpu_capacity`), check `scaling_governor` is `performance` not `powersave`, surface "16 logical CPUs but only 8 physical cores + 4 E-cores → pin llama-server to `taskset -c 0,2,4,6,8,10,12,14` for +35 % t/s, governor is powersave → switch to performance". First : `modules/cpu_topology.py` + GET `/api/cpu-topology`. |
| 31.4 | **`/proc/<pid>/oom_score` + `oom_score_adj` inference-priority OOM hardening** | XS | 5 | When host RAM is tight (32 GiB box loading a 24 GiB GGUF + Firefox + KDE), the OOM killer's heuristic targets the biggest RSS — which is *always* llama-server. Users lose 10 minutes of warm KV cache to a Firefox tab that ballooned. Read `/proc/<pid>/oom_score` + `oom_score_adj` for known inference daemons, suggest `OOMScoreAdjust=-500` Drop-In so the OOM killer eats the *browser* not the model. First : `modules/oom_priority.py` + GET `/api/oom-priority`. |
| 31.5 | **`/sys/class/power_supply/BAT*/` + `AC/online` laptop battery-aware GPU clamp** | XS | 3 | Laptop/eGPU rigs (15 % of audience post-Optimus/USB4) silently chew through battery when the user forgets to unplug — eGPU running 250 W draining a 90 Wh battery in 20 min. Read AC online + battery capacity + cycle_count + status, surface "on battery + GPU at 250 W → 22 min runtime, consider `nvidia-smi -pl 120` while unplugged" + offer auto-clamp toggle in Settings. First : `modules/power_supply.py` + GET `/api/power-supply`. |
| 31.6 | **`/sys/devices/system/edac/mc/mc*/` EDAC controller for system-RAM ECC (parity vs NVIDIA-reported)** | S | 3 | Shipped #6 + #29.4 cover GPU ECC, but llama-server with `--mlock` resident in 64 GiB ECC system RAM is equally exposed; EDAC reports `ce_count` (correctable) + `ue_count` (uncorrectable) per memory controller / DIMM rank. Enumerate `/sys/devices/system/edac/mc/mc*/ce_count` + `ue_count` + `dimmN_label`, delta over reboots, surface "DIMM A1 +47 correctable ECC errors this week — replace before uncorrectable". First : `modules/edac_audit.py` + GET `/api/edac`. |
| 31.7 | **`/sys/class/net/<dev>/{statistics,carrier,speed}` remote-inference NIC health (ollama serving over LAN)** | S | 3 | Homelab ollama hosts serve API to other LAN clients; a flapping NIC or 100 Mb auto-negotiation fallback (loose Cat5e) silently caps the streaming responses at ~12 MB/s instead of the line-rate 1 Gb. Read `statistics/{rx_errors,tx_errors,rx_dropped,carrier_changes}` + `carrier` + `speed` per non-loopback iface, surface "eth0 negotiated 100 Mb (cable issue?) + 14 carrier changes in last hour — your ollama API is rate-limited by the NIC". First : `modules/net_health.py` + GET `/api/net-health`. |
| 31.8 | **`/proc/sys/kernel/{perf_event_paranoid,perf_event_mlock_kb}` profiler-friendliness auditor (nsys / ncu / perf)** | XS | 3 | Users who want to run Nsight Systems / `ncu` / `perf top` on their llama-server hit cryptic permission failures because Ubuntu ships `kernel.perf_event_paranoid=4` (most restrictive) since 22.04 and `perf_event_mlock_kb=516` (too small for nsys traces). One read of both sysctls, surface "perf_event_paranoid=4 → nsys can't sample hardware counters, set to 1 for profiling" + provide a `sysctl.d/99-nsys.conf` snippet. First : `modules/perf_paranoid.py` + GET `/api/perf-paranoid`. |

**Top 4 (fit × urgency)** :
1. 31.3 CPU topology + governor inference-pinning advisor — S, fit 5, the silent ~35 % prompt-processing penalty from SMT-sibling contention + `powersave` governor is the single biggest "easy win the user never knew about" left; reads `core_id`/`thread_siblings_list` + `scaling_governor`, plugs straight into shipped service discovery to emit a ready-to-paste `taskset` snippet alongside a `cpupower frequency-set -g performance` line — biggest tokens/s lift per kB of code in this cycle.
2. 31.2 `/proc/<pid>/smaps_rollup` inference-daemon residence breakdown — S, fit 5, the missing *resident* counterpart to parked #30.4 byte-counter view; answers the recurring "I loaded a 24 GiB GGUF, why does free -h show 4 GiB cached?" mystery with `Shared_Clean` vs `Anonymous` decomposition, directly explains cold-reload tax and feeds back into #30.3 NVMe scheduler / #29.8 mlock / #28.5 thermal-zone advice — high synergy across the shipped stack.
3. 31.4 OOM-priority hardening for inference daemons — XS, fit 5, two-file read but solves the specific "Firefox tab killed my warm llama-server" pain point that every 32 GiB rig hits; trivial `OOMScoreAdjust=-500` Drop-In snippet, complements shipped #29.8 rlimit auditor + #30.4 io accounting (parked) into a full "make systemd treat your model like a first-class citizen" Diagnostics card, lowest effort of the four.
4. 31.1 hwmon NVMe + chipset parity — S, fit 4, fills the obvious gap left by shipped #28.5 thermal-zone correlator (chassis fan RPMs + NVMe per-die temps live in `/sys/class/hwmon`, not `/sys/class/thermal`); reveals the "fan2 at 0 RPM while GPU throttles" failure mode that the thermal-zone view simply cannot see, and is the natural extension reviewers will ask for once #28.5 lands.

Bench (31.5 battery-aware GPU clamp / 31.6 EDAC system-RAM ECC / 31.7 NIC health for remote inference / 31.8 perf_event_paranoid auditor) for cycles after top 4 lands.

---

## R&D #30 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-29), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8 + 29.2/29.4/29.5/29.6). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 30.1 | **`/sys/bus/pci/devices/<gpu>/msi_irqs/` MSI-X vector inventory + per-vector hit-rate** | S | 4 | Nvidia GPUs allocate 1-64 MSI-X vectors (varies by driver version + module params from #29.1) but most boards end up with only the legacy single MSI line because `pci=nomsi` is set by Anaconda installers or the firmware blocks MSI-X for the bridge — a silent ~10 % latency tax on CUDA host→device copies that nobody surfaces. Enumerate `/sys/bus/pci/devices/<gpu>/msi_irqs/<n>`, cross-check `/proc/interrupts` for each vector, surface "your GPU uses 1 MSI line, not MSI-X — check `pci=nomsi` in cmdline + `NVreg_EnableMSI=1`". First : `modules/msi_inventory.py` + GET `/api/msi-inventory`. |
| 30.2 | **`/sys/class/iommu/dmar*/devices/<bdf>` IOMMU group + DMA-passthrough auditor** | S | 4 | Users wanting to pass the GPU to a VM (vfio-pci) often discover too late that their GPU shares an IOMMU group with the chipset USB / SATA / audio — meaning the whole group must be passed (or `pcie_acs_override` patched). We already ship VFIO sentinel (#vfio_sentinel) for the post-passthrough state, but no pre-flight "can this GPU even be passed cleanly?". Walk `/sys/kernel/iommu_groups/*/devices/` to find the GPU's group + siblings, surface "GPU is in IOMMU group 14 with onboard USB controller — clean passthrough requires `pcie_acs_override=downstream,multifunction` or a riser". First : `modules/iommu_group.py` + GET `/api/iommu-group`. |
| 30.3 | **`/sys/block/<nvme>/queue/scheduler` + `nr_requests` + `read_ahead_kb` GGUF mmap I/O tuner** | XS | 4 | A 32 GiB GGUF mmap-loaded from NVMe spends 5-15 s warming the page cache; `none` (no-op) scheduler + `read_ahead_kb=4096` cuts that in half on modern NVMe, but Ubuntu ships `mq-deadline` + `read_ahead_kb=128` by default. Three sysfs reads per NVMe device hosting `~/.cache/llama.cpp` or `~/.cache/huggingface`, classify against an LLM-mmap-friendly baseline, surface "your NVMe runs mq-deadline + 128 KiB readahead — switch to none + 4096 KiB to halve cold-load time". First : `modules/nvme_iosched.py` + GET `/api/nvme-iosched`. |
| 30.4 | **`/proc/<llama-pid>/io` cumulative read_bytes / write_bytes per-inference-daemon I/O accounting** | S | 3 | `/proc/<pid>/io` exposes per-process read_bytes, write_bytes, rchar, wchar that today nobody plots — but for llama-server / ComfyUI it tells you exactly how much your GGUF / SD-checkpoint loader is rereading vs hitting page cache. Delta-sample per inference PID (from shipped service discovery) at sampler tick, persist, surface "llama-server read 320 GiB this week — your GGUF is being kicked out of page cache, add 32 GiB to host RAM". First : migration `proc_io_sample` + `modules/proc_io_accounting.py` + GET `/api/proc-io`. |
| 30.5 | **`/sys/devices/virtual/dmi/id/{bios_version,bios_date,board_name}` motherboard / BIOS revision tracker for ReBAR + Above-4G + AER unlock** | XS | 4 | Shipped #27.1 ReBAR auditor and #18.x AER counter both flag bad configs, but the *fix* for either is "update BIOS to vX.Y" — without surfacing current BIOS version + release date the user can't tell if they're already on the fix. One read of three DMI files, cross-reference against a small JSON catalog of "known-good BIOS for ReBAR/AER on this board", surface "BIOS F11 (2023-04) — F16 (2024-09) enables ReBAR on your X570 board, here's the vendor link". First : `modules/dmi_bios.py` + GET `/api/dmi-bios`. |
| 30.6 | **`nvidia-smi --query-gpu=compute_mode,compute_cap` compute-mode + capability mismatch warner** | XS | 3 | `compute_mode` (DEFAULT / EXCLUSIVE_PROCESS / EXCLUSIVE_THREAD / PROHIBITED) is set per-card and silently breaks multi-tenant inference if left on EXCLUSIVE_PROCESS after a CUDA SDK install; `compute_cap` (sm_86 etc.) mismatches with the model's `--gpu-arch` flag and falls back to PTX JIT (warm-up tax). Two query reads per GPU, surface "compute_mode=EXCLUSIVE_PROCESS → only one CUDA context allowed, your second llama-server crashes silently" + "sm_86 GPU running sm_75-compiled model → PTX JIT recompile each launch". First : `modules/compute_mode.py` + GET `/api/compute-mode`. |
| 30.7 | **`/sys/class/drm/card*/device/gpu_busy_percent` Nouveau cross-check + driver-flavor sanity (parity probe vs nvidia-smi)** | S | 2 | Some hosts dual-load nouveau alongside the proprietary driver (kernel param leftover, kept by DKMS); `gpu_busy_percent` from nouveau then runs in parallel and confuses anyone parsing `/sys/class/drm`. Detect that nouveau is loaded *alongside* `nvidia`, compare `gpu_busy_percent` to `nvidia-smi --query-gpu=utilization.gpu` over a 30 s sample, surface "nouveau + nvidia co-loaded → `modprobe.d/blacklist-nouveau.conf` missing, you risk a hybrid OOPS on resume". First : `modules/nouveau_collision.py` + GET `/api/nouveau-collision`. |
| 30.8 | **`/sys/kernel/security/lockdown` + `/proc/sys/kernel/{kptr_restrict,dmesg_restrict}` driver-debug-friendliness auditor** | XS | 3 | Secure Boot enables kernel lockdown=integrity which silently blocks `nvidia-smi -q -d INFOROM`, blocks `nvidia-bug-report.sh` from dumping kernel state, and hides dmesg lines we tail in #28.7 — symptom is "my bug report is empty, why?". Read those three files, classify lockdown state (none/integrity/confidentiality), surface "lockdown=integrity + Secure Boot → bug-report bundle will miss kernel symbols, here's how to read-only-relax for diagnosis". First : `modules/lockdown_audit.py` + GET `/api/lockdown-audit`. |

**Top 4 (fit × urgency)** :
1. 30.2 IOMMU group + DMA-passthrough auditor — S, fit 4, fills the obvious VM-passthrough pre-flight gap (shipped vfio_sentinel only handles post-passthrough); single `/sys/kernel/iommu_groups/` walk reveals "your GPU is in a group with onboard USB" before the user wastes a Saturday on libvirt configs, and the `pcie_acs_override` snippet is a deterministic fix.
2. 30.3 NVMe I/O scheduler + readahead tuner for GGUF mmap — XS, fit 4, three sysfs reads catch the Ubuntu-default mq-deadline + 128 KiB readahead foot-gun that doubles cold-load time on 32 GiB GGUFs; complements shipped #29.8 rlimit auditor + warmup profiler with the *disk-side* warm-up story, lowest-effort win of the four.
3. 30.5 DMI / BIOS revision tracker — XS, fit 4, the missing "what BIOS am I on?" companion that turns #27.1 ReBAR auditor and #18.x AER advisor from "your BIOS is bad" into "your BIOS is F11, update to F16 — here's the link"; trivial read of 3 DMI files, instantly actionable for the audience that already enabled ReBAR/AER suggestions.
4. 30.1 MSI-X vector inventory — S, fit 4, surgical follow-on to #28.2 GPU-IRQ bench item that we keep deferring; one `/sys/bus/pci/devices/<gpu>/msi_irqs/` enumeration reveals the silent "you got 1 legacy IRQ, not 16 MSI-X" tax that costs ~10 % CUDA copy latency on hosts with `pci=nomsi` or restrictive firmware, and ties directly into #29.1 NVreg_EnableMSI advice.

Bench (30.4 proc/io accounting / 30.6 compute_mode warner / 30.7 nouveau collision / 30.8 lockdown audit) for cycles after top 4 lands.

---

## R&D #29 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-28), modules tree, or parked bench (27.2/27.5/27.6/27.8 + 28.2/28.3/28.6/28.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 29.1 | **`/sys/module/nvidia/parameters/*` kernel-module parameter auditor** | XS | 5 | The nvidia.ko module exposes ~40 tunables under `/sys/module/nvidia/parameters/` (NVreg_EnableMSI, NVreg_UsePageAttributeTable, NVreg_RegistryDwords, NVreg_PreserveVideoMemoryAllocations…) — most distros default several of these in ways that hurt suspend/resume and PAT mapping, and nobody surfaces them. Dump all parameters, diff against a "recommended for LLM rigs" baseline (PreserveVideoMemoryAllocations=1, EnableGpuFirmware=1 on Turing+), surface "NVreg_PreserveVideoMemoryAllocations=0 → your suspend will lose VRAM state — add to modprobe.d". First : `modules/nvidia_modparams.py` + GET `/api/nvidia-modparams`. |
| 29.2 | **`/proc/<pid>/status` `VmRSS` + `VmSwap` of nvidia user-mode daemons (nvidia-persistenced, nvidia-powerd, gdm-nvidia)** | S | 3 | nvidia-persistenced + nvidia-powerd are long-lived root daemons that leak ~5-15 MiB/day on driver 550+ (filed upstream but unpatched); on 16 GiB hosts running llama-server this matters after a month of uptime. Snapshot RSS/Swap of all known nvidia daemons at sampler tick, slope-detect over 24 h, surface "nvidia-persistenced RSS +180 MiB in 7 days — restart suggested". First : migration `nv_daemon_mem` table + `modules/nv_daemon_mem.py` + GET `/api/nv-daemon-mem`. |
| 29.3 | **`/sys/bus/pci/devices/<gpu>/d3cold_allowed` + `d3cold_delay_ms` D3cold-policy auditor (complements shipped #28.1 runtime-PM)** | XS | 4 | Shipped #28.1 catches the `power/control=auto` runtime-PM thrash, but the deeper D3cold policy lives in two separate sysfs knobs (`d3cold_allowed` + `d3cold_delay_ms`) and on some boards the BIOS sets `d3cold_allowed=0` even when runtime-PM=auto, giving D3hot only (worse perf, no power savings — worst of both worlds). One read of both fields per GPU + PCIe parent bridge, surface "d3cold_allowed=0 but runtime-PM=auto on your bridge → bridge can't reach D3cold, you waste ~3 W idle". First : `modules/d3cold_policy.py` + GET `/api/d3cold-policy`. |
| 29.4 | **`nvidia-smi --query-gpu=ecc.errors.uncorrected.volatile.dram,ecc.errors.uncorrected.aggregate.dram` aggregate-vs-volatile ECC drift comparator** | S | 4 | Shipped #6 ECC counters report current/aggregate but don't compare them across reboots — aggregate ECC counters survive reboot (they live in InfoROM), volatile counters reset, and the *delta* reveals whether bad cells are being retired on every boot (a sign of imminent VRAM failure on out-of-warranty 3090s). Persist aggregate value across reboots in SQLite, compare to current aggregate, surface "aggregate uncorrected DRAM ECC errors grew +14 between this boot and last — VRAM degrading". First : migration `ecc_aggregate_history` + `modules/ecc_drift.py` + GET `/api/ecc-drift`. |
| 29.5 | **`/sys/devices/system/cpu/vulnerabilities/*` Spectre/Meltdown/MDS-mitigation cost calculator vs inference throughput** | S | 3 | Spectre-v2 + MDS + SRBDS mitigations cost 5-20 % CPU perf on Skylake-era prompt-processing; LLM rig owners running an air-gapped llama-server box might rationally trade safety for tokens/s but the dashboard never tells them. Enumerate `/sys/devices/system/cpu/vulnerabilities/*`, classify each as "mitigated/vulnerable/not-affected", estimate aggregate prompt-processing cost from a small lookup table, surface "your CPU runs 7 mitigations costing ~12 % prompt-processing — air-gapped? consider mitigations=off". First : `modules/cpu_mitigations.py` + GET `/api/cpu-mitigations`. |
| 29.6 | **`/sys/class/drm/card*/device/mem_info_vram_{total,used}` AMDGPU-style VRAM accounting (parity probe for hybrid hosts)** | M | 2 | A surprising number of homelab boxes have an iGPU + a discrete NVIDIA; we currently ignore the AMD/Intel side completely, but it can be hosting GNOME compositor → 200 MiB of "ghost" usage the user attributes to the NVIDIA card. Enumerate non-NVIDIA `/sys/class/drm/card*` entries, read AMDGPU-style accounting, surface "iGPU is hosting your compositor at 240 MiB — NVIDIA card is actually 100 % free for inference". First : `modules/igpu_accounting.py` + GET `/api/igpu-accounting`. |
| 29.7 | **`nvidia-smi --query-gpu=clocks_throttle_reasons.sw_thermal_slowdown,clocks_throttle_reasons.hw_thermal_slowdown` HW-vs-SW thermal-slowdown distinguisher** | XS | 4 | Shipped #19.2 throttle classifier groups all thermal throttling together, but the HW slowdown bit (set by the GPU's internal thermal-shutdown safety net at ~93 °C) is *very* different from the SW slowdown bit (driver-asserted around the user-set slowdown temp ~83 °C) — the former means the cooler has failed, the latter is normal under load. Separate the two bits, count occurrences per session, surface "you hit HW thermal slowdown 3× today — your cooler is failing, not your power profile". First : extends shipped throttle_bits.py with new HW/SW columns + GET `/api/thermal-slowdown-class`. |
| 29.8 | **`/proc/<llama-pid>/limits` rlimit auditor for LLM daemons (RLIMIT_MEMLOCK, RLIMIT_NOFILE, RLIMIT_STACK)** | S | 4 | llama-server with `--mlock` silently falls back to swap when RLIMIT_MEMLOCK is the systemd default 8 MiB; user sees "I have 64 GiB RAM and `--mlock` set, why is my model swapping?" and never finds the answer. Read `/proc/<pid>/limits` for known inference daemons (via shipped service discovery), compare `Max locked memory` against model size, surface "llama-server RLIMIT_MEMLOCK=8 MiB but you loaded a 32 GiB model with --mlock → mlock silently failed, add `LimitMEMLOCK=infinity` to the unit". First : `modules/rlimit_audit.py` + GET `/api/rlimit-audit`. |

**Top 4 (fit × urgency)** :
1. 29.1 nvidia kernel-module parameter auditor — XS, fit 5, one directory listing reveals ~40 tunables that nvidia-smi never exposes; catches the very common `NVreg_PreserveVideoMemoryAllocations=0` foot-gun that wrecks suspend/resume on LLM rigs (synergy with shipped #20.x suspend safety preflight), one-line modprobe.d fix, trivial to ship.
2. 29.3 D3cold-policy auditor — XS, fit 4, surgical follow-up to last cycle's #28.1 runtime-PM auditor that completes the picture (parent-bridge `d3cold_allowed=0` is the second-most-common "worst of both worlds" idle state); two sysfs reads, immediate "your bridge can't reach D3cold" verdict.
3. 29.8 rlimit auditor for LLM daemons — S, fit 4, solves the recurring "I have 64 GiB and `--mlock` but it still swaps" mystery that today goes undiagnosed; reuses shipped service discovery to target llama-server/ollama/comfyui, one Drop-In fix snippet, plugs into Diagnostics tab.
4. 29.7 HW-vs-SW thermal-slowdown distinguisher — XS, fit 4, refines shipped #19.2 throttle classifier with a critical safety-vs-tuning split that turns "thermal throttling" into either "cooler is dying, stop everything" or "normal load, tune profile"; extends an existing module (lowest-risk code change of the four).

Bench (29.2 nv-daemon RSS leak / 29.4 ECC aggregate drift / 29.5 CPU-mitigation cost / 29.6 iGPU accounting) for cycles after top 4 lands.

---

## R&D #28 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-27) nor parked bench (27.2/27.5/27.6/27.8). Stdlib + jsonschema only, single-GPU desktops / homelab focus, GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 28.1 | **`/sys/class/drm/card*/device/current_link_speed` runtime-PM `power/control` auditor** | XS | 5 | PCIe runtime power management (`/sys/bus/pci/devices/<gpu>/power/control`) flips between `auto` (suspends GPU to D3cold at idle) and `on`; many distros default to `auto` on laptop hybrid rigs, causing ~150-300 ms wake-spike on every llama-server token burst that ruins TTFT. One read of `power/control` + `power/runtime_status` + `power/runtime_suspended_time` per GPU, classify "D3cold idle thrashing", advise `echo on > .../power/control` via systemd Drop-In. First : `modules/runtime_pm.py` + GET `/api/runtime-pm`. |
| 28.2 | **`/proc/interrupts` GPU-IRQ affinity + storm detector** | S | 4 | Nvidia GPUs land all their MSI-X interrupts on CPU0 by default; under heavy inference the CPU0 ksoftirqd saturates and CUDA host→device copies stall — classic "core 0 pegged, others idle" symptom. Parse `/proc/interrupts` rows matching `nvidia`, sample IRQ counts at 1 Hz, compute per-CPU delta, surface "all 4 GPU IRQs pinned to CPU0, storming at 18 k/s during inference → spread via `/proc/irq/<n>/smp_affinity`". First : `modules/gpu_irq.py` + GET `/api/gpu-irq`. |
| 28.3 | **`/sys/kernel/debug/dri/0/amdgpu_pm_info`-style NVIDIA `/proc/driver/nvidia/gpus/<bdf>/information` deep-state dumper** | XS | 4 | The proc-FS `information` + `registry` files expose driver-internal state (GPU UUID, BAR layout, GSP firmware version, MIG mode, persistence-mode source) that nvidia-smi *doesn't* surface; ideal for bug-report bundle (#21.x). Snapshot the 3 files, diff against last run, attach the diff to dr_bundle. First : `modules/proc_nv_information.py` + GET `/api/proc-nv-info`. |
| 28.4 | **`nvidia-smi nvlink --status` + `--errors` for multi-GPU NVLink pair users (homelab dual-3090 / dual-4090)** | M | 4 | Dual-3090 LLM rigs with NVLink bridge are common in the homelab niche we serve; CRC/replay errors on the link silently drop tensor-parallel throughput 40 % without any nvidia-smi -q warning. Run `nvidia-smi nvlink -e` (raw + replay + recovery + flit-CRC counters per lane), persist deltas, surface "NVLink lane 2 replay errors +1.2 k/min — reseat bridge or downgrade to PCIe-only TP". First : migration `nvlink_errors` table + `modules/nvlink_health.py` + GET `/api/nvlink`. |
| 28.5 | **`/sys/class/thermal/thermal_zone*/` chassis + NVMe + chipset thermal-zone correlator** | S | 4 | GPU thermals shipped from day 1, but the *cause* of GPU thermal throttling is often a hot M.2 NVMe right under the card or a chipset fan dying — we have all `thermal_zone*` data sitting unused. Enumerate zones, persist temps at sampler tick, cross-correlate with shipped GPU-throttle events (#19.2), surface "GPU throttle at 14:32 coincided with NVMe-0 spike to 78 °C → airflow advice". First : migration `thermal_zone_sample` + `modules/thermal_zones.py` + GET `/api/thermal-zones`. |
| 28.6 | **`/sys/devices/system/node/node*/meminfo` NUMA node imbalance auditor for dual-socket / Threadripper hosts** | S | 3 | Threadripper / EPYC homelab rigs hosting an LLM often see GPU bound to node 1 while llama-server allocates 60 GiB on node 0 → ~30 % bandwidth penalty on KV-cache transfer. Read `/proc/<llama-pid>/numa_maps` + node meminfo, compute GPU-NUMA-affinity via `/sys/bus/pci/devices/<gpu>/numa_node`, surface "llama-server allocs on node 0, GPU on node 1 — pin via `numactl --cpunodebind=1 --membind=1`". First : `modules/numa_audit.py` + GET `/api/numa-audit`. |
| 28.7 | **`journalctl -k -g 'nvidia\\|nvrm\\|gsp'` since-boot kernel-log tail with ring-buffer classifier** | S | 5 | We have journald correlation (#9) for *events* but no continuous tailer for the kernel's own NVRM/GSP chatter (page-faults, RmInitAdapter retries, GSP-RM watchdog timeouts). Stream the last 4 KiB of `/dev/kmsg`-style nvidia lines every 5 s, classify into 8 known categories (XID already covered by #16.6, but RmInitAdapter / NvKmsKapi / GSP-RM aren't), surface a live "kernel-says" panel beside diagnostics. First : `modules/kernel_nvidia_tail.py` + GET `/api/kernel-nv-tail` (SSE). |
| 28.8 | **`nvidia-smi --query-gpu=clocks.current.video,clocks.max.video` video-engine (NVDEC/NVENC) clock disparity surfacer** | XS | 3 | Few users know that NVDEC/NVENC have their own clock domain — capped at ~1.4 GHz on consumer Ampere while the gr clock runs 1.9 GHz. When transcoding-heavy workloads (Jellyfin, ffmpeg-nvenc) stall, the video clock is often the bottleneck and people blame the gr clock. One-shot read of both fields, plot ratio, surface "video clock floored at 1395 MHz (bin-locked) during NVENC session — not a gr-clock problem". First : `modules/video_clock.py` + GET `/api/video-clock`. |

**Top 4 (fit × urgency)** :
1. 28.1 PCIe runtime-PM auditor — XS, fit 5, single sysfs read that catches the silent D3cold-wake stall ruining TTFT on hybrid laptops + Optimus eGPU rigs (a meaningful slice of our audience post #15.x Wayland widget); trivial advisor with a one-line systemd Drop-In fix, screenshot-friendly.
2. 28.7 kernel-NVRM/GSP log tailer — S, fit 5, fills the obvious gap between #9 journald correlator (event-triggered) and #19.5 GSP-RM crash surfacer (post-mortem) with a continuous live tail; surfaces RmInitAdapter retries that today go straight to dmesg unseen, perfect Diagnostics-tab live panel.
3. 28.5 thermal-zone correlator — S, fit 4, all data is already there in `/sys/class/thermal`, gives "*why* did the GPU throttle?" answers (hot NVMe under the card, dying chipset fan) that turn shipped throttle classifier (#19.2) into actionable airflow advice; one new table + one cross-correlation join.
4. 28.4 NVLink health monitor — M, fit 4, dual-3090/4090 NVLink rigs are exactly the homelab LLM niche the dashboard targets, replay/CRC errors today are invisible to nvidia-smi -q; biggest effort of the four but addresses an audience with zero alternative tooling.

Bench (28.2 GPU-IRQ affinity / 28.3 proc-NV deep-state / 28.6 NUMA audit / 28.8 video-clock) for cycles after top 4 lands.

---

## R&D #27 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-26), modules tree, or parked bench backlog. Stdlib + jsonschema only, single-GPU desktops/homelab focus, all GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 27.1 | **`/sys/class/drm/card*/device/resource{0..5}` BAR-region size + `resizable_bar_supported` auditor** | S | 5 | Resizable BAR (ReBAR) silently boosts ~5-12 % inference throughput on Ampere+ when the host UEFI exposes it, but most desktop boards ship it OFF and users never check. Parse `/sys/bus/pci/devices/<gpu>/resource` (6 BAR regions, hex start/end/flags) + the kernel-exposed `resource0_resize` capability (`/sys/bus/pci/devices/<gpu>/resource0_resize` on 6.1+), compare BAR0 size to total VRAM, surface "BAR0 = 256 MiB ≪ 24 GiB VRAM → enable Above-4G + ReBAR in UEFI". First : migration `pci_bar_snapshot` table + `modules/rebar_audit.py` + GET `/api/rebar-audit`. |
| 27.2 | **`nvidia-smi --query-gpu=encoder.stats.sessionCount,encoder.stats.averageFps,encoder.stats.averageLatency` NVENC session leak watcher** | S | 4 | OBS / Jellyfin / Sunshine streamers regularly hit the consumer NVENC 3-session cap (driver 550+ raised it to 8, but firmware-locked partner cards still cap at 3) and silently fall back to x264 CPU; user blames "stream lag". Poll NVENC session count at 1 Hz, log time-series, detect "session count == cap for >30 s + new session attempts" via `nvidia-smi pmon -c 1 -s u`, alert "NVENC saturated, fallback active". First : migration `nvenc_session` table + `modules/nvenc_sessions.py` + GET `/api/nvenc-sessions`. |
| 27.3 | **`/sys/class/powercap/intel-rapl/` CPU-package wattage harvester (tokens/Watt now includes CPU)** | S | 5 | Shipped #1 tokens/Watt metric uses GPU power only, but llama-server prompt-processing burns 50-100 W on the CPU side that the user pays for too — true efficiency for a 350 W GPU + 90 W CPU box is meaningfully different. Read `/sys/class/powercap/intel-rapl:0/energy_uj` (Intel) or `/sys/devices/platform/amd_pmf/energy_uj` (AMD) at sampler tick, delta-µJ → Watts, fold into shipped tokens/Watt + €/month widgets as "total system efficiency". First : migration adds `cpu_power_w` column to sampler + `modules/cpu_power.py` + extend GET `/api/efficiency`. |
| 27.4 | **GPU `nvidia-smi --query-gpu=power.management,power.default_limit,power.min_limit,power.max_limit` envelope auditor** | XS | 4 | Users set `nvidia-smi -pl 250` (e.g. 3090 from 350 → 250 W) for silence then forget; on driver upgrade the limit sometimes resets to `default_limit`, costing tokens/s with no notification. One-shot read of all 4 power fields per GPU, compare current `power.limit` vs last-stored value + against the GPU's profile, surface "power limit drifted from 250 → 350 W (default) — your tariff cost just jumped 40 %". First : migration `power_envelope` table + `modules/power_envelope.py` + GET `/api/power-envelope`. |
| 27.5 | **`/sys/firmware/acpi/platform_profile` + `/sys/devices/system/cpu/intel_pstate/no_turbo` chassis-level perf-mode advisor** | S | 4 | Laptop/SFF eGPU rigs ship with ACPI platform_profile=`low-power` or Intel turbo disabled in BIOS; the GPU is uncapped but the host CPU throttles prompt-processing 30 %. Detect platform_profile available values (`low-power|balanced|performance`) + `intel_pstate/no_turbo`, cross-reference against active workload (#19.4 warmup tag), surface "platform_profile=low-power during 1.2 t/s llama session → switch to performance". First : `modules/platform_profile.py` + GET `/api/platform-profile`. |
| 27.6 | **`/sys/kernel/mm/transparent_hugepage/enabled` + HF mmap THP advisor** | XS | 3 | GGUF mmap-loading benefits massively from transparent hugepages (=`always` or `madvise`); most distros ship `madvise` but Ubuntu Server 24.04 LTS defaults to `never` on KVM hosts, causing 3-5 s extra model load + worse TLB-pressure during inference. Single read of THP enabled mode + defrag mode + `/proc/meminfo`'s `AnonHugePages`, surface "THP=never during 8 GiB mmap'd llama load → switch to madvise". First : `modules/thp_advisor.py` + GET `/api/thp-advisor`. |
| 27.7 | **`nvidia-smi --query-gpu=enforced.power.limit,clocks.applications.gr,clocks.applications.mem` "applied vs enforced" gap detector** | S | 4 | `nvidia-smi -ac <mem,gr>` sets *application* clocks but the GPU enforces the *lower* of application + boost + thermal + power limits; users see "I set 1900 MHz but card runs at 1695" with no explanation. Sample both pairs at 1 Hz, compute gap, classify the *binding* constraint (power / thermal / app-clock-cap), surface "your -ac 1900 is never reached — power limit binds at 1695 MHz, raise -pl or undervolt". First : migration `clock_gap` table + `modules/clock_gap.py` + GET `/api/clock-gap`. |
| 27.8 | **`/proc/<pid>/oom_score_adj` advisor for llama-server / ComfyUI under memory pressure** | XS | 4 | Builds on shipped #24.6 OOM correlator : the *fix* nobody mentions is setting `oom_score_adj=-500` on long-lived inference daemons so systemd-oomd kills cache-heavy browsers/IDEs first. Detect known inference PIDs (llama-server, ollama, comfyui, jupyter-server) via shipped service discovery, read their current `oom_score_adj`, surface "your llama-server has oom_score_adj=0 (default-killable) → set -500 via systemd Drop-In, here's the snippet". First : `modules/oom_score_advisor.py` + GET `/api/oom-score-advisor`. |

**Top 4 (fit × urgency)** :
1. 27.1 ReBAR / BAR-size auditor — S, fit 5, single sysfs read that catches the ~5-12 % free-perf win every Ampere+ desktop owner with a 4-year-old UEFI is leaving on the table; complements shipped #25.4 slot mapper with the post-link "is your VRAM actually addressable in one shot?" diagnostic, screenshot-friendly.
2. 27.3 CPU-package RAPL harvester — S, fit 5, the missing half of our flagship tokens/Watt metric : adds CPU draw to the existing GPU-only efficiency widget so the €/month and tokens/Watt KPIs finally reflect the *full* socket; one new sampler column, one new module, immediate user trust win.
3. 27.7 applied-vs-enforced clock gap detector — S, fit 4, surgical follow-up to shipped #19.2 throttle classifier + #25.5 throttle-bits decoder : explains the single most-asked "why doesn't my -ac stick?" Reddit question with a 1 Hz delta and a binding-constraint label, fills a clear diagnostic gap.
4. 27.4 power-envelope drift auditor — XS, fit 4, trivial 4-field read that catches the silent post-driver-upgrade `-pl` reset (we have anecdotal reports from R&D #24.6 OOM correlator users); one cycle, immediate "your power limit changed" alert, plays nicely with #22.x driver-vault rollback.

Bench (27.2 NVENC sessions / 27.5 platform_profile / 27.6 THP advisor / 27.8 oom_score_adj) for cycles after top 4 lands.

---

## R&D #26 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-25), modules tree, or parked bench backlog. Stdlib + jsonschema only, single-GPU desktops/homelab focus, all GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 26.1 | **`/proc/driver/nvidia/gpus/<bdf>/information` static-asset auditor** | S | 5 | Distinct from shipped #18.4 procfs deep-state diff (which tracks *dynamic* counters) : the `information` and `registry` pseudo-files expose static facts (Device PCI ID, IRQ, Video BIOS family, GPU UUID encoding revision) that change ONLY on driver-version or hardware swap. Hashing them per-boot catches "the card was reseated and the BAR moved" or "vBIOS revision changed but vbios_version string didn't" — a known NVIDIA partner-card bug on used 3090s. First : migration `nv_info_snapshot` table + `modules/nv_info_audit.py` + GET `/api/nv-info-audit`. |
| 26.2 | **CUDA context-leak detector via `fuser` on `/dev/nvidia*` device nodes** | S | 5 | When a Jupyter kernel or llama-server crashes mid-allocation, the CUDA context can stay pinned to `/dev/nvidia0` / `/dev/nvidiactl` even though `nvidia-smi` shows no process — the file-descriptor leak survives until reboot and silently caps VRAM. Run `fuser /dev/nvidia*` (no sudo needed for our own UID, sudo-cached otherwise), cross-reference PIDs against `/proc/<pid>/comm` and shipped #15.2 per-process VRAM tracker, surface "PID 12345 (zombie python) still holds /dev/nvidia0 but uses 0 MiB → kill to reclaim". First : `modules/cuda_ctx_leak.py` + GET `/api/cuda-ctx-leak`. |
| 26.3 | **GPU fan-controller RPM-to-PWM linearity calibration probe** | M | 4 | Shipped #1 fan curve assumes PWM% maps linearly to RPM, but degraded sleeve-bearing fans (very common on used 1080 Ti / 2080 Ti / Founders 3090) show 30 % PWM = 1200 RPM but 60 % PWM = 1300 RPM (stuck blade region). One-shot calibration sweep (10 → 100 % in 10 % steps, 5 s dwell), record RPM via `nvidia-smi --query-gpu=fan.speed,fan.speed.rpm` if exposed else `/sys/class/hwmon/`, plot the curve and flag non-monotonic regions. First : migration `fan_calibration` table + `modules/fan_calibration.py` + POST `/api/fan-calibration/run`. |
| 26.4 | **`nvidia-smi nvlink --status` / SLI bridge sanity (single-GPU often left enabled)** | XS | 3 | Even single-GPU rigs sometimes have `nvlink` virtually enabled via leftover `nvidia-xconfig --sli=on` from a previous build; this consumes ~3 W idle and is invisible. Parse `nvidia-smi nvlink --status` (returns "GPU 0: not supported" or partial NVLink lanes), `nvidia-xconfig --query-gpu-info`, and grep `/etc/X11/xorg.conf*` for `Option "SLI"`; surface "SLI option set in xorg.conf with single GPU → safe to remove". First : `modules/nvlink_audit.py` + GET `/api/nvlink-audit`. |
| 26.5 | **`/sys/class/drm/card*/device/{current_link_width,max_link_width}` PCIe degradation watcher** | S | 5 | Distinct from shipped #6.x PCIe link-state histogram (which tracks ASPM transitions) and #23.x AER counters : this watches the *negotiated* link width vs the card's advertised max. A flaky riser cable, dirty PCIe slot, or thermal-creep on the connector silently drops a card from x16 → x8 → x4 between reboots; perf tanks 30 % on inference but no error is logged. Sample link_width + link_speed at boot and every 5 min, alert on any downgrade, store time-series. First : migration `pcie_link_width` table + `modules/link_width_watch.py` + GET `/api/link-width`. |
| 26.6 | **`nvidia-smi --query-supported-clocks` workload sweet-spot finder** | M | 4 | Each GPU exposes a discrete list of supported (memory, graphics) clock pairs via `--query-supported-clocks=mem,gr`; combined with shipped #19.4 warmup-profile tokens/W data, we can synthesize "for llama-3.1-8b-Q4 on this 3090, the optimal `--lock-gpu-clocks=1395,1695` (instead of stock 1965) costs 4 % tokens/s but saves 18 % power". One-shot sweep across supported clocks during a tagged warmup, persist Pareto frontier, propose `nvidia-smi -lgc` command. First : migration `clock_pareto` table + `modules/clock_sweet_spot.py` + GET `/api/clock-pareto`. |
| 26.7 | **Hugging Face token / `git credential.helper` leak scanner** | XS | 4 | Users frequently leave their HF write-token in `~/.cache/huggingface/token`, `~/.netrc`, or `git config --global credential.helper store` plain-text — and our shipped #14 dr_bundle export could inadvertently package them. One-shot scan of the canonical token locations + `git config --global --get credential.helper`, classify storage (plain / libsecret / gnome-keyring / store), surface "HF token stored as plain text + dr_bundle would include it → consider `huggingface-cli logout` or move to keyring". First : `modules/secret_leak_scan.py` + GET `/api/secret-leak-scan`. |
| 26.8 | **GPU memory-bandwidth saturation gauge via NVML utilization.memory** | S | 4 | `nvidia-smi --query-gpu=utilization.memory` returns the % of cycles the memory controller was busy, NOT the % of VRAM used — wildly misunderstood. A workload at 95 % SM util + 30 % mem-util is compute-bound (the model fits in cache); 40 % SM util + 95 % mem-util is bandwidth-bound (bigger batch won't help, smaller model will). Sample at 1 Hz, classify each tagged workload (#19.4) as compute / memory / mixed bound, surface "your llama-3.1-70b Q4 is 88 % memory-bound → quantize further or switch to Q3 for same tokens/s". First : migration `mem_util_window` table + `modules/mem_bandwidth_class.py` + GET `/api/mem-bandwidth`. |

**Top 4 (fit × urgency)** :
1. 26.5 PCIe link-width watcher — S, fit 5, catches the silent x16→x8→x4 downgrade that quietly kills inference throughput on flaky risers / dirty slots / OcuLink wear (a core differentiator for our eGPU/OcuLink positioning); two sysfs reads + a table + a "you lost a lane" alert, screenshot-friendly.
2. 26.2 CUDA context-leak detector — S, fit 5, surfaces the zombie-FD pattern that traps VRAM until reboot after Jupyter / llama-server crashes; reuses shipped #15.2 per-process VRAM tracker, one-shot `fuser` plus a "kill PID 12345" GUI button, immediate "why is my VRAM gone?" diagnostic.
3. 26.1 `/proc/driver/nvidia` static-asset auditor — S, fit 5, complements shipped #18.4 deep-state diff with the *static* slice (BARs, IRQ, vBIOS family, GPU UUID encoding) that detects reseated cards / partner-card vBIOS bugs; cheap hash-per-boot, fills a real gap.
4. 26.8 NVML memory-bandwidth saturation gauge — S, fit 4, finally explains the utilization.memory metric users always misread; combined with shipped warmup-profile tagging, turns into a concrete "quantize further" / "bigger batch" recommendation engine.

Bench (26.3 fan PWM-linearity / 26.4 NVLink-SLI leftover / 26.6 supported-clocks Pareto / 26.7 HF-token leak) for cycles after top 4 lands.

---

## R&D #25 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-24), modules tree, or parked bench backlog. Stdlib + jsonschema only, single-GPU desktops/homelab focus, all GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 25.1 | **`nvidia-smi --query-retired-pages` retired-page / row-remap trend** | S | 5 | Distinct from shipped #20.x ECC remap : `--query-retired-pages=gpu_uuid,timestamp,address,cause` returns the legacy (Pascal/Volta) page-retirement table including pending vs active causes (double-bit, multiple-single-bit). Older 1080 Ti / Titan V / P40 homelab cards (very common on used-GPU forums) never expose row-remap but DO populate this table; we currently miss them. Poll daily, persist `retired_page` table, surface "3 retired pages added since last week → schedule swap". First : migration `retired_page` table + `modules/retired_pages.py` + GET `/api/retired-pages`. |
| 25.2 | **NVMe / disk `discard` (TRIM) status auditor for model storage** | XS | 5 | If the partition holding `~/.cache/huggingface` is mounted without `discard` AND `fstrim.timer` is masked/disabled, the SSD silently degrades after a few model swaps (write-amp 4-6×). Parse `/proc/mounts` for `discard`, `systemctl is-enabled fstrim.timer`, and run `lsblk -o NAME,DISC-GRAN,DISC-MAX,ROTA --json` (no sudo), classify each model-cache directory's underlying device, surface "fstrim disabled + no discard mount = SSD wear". First : `modules/trim_audit.py` + GET `/api/trim-audit`. |
| 25.3 | **`nvidia-bug-report.sh` artifact prepper (no auto-run)** | S | 4 | When users hit XID 79 / GSP crash / driver hang, NVIDIA support always asks for `nvidia-bug-report.log.gz`; the script needs sudo, locks the host for ~30 s, and most users don't know it exists. We prep a one-click button that : (1) checks if `/usr/bin/nvidia-bug-report.sh` exists, (2) shows the exact command + sudo prompt the user must paste, (3) auto-attaches the resulting .gz to the next dr_bundle (shipped #14). Cross-references our XID log (#18.5) to pre-fill "report context : 2× XID 79 in last 24 h". First : `modules/bug_report_prep.py` + GET `/api/bug-report-prep`. |
| 25.4 | **GPU PCIe-slot physical-location / chassis-mapping helper** | S | 4 | Multi-GPU users RMA the wrong card because `nvidia-smi -i 0` ↔ "the leftmost slot" is non-obvious (NVML index ≠ PCI BDF ≠ riser cable label). Read `/sys/bus/pci/devices/<bdf>/{slot,physical_location}` + `dmidecode -t slot` if cached (no live sudo : reuse cached snapshot from setup wizard), produce "GPU 0 = NVML idx 0 = PCI 0000:01:00.0 = PCIe x16 slot 1 (top), serial XXX". Bonus : optional photo upload tied to UUID for visual confirmation. First : migration `gpu_physical_map` table + `modules/slot_mapper.py` + GET `/api/slot-map`. |
| 25.5 | **`nvidia-smi --query-gpu=clocks_throttle_reasons.sw_thermal_slowdown` SW vs HW throttle splitter** | S | 5 | Our shipped #19.2 throttle classifier groups thermal causes but doesn't separate driver-level (software) thermal limit from hardware hot-spot trip. The bitfield exposes 8 distinct reasons (`hw_thermal_slowdown`, `sw_thermal_slowdown`, `hw_power_brake_slowdown`, `sync_boost`, `applications_clocks_setting`, `display_clock_setting`, …); we currently store the aggregate. Decode each bit into its own boolean column, time-series per reason, alert on `hw_thermal_slowdown=1` (the dangerous one : VRM/hotspot, not edge temp). First : migration `throttle_reasons_v2` columns + `modules/throttle_bits.py` + GET `/api/throttle-bits`. |
| 25.6 | **Kernel IOMMU / VFIO passthrough leak detector** | S | 4 | Homelab Proxmox users who passed their GPU to a VM once and rebooted-back-to-bare-metal sometimes still have `vfio-pci` bound to the GPU (modprobe.d leftover), causing nvidia.ko to fail to claim it; symptom = "GPU vanished after reboot". Read `/sys/bus/pci/devices/<gpu>/driver` symlink (`vfio-pci` vs `nvidia`), grep `/etc/modprobe.d/*.conf` for `vfio-pci.ids=` matching the GPU vendor:device, surface "GPU is held by vfio-pci, driver mismatch". First : `modules/vfio_leak.py` + GET `/api/vfio-leak`. |
| 25.7 | **GPU clock-spread / clock-jitter histogram during steady-state inference** | M | 4 | A healthy GPU running a fixed workload should hold a tight clock band (±10 MHz on SM clock); a thermally-paste-degraded or VRM-stressed card shows ±50–100 MHz oscillation that no average metric reveals. Sample SM + memory clocks at 1 Hz, compute rolling-window standard-deviation over 60 s during shipped-#19.4 warmup-tagged active workloads, surface a histogram + "your card's clock-jitter is 3× baseline → check airflow / paste". First : migration `clock_jitter` table + `modules/clock_jitter.py` + GET `/api/clock-jitter`. |
| 25.8 | **systemd-resolved / DNS-hijack model-download breakage detector** | XS | 3 | HF / Ollama downloads fail silently when corporate DNS (or Pi-hole misconfig) hijacks `huggingface.co` or `cdn-lfs.huggingface.co`; user blames "internet slow". One-shot resolve of the canonical model-mirror hosts via `socket.getaddrinfo`, compare against expected ASNs (cached lookup-table shipped in repo), surface "huggingface.co resolves to 192.168.x.x (Pi-hole sinkhole)". First : `modules/dns_probe.py` + GET `/api/dns-probe`. |

**Top 4 (fit × urgency)** :
1. 25.1 Retired-page / row-remap trend — S, fit 5, fills the Pascal/Volta-era gap left by shipped row-remap modules (which only Ampere+ exposes); the *used-3090-and-older* homelab cohort gets actual silicon-degradation evidence, one-shot subprocess + table + chart, very screenshot-friendly.
2. 25.5 Throttle-bits decoder — S, fit 5, surgical upgrade to shipped throttle classifier : separates the *benign* (sw_thermal, display_clock) from the *dangerous* (hw_thermal_slowdown, hw_power_brake) bits; one migration that adds 8 boolean columns, no new external surface, immediate "is my card OK?" diagnostic value.
3. 25.2 TRIM / discard auditor — XS, fit 5, single-shot stdlib scan that catches the silent SSD-wear pattern killing model-cache disks; trivial module, complements shipped #23.2 FS mount audit by adding the SSD-lifecycle dimension nobody surfaces.
4. 25.3 nvidia-bug-report prepper — S, fit 4, removes the friction between "user has a driver crash" and "user files actionable NVIDIA forum / bug-report ticket"; auto-correlates with shipped XID log + dr_bundle export, screenshot-friendly one-click button.

Bench (25.4 slot mapper / 25.6 VFIO leak / 25.7 clock-jitter histogram / 25.8 DNS probe) for cycles after top 4 lands.

---

## R&D #24 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-23) or parked backlog. Stdlib + jsonschema only, single-GPU desktops/homelab focus, all GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 24.1 | **`nvidia-smi -q -d ACCOUNTING` per-PID lifetime stats harvester** | S | 5 | NVML accounting mode (when enabled) records peak VRAM + GPU util + wall-time per terminated PID — the only post-mortem proof of what a crashed inference job actually consumed. We currently lose all per-PID context the moment llama-server / ComfyUI exits. Enable accounting (idempotent `nvidia-smi --accounting-mode=1`), poll `nvmlDeviceGetAccountingPids` + `nvmlDeviceGetAccountingStats`, persist to new `accounting_stats` table; complements shipped #19.4 warmup profiler with post-exit ground truth. First : migration `accounting_stats` table + `modules/nvml_accounting.py` + GET `/api/accounting`. |
| 24.2 | **GPU-attached PCIe-AER (Advanced Error Reporting) counter** | S | 5 | PCIe correctable / uncorrectable errors on the GPU's root port + GPU device cause silent throughput collapses; kernel logs them but nobody surfaces deltas. Read `/sys/bus/pci/devices/<gpu>/aer_dev_correctable` + `aer_dev_fatal` + `aer_dev_nonfatal` (sysfs, no sudo), delta-track per cycle, alert on growth. First : migration `pcie_aer_event` table + `modules/pcie_aer.py` + GET `/api/pcie-aer`. |
| 24.3 | **DKMS / kernel-module rebuild status surfacer** | XS | 5 | After every kernel upgrade, `dkms autoinstall` may have failed silently and the user runs the next boot on the previous kernel's nvidia.ko — or worse, nouveau falls back. Parse `dkms status` (single-shot subprocess) + cross-check `uname -r` against `/lib/modules/$(uname -r)/updates/dkms/nvidia.ko` existence, surface "nvidia not built for kernel 6.12.5". First : `modules/dkms_status.py` + GET `/api/dkms`. |
| 24.4 | **GPU thermal-pad / VRAM-temp drift detector via NVML T_memory** | S | 5 | RTX 3090 / 3090 Ti / A6000 are notorious for VRAM-pad degradation : G6X temp climbs 5-10 °C over 6-12 months, leading to throttle + premature failure. NVML exposes `nvmlDeviceGetTemperature(NVML_TEMPERATURE_MEMORY)` (Ampere+); fit weekly median against same-workload baseline (joined from shipped #19.4 warmup profiler), alert on >+8 °C drift. First : migration `vram_temp_baseline` table + `modules/vram_temp_drift.py` + GET `/api/vram-temp-drift`. |
| 24.5 | **`nvidia-smi vgpu` / SR-IOV / vGPU-host capability probe** | XS | 3 | Some Ada / Hopper consumer-adjacent cards expose vGPU host bits via `nvidia-smi vgpu -q` even unlicensed; homelab Proxmox users want to know if their RTX A6000 / L40 supports vGPU before buying NVIDIA AI Enterprise. Probe NVML `nvmlDeviceGetVirtualizationMode` + parse `nvidia-smi vgpu -q`, surface "Host driver capable / not capable". First : `modules/vgpu_probe.py` + GET `/api/vgpu-probe`. |
| 24.6 | **systemd-oomd / earlyoom / OOM-killer GPU-process death log** | S | 5 | When a 24 GB model loads on a 32 GB RAM box, systemd-oomd or kernel OOM frequently kills llama-server with no on-screen feedback; user blames the GPU. Parse `journalctl _COMM=systemd-oomd` + `dmesg | grep -i 'killed process'` for the last 7 days, cross-reference PIDs against GPU-process history (shipped per-process VRAM trend), surface "your llama-server was OOM-killed 3× this week". First : `modules/oom_correlator.py` + GET `/api/oom-gpu`. |
| 24.7 | **GPU-aware CPU governor / EPP (energy-performance-preference) advisor** | S | 4 | Intel P-core / AMD EPYC boxes running `powersave` governor cap llama-server prompt-processing throughput by 30-40% (single-thread tokenizer bound); users tune nvidia clocks but ignore CPU side. Read `/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` + `energy_performance_preference`, detect "GPU pegged + CPU at powersave" mismatch during active workloads (joined with shipped sampler), recommend `performance` or `schedutil`. First : `modules/cpu_governor.py` + GET `/api/cpu-governor`. |
| 24.8 | **HuggingFace + Ollama model registry "stale model" advisor** | S | 4 | Users pull `llama-3.1-8b-q4_0` then never re-pull when `llama-3.2` / `q4_K_M` lands; gguf files sit for months while better quants exist locally or upstream. Walk `~/.cache/huggingface/hub/models--*` + `~/.ollama/models/manifests/`, parse model card SHAs (cached `model_id` mapping), surface "newer revision available" vs HF API (opt-in network) + "you have q4_0 and q4_K_M of same model — keep K_M" dedupe hint. First : `modules/model_staleness.py` (extends shipped #15.3 dedup + #14.2 HF janitor) + GET `/api/model-staleness`. |

**Top 4 (fit × urgency)** :
1. 24.1 NVML accounting harvester — S, fit 5, the missing post-mortem layer : every other module tracks live processes, this one captures the *terminated* ones with peak VRAM + GPU util + wall-time; idempotent accounting-mode flip + new table, screenshot-friendly "top-10 most expensive crashed jobs this week".
2. 24.2 PCIe-AER counter — S, fit 5, pure sysfs delta-tracking, no sudo, complements shipped #20-series PCIe link-thrasher + ASPM audit by catching the *correctable-error* tier that precedes link drops; one new table, one endpoint.
3. 24.3 DKMS rebuild status — XS, fit 5, a single subprocess + filesystem check defuses the #1 post-kernel-upgrade "my GPU disappeared" crisis; trivial module, huge perceived value, ships in one cycle.
4. 24.4 VRAM thermal-pad drift — S, fit 5, addresses the 3090 / 3090 Ti / A6000 memory-pad-degradation cohort (large + vocal homelab segment), builds on already-shipped warmup-profiler baseline data, fills the "is my card dying?" question alongside #22.1 reset counter + #18.5 XID reporter.

Bench (24.5 vGPU probe / 24.6 OOM correlator / 24.7 CPU governor / 24.8 model staleness) for cycles after top 4 lands.

---

## R&D #23 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-22) or parked backlog. Stdlib + jsonschema only, all GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 23.1 | **Inference batch-size / context-length advisor from real VRAM headroom** | M | 5 | llama-server / vLLM / ComfyUI users guess `-c 4096` / `--n-batch 512` and OOM at the 6th request; we already track per-workload peak VRAM (#19.4 warmup profiler) and parse GGUF (#7). Combine peak VRAM + model size + driver overhead → max safe `--n-batch` and `--ctx-size` per model. First : `modules/batch_advisor.py` (joins gguf_parser + vram_quota + warmup_profiler) + GET `/api/batch-advisor`. |
| 23.2 | **Model storage filesystem mount-option auditor** | S | 5 | Users keep models on btrfs with compression on (kills mmap throughput by 3-4×) or NFS without `noatime` (~5× slower load), have no idea. Read `/proc/mounts`, classify each model dir (HF cache, ComfyUI `models/`, llama-server, LM Studio) by fstype + flags, recommend `noatime,nodiratime,lazytime` + warn on `compress=zstd`. First : `modules/fs_mount_audit.py` + GET `/api/fs-mount`. |
| 23.3 | **Headless-rig display-emulator (HDMI dummy plug) detector** | XS | 4 | Many remote/Sunshine/Parsec/RDP streaming + LLM-rig setups need a dummy HDMI plug to enable hw nvenc / desktop composition; missing one silently drops nvenc to software or breaks Wayland session creation. Read `/sys/class/drm/card*-HDMI-*/status` + EDID size + connector states, detect "no monitor + hw-encode requested", advise. First : `modules/display_dummy.py` + GET `/api/display-dummy`. |
| 23.4 | **PCIe ASPM L0s/L1/L1.1 mismatch audit** | S | 5 | ASPM misconfig causes random NVMe stalls + GPU PCIe link drops on consumer mainboards (Z690 / B650 are the worst); nobody surfaces "supported but disabled" mismatches. Read `/sys/bus/pci/devices/<gpu>/link/l1_aspm`, parse `lspci -vvv` ASPM section (sudo-free for capability decode), flag mismatches between capable & enabled per device on the GPU's upstream root port. First : `modules/pcie_aspm.py` + GET `/api/pcie-aspm`. |
| 23.5 | **NVLink CRC / replay error-counter monitor** | S | 4 | 3090 / A6000 / A100 NVLink-bridged users get silent link CRC and replay errors → corrupted gradients + diverging training losses, nobody surfaces it. NVML exposes `nvmlDeviceGetNvLinkErrorCounter` (replay/recovery/CRC-flit/CRC-data) per link; delta-track and alert on non-zero growth. First : migration `nvlink_err_event` table + `modules/nvlink_health.py` + GET `/api/nvlink`. |
| 23.6 | **`/proc/driver/nvidia/gpus/*/information` deep-state diff** | XS | 5 | This procfs tree exposes IRQ, GPU UUID, GPU-Excluded state, board part-number, BAR1 size, video BIOS rev — keys *no NVML field* provides. Snapshot at boot, diff every cycle, alert on `GPU Excluded: Yes` (RMA candidate) or unexpected BAR1 shrink. First : `modules/proc_nvidia.py` + GET `/api/proc-nvidia`. |
| 23.7 | **fwupd / linux-firmware update awareness for GPU-adjacent hw** | S | 4 | PCIe re-timers, NVMe firmware, mainboard BIOS land via `fwupd` and frequently fix link-flap / power-state bugs that look like GPU issues. Run `fwupdmgr get-updates --json` (read-only, no sudo), filter to GPU's PCIe upstream path topology, surface "GPU's PCIe re-timer has firmware update available". First : `modules/fwupd_probe.py` + GET `/api/fwupd-gpu`. |
| 23.8 | **NCCL / CUDA P2P bandwidth + topology probe** | M | 4 | Dual-3090 NVLink-bridged or PCIe-only multi-GPU LLM rigs : P2P bandwidth between cards is the bottleneck nobody measures. Run `nvidia-smi topo -m` for matrix + opportunistic `p2pBandwidthLatencyTest` (if CUDA samples present, else skip), cache GB/s per pair, surface degraded links. First : migration `p2p_bench` table + `modules/p2p_probe.py` + GET `/api/p2p-bench`. |

**Top 4 (fit × urgency)** :
1. 23.1 Batch-size / ctx-length advisor — M, fit 5, transforms three already-shipped modules (GGUF parser, VRAM quota, warmup profiler) into a single actionable recommendation; eliminates the #1 inference-OOM trial-and-error loop for LLM rigs.
2. 23.6 procfs deep-state diff — XS, fit 5, single-file module, catches "GPU Excluded" RMA condition + BAR1 changes that NVML simply can't see; complements shipped #21.3 GSP + #18.5 XID + #22.1 reset counter trio.
3. 23.4 PCIe ASPM audit — S, fit 5, consumer-mainboard ASPM mismatch is the silent cause of "my training pauses for 200 ms every 30 s"; pure sysfs + lspci-cap parse, no sudo.
4. 23.2 FS mount-option auditor for model storage — S, fit 5, btrfs+compression + NFS-without-noatime are widespread footguns; one /proc/mounts scan + per-model-dir mapping, screenshots beautifully on the LM Studio / Ollama / ComfyUI multi-tool audience.

Bench (23.3 dummy-plug / 23.5 NVLink health / 23.7 fwupd / 23.8 P2P probe) for cycles after top 4 lands.

---

## R&D #22 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-21) or parked backlog. Stdlib + jsonschema only, all GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 22.1 | **GPU reset / GPU-recovery counter via nvidia-smi -q --reset & sysfs** | S | 5 | When an Xid 79 / 95 happens, NVIDIA sometimes auto-recovers via GPU reset; the user only notices the freeze, never the count. Sample `/sys/class/drm/card*/device/reset_count` (where exposed) + parse `nvidia-smi -q -d ACCOUNTING` `Gpu Reset Information`, store deltas, alert on >0/day. First : migration `gpu_reset_event` table + `modules/gpu_reset_counter.py` + GET `/api/gpu-reset`. |
| 22.2 | **Open-kernel vs proprietary driver advisor** | S | 5 | R555+ split between `nvidia-open` (GSP-based, recommended for Turing+) and legacy `nvidia` is confusing; many users run the wrong one and lose perf or stability. Detect installed flavor via `/sys/module/nvidia/version` + `modinfo nvidia | grep license`, cross-check against GPU arch from NVML, surface "your 3090 should be on nvidia-open" or vice-versa. First : `modules/driver_flavor.py` + GET `/api/driver-flavor`. |
| 22.3 | **Per-process VRAM leak detector (long-running session diff)** | M | 5 | LLM servers and ComfyUI accumulate VRAM over hours due to fragmentation / cache bloat; users restart blindly. Sample NVML per-process `usedGpuMemory` every minute, fit per-PID linear trend, flag >+5%/h growth. First : migration `vram_proc_trend` table + `modules/vram_leak_detector.py` + GET `/api/vram-leak`. |
| 22.4 | **Headless-rig display-emulator (dummy-plug) detector** | XS | 4 | Many remote/RDP/Sunshine streaming setups need a dummy HDMI plug to enable hw-encode; missing one silently drops to software. Read `/sys/class/drm/card*-HDMI-*/status` + EDID size, detect "connected but EDID is virtual" pattern, advise. First : `modules/display_dummy.py` + GET `/api/display-dummy`. |
| 22.5 | **CUDA toolkit / runtime version inventory (multi-install collisions)** | S | 5 | Conda envs, system apt, and `cuda-toolkit-12-4` side-by-side installs collide; user gets `libcudart.so.11.0 not found` randomly. Walk `/usr/local/cuda*`, `~/miniconda3/envs/*/lib/libcudart*`, `~/.local/lib`, `LD_LIBRARY_PATH`, surface version matrix + conflict warnings. First : `modules/cuda_inventory.py` + GET `/api/cuda-inventory`. |
| 22.6 | **PCIe ASPM (Active State Power Management) audit** | S | 4 | ASPM L1/L1.1 misconfig causes random NVMe stalls + GPU PCIe link drops on consumer mainboards; nobody surfaces ASPM state. Read `/sys/bus/pci/devices/<gpu>/link/l1_aspm`, `lspci -vvv | grep ASPM`, flag mismatches between supported & enabled. First : `modules/pcie_aspm.py` + GET `/api/pcie-aspm`. |
| 22.7 | **Inference batch-size advisor from real VRAM headroom** | M | 4 | llama-server / vLLM users guess `-c 4096` and OOM at 6th request; we already track VRAM utilization peaks. Combine peak VRAM from #19.4 warmup profiler + model size from GGUF parser → max safe `--n-batch` + `--ctx-size` recommendation. First : `modules/batch_advisor.py` (extends gguf_parser + vram_quota) + GET `/api/batch-advisor`. |
| 22.8 | **Filesystem mount-option auditor for model storage** | S | 4 | Users put models on btrfs with compression on (kills mmap speed) or NFS without `noatime` (5× slower load). Read `/proc/mounts`, classify by fstype + flags, cross-reference HF cache + ComfyUI/llama-server model dirs, recommend `noatime,nodiratime,lazytime`. First : `modules/fs_mount_audit.py` + GET `/api/fs-mount`. |

**Top 4 (fit × urgency)** :
1. 22.3 Per-process VRAM leak detector — M, fit 5, the silent killer of every 24/7 ComfyUI / Ollama rig; pure-NVML sampler + linear fit, leverages already-running per-PID sampling.
2. 22.1 GPU reset counter — S, fit 5, complements shipped #18.5 XID reporter + #21.3 GSP-RM surfacer by capturing the *recovery* side; trivial sysfs read, alerts on RMA-candidate cards.
3. 22.5 CUDA toolkit inventory + collision detector — S, fit 5, parallel value to shipped #15.5 CUDA/cuDNN compat matrix but for the *installed* side; resolves the #1 "why won't my torch see my GPU" forum question.
4. 22.2 Open-kernel vs proprietary driver advisor — S, fit 5, R555+ default split is fresh on Ubuntu 24.04 / Fedora 41 / Arch; many users on wrong driver flavor and don't know it.

Bench (22.4 dummy-plug / 22.6 PCIe ASPM / 22.7 batch advisor / 22.8 FS mount audit) for cycles after top 4 lands.

---

## R&D #21 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-20) or parked backlog. Stdlib + jsonschema only, all GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 21.1 | **GPU clock-domain stability audit (P-state pinning)** | S | 5 | RTX 30/40 cards on Linux randomly drop to P8 mid-inference even at 70% util; users see token/s halve with no thermal cause. Sample `nvmlDeviceGetCurrentClocksThrottleReasons` + `nvmlDeviceGetPerformanceState` at 1Hz, detect downshifts >2 levels within active workload windows, propose `nvidia-smi --lock-gpu-clocks` advisory. First : `modules/pstate_audit.py` (extends `throttle_cause`) + `api/pstate_audit.py` GET `/api/pstate-audit`. |
| 21.2 | **Per-card persistence-mode + clock-init drift detector** | XS | 5 | `nvidia-persistenced` not running on boot = 2-3s NVML stall on first sample + 200ms `nvidia-smi` cold-start; many distros ship it disabled. Probe `systemctl is-active nvidia-persistenced`, time first NVML call after fresh `nvmlInit`, surface boot-up warm-up cost. First : `modules/persistence_check.py` + GET `/api/persistence`. |
| 21.3 | **Firmware GSP-RM crash + fallback-mode surfacer** | S | 5 | Open-kernel driver (R555+) uses GSP firmware for clock/power control; when GSP crashes, card silently falls back to legacy host-RM with worse perf. Grep `dmesg` for `NVRM: GPU at PCI:* GSP RPC` + `RmGspBootBinaryImage`, check `/sys/module/nvidia/parameters/NVreg_EnableGpuFirmware`. First : `modules/gsp_health.py` + GET `/api/gsp-health`. |
| 21.4 | **NCCL / multi-GPU collective latency probe** | M | 4 | Dual-3090 NVLink-bridged or PCIe-only multi-GPU LLM rigs : P2P bandwidth between cards is the bottleneck nobody measures. Spawn tiny CUDA peer-copy benchmark via subprocess (`nvidia-smi topo -m` + cached `cuda_p2pBandwidthLatencyTest` if present), record GB/s per pair. First : migration `p2p_bench` table + `modules/p2p_probe.py` + GET `/api/p2p-bench`. |
| 21.5 | **Per-user `~/.cache/torch` + ComfyUI inputs disk-bloat janitor** | S | 5 | Stable-Diffusion / ComfyUI users accumulate 50-200 GB of cached upscalers, controlnets, and `inputs/` PNGs nobody tracks. Walk known cache paths (`~/.cache/torch`, `~/.cache/huggingface/diffusers`, ComfyUI `models/`, `output/`, `input/`), surface size + mtime trend, dry-run prune for files unread >90 days. First : `modules/sd_janitor.py` (extends `hf_dedup`) + GET `/api/sd-janitor`. |
| 21.6 | **NVLink health + error-counter monitor** | S | 4 | 3090/A6000 NVLink users get silent CRC errors → corrupted gradients, model diverges. NVML exposes `nvmlDeviceGetNvLinkErrorCounter` for replay/recovery/CRC/ECC per link; nobody surfaces it. First : `modules/nvlink_health.py` + GET `/api/nvlink`. |
| 21.7 | **fwupd / linux-firmware update awareness for GPU-adjacent hw** | S | 4 | PCIe re-timers, NVMe firmware, mainboard BIOS updates land via `fwupd` and can fix link-flap / power bugs affecting GPUs. Call `fwupdmgr get-updates --json` (read-only), filter to GPU-path PCIe topology, surface "GPU's PCIe re-timer has firmware update available". First : `modules/fwupd_probe.py` + GET `/api/fwupd-gpu`. |
| 21.8 | **`/proc/driver/nvidia/gpus/*/information` deep-state surfacer** | XS | 4 | This procfs tree exposes IRQ, GPU UUID, GPU Excluded state, board part-number — info no NVML field gives. Read all keys, diff against last-boot snapshot, surface unexpected changes ("GPU now Excluded — RMA candidate"). First : `modules/proc_nvidia.py` + GET `/api/proc-nvidia`. |

**Top 4 (fit × urgency)** :
1. 21.1 P-state pinning audit — S, fit 5, answers "why did my token/s halve?" — extends shipped #19.2 throttle classifier with the silent-downshift case it doesn't catch.
2. 21.2 persistence-mode check — XS, fit 5, single endpoint, instantly actionable, defuses the "first nvidia-smi takes 3 seconds" cold-start complaint every fresh install hits.
3. 21.3 GSP-RM crash surfacer — S, fit 5, R555+ open-kernel driver is now default on Ubuntu 24.04 / Fedora 41; silent GSP fallback is the new XID-class bug nobody monitors.
4. 21.5 SD/ComfyUI cache janitor — S, fit 5, image-gen users are an under-served audience parallel to LLM rigs; reclaiming 100 GB is screenshot gold and extends shipped #15.3 dedup logic.

Bench (21.4 NCCL p2p / 21.6 NVLink health / 21.7 fwupd / 21.8 procfs surfacer) for cycles after top 4 lands.

---

## R&D #20 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-19) or parked backlog. Stdlib + jsonschema only, all GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 20.1 | **NVIDIA Container Toolkit / Docker GPU visibility audit** | S | 5 | Homelab users run Ollama, ComfyUI, Immich, Frigate in containers — `--gpus all` vs `NVIDIA_VISIBLE_DEVICES=UUID` vs broken toolkit silently degrade to CPU. Parse `/var/run/docker.sock` (HTTP over unix socket, stdlib `http.client`) + `/proc/<pid>/cgroup` + `nvidia-ctk --version` to surface which containers actually see which GPU. First : `modules/container_gpu.py` + `api/container_gpu.py` GET `/api/container-gpu`. |
| 20.2 | **VBIOS revision + signed-firmware drift tracker** | S | 5 | Used / second-hand 3090s and mining-card flashes ship mismatched VBIOS; users only find out when power limits won't apply or P-states lock. Hash NVML `nvmlDeviceGetVbiosVersion` + `/sys/bus/pci/devices/*/rom` size, store snapshots, alert on change between boots. First : migration `vbios_snapshot` table + `modules/vbios_tracker.py` + GET `/api/vbios`. |
| 20.3 | **Kernel-module taint + dkms build-status surfacer** | S | 4 | After every kernel update, `nvidia.ko` rebuild can fail silently → fallback to nouveau on next boot, dashboard goes dark. Read `/proc/sys/kernel/tainted`, `dkms status`, `/var/lib/dkms/nvidia/*/build/make.log` tail. First : `modules/dkms_health.py` + GET `/api/dkms-health`. |
| 20.4 | **GPU memory ECC scrub + retired-page trend** | M | 4 | We already report ECC counters (#6) but not the page-retirement *rate*; a 3090 nearing remap-table exhaustion is a silent killer for long-running training. Sample `nvmlDeviceGetRetiredPages` daily, fit linear trend, project "weeks until full". First : migration `ecc_retired_trend` table + `modules/ecc_trend.py` + GET `/api/ecc-trend`. |
| 20.5 | **Hibernate / suspend-to-RAM safety preflight** | S | 5 | Closing a laptop lid with CUDA context open = corrupt VRAM, kernel panic on resume. Detect open NVML contexts + active `/dev/nvidia*` fds, block suspend via systemd-inhibit, warn from UI before lid close. First : `modules/suspend_guard.py` (polls `/proc/*/fd/* -lname /dev/nvidia*`) + GET `/api/suspend-guard` + POST `/api/suspend-guard/release`. |
| 20.6 | **Per-window-manager VRAM cost analyzer** | S | 3 | KDE Plasma 6 + animations eats 600-900 MB VRAM idle vs i3 ~150 MB; LLM users with 12 GB cards lose real context length. Parse `$XDG_SESSION_DESKTOP`, `compton`/`picom`/`kwin_wayland` PIDs, attribute VRAM via NVML compute-process list. First : `modules/wm_vram_cost.py` + GET `/api/wm-vram`. |
| 20.7 | **Backup-power runtime estimator for current GPU load** | S | 5 | UPS users (`ups_nut` already integrated) get raw runtime in minutes — useless mid-training. Combine NUT `battery.runtime` + current `power.draw` + queued job ETA from #14 calendar → "this job will outlast battery by 7 min, pause now?". First : `modules/ups_eta.py` (extends ups_nut) + GET `/api/ups-eta`. |
| 20.8 | **Kernel page-fault / GPU-driver crash dump scraper** | M | 4 | `nvidia-bug-report.sh` output, `dmesg` `NVRM: Xid`, `/var/crash/*nvidia*` are scattered; nobody has a single "did my GPU crash last week?" timeline. Tail journald + `/var/crash/`, dedup by stack hash, render timeline overlay on existing metrics graph. First : migration `gpu_crash_event` table + `modules/crash_scraper.py` + GET `/api/gpu-crashes`. |

**Top 4 (fit × urgency)** :
1. 20.5 Hibernate / suspend safety preflight — S, fit 5, prevents the #1 data-loss bug for laptop-RTX and lid-closing homelab users; trivial systemd-inhibit wrapper.
2. 20.1 Container Toolkit GPU visibility audit — S, fit 5, every homelab Ollama/ComfyUI user hits "why is it on CPU?" — instant value, screenshots well.
3. 20.7 UPS runtime estimator vs current GPU load — S, fit 5, leverages already-shipped `ups_nut` + reservation calendar, turns a raw metric into an actionable verdict.
4. 20.2 VBIOS drift tracker — S, fit 5, niche but acutely needed for the used-3090 / mining-card resale community that's our exact audience; one NVML call + hash store.

Bench (20.3 dkms / 20.4 ECC trend / 20.6 WM cost / 20.8 crash scraper) for cycles after top 4 lands.

---

## R&D #19 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-18) or parked backlog. Stdlib + jsonschema only, all GUI-controllable from Svelte 5 settings.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 19.1 | **GPU process priority / nice + ionice advisor** | S | 5 | Llama-server + Blender + game on same box → one starves another. Detect competing GPU PIDs via NVML, read `/proc/<pid>/{stat,io}`, recommend `renice` / `ionice` per workload class. First : `modules/proc_priority.py` polling `nvmlDeviceGetComputeRunningProcesses` + `/proc/*/stat`; `api/proc_priority.py` GET `/api/proc-priority` + POST `/api/proc-priority/apply`. |
| 19.2 | **Thermal throttle root-cause classifier** | S | 5 | "Why is my card slow?" — combine NVML `currentClocksThrottleReasons` bitmask, ambient delta vs intake, fan RPM, power-limit hit count → human verdict (case airflow vs TDP cap vs hot-spot). First : `modules/throttle_classifier.py` 30s sampler writing `throttle_event` rows + GET `/api/throttle-classifier/summary`. |
| 19.3 | **systemd-oomd / earlyoom risk preview for VRAM-pressure** | S | 4 | Modern Ubuntu kills llama-server mid-inference when RSS spikes; users blame the GPU. Read `/proc/pressure/memory`, `oomd.conf`, journald `oom-kill` lines, project to current VRAM headroom. First : `modules/oom_preview.py` + GET `/api/oom-preview`. |
| 19.4 | **Per-model warm-up profiler (cold→hot latency curve)** | M | 5 | First token after model load is 3-10× slower; users don't know whether to keep model warm or swap. Hook into existing 17.5 hot-swap timeline, record `(model, load_ts, first_token_ms, steady_token_ms)`, plot decay curve. First : migration `model_warmup` table + `modules/warmup_profiler.py` + GET `/api/warmup-profile`. |
| 19.5 | **HDMI-CEC / display power coordinator** | S | 4 | Headless rig with attached monitor wastes 20-40W keeping panel awake; users want auto-blank when no SSH/VNC session for N min. Use `cec-ctl` if present, fall back to `xset dpms` / `wlr-randr`. First : `modules/display_power.py` + GET `/api/display-power` + POST `/api/display-power/blank`. |
| 19.6 | **CUDA-MPS daemon health + share-mode probe** | S | 5 | Multi-tenant LLM rigs use Multi-Process Service to share one card; when MPS dies, jobs serialize silently. Check `nvidia-cuda-mps-control` socket, parse `pipe/log` dir, surface active clients + per-client SM%. First : `modules/mps_probe.py` + GET `/api/mps`. |
| 19.7 | **GPU-attached USB / capture-card power-budget auditor** | S | 4 | Streamers attach capture cards / VR HMDs to PCIe lanes shared with GPU; 12VHPWR + USB-C draw on RTX 4090/5090 can trip PSU OCP. Walk `/sys/bus/usb/devices/*/power/` + `lspci -t` topology to flag co-resident high-power devices. First : `modules/power_budget.py` + GET `/api/power-budget`. |
| 19.8 | **Frame-time / DXVK-state log scraper for gaming sessions** | M | 4 | Gamers on Linux already write `DXVK_HUD=1` or `MANGOHUD_LOG=1` logs; nobody aggregates them. Tail `~/.cache/MangoHud/` + `DXVK_LOG_PATH`, ingest p1/p99 frametimes per game per session, overlay GPU clocks. First : migration `frametime_session` table + `modules/frametime_scraper.py` (pure stdlib regex) + GET `/api/frametime/sessions`. |

**Top 4 (fit × urgency)** :
1. 19.2 Thermal throttle root-cause classifier — S, fit 5, answers the single most-asked support question on r/nvidia, killer screenshot.
2. 19.6 CUDA-MPS daemon health probe — S, fit 5, defuses silent multi-tenant LLM-rig stalls, low-LOC win.
3. 19.1 GPU process priority advisor — S, fit 5, immediate value for mixed gaming/LLM/Blender boxes, single new endpoint.
4. 19.4 Per-model warm-up profiler — M, fit 5, extends shipped #17.5 hot-swap orchestrator with the missing latency-decay view that justifies keeping models resident.

Bench (19.3 / 19.5 / 19.7 / 19.8) for cycles after top 4 lands.

---

## R&D #18 survey (started 2026-05-23)

8 fresh angles, none in shipped list (#6-17) or parked backlog. Stdlib + jsonschema only, all GUI-controllable.

| # | Feature | Effort | Fit | Why it matters / first module |
|---|---------|--------|-----|-------|
| 18.1 | **NVMe-as-VRAM-swap monitor** | S | 5 | llama.cpp / Ollama mmap to NVMe when VRAM full — silently shreds SSD endurance. Surface IOPS + TBW-projected lifespan delta per LLM session. First : `modules/nvme_swap.py` polling `/sys/block/nvme*/stat` + `pidof llama-server` mmap maps; `api/nvme_swap.py` GET `/api/nvme-swap`. |
| 18.2 | **CUDA / cuDNN / driver compat matrix** | S | 5 | LLM users hit cryptic CUDA-version mismatches weekly (torch built for 12.4 vs driver 555 vs cuDNN 9.1). Read `/usr/local/cuda*/version.json`, `ldconfig -p \| grep cudnn`, NVML driver string → matrix card. First : `modules/cuda_matrix.py` + `api/cuda_matrix.py` GET `/api/cuda-matrix`. |
| 18.3 | **CUDA_VISIBLE_DEVICES UUID pinning advisor** | XS | 5 | Multi-GPU rigs reorder on driver update — jobs land on wrong card. Detect index-based env vars in running procs (`/proc/*/environ`) and suggest UUID form. First : `modules/cvd_advisor.py` + GET `/api/cvd-advisor`. |
| 18.4 | **Cron / systemd-timer × GPU-reservation collision detector** | S | 4 | Backups + nightly bench + reserved training all stomp at 03:00. Parse `/etc/cron.*`, `systemctl list-timers --all`, overlay against 17.4 reservation calendar. First : `modules/timer_collision.py` + GET `/api/timer-collisions`. |
| 18.5 | **Display-output topology map** | S | 4 | eGPU / iGPU+dGPU users need to see which HDMI/DP runs off which GPU (gaming on dGPU but desktop on iGPU = wasted VRAM). Parse `/sys/class/drm/card*-*/status` + EDID. First : `modules/display_topo.py` + GET `/api/display-topology`. |
| 18.6 | **PCIe link-state thrasher detector** | S | 5 | OcuLink + riser users see Gen3↔Gen4 flapping mid-job, killing throughput. Sample `lspci -vv` link width/speed every 10s, alert on >N transitions/hour. Extends #14.5 with histogram + frequency. First : `modules/pcie_thrash.py` + GET `/api/pcie-thrash`. |
| 18.7 | **GPU clock-vs-temp scatter atlas** | M | 5 | Per-card thermal-headroom map: bin core clock by junction temp over a week → see boost cliff. Killer screenshot. First : storage migration `clock_temp_bin` table + `modules/clock_atlas.py` aggregator + GET `/api/clock-atlas`. |
| 18.8 | **Headless audio-cue engine (stdlib `wave`)** | XS | 4 | Long-job-done chime for headless rigs (`espeak` was parked in #7.9 as too heavy). Generate sine-wave WAV on the fly via stdlib `wave` + `aplay` subprocess, no dep. First : `modules/audio_cue.py` + GET/POST `/api/audio-cue/test`. |

**Top 4 (fit × urgency)** :
1. 18.3 CUDA_VISIBLE_DEVICES UUID advisor — XS, fit 5, immediate value, single endpoint.
2. 18.1 NVMe-as-VRAM-swap monitor — S, fit 5, real SSD-lifespan pain for LLM users, demo-worthy.
3. 18.2 CUDA/cuDNN/driver compat matrix — S, fit 5, defuses biggest LLM-rig support-question category.
4. 18.6 PCIe link-state thrasher detector — S, fit 5, extends shipped #14.5 with the missing frequency view (OcuLink crowd).

Bench (18.4 / 18.5 / 18.7 / 18.8) for cycles after top 4 lands.

---

## 💡 R&D iteration #17 (2026-05-23 00:58) — AUTO-OPENED post #16 complete

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 17.1 | **ECC remap scrubber scheduler** | S | 5 | nightly --query-remapped-rows, EOL prediction |
| 17.2 | Per-prompt energy receipt (signed) | M | 5 | tamper-evident JSONL chain, Wh+€+gCO2 |
| 17.3 | TDP profile auto-switch | S | 5 | rolling 60s util → pick 1 of 3 power caps |
| 17.4 | GPU reservation calendar | M | 4 | iCal export + cgroup soft enforce |
| 17.5 | **LLM hot-swap orchestrator** | M | 5 | Ollama/llama.cpp LRU eviction, swap timeline |
| 17.6 | Tokenizer-aware cost + ctx pressure | M | 4 | stdlib tokenizer.json walk |
| 17.7 | **Idle-rig one-liner probe** | XS | 5 | GET /idle.txt for tmux/Conky/motd |
| 17.8 | Scheduled lm-eval-harness runs | M | 4 | subprocess wrap, accuracy regression |

Start order : 17.7 (XS, quickest win) → 17.1 (S, RMA-grade ECC) → 17.3 (S) → 17.5/17.2/17.4/17.6/17.8 (mid effort).

---

## ✅ R&D iteration #16 complete (4/4 — 2026-05-23 00:43) + UI sprint 7
Backend : 16.4 driver vault · 16.6 NOC · 16.7 LM-Studio · 16.8 DR bundle.
UI cycle 7 : 4 cards added (Settings → Integrations now has 25 cards total).

---

## 💡 R&D iteration #16 (2026-05-23 00:30) — AUTO-OPENED post #15 complete

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 16.1 | PCIe lane negotiation auditor | S | 5 | Extends 14.5 with timeline + RMA CSV export |
| 16.2 | NVRAM / VBIOS drift watchdog | XS | 4 | Hash check on each boot |
| 16.3 | Coil-whine spectrogram (mic FFT) | M | 5 | Stdlib FFT, 2-20kHz band |
| 16.4 | Driver rollback vault | S | 5 | Cache last 3 .deb, side-by-side diff |
| 16.5 | Per-card aging curve | M | 5 | Clock@TDP vs hours-on vs spec |
| 16.6 | **NOC board** | S | 5 | /noc route, 10vw fonts, kiosk mode |
| 16.7 | **LM-Studio model bridge** | S | 5 | Scan LM-Studio models dir + dedup cross-ref |
| 16.8 | **1-click DR bundle** | S | 5 | VACUUM INTO + tar.zst + restore.sh |

Start order : 16.6 (S, big screenshot demo) → 16.8 (S, useful) → 16.4 (S) → 16.7 (S) → 16.1/16.2/16.3/16.5 (mid effort).

---

## ✅ R&D iteration #15 complete (4/4 — 2026-05-23 00:14) + UI sprint 6
Backend : 15.2 tariff · 15.3 dedup · 15.7 Discord · 15.8 boot profile.
UI cycle 6 : 4 cards added (Settings → Integrations now has 21 cards total).

---

## 💡 R&D iteration #15 (2026-05-22 23:55) — AUTO-OPENED post #14 complete

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 15.1 | GPU bus-reset surgeon | M | 5 | FLR/SBR via sysfs, no reboot needed |
| 15.2 | **Tariff-aware job scheduler** | S | 5 | Peak/off-peak CSV overlay + €/job estimator |
| 15.3 | HF cache dedup + symlink farm | S | 4 | SHA-bucket dedup, reclaim TBs |
| 15.4 | VRAM memcpy sentinel | M | 4 | Periodic bit-flip verifier |
| 15.5 | Per-app fan curve profiles | M | 4 | Blender→aggressive, browser→silent |
| 15.6 | BTRFS/ZFS config snapshot rollback | M | 4 | Snapshot /etc + GPU state before apply |
| 15.7 | Discord rich-presence bridge | S | 4 | Local IPC, air-gap-safe |
| 15.8 | **Boot-time profile applicator** | S | 5 | Apply persisted profile 5s after boot |

Start order : 15.8 (S, daily-driver, screenshot-friendly) → 15.2 (S) → 15.3 (S) → 15.7 (S) → 15.1/15.5/15.6/15.4 (M).

---

## ✅ R&D iteration #14 complete (4/4 — 2026-05-22 23:51) + UI sprint 5
Backend : 14.1 Xid · 14.2 lab accounting · 14.4 inference cost · 14.5 hot-swap.
UI cycle 5 : 4 cards added (Settings → Integrations now has 17 cards total).

---

## 💡 R&D iteration #14 (2026-05-22 23:35) — AUTO-OPENED post #13 complete

8 NEW angles, none in the backlog :

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 14.1 | **Xid fault-code dictionary** | S | 5 | Parse dmesg NVRM:Xid → JSON dict → human cause + remediation |
| 14.2 | Per-user lab time accounting | M | 5 | UID → GPU-seconds + VRAM-hours + Wh, CSV export |
| 14.3 | Crypto-miner signature library | S | 4 | xmrig/t-rex/lolMiner detection on shared boxes |
| 14.4 | **Inference cost calculator** | M | 5 | Per-prompt €/token via wall-meter + LLM logs |
| 14.5 | **Hot-swap / cable drift detector** | S | 5 | PCIe link-renegotiate / DRM disconnect events |
| 14.6 | Steam/Heroic game library scan | M | 4 | Tag GPU sessions by detected game |
| 14.7 | Wayland vs Xorg session probe | XS | 4 | Compositor + ExplicitSync support detection |
| 14.8 | ZRAM/swap pressure correlator | S | 4 | PSI memory + ZRAM stats vs GPU stutters |

Start order : 14.1 (XS-S, high fit, screenshot) → 14.5 → 14.4 → 14.2.

---

## ✅ R&D iteration #13 complete (4/4 — 2026-05-22 23:31) + UI sprint 4
Backend : 13.3 VRAM quota · 13.4 carbon · 13.6 hot-GPU wizard · 13.7 best-GPU.
UI cycle 4 : 4 cards added (Settings → Integrations now has 13 cards total).

---

## 💡 R&D iteration #13 (2026-05-22 23:20) — AUTO-OPENED post #12 complete

8 NEW angles :

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 13.1 | Undervolt auto-pilot (idle-aware) | M | 5 | Step `nvidia-smi -pl` down during idle, kWh saved tracker |
| 13.2 | GPU reservation queue | M | 5 | SQLite-backed Gantt + iCal export reuses 11.6 infra |
| 13.3 | **VRAM quota enforcer** | S | 5 | Per-process budget, dry-run logs hypothetical kills |
| 13.4 | Carbon-intensity overlay (local CSV) | S | 4 | gCO2/kWh × W → gCO2/token, air-gap-safe (local file) |
| 13.5 | Boot-time driver regression smoke test | M | 4 | 6s matmul perf vs rolling baseline tagged by driver version |
| 13.6 | **Hot-GPU diagnostic wizard** | S | 5 | Step-through dust/driver/fan/ambient verdict tree |
| 13.7 | **Workload power-balancer (multi-GPU)** | S | 5 | /api/best-gpu returns CUDA_VISIBLE_DEVICES suggestion |
| 13.8 | Inference latency budget sentinel | M | 5 | p50/p95/p99 tok latency vs SLO, alerts via notif hub |

Start order : 13.7 → 13.3 → 13.6 → 13.4 → 13.1 / 13.2 / 13.5 / 13.8.

---

## ✅ R&D iteration #12 complete (8/8 — 2026-05-22 23:13) + UI sprint 3
Backend : 12.1 wall-meter · 12.2 SMART · 12.3 LAN peer beacon · 12.4 rule engine ·
12.5 CI tag · 12.6 /embed iframe · 12.7 air-gap · 12.8 print mode.
UI cards added to Settings → Integrations : disk / airgap / wall / peers (fdc7fb9).

---

## 💡 R&D iteration #12 (2026-05-22 16:18) — AUTO-OPENED post #11 complete

Per auto-rebound rule. 8 NEW angles : wall-meter, SMART disk, LAN peers, rule engine, CI runner tag, embed iframe, air-gap, print mode.

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 12.1 | **Wall-meter reconciler (Shelly/Tasmota)** | S | 5 | Real PSU+idle delta via smart-plug, true tok/Wh |
| 12.2 | SMART disk health correlator | S | 4 | Disk-vs-GPU read latency overlay |
| 12.3 | **LAN peer discovery (UDP beacon)** | M | 5 | Zero-config Fleet tab via mDNS/SO_BROADCAST |
| 12.4 | **Rule engine (declarative JSON)** | M | 5 | IF temp>80 FOR 5min THEN ... — no plugin file |
| 12.5 | CI runner GPU tag endpoint | S | 4 | /api/ci-tag for self-hosted GH runners / Jenkins |
| 12.6 | **`/embed` read-only iframe view** | S | 5 | Token-gated single-card for Notion/status pages |
| 12.7 | Air-gapped mode | M | 5 | Disable all outbound URL fetches, audit attempts |
| 12.8 | **Print mode (CSS @media print)** | XS | 4 | Maintenance log printable, signature line |

**Start order** : 12.8 (XS, quick win) → 12.6 (S, demo-friendly) → 12.1 (S) → 12.5 (S) → 12.3/12.4 (M, longer).

---

## ✅ R&D iteration #11 complete (4/4 priority + Docker bonus — 2026-05-22 16:16)
11.1 healthz/readyz + watchdog · 11.4 service discovery · 11.5 weekly report · 11.6 iCal feed · Dockerfile + docker-compose.

---

## 💡 R&D iteration #11 (2026-05-22 16:05) — AUTO-OPENED post #10 complete

Per the auto-rebound rule. 8 NEW angles : k8s probes, AI sidekick, heat plume, service discovery, weekly report, iCal, WoL, maintenance coach.

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 11.1 | **`/healthz` + `/readyz` k8s probes** | XS | 5 | Sub-ms liveness + 503 readiness on NVML/SQLite/snapshot-age |
| 11.2 | AI sidekick (NL state query via local LLM) | M | 5 | Allow-list SQL templates, uses local llama.cpp endpoint we monitor |
| 11.3 | Heat plume map (multi-sensor SVG) | M | 5 | hwmon discovery + RBF interpolation, animated over history |
| 11.4 | **Auto-service discovery (LLM stacks)** | S | 5 | ss -tlnp + cmdline fingerprint → Ollama/vLLM/Chroma cards |
| 11.5 | **Print-friendly weekly report** | S | 5 | Self-contained HTML + plain-text email via existing SMTP |
| 11.6 | iCal feed of GPU events | XS | 4 | RFC 5545 .ics for calendar subscribe |
| 11.7 | Wake-on-LAN + suspend-aware scheduler | S | 4 | Magic packet sender + snapshot-gap detection |
| 11.8 | Spaced-repetition maintenance coach | S | 4 | Dust/paste/cable cadence modulated by thermal drift |

**Start order** : 11.1 (XS, ops-essential) → 11.4 (S, service discovery is screenshot-worthy) → 11.5 (S, useful) → 11.6 (XS, iCal) → 11.7 / 11.8 → 11.2 / 11.3 (last).

---

## ✅ R&D iteration #10 complete (4/4 priority shipped — 2026-05-22 15:57)
10.1 vector DB watchdog · 10.3 HF model card cross-ref · 10.6 ANSI/tldr endpoint · 10.7 SVG badge generator.

---

## 💡 R&D iteration #9 (2026-05-22 15:18) — AUTO-OPENED post #8 complete

Per the auto-rebound rule. 8 fresh angles (containers, GPU passthrough, auth, HF cache, multi-user audit, time-series anomaly).

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 9.1 | **VFIO/Passthrough Sentinel** | S | 5 | Detect GPU bound to vfio-pci → which VM holds it. Unique demo |
| 9.2 | Container Topology Map | M | 5 | Docker/Podman/k8s → GPU tree via /proc + cgroups |
| 9.3 | Dashboard Auth + Read-Only Share | M | 5 | HMAC tokens, scope claims, safe Tailscale Funnel |
| 9.4 | HF Cache Janitor | S | 5 | Surface 80+ GB cold model files, dry-run prune |
| 9.5 | Quant Recommender | S | 4 | Given VRAM budget, suggest best quant for HF model |
| 9.6 | Multi-User Audit Log | S | 5 | Every settings mutation → audit_log row, blame per-user |
| 9.7 | Process Timeline Replay | M | 5 | Swimlane chart of PIDs claiming VRAM over time |
| 9.8 | Holt-Winters Anomaly Scorer | M | 4 | Stdlib triple-expo smoothing, dedup notif storms |

**Start order** : 9.1 (S, unique demo) → 9.4 (S, useful) → 9.6 (S, ties to existing auth idea) → 9.3 (M, security) → 9.7 (M, visual).

---

## ✅ R&D iteration #8 complete (4/4 priority shipped — 2026-05-22 15:13)
8.1 history scrubber · 8.2 thermal coach R² + notif bridge · 8.7 Jupyter monitor · 8.4 llama-bench monitor.

---

## 💡 R&D iteration #8 (2026-05-22 14:45) — AUTO-OPENED post #7 complete

Per the auto-rebound rule. Surveyed angle : visual / demo-able features
to defuse "yet another wrapper" criticism. 8 candidates :

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 8.1 | **History scrubber (time-travel slider)** | S | 5 | Drag timeline → all gauges snap to past second. *The* killer demo GIF |
| 8.2 | **Predictive throttle alert** | S | 5 | Linreg on temp → '⚠ throttle in ~4min32s'. Magical, zero deps |
| 8.3 | ROCm backend (AMD support) | L | 5 | Doubles addressable audience. Defuses 'NVIDIA-only' objection |
| 8.4 | llama-bench scheduled runner | M | 5 | Nightly bench + drift chart. Catches driver regressions |
| 8.5 | ComfyUI workflow overlay | M | 5 | Annotate timeline with node spans. Image-gen audience |
| 8.6 | Power-draw replay calibration | L | 4 | Replay last hour as synthetic CUDA load. Niche but unique |
| 8.7 | Jupyter kernel monitor | S | 4 | Per-notebook GPU attribution |
| 8.8 | TensorBoard plugin | M | 4 | Credibility in ML ecosystem |

**Start order** : 8.1 (S, instant demo) → 8.2 (S, magical) → 8.5 (M, very visual) → 8.3 (L, audience expansion).

---

## ✅ R&D iteration #7 complete (3/3 priority shipped — 2026-05-22 14:40)
7.4 InfluxDB line protocol · 7.10 Prometheus AlertManager rules · 7.5 UPS/NUT awareness.

---

## 💡 R&D iteration #7 (2026-05-22 14:25) — AUTO-OPENED post #6 complete

Per the standing **auto-rebound rule**, surveyed NEW tools (OpenTelemetry,
WebSocket streaming, MQTT, SSH fleet aggregator, InfluxDB line protocol,
UPS/NUT, remote action console, eBPF tracer, Loki push, audio cues, Prometheus
AlertManager). Frontier : **integration / fleet / actions** (the dashboard
is now mature for single-host monitoring).

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 7.1 | WebSocket live stream (replace polling) | M | 5 | Sub-second updates, less CPU on idle tabs |
| 7.2 | MQTT bridge (Home Assistant compat) | M | 5 | Trigger smart-plug shutoff / room AC on overtemp |
| 7.3 | SSH fleet aggregator | L | 5 | Multi-host fleet wall via ssh -L + ControlMaster |
| 7.4 | **InfluxDB line protocol pusher** | S | 4 | One-line Grafana/Influx integration |
| 7.5 | UPS / NUT awareness | M | 5 | Auto-throttle PL on battery, extend uptime |
| 7.6 | Remote action console (sudo wrap) | M | 5 | One-click reboot/restart-vllm with audit log |
| 7.7 | eBPF GPU syscall tracer | L | 4 | bpftrace bridge for ioctl storms, OOM hits |
| 7.8 | Loki log push | S | 4 | Grafana ecosystem completion |
| 7.9 | Audio cue engine (espeak) | XS | 3 | Spoken alerts for headless rigs |
| 7.10 | Prometheus AlertManager rules export | S | 4 | rules.yaml download button |

**Start order** : 7.4 (S, immediate Grafana value) → 7.10 (S) → 7.5 (M, real LLM-rig pain) → 7.1 (M, infra) → 7.2 (M).

---

## ✅ R&D iteration #6 complete (5/5 priority shipped — 2026-05-22 13:40)
6.1 unified notification hub · 6.2 deadman heartbeat · 6.3 sys-context · 6.7 journalctl bridge · 6.8 cgroup power accounting.

## 💡 R&D iteration #6 (2026-05-22 13:50) — AUTO-OPENED post #5

Per the standing **auto-rebound rule**, surveyed fresh tools (Apprise, Healthchecks, journalctl, cgroup/systemd-cgtop, glances iowait, restic/borg, kernel sysrq, Tailscale/Caddy reverse-proxy) — identified 8 candidates :

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 6.1 | **Unified Notification Hub (Apprise-style)** | M | 5 | Discord/Slack/Matrix/Gotify/ntfy/Pushover/SMTP via urllib. Per-channel filters, retry queue |
| 6.2 | **Deadman heartbeat + inbound pings** | S | 5 | Outbound healthcheck ping + `/api/heartbeat/<token>` for training scripts |
| 6.3 | **System-context sidecar (iowait/swap/load)** | M | 5 | /proc samples on same cadence → 'Probable IO stall' band annotation |
| 6.4 | Reverse-proxy / remote-access wizard | M | 4 | Generated Caddy/Nginx/Tailscale Serve snippets + probe |
| 6.5 | Hung-GPU auto-recovery ladder | L | 4 | sysrq + nvidia-smi --gpu-reset → kill train → modprobe |
| 6.6 | Snapshot backup rotation | M | 4 | Hourly/daily/weekly tarball with content-hash dedup |
| 6.7 | **journalctl bridge with saved filters** | S | 5 | Filtered tail of nvidia/xid/oom/thermal + chart marker on XID |
| 6.8 | **cgroup per-process power accounting** | M | 5 | Watts-per-PID via SM-share split, grouped by systemd.slice |

**Start order** : 6.2 (S, quick-win) → 6.7 (S, immediate utility) → 6.1 (M, unlocks future channels) → 6.3 (M) → 6.8 (M).

---

## ✅ R&D iteration #5 complete (3/3 priority shipped — 2026-05-22 13:46)

Per the standing **auto-rebound R&D rule** (user 'fais vivre le plan'), surveyed fresh tools (lm-sensors / sysstat / perf / btop / intel_gpu_top / k6 / fio / supervisord / WaybarBatteryNvidia / smartmontools / gpu-burn / NVML bindings) and identified 8 candidates :

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 5.1 | **Thermal headroom coach** | M | 5 | 'You have 17°C headroom · fan can be 8% gentler' — surfaces a DECISION |
| 5.2 | **Driver/kernel drift detector** | S | 5 | Snapshot driver+kernel+ECC+MIG → diff on each boot. LLM rigs break silently after apt upgrade |
| 5.3 | Stress-test runner in-process | L | 5 | cuBLAS sgemm 60s/5m/30m + live throttle reasons. Validates undervolts |
| 5.4 | **Waybar / polybar / i3blocks / tmux JSON output** | XS | 5 | `/api/bar?fmt=waybar` → one-line GPU status for desktop bars |
| 5.5 | eBPF-style GPU wakeup tracer | M | 5 | When GPU wakes from idle, log WHICH process did it. 'Sleep-stealers' top-N |
| 5.6 | fio-style VRAM bandwidth self-test | S | 4 | Weekly cron benchmark → trend chart, catches silent VRAM degradation |
| 5.7 | NVENC/NVDEC session inspector | S | 4 | Encoder util now anonymous — map to PIDs (OBS, ffmpeg, etc.) |
| 5.8 | Supervisor mode for ML daemons | L | 5 | Start/stop/restart vllm/ollama from dashboard + GPU-health gate |

**Start order** : 5.4 (XS, quick-win, immediate value) → 5.2 (S, high user value LLM rig context) → 5.1 (M, flagship decision-surfacing).

## ✅ R&D #3 complete (5/5)
3.1 sysreport tar.gz · 3.2 header status chip · 3.3 uptime % · 3.4 anomaly bands · 3.5 keyboard cheat-sheet.

## ✅ UX polish session (cycles 139-145, ~22 commits)
Migration v0.3.0 + systemd · sw.js root + Cache-Control · Wizard restart button · Auto-detect log source · Inline price edit · Modules toggle auto-restart · Fan curve immediate apply · History infinite-loop fix · A/B benchmark UI · Reorder cards · Strips → single merged strip with ‹ › arrows · Merge VRAM+PCIe into GPU card · Horizontal centered group labels · Sticky top+bottom · VersionFooter pull-update · Update check moved to About · scripts/test-update-flow.sh.

## 💡 R&D iteration #4 (2026-05-22 13:15)

Survey of nvtop/MangoHud/LACT/CoreCtrl/GPU-Z/DCGM/Netdata/Pi-hole inspired 11 candidate features. Priority list :

| # | Feature | Effort | Fit | Notes |
|---|---------|--------|-----|-------|
| 4.1 | **Prometheus `/metrics` endpoint** | XS | 5 | OpenMetrics text · gpu_index labels · zero deps |
| 4.2 | **Clocks-event-reasons decoder** | S | 5 | WHY GPU throttles (power/thermal/HW) · strip under chart |
| 4.3 | ECC + memory health panel | S | 4 | Corrected/uncorrected counters · remapped rows · Telegram alert |
| 4.4 | **Fan curve hysteresis** | S | 5 | Ramp-down delay + hotspot input · stop osc on bursty LLM |
| 4.5 | Idle-state audit | S | 4 | 'You pull 25W idle (expected 8-15W)' + checklist |
| 4.6 | Per-process accounting tab | M | 5 | Kill button · sparkline per PID · session log |
| 4.7 | MangoHud OSD bridge | S | 4 | Write /tmp/gpu-dashboard.mango in MangoHud format |
| 4.8 | Workload tagger + replay | M | 5 | Drag-select chart → tag range "llama-70b Q4" · sessions table |
| 4.9 | PCIe link quality probe | M | 5 | Active bandwidth burn + replay-counter delta · OcuLink |
| 4.10 | Allow/block list for triggers | S | 4 | Pi-hole style · 'never let Steam touch GPU' · kill action |
| 4.11 | Undervolt auto-tuner | L | 5 | Sweep PL × offsets under user workload → Pareto front |

**Start order** : 4.1 (XS) → 4.2 (S) → 4.4 (S) → 4.5 (S) → 4.3 (S).

Then re-evaluate based on user feedback.

---

## 🔄 In progress

**User redirect 2026-05-22 07:35** : "à toi de trouver de nouvelles features
... sur des logiciels équivalent ou pas ... en gros r&d". Loop now in
autonomous R&D mode — survey equivalent tools, document findings in the
R&D section below, implement what fits the project's spirit (Linux/NVIDIA,
no SaaS, no paid tier).

### Cycle 115 — About → Stats reorg (3 commits)
- `86886fa` SettingsModal About stripped to version/vBIOS/paths/license/repo + hint
- `1c6a333` i18n keys companion
- `bdb3d02` Refreshed docs/modal/about.png + docs/stats.png

---

## 💡 R&D — Feature discovery from equivalent tools (2026-05-22)

### Scan : what equivalent tools do that we don't

| Tool | Notable feature | Worth porting ? |
|---|---|---|
| **MSI Afterburner** | OC Scanner — auto-find max stable clocks | ❌ risky on Linux + needs hours of stress |
| **MSI Afterburner / RTSS** | On-screen display overlay in games | ⚠️ complex (Vulkan layer / MangoHud territory) |
| **CoreCtrl / LACT** | **Per-application profiles** — auto-switch on app launch | ✅ **HIGH** — extends auto_profile_daemon |
| **GreenWithEnvy** | Fan curve hysteresis tunable per-curve | already ±2°C global |
| **nvtop / nvitop** | Per-process VRAM + util tree real-time | ✅ Cards.svelte has it static — can be expanded |
| **HWiNFO** | NVENC/NVDEC/OpticalFlow utilization | ✅ **HIGH** — nvidia-smi exposes these |
| **HWiNFO** | PCIe link state (gen + width + state) | ✅ low effort, big info value |
| **GPU-Z** | vBIOS download + sensors snapshot CSV | partial (we have CSV export) |
| **Mission Center** | Monthly GPU energy budget | ✅ extends our €/month with **budget alert** |
| **CoreCtrl** | Performance state lock (P0/P2/P8) | ✅ via nvidia-smi -lgc, niche |
| **Custom** | **Compare-A-vs-B benchmark** | ✅ **HIGH** — leverages samples + profiles |
| **Custom** | **Plot SVG export** | ✅ low-effort report polish |

### Picked for upcoming cycles (ranked by value/effort)

1. **Per-app profile triggers** (cycles 116-119) ✅ DONE — 38 new tests, full stack delivered
   - Cycle 116 ✓ : backend module `modules/app_triggers.py` + 17 tests (`d38e798`)
   - Cycle 117 ✓ : wired into auto_profile_daemon (`c5615f5`)
   - Cycle 118 ✓ : UI tab + GET/POST + 8 tests (`f07c6c6` + `03001ff` + `241be22`)
   - Cycle 119 ✓ : README + CHANGELOG + permanent gallery screenshot (`0758ffc`)
2. **NVENC/NVDEC + PCIe metrics** (cycle 120) ✅ DONE
   - util_enc/util_dec/pcie_gen/pcie_width plumbed into /api/state
   - GPU card shows 'ENC X% · DEC Y%' when active
   - New PCIe card with downgrade warning (current < max)
   - 5 new TDD tests · 567 → 572
3. **Monthly power budget tracker** (cycle 121) ✅ DONE
   - /api/power-stats + /api/electricity expose kwh_month/forecast_kwh/budget_kwh/over_budget
   - POST /api/electricity/config accepts budget_kwh
   - Electricity card shows actual progress bar + forecast band + ⚠️ over_budget
   - 8 new TDD tests · 572 → 580
4. **Compare-A-vs-B benchmark** (cycles 122-123) ✅ DONE
   - Cycle 122 ✓ : benchmark helpers + 7 tests (`a42ba7b`)
   - Cycle 123 ✓ : POST /api/benchmark/run + UI tab + 7 more tests (`a3e8b57`) — total 14 tests
5. **Plot SVG export** (cycle 124) ✅ DONE
   - charts.ts exportSvgAsFile() helper (clone + xmlns + bg rect + blob download)
   - HistoryChart.svelte ⬇️ button top-right with timestamped filename
   - 1 new i18n key × 2 langs · bundle +0.2 KB
6. **GPU process tree expansion** (cycle 125) ✅ DONE
   - /api/processes attaches cmdline from /proc/<pid>/cmdline
   - Cards.svelte processes table : 3px VRAM bar per row (green/amber/red) + cmdline tooltip
   - 4 new TDD tests · 594 → 598

---

## 💡 R&D iteration #2 — survey 2 (2026-05-22, cycle 127+)

### New scan : tools not covered in iteration #1

| Tool | Notable feature | Worth porting ? |
|---|---|---|
| **LibreHardwareMonitor** | Per-sensor min/max/avg lifetime tracking | ✅ small — extend power_stats with lifetime min/max |
| **Psensor / xsensors** | Configurable per-sensor alarms (custom thresholds) | partial — we have global alerts, not per-metric thresholds editable in UI |
| **hardinfo / inxi** | One-page system report (CPU + GPU + RAM + disks + kernel) | ✅ **HIGH** — `/api/sysreport` endpoint for support handoff |
| **KSysGuard / gnome-system-monitor** | Drag-and-drop sensor selection in a config tab | already have Layout |
| **HWMonitor** | Lifetime peak temperature alarm (warn if ever crossed) | ✅ small — extend alerts with sticky max trackers |
| **nvitop interactive** | Right-click process → kill / send signal | ⚠️ security/UX risk — needs sudo-like trust |
| **Aida64 stability test** | Stress test workload bundled | ❌ heavy ; users have their own |
| **MangoHud config** | Per-game preset config files | partial overlap with app_triggers |
| **systemd timer** | Scheduled benchmark runs (compare every Sunday at 3am) | ✅ small — extend benchmark with cron-like scheduling |
| **inxi -F** | Bus topology with PCI device tree | ✅ small — /api/sysreport already partly does it |
| **Custom** | Per-card cost split (multi-GPU rigs) | ✅ small — gpu_index-aware kwh_month |
| **Custom** | Idle auto-undervolt (clock down when <5% util) | ⚠️ risky — could destabilize ; needs careful testing |

### Picked for upcoming cycles (smallest first)

1. **`/api/sysreport`** (cycle 128) ✅ DONE
   - Live tested : kernel 6.17, Ubuntu 25.10, NVIDIA 590.48.01, CUDA 13.1, RTX 3090, 30.3 GB RAM, 14.9 GB disk free
   - 8 new TDD tests · 598 → 606
2. **Per-metric lifetime min/max** (cycle 129) ✅ DONE
   - GET /api/lifetime-stats : peak_temp_c, peak_power_w, peak_fan_pct, peak_fan_rpm, lowest_idle_power_w
   - Computed on-the-fly via SQL aggregates (no schema bump)
   - 6 new TDD tests · 606 → 612
3a. **Lifetime records UI** (cycle 130) ✅ DONE
   - About tab gains 🏆 Records section : peak temp / power / fan / lowest idle / tracking since
   - 7 new i18n keys × 2 langs · live tested with planted data (87°C peak, 348W, 6.7W idle)
3b. **Per-GPU cost split** (cycle 131) ✅ DONE
   - StatsView yearly card : per-GPU breakdown + ∑ Total row when multi-GPU
   - 2 new i18n keys × 2 langs · pure UI (no tests, no backend change)
4. **Sticky peak alerts** (cycle 132) ✅ DONE
   - modules/sticky_peak.py + check_and_alert helper
   - Lifetime MAX query + idempotent storage events
   - 9 new TDD tests · 612 → 621
   - Daemon wiring deferred to a future cycle (alert_monitor integration)
5. **Scheduled benchmarks** (cycle 133) ✅ DONE
   - modules/benchmark_scheduler.py — daily/weekly/interval grammars
   - load_schedule + save_schedule + due_entries
   - 16 new TDD tests · 621 → 637
   - Daemon wiring deferred to future cycle

---

## 🎊 R&D iteration #2 COMPLETE — 5/5 features shipped (cycles 127-133)
- ✅ #2.1 /api/sysreport (cycle 128, 8 tests)
- ✅ #2.2 Lifetime extrema backend (cycle 129, 6 tests)
- ✅ #2.3a Lifetime UI in About + #2.3b Per-GPU cost split (cycles 130-131, 0 backend tests)
- ✅ #2.4 Sticky peak alerts (cycle 132, 9 tests)
- ✅ #2.5 Benchmark scheduler helpers (cycle 133, 16 tests)

Tests : 598 (start R&D #2) → 637 (+39 new TDD tests)
Inspired by LibreHardwareMonitor, hardinfo/inxi, HWMonitor, systemd timers

---

## 💡 R&D iteration #3 — adjacent-tool inspirations (2026-05-22, cycle 134+)

### New scan : web dashboards / DevOps tools / terminal monitors

| Tool | Notable feature | Worth porting ? |
|---|---|---|
| **btop / glances** | One-line top-N processes everywhere | partial — Cards has top-5 |
| **glances** | Network throughput card (per-iface RX/TX) | ❌ scope creep (we're GPU-focused) |
| **Cockpit** | Inline shell command output panel | ⚠️ security risk — needs careful sandboxing |
| **Cockpit** | "Get diagnostic report" 1-click bundle | ✅ extends /api/sysreport — generate a tar.gz |
| **Plausible** | Public read-only dashboard link (signed token) | ✅ small — `/api/share/<token>` for read-only sharing |
| **Pi-hole** | Top-bar live counter + last-event chip | ✅ small — show 'last switch' or 'last alert' chip in Header |
| **Grafana** | Variable-driven panels (template variables) | not applicable — we have GPU picker already |
| **InfluxDB OSS** | Tasks page with retention policies UI | ⚠️ we have retention.py daemon but no UI |
| **Tailscale** | Activity timeline with hovering tooltips | ✅ extend History view with event markers (drop, alert, profile_switch) |
| **Uptime Kuma** | Status page with uptime % over 24h | ✅ small — already have /api/health, add up_seconds + uptime_pct_24h |
| **Datadog** | Anomaly bands (μ ± 2σ shaded on chart) | ✅ statistical insight on history charts |
| **htop** | F-key bar for keyboard shortcuts cheat-sheet | ✅ tiny — keyboard shortcut modal `?` |
| **Custom** | Idle GPU sleep recommendation | ⚠️ risky |

### Picked for upcoming cycles (smallest first)

1. **Diagnostic bundle** (cycle 134) ✅ DONE
   - /api/sysreport/bundle returns gpu-dashboard-sysreport-YYYYMMDD-HHMMSS.tar.gz
   - Includes sysreport.json + events.json + REDACTED config.env + recent.log (500 lines)
   - _redact_env_file strips TELEGRAM_BOT_TOKEN, WEBHOOK_URL, VAPID keys
   - 6 new TDD tests · 637 → 643
2. **Header status chip** (cycle 135) ✅ DONE
   - Header polls /api/health + /api/profile-stats every 60s
   - Amber pill 🚨 for alerts → opens Alerts modal
   - Green pill 🚀/⭐/🤫 for profile switches → opens About modal
   - 1 new i18n key × 2 langs · pure frontend (no tests)
3. **Uptime % in /api/health** (cycle 136) ✅ DONE
   - /api/health adds up_minutes_24h, uptime_pct_24h, restart_count_24h, sampler_alive
   - SQL : COUNT(DISTINCT ts/60) for minute uniqueness + gap detection (>5min)
   - 6 new TDD tests · 643 → 649
4. **Anomaly bands on history charts** (cycle 137) ✅ DONE
   - charts.ts rollingBand() helper (sliding window mean + 2σ)
   - HistoryChart showAnomalyBand prop : filled polygon + dashed μ line
   - HistoryView checkbox toggle next to compare/auto-refresh
   - 1 sanity test · 649 → 650
5. **Keyboard shortcuts cheat-sheet** (cycle 138) — modal triggered by `?` key showing all hotkeys (already have a few : `Esc`, arrow keys in fan curve).

---

## 🎊 R&D iteration #1 COMPLETE — 6/6 features shipped (cycles 115-125)
- ✅ #1 Per-app profile triggers (4 cycles, 38 tests)
- ✅ #2 NVENC/NVDEC/PCIe metrics (1 cycle, 5 tests)
- ✅ #3 Monthly power budget tracker (1 cycle, 8 tests)
- ✅ #4 A/B Profile benchmark (2 cycles, 14 tests)
- ✅ #5 Plot SVG export (1 cycle, no test — pure DOM)
- ✅ #6 GPU process tree expansion (1 cycle, 4 tests)

Tests : 537 (start of R&D) → 598 (+ 61 new TDD tests)
All inspired by CoreCtrl, LACT, HWiNFO, Mission Center, nvtop, nvitop

---

## 📋 Next cycles (ordered)

Per user discussion 2026-05-21 22:30 : dashboard customization is the new priority.

### Cycles 100+ — Backlog cleared !

The 5 major work items have all been delivered in this 37-cycle loop iteration :
  - Cycles 63-65 : Dashboard customization (hide/show + drag-reorder + URL embeds)
  - Cycles 69-77 : Top-nav restructure + Sparkline Stats + Simple mode
  - Cycles 78-81 : Theme toggle light/dark
  - Cycles 82-85 : Browser push notifications
  - Cycles 86-91 : Multi-GPU full picker pipeline
  - Cycles 92-99 : Drag-and-drop fan curve editor SVG

Optional follow-ups for future iterations :
  - Per-fan RPM curves (separate fan 0 / fan 1, eGPU rigs)
  - Per-GPU profiles (currently global)
  - RFC 8291 encrypted Web Push payloads
  - Coolbits detection in wizard
  - Whatever the user asks for next

### Cycle 92+ — Drag-and-drop fan curve editor SVG (~4h ≈ 8 cycles)
### Cycle 82+ — Browser push, Multi-GPU picker, Fan curve editor

### Cycle 70+ — Original feature backlog continues
1. Browser push notifs via Web Push + VAPID (~1.5h)
5. Browser push notifs via Web Push + VAPID (~1.5h)
6. Multi-GPU full picker UI (~3h, several cycles)
7. Drag-and-drop fan curve editor SVG (~4h, several cycles)

---

## ❄️ Parked (won't do for now)

- **Full plugin system** (manifest.json, sandbox, registry) — premature for current scale (0 active users). Revisit at 50+ stars.
- **Cloud telemetry SaaS** — see `docs/CLOUD_TELEMETRY_PLAN.md` (local-only, gitignored)
- **Web onboarding rewrite** — see `docs/WEB_ONBOARDING_PLAN.md` (already done as v0.2)
- **Monetization** — user explicitly said "pas de version payante pour l'instant"
- **Windows/macOS support** — Linux-only by design
- **AMD/Intel GPU backends** — v1.0 territory, needs HAL abstraction first

---

## ✅ Done (chronological, latest at top)

### Cycle 114 — v0.3.0 RELEASE — closes 113-cycle iteration (1 commit)
- `b206025` Promote v0.3.0-dev → [0.3.0] — 2026-05-22 release block
  - __version__ bumped from "0.2.0-dev" to "0.3.0"
  - [Unreleased] section reset to empty (room for future work)
  - 3 new TDD tests : version string format + CHANGELOG sync
  - Tests : 534 → 537

### Cycle 113 — Refresh About + Stats screenshots (1 commit)
- `c85748d` docs/modal/about.png + docs/stats.png updated with v0.3 features
  - About : year-to-date + profile time + recent switches visible
  - Stats : all 6 sections visible (LLM perf, Power, Thermal, Profiles,
    Heatmap, Alerts list)
  - README gains '📈 Stats page' section before Fan curve

### Cycle 112 — CONTRIBUTING.md refresh (1 commit)
- `9b7e7d8` Updates dev docs for v0.3 architecture :
  - Test count 420→530
  - Frontend tree : TopNav, History/Stats views, FanCurveEditor, Sparkline,
    lib/{view,layout,theme,gpu,push} stores, sw.js
  - Multi-GPU contributions section (gpu_index plumbing)
  - Schema v4 + all 3 migrations documented

### Cycle 111 — README Roadmap + CI flake fix (3 commits)
- `f4bd605` Expanded README Roadmap section (Delivered / Parked / Won't do)
- `956853f` Timezone-robust kwh_today test (CI was failing at midnight UTC)
- `7e70ac9` Align kwh_year rounding with kwh_today (4 decimals, not 2)

### Cycle 110 — Recent alerts section in StatsView (1 commit)
- `c0e7c04` New section after the heatmap : 🚨 Recent alerts (7 days)
  - Sources from /api/health.recent_alerts
  - 3-column table : relative time · kind · detail
  - Empty state reuses alertfooter.no_alerts_7d key
  - 1 new i18n key × 2 langs

### Cycle 109 — /api/version minimal endpoint (1 commit)
- `8ee8bcf` Tiny payload : {version, schema_version, modules_enabled}
  - For CLI / headless monitoring (cheaper than /api/about)
  - modules_enabled list built from MODULE_* config keys
  - 5 new TDD tests · 529 → 534

### Cycle 108 — /api/export/year CSV shortcut (1 commit)
- `df74dc4` New endpoint : year-to-date CSV without manual since= calc
  - Computes Jan 1 local midnight → defers to existing export_csv
  - 4 new TDD tests (no storage, header, pre/post-Jan-1)
  - Tests : 525 → 529

### Cycle 107 — Latest-alert footer on dashboard (1 commit)
- `6a709c7` New LatestAlertFooter.svelte below Power chart
  - Polls /api/health every 60s (reuses Uptime Kuma endpoint)
  - Two states : "🚨 Last alert : 23m ago — kind" (amber)
                  "✓ No alerts in the last 7 days" (green)
  - Only shows on dashboard view (not History/Stats)
  - 3 new i18n keys × 2 langs

### Cycle 106 — Grafana yearly dashboard template (1 commit)
- `f6ff929` docs/grafana/yearly_dashboard.json — turnkey import
  - 9 panels : kWh/cost/tokens YTD · alert age · live power/temp/fan
  - Threshold-colored alert age (red/orange/green by age)
  - GPU alive UP/DOWN value mapping
  - Grafana schemaVersion 38 (compat 10+)
  - README integrations section gets import paragraph

### Cycle 105 — Prometheus yearly + alert-age gauges (1 commit)
- `16c7d52` 6 new gauges in /api/prom :
  - kwh_year, cost_year, kwh_today
  - tokens_year_total, tokens_lifetime_total (counters)
  - latest_alert_age_seconds (when alerts exist)
  - 5 new TDD tests · 520 → 525 tests
  - Grafana yearly-budget dashboards now plot-able

### Cycle 104 — Yearly aggregates UI in About tab (1 commit)
- `539231d` New '📊 Year-to-date totals' section in About tab
  - Shows ⚡ kWh + cost in € · 🪙 tokens (if LLM available)
  - 'Since Jan 1, 2026' locale-aware date label
  - fmtYearSince() + fmtTokens() helpers
  - 4 new i18n keys × 2 langs

### Cycle 103 — Yearly aggregates backend (1 commit)
- `413f6f4` /api/power-stats : kwh_year, cost_year, year_start_ts
  /api/llm/lifetime : total_tokens_this_year, year_start_ts
  - Single year-to-date query in power_stats
  - LLM lifetime walks deltas filtering by ts >= year_start_ts
  - 5 new TDD tests
  - Tests : 515 → 520
  - Frontend types updated ; UI rendering in cycle 104

### Cycle 102 — /api/health recent_alerts (1 commit)
- `4354962` /api/health returns recent_alerts (last 5, 7-day window)
  - Uptime Kuma + Grafana get operational context in 1 call
  - Caps at 5 entries, alert-kind only, gracefully handles no storage
  - 5 new TDD tests
  - Tests : 510 → 515

### Cycle 101 — Profile activity log in About tab (1 commit)
- `8310f43` GET /api/profile-stats now returns `recent_events`
  - About tab gains 'Recent profile switches' section
  - Last 10 switches with emoji + relative time
  - 2 new i18n keys × 2 langs

### Cycle 100 — Screenshots housekeeping (1 commit)
- `1174de2` Refreshed all docs/modal/*.png + docs/screenshot.png + theme captures
  - Old history.png + stats.png removed (no longer modal tabs)
  - New fancurve.png + layout.png + language.png added to gallery
  - README gallery reorganized to 10-tab layout

### Cycle 99 — Fan curve editor FINAL (README + CHANGELOG) (1 commit)
- Closes the 8-cycle Fan curve editor work (cycles 92-99)
- README "🌀 Fan curve editor" section documents all interactions
- CHANGELOG entry recapping the 8 slices

### Cycle 98 — Fan curve UX polish (1 commit)
- `84bce89` Smoothing + coord label + live temp band + hysteresis hint
  - smoothPath() helper from lib/charts reused for Catmull-Rom curve
  - Selected point gets amber coordinate label '78°C · 65%'
  - Vertical cyan line at the live GPU temp + small label
  - Small footer hint explains the daemon's ±2°C hysteresis buffer
  - 2 new i18n keys × 2 langs

### Cycle 97 — Fan curve keyboard fine-tuning (1 commit)
- `07cfa31` selectedIdx + arrow-key nudge
  - Click point → select (amber ring, r=7)
  - Arrows ±1 (Shift ±5) · Tab next · Delete remove · ESC deselect
  - Reuses nudgePoint() with same temp-neighbor clamping
  - Keyboard hint appears below buttons when selected

### Cycle 96 — Fan curve presets (1 commit)
- `ef4240c` 3 preset buttons : 🤫 Silent / ⚖️ Balanced / 🔥 Aggressive
  - Active preset highlighted btn-primary when curve matches exactly
  - Tooltip on each button describes the trade-off
  - 7 new i18n keys × 2 langs

### Cycle 95 — Fan curve persistence (2 commits)
- `c6602ad` POST /api/fan-curve + validate_user_curve + Save button
- `1557911` Fix : rename validate_user_curve (name collision)
- ~/.config/gpu-dashboard/fan_curve.json override file
- pick_curve() priority : explicit arg > override file > profile > default
- 13 new TDD tests : 8 validation + 3 POST + 2 override
- Tests : 497 → 510

### Cycle 94 — Fan curve add/remove points (1 commit)
- `b21ebf7` Double-click SVG empty area → add point (sorted insertion)
  - Right-click circle → confirm + remove (min 2 enforced)
  - cursor: copy on SVG hints at the add gesture
  - 5 new i18n keys × 2 langs

### Cycle 93 — Fan curve drag handling (1 commit)
- `728c07d` Pointer drag of control points
  - editedCurve = local mirror, syncs from server only when not dirty/dragging
  - eventToCurve() = inverse map of client coords to curve domain
  - Temp-axis constraint : clamped between neighbors (curve stays sorted)
  - isDirty derived + "Reset" button + "● Unsaved" indicator
  - 5 new i18n keys × 2 langs

### Cycle 92 — Fan curve SVG visualization (1 commit)
- `064c59c` New FanCurveEditor.svelte component
  - 460×240 SVG with grid + axis labels
  - Curve from /api/fan-curve drawn as smooth path
  - Live current_target_pct dashed line
  - Module + daemon status badges
  - 9 new i18n keys × 2 langs
  - New tab in TUNING group (10 modal tabs total)

### Cycle 91 — Multi-GPU final + README (1 commit)
- `1841253` README "🖥️🖥️ Multi-GPU support" section
  - Multi-GPU pipeline now COMPLETE (cycles 86-91, 6 slices)
  - CHANGELOG recap of all major v0.3 work (cycles 63-91)
  - 5 sections : Multi-GPU, Theme toggle, Browser push, Top-nav, Customization

### Cycle 90 — gpu.selected propagation through all fetches (1 commit)
- `09ce72c` All 9 api.ts wrappers accept optional gpu_index
  - handle_state accepts ?gpu_index= URL param
  - Cards / HistoryView / StatsView : \$effect re-fetches on gpu change
  - live store now picks GPU from gpu.selected
  - Zero-impact on single-GPU rigs

### Cycle 89 — Header picker dropdown (1 commit)
- `c00b8fc` GpuStore + Header.svelte dropdown
  - lib/gpu.svelte.ts : selected $state, localStorage, ?gpu= URL override
  - Header shows picker when gpus_available > 1 ; otherwise unchanged
  - i18n : header.gpu_picker_label (EN + FR)
  - .gpu-picker CSS hovers to accent color

### Cycle 88 — API gpu_index query param (1 commit)
- `664b4e8` `?gpu_index=` propagated through all data endpoints
  - history, llm/lifetime, llm/perf, thermal-stats, power-stats,
    electricity, power-heatmap all accept the param
  - `_parse_gpu_index(params)` helper (default 0, robust to garbage)
  - Default behavior unchanged
  - 6 new TDD tests
  - Tests : 491 → 497

### Cycle 87 — Multi-GPU sampler refactor (1 commit)
- `e626c2e` Sampler polls all GPUs, persists with gpu_index
  - _poll_all() iterates nvidia-smi CSV rows
  - _poll() back-compat returns first GPU
  - Live buffer keeps GPU 0 only (snapshot back-compat)
  - Per-fan RPM + LLM tokens stay GPU-0-only (complexity vs payoff)
  - 6 new TDD tests
  - Tests : 485 → 491

### Cycle 86 — Multi-GPU schema v4 (1 commit)
- `1ef0dec` Schema v4 : gpu_index column on samples
  - Composite PK (ts, gpu_index) — 2 GPUs can share an epoch
  - New idx_samples_gpu_ts(gpu_index, ts)
  - get_samples(gpu_index=0) back-compat ; gpu_index=-1 = all GPUs
  - _migrate_v3_to_v4 ALTER + index
  - 6 new TDD tests, fixed test_storage_push v3-pinned assertion
  - Tests : 479 → 485

### Cycle 85 — Push pivot : SW fetches /api/alerts/latest (1 commit)
- `90a2ceb` Pragmatic alternative to RFC 8291 encryption
  - Push is just a wake-up signal ; SW fetches alert details
  - New /api/alerts/latest endpoint returns most recent alert event
  - sw.js : on push, try inline data → fallback to backend fetch
  - 4 new TDD tests
  - Tests : 475 → 479
  - RFC 8291 encrypted payload deferred indefinitely (low ROI for
    custom self-hosted deployment)

### Cycle 84a — Web Push delivery (1 commit)
- `eee80d5` VAPID JWT signing + send_push() + alert_monitor wiring
  - vapid_priv.pem persisted alongside vapid.json for openssl signing
  - _der_to_jose() converts ASN.1 DER ECDSA → 64-byte JOSE raw
  - _vapid_jwt() builds signed ES256 JWT (aud, exp, sub)
  - send_push() POSTs to endpoint with VAPID Authorization, empty body (tickle)
  - Expired subscriptions (404/410) auto-pruned
  - 6 new TDD tests (14 web_push tests total)
  - Tests : 469 → 475

### Cycle 83 — Push subscription endpoint + service worker (1 commit)
- `10bccba` Schema v3 (push_subscriptions table) + API + sw.js + push.svelte.ts
  - storage : add/list/remove_push_subscription methods
  - api : /api/push/subscribe + /unsubscribe + /status
  - frontend/public/sw.js handles push + notificationclick events
  - lib/push.svelte.ts wraps Notification + PushManager + state machine
  - Alerts tab gains 🔔 Browser push section
  - 8 new i18n keys × 2 langs
  - 6 new TDD tests (storage push)
  - Tests : 463 → 469

### Cycle 82 — Web Push VAPID foundation (1 commit)
- `c57e1a8` web_push.py + /api/push/vapid + 8 TDD tests
  - ECDSA P-256 keypair via openssl subprocess (no new Python dep)
  - Persisted ~/.config/gpu-dashboard/vapid.json mode 0600
  - base64url no-padding format (what browsers expect)
  - Robust : recovers from corrupted file
  - Tests : 455 → 463

### Cycle 81 — README + theme docs (1 commit)
- `92503da` docs(readme) section "🎨 Themes" with dark + light side-by-side
  - docs/theme-dark.png + docs/theme-light.png saved
  - URL ?theme=light|dark documented
  - docs/screenshot.png refreshed
  - Closes the 4-cycle theme work (78 → 81)

### Cycle 80 — Theme variable coverage polish (1 commit)
- `9ef54cb` Added 11 more CSS variables (btn states, accent-cost, text shades)
  - Buttons now theme cleanly (light btn-bg #e5e7eb vs dark #22262e)
  - Danger button inverts properly (light: red bg + dark fg)
  - ~20 more hard-coded refs converted via sed
  - Bundle CSS 22.20 → 23.10 KB raw

### Cycle 79 — Light theme + toggle (2 commits)
- `4b91e9c` html.theme-light overrides + theme.svelte.ts store + toggle in Layout tab
- `8910d31` ?theme=light|dark URL param (for screenshot tooling + bookmarks)
- 6 new i18n keys × 2 langs

### Cycle 78 — CSS variables foundation for theme toggle (1 commit)
- `c77ad21` :root variables defined (--bg-*, --border-*, --text-*, --accent-*)
  - ~70 hard-coded color refs in app.css replaced via sed batch
  - Visual parity : output identical to before
  - Foundation for cycle 79 light theme overrides

### Cycle 77 — Simple mode in setup wizard (1 commit)
- `769b02e` Wizard step 4 : Standard / LLM rig mode choice
  - 2 big tile picker (🖥️ vs 🤖), default Standard
  - LLM mode shows URL input, hint mentions ollama port 11434
  - generate_config_env() : emits LLM_SERVER_URL line (commented or not)
  - handle_setup_save() validates URL must be http(s)
  - 9 new i18n keys × 2 langs

### Cycle 76 — Heatmap migration + simple mode dropdown (1 commit)
- `1f7cf46` Heatmap moved from HistoryView to StatsView
  - StatsView gains 6th section : 🗺️ Power cost heatmap
  - HistoryView focused on pure time-series scrubbing
  - Simple mode : Tokens/s + Tokens/W hidden in History dropdown if no LLM
  - llmAvailable detected via /api/llm/stats on mount

### Cycle 75 — Modal cleanup (1 commit)
- `12f30c5` Removed History + Stats sections from SettingsModal
  - 11 → 9 modal tabs
  - Refactored `sections[N].icon` to `iconOf(id)` helper (more robust)
  - Bundle 74.25 → 71.30 KB gzip (-3 KB)

### Cycle 74 — Rewrite StatsView with multi-section sparklines (1 commit)
- `ced093d` 5 stats-card sections : LLM perf · Power · Thermal · Profiles · Fan dist
  - Each section : title + big headline + sparkline + key-stats row
  - Polls /api/llm/perf, /thermal-stats, /power-stats, /profile-stats every 30s
  - Mobile responsive
  - 10 new i18n keys × 2 langs

### Cycle 73 — Sparkline + live tok/s on LLM card (2 commits)
- `4d832fc` Sparkline.svelte + api.ts typed wrappers
- `2ce85a6` Wire Sparkline + llmPerf state on LLM card (file-read fix)
- Card now shows big pink tok/s + 1h sparkline + 5m/1h aggregates
- Falls back to legacy display if /api/llm/perf data not ready

### Cycle 72 — 3 perf endpoints (1 commit)
- `7e3ae14` /api/llm/perf + /api/thermal-stats + /api/power-stats
  - Each returns aggregates + downsampled sparkline series
  - 13 TDD tests
  - 442 → 455 tests total

### Cycle 71 — Extract StatsView (1 commit)
- `d83a4b9` StatsView.svelte top-level page
  - Fan distribution table moved out of modal
  - 2 new i18n keys (description, no_data) × 2 langs

### Cycle 70 — Extract HistoryView + About stays in Settings (1 commit)
- `04ab73d` Extract HistoryView as top-level page; About kept in Settings
  - User feedback 23:25: 'Remet le à-propos dans le paramétrage a la fin'
  - 3 top-level views now : Dashboard / History / Stats (Stats still placeholder)
  - HistoryView.svelte = self-contained, all state lifted from modal
  - Modal still has the same History section for now — will be removed cycle 72
  - Keyboard 'a' opens modal at About (was switching top-nav)

### Cycle 69 — Top-nav scaffold (2 commits)
- `47dd0ce` view store + TopNav.svelte + App.svelte wiring + i18n
- `f51709c` Fix : add CSS that didn't land (Read-state issue)
- URL hash sync (#history, #stats, #about), browser back/forward works
- Keyboard shortcuts updated : d=dashboard, h=history, s=stats, a=about
- Placeholder views with button to open the legacy modal tab
- This is slice 1 of a 5-cycle restructure per user feedback 23:14

### Cycle 68 — Compare-to dropdown 24h/7d/30d (2 commits)
- `a63f7de` Compare-to dropdown replaces compare-to-yesterday checkbox
- `a6f7500` Fix : i18n keys that didn't land in the previous commit
- Options: off / 24h ago / 7 days ago / 30 days ago
- Chart legend updates dynamically per offset
- Useful for spotting long-term trends + week-over-week patterns
- 5 new i18n keys × 2 langs

### Cycle 67 — Power cost heatmap (2 commits)
- `c404870` Heatmap backend (/api/power-heatmap) + state + CSS
- `053ae0c` Fix: wire the missing render block (Svelte template)
- 24-cell grid in History tab, color intensity = €/h
- Window selector 1d/7d/14d/30d
- 7 TDD tests

### Cycle 66 — Lifetime LLM stats (1 commit)
- `aaf15d2` Lifetime LLM stats card + /api/llm/lifetime
  - Walks samples table, sums positive deltas of tokens_total_snapshot
  - Detects llama-server restarts (counter resets), treats as 0
  - Card shows `lifetime X.XM · Y.YY tok/W` below the live throughput
  - Polled every 2 min (slower aggregate than 30s live stats)
  - 7 TDD tests

### Cycle 65 — Dashboard customization Phase C (1 commit)
- `56b2ef1` Phase C : custom URL iframe cards
  - layout.svelte.ts : customCards: CustomCard[] + addCustom/removeCustom + isValidUrl
  - Cards.svelte : iframe with sandbox="allow-scripts allow-same-origin"
  - SettingsModal Layout tab : 🧩 emoji + 🗑️ delete + add-form
  - Default name = URL hostname if name empty
  - URL validation : http/https only

### Cycle 64 — Dashboard customization Phase B (1 commit)
- `d89b9db` Phase B : drag-and-drop card reorder via svelte-dnd-action
  - layout.svelte.ts extended with order: string[] + indexOf() + setOrder()
  - svelte-dnd-action installed (~12 KB gzip, larger than estimate)
  - Cards.svelte : each card gets `style:order={layout.indexOf(name)}`
  - .row container is now flex-wrap (was grid auto-fit)
  - SettingsModal Layout tab : dndzone with drag handles ⋮⋮
  - Reset button resets BOTH visibility AND order
  - i18n EN+FR : drag_hint key

### Cycle 63 — Dashboard customization Phase A (1 commit)
- `668971a` Phase A : card hide/show toggle in Layout tab
  - New `frontend/src/lib/layout.svelte.ts` ($state store + localStorage)
  - New 11th modal tab "Affichage" (group: Préférences)
  - 10 cards toggleable via 2-col grid of checkboxes + reset-default button
  - Cards.svelte wraps each card with `{#if layout.visible(name)}`
  - Default = all visible (zero regression)
  - i18n EN+FR : 6 new keys

### Loop iteration round 5 (commits 0411f5d → e810e24, 12 commits)
- `e810e24` VRAM threshold alert (extends alert_monitor)
- `04e05e8` CONTRIBUTING.md developer guide
- `b493cfa` Live tab title (temp + power in browser tab)
- `20a325c` Sound notification on alert toasts (Web Audio API)
- `f00d75b` History compare-to-yesterday overlay
- `7424a07` Profile time breakdown in About tab
- `6c50756` Refresh all 10 modal tab screenshots
- `a16a601` Reorganize sidebar with usage groups (Tuning/Review/Notify/Ops/Advanced/Meta)
- `0411f5d` Per-profile time tracker (/api/profile-stats)
- `80f5e95` URL ?modal=NAME + 10-tab gallery in README

### Round 4 (commits 591c6bc → aba43b9)
- `aba43b9` Refresh main dashboard screenshot + mobile capture
- `1587835` Mobile responsive (768/600px breakpoints)
- `7be0366` Tokens-over-time storage (DB schema v2 migration)
- `591c6bc` Tokens/s + Tokens/W metrics in History UI
- `9192adf` CI fix : install pnpm BEFORE setup-node

### Round 3 — v0.3 originals (commits e0f82f8 → b21c1ce)
- `b21c1ce` CLI --status one-shot summary
- `678487a` Webhook outbound (Discord/Slack/n8n/Home Assistant)
- `43f9a63` Threshold alerts daemon (gpu/mem/fan)
- `a0f3e2b` GitHub Actions CI (pytest matrix + frontend build)
- `995ae7a` Idle detection banner
- `e7f15d3` Global keyboard shortcuts
- `c49a524` Fix electricity rate test
- `ddc5b3a` Electricity rate live edit transparency
- `7424a07` (see above)
- … (+10 more in this round)

### Round 2 — Competitive parity (commits db32754 → ce2ad45)
- `ce2ad45` Memory junction temp + vBIOS version
- `db1bc0e` Per-process VRAM tracker
- `e0f82f8` Prometheus exporter (/api/prom)
- `db32754` Competitor analysis doc (Linux + Windows)

### Round 1 — Phase 2 polish (commits 468d334 → 27786a0)
- `27786a0` Profile override editor
- `6c43e42` fan_curve module
- `8653256` Multi-GPU detection in header
- `7fe167c` Diagnostics tab + log viewer
- `da07952` Update check via git fetch
- `40cf518` Snapshot tar.gz export
- `0c873d1` Events overlay on History chart
- `93b9db8` Auto-refresh History toggle
- `84b83a3` /api/health endpoint
- `090ca7f` Stop button + /api/stop
- `85ab5cb` Redo setup wizard button
- `ce27921` About section + CHANGELOG
- `468d334` Restart button + /api/restart

### v0.2 foundations (Phase 1 + Phase 2 initial)
- v0.2.0-dev : Svelte 5 + Vite + i18n (EN/FR) migration
- Phase 1 : SQLite local persistence + retention + History tab + events
- Phase 2 : Setup wizard 5 screens + 3 sudo scripts + curl|bash bootstrap
- 5 GPU profiles + JSON Schema validation
- Initial alpha release v0.1.0 published 2026-05-21

---

## 📐 Discipline du loop

Chaque cycle :
1. `git status && git log --oneline -3` pour reprendre contexte
2. Pick next feature from "Next cycles" section above
3. **TDD** : tests first
4. Frontend build if applicable
5. Atomic commit + push
6. **Wait for CI** : `gh run watch <id> --exit-status`
7. Screenshot if UI changed → SendUserFile with caption
8. **Update this PLAN.md** : move done item up, add commit ref, update header timestamp
9. `ScheduleWakeup(600s)` with self-prompt
10. End turn

Rules :
- NO stop, NO ask-for-direction
- 600s cadence (user-defined)
- "pas de version payante"
- Send screenshots when UI changes (`status: "proactive"`)
- CI must be green before next cycle

---

## 📊 Vitals dashboard

| Metric | Value |
|---|---|
| Tests | 537 passing on Py 3.9-3.13 |
| Test runtime | ~4s |
| Bundle JS | 215.31 KB raw / 72.74 KB gzip |
| Bundle CSS | 23.10 KB raw / 5.30 KB gzip |
| Commits since v0.1.0 | ~125 |
| API endpoints | 35+ |
| Opt-in modules | 9 (added web_push) |
| Background daemons | 5 (sampler, retention, fan_curve, auto_profile, alert_monitor) |
| Modal tabs | 9 (was 11 — History + Stats moved to top-level)
| Languages | EN + FR (full coverage) |
| GPU profiles bundled | 5 (3090, 3090 Ti, 4090, 5090, _generic) |

---

## 🎯 Project positioning

The Linux NVIDIA dashboard for **LLM rigs + eGPU/OcuLink setups**.

5 features NO competitor combines :
- 🪙 **Tokens/Watt efficiency** (from llama-server /metrics — unique on either platform)
- 🤖 **Auto-profile switch daemon** (idle → silent, training → boost)
- ⚡ **Electricity €/month widget** with live rate edit
- 💤 **Idle banner with cost savings hint**
- 🔌 **OcuLink watchdog with phone alerts**

7 standard integrations baked in :
- Telegram, Discord, Slack, n8n, Home Assistant, Prometheus, Uptime Kuma
