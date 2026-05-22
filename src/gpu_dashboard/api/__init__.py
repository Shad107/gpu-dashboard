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

# Public handlers + builders from the legacy monolith.
from ._monolith import *  # noqa: F401,F403

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
    _llm_model_served,
    _parse_gpu_index,
    _parse_llamacpp_metrics,
    _tokens_per_watt,
    _read_cmdline,
    _redact_env_file,
    # Linear regression + R² (thermal coach)
    _linear_fit,
    _r_squared,
    # Drift detector
    _diff_snapshots,
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
