"""HTTP API handlers package.

This package is being split out from a single 4800-line `api.py` into
logical submodules. During the migration :

  - `_monolith.py` contains the un-migrated handlers.
  - Each new submodule (auth.py, integrations.py, ...) extracts a coherent
    group of handlers. They are added to the re-export below as they land.

External callers (server.py, tests, modules) continue to do
`from gpu_dashboard import api ; api.handle_X(...)` exactly as before —
the names are re-exported here.
"""

# Migrated submodules — names here take precedence over _monolith re-exports
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
)

# Public handlers + builders from the legacy monolith.
from ._monolith import *  # noqa: F401,F403

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
)
from .diagnostics import _JOURNAL_FILTERS, _diff_snapshots  # noqa: F401,F811
# Helpers moved to submodules — re-export for tests
from .llm import (  # noqa: F401,F811
    _parse_llamacpp_metrics,
    _tokens_per_watt,
    _llm_model_served,
)
from .power import _POWER_PROFILES, _read_power_profile  # noqa: F401,F811

# Private helpers used by tests (and by future submodules during migration).
# `from X import *` skips underscore-prefixed names, so we list these explicitly.
from ._monolith import (  # noqa: F401
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
    _redact_env_file,
    # Linear regression + R² (thermal coach)
    _linear_fit,
    _r_squared,
    # Drift detector

    # Heartbeats (deadman)
    _load_heartbeats,
    _save_heartbeats,
    # cgroup attribution
    _normalize_cgroup,
    # Alert escalation
    _alert_consecutive_to_for,
    # SVG badge generator (R&D #10.7)
    _badge_svg,
    _BADGE_TEMP_COLORS,
    # ANSI/tldr endpoint (R&D #10.6)
    _color,
    _temp_color,
    _spark,
    _ANSI,
    # Module-level CPU/vmstat caches (used by tests)
    _LAST_CPU_LINE,
    _LAST_CPU_TS,
    _LAST_VMSTAT,
    _LAST_VMSTAT_TS,
)
