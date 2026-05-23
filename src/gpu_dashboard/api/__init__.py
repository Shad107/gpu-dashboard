"""HTTP API handlers package.

This package is being split out from a single 4800-line `api.py` into
logical submodules. During the migration :

  - `_core.py` contains the un-migrated handlers.
  - Each new submodule (auth.py, integrations.py, ...) extracts a coherent
    group of handlers. They are added to the re-export below as they land.

External callers (server.py, tests, modules) continue to do
`from gpu_dashboard import api ; api.handle_X(...)` exactly as before —
the names are re-exported here.
"""

# Migrated submodules — names here take precedence over _core re-exports
# below (they're defined after, so they win the second pass).
from .auth import (  # noqa: F401
    handle_auth_tokens_list,
    handle_auth_token_create,
    handle_auth_token_delete,
    handle_auth_share_create,
    handle_audit_log,
)
from .integrations import (  # noqa: F401
    handle_ical_feed,
    handle_weekly_report,
    handle_service_discovery,
    handle_watchdog_status,
    handle_watchdog_enable,
    handle_watchdog_disable,
    handle_healthz,
    handle_readyz,
    handle_vector_db,
    handle_hf_card,
    handle_hf_janitor,
    handle_vfio_status,
)
from .llm import (  # noqa: F401
    handle_llm_lifetime,
    handle_llm_perf,
    handle_llm_stats,
    handle_llamabench_status,
    handle_jupyter_kernels,
    handle_snapshot_at,
)
from .power import (  # noqa: F401
    handle_set_power_limit,
    handle_set_offsets,
    handle_profile_stats,
    handle_auto_profile_status,
    handle_power_profiles_list,
    handle_power_profile_apply,
)
from .cost import (  # noqa: F401
    handle_power_stats,
    handle_power_heatmap,
    handle_electricity_config,
    handle_electricity,
)
from .diagnostics import (  # noqa: F401
    handle_journal_tail,
    handle_drift_check, detect_drift_on_startup,
    handle_bar,
    handle_ecc_health,
    handle_idle_audit,
    handle_clock_events,
    handle_thermal_stats,
    handle_thermal_coach,
    handle_sys_context,
    handle_cgroup_power,
    handle_prom, handle_prometheus_metrics,
    handle_alertmanager_rules, build_alertmanager_rules_yaml,
    handle_logs,
)
from .alerts import (  # noqa: F401
    handle_alerts_config_get, handle_alerts_config_post,
    handle_alerts_latest,
    handle_notif_channels_list, handle_notif_channel_save, handle_notif_channel_test,
    handle_heartbeat_list, handle_heartbeat_ping, handle_heartbeat_config,
)
from .ops import (  # noqa: F401
    handle_processes,
    handle_health,
    handle_sysreport, handle_sysreport_bundle,
    handle_version, handle_about,
    handle_stop, handle_restart,
    handle_update_check, handle_update_pull,
    handle_snapshot,
    handle_modules_list, handle_modules_toggle,
    handle_setup_detect, handle_setup_recheck, handle_setup_save,
)
from .state import (  # noqa: F401
    handle_state, handle_history, handle_events,
    handle_export, handle_export_year,
    handle_lifetime_stats,
)
from .tuning import (  # noqa: F401
    handle_benchmark_run,
    handle_app_triggers_get, handle_app_triggers_post,
    handle_profile_save,
    handle_fan_curve_get, handle_fan_curve_post,
    handle_push_vapid, handle_push_subscribe, handle_push_unsubscribe, handle_push_status,
    handle_alerts_test,
)
from .integrations import (  # noqa: F401,F811
    handle_badge, handle_tldr,
    handle_ups_status, handle_influxdb_status,
)

# Public handlers + builders from the legacy monolith.
from ._core import *  # noqa: F401,F403

# Re-import migrated symbols AFTER the wildcard so they're not shadowed.
from .auth import (  # noqa: F401,F811
    handle_auth_tokens_list,
    handle_auth_token_create,
    handle_auth_token_delete,
    handle_auth_share_create,
    handle_audit_log,
)
from .integrations import (  # noqa: F401,F811
    handle_ical_feed,
    handle_weekly_report,
    handle_service_discovery,
    handle_watchdog_status,
    handle_watchdog_enable,
    handle_watchdog_disable,
    handle_healthz,
    handle_readyz,
    handle_vector_db,
    handle_hf_card,
    handle_hf_janitor,
    handle_vfio_status,
)
from .llm import (  # noqa: F401,F811
    handle_llm_lifetime,
    handle_llm_perf,
    handle_llm_stats,
    handle_llamabench_status,
    handle_jupyter_kernels,
    handle_snapshot_at,
)
from .power import (  # noqa: F401,F811
    handle_set_power_limit,
    handle_set_offsets,
    handle_profile_stats,
    handle_auto_profile_status,
    handle_power_profiles_list,
    handle_power_profile_apply,
)
from .cost import (  # noqa: F401,F811
    handle_power_stats,
    handle_power_heatmap,
    handle_electricity_config,
    handle_electricity,
)
from .diagnostics import (  # noqa: F401,F811
    handle_journal_tail,
    handle_drift_check, detect_drift_on_startup,
    handle_bar,
    handle_ecc_health,
    handle_idle_audit,
    handle_clock_events,
    handle_thermal_stats,
    handle_thermal_coach,
    handle_sys_context,
    handle_cgroup_power,
    handle_prom, handle_prometheus_metrics,
    handle_alertmanager_rules, build_alertmanager_rules_yaml,
    handle_logs,
)
from .diagnostics import (  # noqa: F401,F811
    _JOURNAL_FILTERS, _diff_snapshots,
    _normalize_cgroup, _linear_fit, _r_squared,
    _LAST_CPU_LINE, _LAST_CPU_TS, _LAST_VMSTAT, _LAST_VMSTAT_TS,
)
from .alerts import (  # noqa: F401,F811
    handle_alerts_config_get, handle_alerts_config_post,
    handle_alerts_latest,
    handle_notif_channels_list, handle_notif_channel_save, handle_notif_channel_test,
    handle_heartbeat_list, handle_heartbeat_ping, handle_heartbeat_config,
)
from .alerts import (  # noqa: F401,F811
    _alert_consecutive_to_for, _load_heartbeats, _save_heartbeats,
)
from .ops import (  # noqa: F401,F811
    handle_processes,
    handle_health,
    handle_sysreport, handle_sysreport_bundle,
    handle_version, handle_about,
    handle_stop, handle_restart,
    handle_update_check, handle_update_pull,
    handle_snapshot,
    handle_modules_list, handle_modules_toggle,
    handle_setup_detect, handle_setup_recheck, handle_setup_save,
)
from .ops import _git, _redact_env_file, _REDACT_KEYS  # noqa: F401,F811
from .state import (  # noqa: F401,F811
    handle_state, handle_history, handle_events,
    handle_export, handle_export_year,
    handle_lifetime_stats,
)
from .tuning import (  # noqa: F401,F811
    handle_benchmark_run,
    handle_app_triggers_get, handle_app_triggers_post,
    handle_profile_save,
    handle_fan_curve_get, handle_fan_curve_post,
    handle_push_vapid, handle_push_subscribe, handle_push_unsubscribe, handle_push_status,
    handle_alerts_test,
)
from .integrations import (  # noqa: F401,F811
    handle_badge, handle_tldr,
    handle_ups_status, handle_influxdb_status,
)
# Helpers moved to integrations.py with badge/tldr
from .integrations import (  # noqa: F401,F811
    _badge_svg, _BADGE_TEMP_COLORS,
    _color, _temp_color, _spark, _ANSI,
)
# Helpers moved to submodules — re-export for tests
from .llm import (  # noqa: F401,F811
    _parse_llamacpp_metrics,
    _tokens_per_watt,
    _llm_model_served,
)
from .power import _POWER_PROFILES, _read_power_profile  # noqa: F401,F811

# Private helpers used by tests (and by future submodules during migration).
# `from X import *` skips underscore-prefixed names, so we list these explicitly.
from ._core import (  # noqa: F401
    Response,
    # Snapshot + GPU detection
    _gpu_card_snapshot,
    _gpus_available,
    # Per-card derived state
    _per_fan_state,
    _tuning_state,
    _watchdog_state,
    _services_state,
    _fan_distribution,
    _parse_gpu_index,
    _read_cmdline,
    # Linear regression + R² (thermal coach) — moved to api.diagnostics
    # (kept for compat: import from there above)
    # Drift detector

    # Heartbeats (deadman) — moved to api.alerts (above)
    # cgroup attribution — moved to api.diagnostics (above)
    # Alert escalation — moved to api.alerts (above)
    # SVG badge generator (R&D #10.7) — moved to api.integrations (above)
    # ANSI/tldr endpoint (R&D #10.6) — moved to api.integrations (above)
    # Module-level CPU/vmstat caches — moved to api.diagnostics (above)
)

from .embed import handle_embed  # noqa: F401,F811
from .ci_tag import handle_ci_tag  # noqa: F401,F811
from .wall_meter import handle_wall_meter  # noqa: F401,F811
from .rules import handle_rules_list, handle_rules_save, handle_rules_evaluate  # noqa: F401,F811
from .peers import handle_peers  # noqa: F401,F811
from .airgap import handle_airgap_status, handle_airgap_audit  # noqa: F401,F811
from .disk_health import handle_disk_health  # noqa: F401,F811
from .best_gpu import handle_best_gpu, handle_best_gpu_env  # noqa: F401,F811
from .vram_quota import handle_vram_quota_status, handle_vram_quota_save, handle_vram_quota_evaluate  # noqa: F401,F811
from .hot_gpu_wizard import handle_hot_gpu_wizard  # noqa: F401,F811
from .carbon import handle_carbon  # noqa: F401,F811
from .xid import handle_xid, handle_xid_decode  # noqa: F401,F811
from .hot_swap import handle_hot_swap_status, handle_hot_swap_evaluate  # noqa: F401,F811
from .inference_cost import handle_inference_cost  # noqa: F401,F811
from .lab_usage import handle_lab_usage_live  # noqa: F401,F811
from .boot_profile import handle_boot_profile_status, handle_boot_profile_save, handle_boot_profile_clear, handle_boot_profile_apply_now  # noqa: F401,F811
from .tariff import handle_tariff_status, handle_tariff_estimate, handle_tariff_cheapest  # noqa: F401,F811
from .hf_dedup import handle_hf_dedup_plan, handle_hf_dedup_execute  # noqa: F401,F811
from .discord_rpc import handle_discord_rpc_status  # noqa: F401,F811
from .noc import handle_noc  # noqa: F401,F811
from .dr_bundle import handle_dr_bundle_list, handle_dr_bundle_create, handle_dr_bundle_delete  # noqa: F401,F811
from .lm_studio import handle_lm_studio_inventory  # noqa: F401,F811
from .driver_vault import handle_driver_vault_status, handle_driver_vault_stash, handle_driver_vault_rollback_script  # noqa: F401,F811
from .idle_probe import handle_idle_txt, handle_idle_json  # noqa: F401,F811
from .ecc_remap import handle_ecc_remap_status, handle_ecc_remap_record, handle_ecc_remap_rma_csv  # noqa: F401,F811
from .tdp_auto import handle_tdp_auto_status, handle_tdp_auto_save, handle_tdp_auto_evaluate, handle_tdp_auto_preview  # noqa: F401,F811
from .llm_swap import handle_llm_swap_status, handle_llm_swap_pin, handle_llm_swap_suggest  # noqa: F401,F811
from .cuda_advisor import handle_cuda_advisor_status  # noqa: F401,F811
from .nvme_swap import handle_nvme_swap_status  # noqa: F401,F811
from .cuda_matrix import handle_cuda_matrix_status  # noqa: F401,F811
from .pcie_histogram import handle_pcie_histogram_status  # noqa: F401,F811
from .throttle_cause import handle_throttle_cause_status  # noqa: F401,F811
from .mps_health import handle_mps_health_status  # noqa: F401,F811
from .process_nice import handle_process_nice_status  # noqa: F401,F811
from .warmup_profile import handle_warmup_profile_status, handle_warmup_profile_probe  # noqa: F401,F811
from .suspend_guard import handle_suspend_guard_status  # noqa: F401,F811
from .container_audit import handle_container_audit_status  # noqa: F401,F811
from .ups_runtime import handle_ups_runtime_status  # noqa: F401,F811
from .vbios_drift import handle_vbios_drift_status, handle_vbios_drift_rebaseline  # noqa: F401,F811
from .pstate_audit import handle_pstate_audit_status  # noqa: F401,F811
from .persistence_mode import handle_persistence_mode_status  # noqa: F401,F811
from .gsp_status import handle_gsp_status  # noqa: F401,F811
from .sd_cache_janitor import handle_sd_cache_janitor_status  # noqa: F401,F811
from .vram_leak import handle_vram_leak_status  # noqa: F401,F811
from .gpu_reset import handle_gpu_reset_status  # noqa: F401,F811
from .cuda_inventory import handle_cuda_inventory_status  # noqa: F401,F811
from .driver_flavor import handle_driver_flavor_status  # noqa: F401,F811
from .proc_deep_state import handle_proc_deep_state_status  # noqa: F401,F811
from .pcie_aspm import handle_pcie_aspm_status  # noqa: F401,F811
from .fs_mount_audit import handle_fs_mount_audit_status  # noqa: F401,F811
from .batch_advisor import handle_batch_advisor_status  # noqa: F401,F811
from .dkms_status import handle_dkms_status  # noqa: F401,F811
from .pcie_aer import handle_pcie_aer_status  # noqa: F401,F811
from .mem_temp_drift import handle_mem_temp_drift_status  # noqa: F401,F811
from .accounting import handle_accounting_status  # noqa: F401,F811
from .trim_audit import handle_trim_audit_status  # noqa: F401,F811
from .throttle_bits import handle_throttle_bits_status  # noqa: F401,F811
from .retired_pages import handle_retired_pages_status  # noqa: F401,F811
from .bug_report_prep import handle_bug_report_prep_status  # noqa: F401,F811
from .pcie_width_watcher import handle_pcie_width_watcher_status  # noqa: F401,F811
from .cuda_ctx_leak import handle_cuda_ctx_leak_status  # noqa: F401,F811
from .proc_static_audit import handle_proc_static_audit_status  # noqa: F401,F811
from .mem_bw_gauge import handle_mem_bw_gauge_status  # noqa: F401,F811
from .power_envelope_drift import handle_power_envelope_drift_status  # noqa: F401,F811
from .rebar_audit import handle_rebar_audit_status  # noqa: F401,F811
from .cpu_rapl import handle_cpu_rapl_status  # noqa: F401,F811
from .clock_gap import handle_clock_gap_status  # noqa: F401,F811
from .pcie_rpm_audit import handle_pcie_rpm_audit_status  # noqa: F401,F811
from .thermal_zones import handle_thermal_zones_status  # noqa: F401,F811
from .nvrm_tail import handle_nvrm_tail_status  # noqa: F401,F811
from .nvlink_health import handle_nvlink_health_status  # noqa: F401,F811
from .kmod_params import handle_kmod_params_status  # noqa: F401,F811
from .thermal_slowdown_kind import handle_thermal_slowdown_kind_status  # noqa: F401,F811
from .d3cold_policy import handle_d3cold_policy_status  # noqa: F401,F811
from .rlimit_audit import handle_rlimit_audit_status  # noqa: F401,F811
from .dmi_bios import handle_dmi_bios_status  # noqa: F401,F811
from .nvme_iosched import handle_nvme_iosched_status  # noqa: F401,F811
from .iommu_groups import handle_iommu_groups_status  # noqa: F401,F811
from .msi_inventory import handle_msi_inventory_status  # noqa: F401,F811
from .oom_priority import handle_oom_priority_status  # noqa: F401,F811
from .cpu_topology import handle_cpu_topology_status  # noqa: F401,F811
from .proc_smaps import handle_proc_smaps_status  # noqa: F401,F811
from .hwmon_inventory import handle_hwmon_inventory_status  # noqa: F401,F811
from .vm_sysctl_audit import handle_vm_sysctl_status  # noqa: F401,F811
from .psi_pressure import handle_psi_pressure_status  # noqa: F401,F811
from .proc_wchan import handle_proc_wchan_status  # noqa: F401,F811
from .cgroup_memcap import handle_cgroup_memcap_status  # noqa: F401,F811
from .clocksource_audit import handle_clocksource_status  # noqa: F401,F811
from .nic_health import handle_nic_health_status  # noqa: F401,F811
from .proc_io_accounting import handle_proc_io_status  # noqa: F401,F811
from .cgroup_cpuio import handle_cgroup_cpuio_status  # noqa: F401,F811
from .thp_audit import handle_thp_audit_status  # noqa: F401,F811
from .buddyinfo_frag import handle_buddyinfo_status  # noqa: F401,F811
from .proc_sched import handle_proc_sched_status  # noqa: F401,F811
from .oomd_correlator import handle_oomd_status  # noqa: F401,F811
from .cpu_boost import handle_cpu_boost_status  # noqa: F401,F811
from .net_sysctl_audit import handle_net_sysctl_status  # noqa: F401,F811
from .smt_audit import handle_smt_audit_status  # noqa: F401,F811
from .numa_placement import handle_numa_placement_status  # noqa: F401,F811
from .kernel_taint import handle_kernel_taint_status  # noqa: F401,F811
from .cpu_microcode import handle_cpu_microcode_status  # noqa: F401,F811
from .hwp_epp import handle_hwp_epp_status  # noqa: F401,F811
from .cpuidle_audit import handle_cpuidle_status  # noqa: F401,F811
from .limits_audit import handle_limits_audit_status  # noqa: F401,F811
from .cpu_vulns import handle_cpu_vulns_status  # noqa: F401,F811
from .hw_watchdog import handle_hw_watchdog_status  # noqa: F401,F811
from .gpu_cpu_affinity import handle_gpu_cpu_affinity_status  # noqa: F401,F811
from .cpu_cache_topology import handle_cache_topology_status  # noqa: F401,F811
from .pcie_aer_trend import handle_pcie_aer_trend_status  # noqa: F401,F811
from .gpu_irq_affinity import handle_gpu_irq_affinity_status  # noqa: F401,F811
from .modprobe_audit import handle_modprobe_audit_status  # noqa: F401,F811
from .proc_maps_libs import handle_proc_maps_libs_status  # noqa: F401,F811
from .cmdline_audit import handle_cmdline_audit_status  # noqa: F401,F811
from .coredump_ready import handle_coredump_ready_status  # noqa: F401,F811
from .host_class import handle_host_class_status  # noqa: F401,F811
from .sysctl_d_audit import handle_sysctl_d_audit_status  # noqa: F401,F811
from .ksm_advisor import handle_ksm_advisor_status  # noqa: F401,F811
from .vm_tuning_deep import handle_vm_tuning_deep_status  # noqa: F401,F811
from .gpu_pci_bind import handle_gpu_pci_bind_status  # noqa: F401,F811
from .nic_queue_affinity import handle_nic_queue_affinity_status  # noqa: F401,F811
from .panic_policy import handle_panic_policy_status  # noqa: F401,F811
from .edac_ram_ecc import handle_edac_ram_ecc_status  # noqa: F401,F811
from .inotify_audit import handle_inotify_audit_status  # noqa: F401,F811
from .zswap_zram_audit import handle_zswap_zram_audit_status  # noqa: F401,F811
from .cpu_epb import handle_cpu_epb_status  # noqa: F401,F811
from .cooling_devices import handle_cooling_devices_status  # noqa: F401,F811
from .hybrid_cpu_topo import handle_hybrid_cpu_topo_status  # noqa: F401,F811
from .file_locks_audit import handle_file_locks_audit_status  # noqa: F401,F811
from .nic_ring_audit import handle_nic_ring_audit_status  # noqa: F401,F811
from .irq_rates_audit import handle_irq_rates_audit_status  # noqa: F401,F811
from .zoneinfo_audit import handle_zoneinfo_audit_status  # noqa: F401,F811
from .block_queue_audit import handle_block_queue_audit_status  # noqa: F401,F811
from .watchdog_inventory import handle_watchdog_inventory_status  # noqa: F401,F811
from .net_proto_counters import handle_net_proto_counters_status  # noqa: F401,F811
from .disk_io_latency import handle_disk_io_latency_status  # noqa: F401,F811
from .slab_audit import handle_slab_audit_status  # noqa: F401,F811
from .entropy_audit import handle_entropy_audit_status  # noqa: F401,F811
from .nf_conntrack_audit import handle_nf_conntrack_audit_status  # noqa: F401,F811
from .sysvipc_audit import handle_sysvipc_audit_status  # noqa: F401,F811
from .mdraid_health import handle_mdraid_health_status  # noqa: F401,F811
from .keyring_audit import handle_keyring_audit_status  # noqa: F401,F811
from .security_posture import handle_security_posture_status  # noqa: F401,F811
from .vfs_limits_audit import handle_vfs_limits_audit_status  # noqa: F401,F811
from .nvidia_rm_audit import handle_nvidia_rm_audit_status  # noqa: F401,F811
from .mce_audit import handle_mce_audit_status  # noqa: F401,F811
from .acpi_audit import handle_acpi_audit_status  # noqa: F401,F811
from .sched_audit import handle_sched_audit_status  # noqa: F401,F811
from .dma_audit import handle_dma_audit_status  # noqa: F401,F811
from .ftrace_audit import handle_ftrace_audit_status  # noqa: F401,F811
from .usb_topology_audit import handle_usb_topology_audit_status  # noqa: F401,F811
from .journal_audit import handle_journal_audit_status  # noqa: F401,F811
from .rtc_clock_audit import handle_rtc_clock_audit_status  # noqa: F401,F811
from .tpm_audit import handle_tpm_audit_status  # noqa: F401,F811
from .wmi_vendor_audit import handle_wmi_vendor_audit_status  # noqa: F401,F811
from .kmsg_audit import handle_kmsg_audit_status  # noqa: F401,F811
from .sock_pool_audit import handle_sock_pool_audit_status  # noqa: F401,F811
from .iio_sensor_audit import handle_iio_sensor_audit_status  # noqa: F401,F811
from .drm_audit import handle_drm_audit_status  # noqa: F401,F811
from .cgroup_memevents_audit import handle_cgroup_memevents_audit_status  # noqa: F401,F811
from .power_supply_audit import handle_power_supply_audit_status  # noqa: F401,F811
from .typec_audit import handle_typec_audit_status  # noqa: F401,F811
from .perf_pmu_audit import handle_perf_pmu_audit_status  # noqa: F401,F811
from .iomem_pci_audit import handle_iomem_pci_audit_status  # noqa: F401,F811
from .ksm_audit import handle_ksm_audit_status  # noqa: F401,F811
from .i2c_smbus_audit import handle_i2c_smbus_audit_status  # noqa: F401,F811
from .module_integrity_audit import handle_module_integrity_audit_status  # noqa: F401,F811
from .psi_pressure_audit import handle_psi_pressure_audit_status  # noqa: F401,F811
from .cpu_vulnerabilities_audit import handle_cpu_vulnerabilities_audit_status  # noqa: F401,F811
from .rapl_power_cap_audit import handle_rapl_power_cap_audit_status  # noqa: F401,F811
from .ima_integrity_audit import handle_ima_integrity_audit_status  # noqa: F401,F811
from .swap_tunables_audit import handle_swap_tunables_audit_status  # noqa: F401,F811
from .hugepages_audit import handle_hugepages_audit_status  # noqa: F401,F811
from .io_uring_runtime_audit import handle_io_uring_runtime_audit_status  # noqa: F401,F811
from .kvm_misc_audit import handle_kvm_misc_audit_status  # noqa: F401,F811
from .edac_ecc_audit import handle_edac_ecc_audit_status  # noqa: F401,F811
from .efi_boot_order_audit import handle_efi_boot_order_audit_status  # noqa: F401,F811
from .numa_topology_audit import handle_numa_topology_audit_status  # noqa: F401,F811
from .hwmon_sensors_audit import handle_hwmon_sensors_audit_status  # noqa: F401,F811
from .sata_link_pm_audit import handle_sata_link_pm_audit_status  # noqa: F401,F811
from .bdi_writeback_audit import handle_bdi_writeback_audit_status  # noqa: F401,F811
from .proc_crypto_audit import handle_proc_crypto_audit_status  # noqa: F401,F811
from .wakeup_sources_audit import handle_wakeup_sources_audit_status  # noqa: F401,F811
from .livepatch_audit import handle_livepatch_audit_status  # noqa: F401,F811
from .backlight_pwm_audit import handle_backlight_pwm_audit_status  # noqa: F401,F811
from .loadavg_pressure_audit import handle_loadavg_pressure_audit_status  # noqa: F401,F811
from .pagetypeinfo_audit import handle_pagetypeinfo_audit_status  # noqa: F401,F811
from .cgroup_root_audit import handle_cgroup_root_audit_status  # noqa: F401,F811
from .scsi_transport_audit import handle_scsi_transport_audit_status  # noqa: F401,F811
from .alsa_cards_audit import handle_alsa_cards_audit_status  # noqa: F401,F811
from .kernel_build_config_audit import handle_kernel_build_config_audit_status  # noqa: F401,F811
from .dmi_smbios_audit import handle_dmi_smbios_audit_status  # noqa: F401,F811
from .pid_rlimits_audit import handle_pid_rlimits_audit_status  # noqa: F401,F811
from .iommu_groups_audit import handle_iommu_groups_audit_status  # noqa: F401,F811
