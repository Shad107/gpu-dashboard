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
