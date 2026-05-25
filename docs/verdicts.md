# Verdict contract

Every audit module in `gpu_dashboard.modules.*` returns a `status()`
dict containing a `verdict` field shaped like:

```python
{"verdict": "<verdict-string>",
 "reason":  "<one-sentence human explanation>",
 "recommendation": "<paste-ready shell snippet or empty>"}
```

The verdict string itself is module-specific (~625 unique values
across 411 modules — full enumeration is in each module's
docstring, not here). What matters for UI consumers and operators
is the **canonical contract** that maps each module-specific
verdict to one of five severity tiers.

## Severity ladder

UI rendering in `frontend/src/components/SettingsModal.svelte`
treats verdicts in the following priority order. Cards apply the
worst-fitting severity color, and the cold-start `collection_profile_audit`
treats anything above `ok` as actionable.

| Severity      | UI color           | When the module uses this |
|---------------|--------------------|---------------------------|
| `err`         | `var(--err)` red   | A real fault: hardware fault, OOM, kernel panic risk, mitigation broken. Demands immediate operator action. Examples: `bad_xids_present`, `bond_degraded_slave`, `arp_table_overflow`. |
| `warn`        | `var(--warn)` yellow | A performance regression or imminent fault: tunable misconfigured, queue approaching capacity, drift detected. Examples: `arc_eating_ram`, `affinity_hint_mismatch`, `bios_stale_gt_3y`. |
| `accent`      | `var(--accent)` blue | An informational signal that may or may not be intentional: knob set to a non-default but valid value, capability missing but not load-bearing. Examples: `autosuspend_delay_unset`, `bbr_available_unused`, `autogroup_off`. |
| `ok`          | `var(--ok)` green  | Module ran, found nothing actionable. The fleet's baseline. |
| `unknown` / `requires_root` | `var(--text-dim)` grey | Module could not collect data: surface absent on minimal kernel (`unknown`) or readable only as root (`requires_root`). Not a failure — graceful degradation. |

## Five universal verdicts

Across the 411-module fleet, five verdict strings are reserved as
universal-meaning and reused by many modules:

| Verdict          | Meaning |
|------------------|---------|
| `ok`             | Module ran successfully and found nothing actionable. |
| `unknown`        | Module could not collect data. Almost always because the sysfs/procfs surface the module reads doesn't exist on this host (e.g. XFS audit on a host with only ext4). Not a bug — modules are designed to surface `unknown` rather than traceback. |
| `requires_root`  | Module's data source is mode 0600 / mode 0700 / restricted-by-LSM and the dashboard service is running as a non-root user. The reason string typically includes a `sudo systemctl edit gpu-dashboard.service` snippet if root-mode is desired. |
| `n/a` / `na` / `not_applicable` | Module's question doesn't apply to this host (e.g. battery-related audit on a desktop with no battery). Distinct from `unknown` — the surface exists but the question is moot. |
| `disabled` / `off` | The kernel feature being audited is intentionally turned off (e.g. CONFIG_PSI=n, swap=off, hugepages=never). The module surfaces this so the operator knows; not necessarily a problem. |

## Looking up a specific verdict

The 620+ module-specific verdicts (`disk_swap_higher_than_zram`,
`xfs_stats_unreadable`, `iommu_disabled`, etc.) all carry their
own meaning in the module's docstring. To find one:

```bash
grep -rlw "verdict_name" src/gpu_dashboard/modules/
# Then read the matching module's docstring for the verdict's
# meaning, ladder position, and recovery recipe.
```

Each module's docstring includes a Verdicts section listing the
verdicts the module emits in priority order. That is the source
of truth — keeping this doc in sync with every module-specific
verdict would be ~625 entries and immediately rot. The contract
above is stable; the specifics live with the code.

## Special verdicts in collection_profile_audit

Distinct from per-module verdicts, the fleet self-observability
audit (`/api/collection-profile-audit`) emits four:

| Verdict             | Meaning |
|---------------------|---------|
| `ok`                | Per-module budget honored, optimizable aggregate under fleet budget. |
| `module_too_slow`   | ≥1 module (excluding EXPECTED_SLOW) exceeded the per-module budget (default 500 ms). |
| `collection_slow`   | Optimizable aggregate exceeded the fleet budget (default 5000 ms). Excludes EXPECTED_SLOW modules — see `docs/hardening-log.md` H10. |
| `unknown`           | Module enumeration failed (packaging or import-time bug). |

These thresholds are configurable via query string —
see `docs/hardening-log.md` H12.

## Contract tests

The fleet contract is regression-gated by:

- `tests/test_module_fleet_health.py` — every status() returns a
  dict containing `verdict` or `ok`; `ok=False` returns also include
  an actionable secondary key (H7).
- `tests/test_module_missing_paths.py` — every status() with a
  path-typed default parameter degrades gracefully when the path is
  missing (H4).

A new module that breaks the contract will fail CI before reaching
users.
