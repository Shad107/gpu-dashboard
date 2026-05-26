# Hardening sprint log

Compact running log of hardening sprint findings since the R&D
discovery track closed (per R&D #112 survey).

Each entry records what was checked, what was shipped (if any), and
why. "No change shipped" entries are deliberate — recording that we
*looked* keeps the next operator from re-doing the same work.

| # | Commit  | Subject                                | Result |
|---|---------|----------------------------------------|---|
| 1 | d9250ac | Fleet health harness                   | 376 modules pass / 35 skipped / 1 setup-helper carve-out (watchdog_setup). |
| 2 | c6ce519 | collection_profile_audit               | Surfaced 3 modules exceeding 500 ms budget on first run. |
| 3 | a7047ed | EXPECTED_SLOW marker                   | mem_bw_gauge / bug_report_prep / dkms_status marked intrinsic. Verdict shifted from misleading module_too_slow to honest collection_slow. |
| 4 | d603829 | Synthetic missing-path harness         | 227 modules pass first run. Codifies existing OSError hygiene as a regression gate. |
| 6 | 923e6d6 | UI sprint 103 — collection_profile card | Lazy-load Settings card with [expected] badge. |
| 7 | df814f7 | ok=False return-shape contract         | 67 modules return ok=False; 0 degenerate. Contract codified. |
| 8 | 77f61f3 | Bundle-size investigation              | 1.75 MB / 403 kB gzip. Refactor deferred — LAN deployment, no CDN. Vite warning silenced; analysis in docs/bundle-size.md. |
| 9 | (this)  | EXPECTED_SLOW re-audit                 | **No change shipped.** Top 10 re-checked; the 7 unflagged candidates are all under budget (highest is bpf_program_inventory_audit at 383 ms). No new intrinsic-slow modules to mark. |
| 10 | 44efb55 | Split aggregate cost                  | `collection_slow` now gates on `optimizable_total_ms` (excludes EXPECTED_SLOW). Verdict flipped from always-firing to honest `ok`. |
| 11 | 6de1d23 | BackendOfflineError                   | `safeFetch` + 502/503/504 detection in api.ts. Replaces cryptic "HTTP 502" / "Unexpected token" toasts. |
| 12 | 471b04b | Budget query-param overrides          | `/api/collection-profile-audit?slow_module_ms=N&slow_total_ms=N` with input validation. Useful on slow hardware. **Side observation:** `bpf_program_inventory_audit` and `inotify_audit` vary 380–580 ms across runs on this VM — borderline. Worth a future investigation as queued H13. |
| 13 | f83c92a | Borderline module investigation       | Both `bpf_program_inventory_audit` and `inotify_audit` walk `/proc/<pid>/fdinfo/*` — cost is intrinsic to the data they surface. Marked both `EXPECTED_SLOW`. Identified four-module fdinfo-walker family (bpf / inotify / drm_fdinfo / fdinfo_kinds) sharing the same scan; future shared cache could amortize across all four — deferred. |
| 14 | 8fbf3c6 | UI inputs for budget overrides        | Two number inputs in the collection_profile card pipe through to `?slow_module_ms=N&slow_total_ms=N`. Round-trip verified. |
| 15 | d19ca94 | Shared fdinfo cache — design only     | **No code shipped.** Designed `_proc_fd_cache.py` shape + refactor plan in docs/fdinfo-shared-cache.md. Defer triggers documented (5th fdinfo walker, user-reported slowness, optimizable >4000 ms). Saving would be ~750 ms across 4 walkers; verdict is already `ok` so no correctness motivation. |
| 16 | d04bc5a | Verdict contract doc                  | Discovered the fleet has 625 distinct verdict strings — full enumeration would rot immediately. Shipped docs/verdicts.md covering the **contract** (5-tier severity ladder, 5 universal verdicts, collection_profile_audit verdicts) plus a `grep` recipe for module-specific lookups. Per-module verdicts stay in module docstrings (source of truth). |
| 17 | 6f399eb | safeFetch demo adoption (5 cards)      | Wired the safeFetch helper from H11 into 5 representative fetchers (state, dkms, cpu_microcode, keyring, collection_profile). Demo only — left ~395 raw fetchers in place. |
| 18 | 379643c | Broader safeFetch rollout (426 cards)  | Self-correction after the user called out 25 standby cycles. Mechanical sed migration of every `fetch("/api...")` to `safeFetch("/api...")` in api.ts. 438 total call sites now flow through BackendOfflineError on backend-down. Build/bundle unchanged. **Lesson logged**: the /loop being re-invoked IS a triggering event for queued-with-design work; standby was wrong here. |
| 15.1 | 56cb544 | Shared _proc_fd_cache utility         | Process-local short-TTL cache for /proc/<pid>/{fd, fdinfo}/*. 9 tests cover empty proc, missing dirs, tmp-path bypass, invalidate, TTL expiry, live smoke. Walkers not yet migrated. |
| 15.2 | 529d7f9 | fdinfo walkers consume cache          | Refactored bpf/inotify/drm_fdinfo/fdinfo_kinds to use the shared cache. TTL bumped from 1s to 15s to span the alphabetical fleet sweep. 3 of 4 walkers dropped out of top_slowest; only bpf (first alphabetically) still pays the cold-cache scan. 613 regression tests pass. |
| 15.3 | (this)  | Drop inotify EXPECTED_SLOW            | Measured: inotify_audit cold-cache ~478 ms, warm-cache ~48 ms — both under the 500 ms budget on solo calls. Marker no longer needed. bpf stays marked (cold-cache 475–517 ms; alphabetical-first cost pushes it borderline). |

## H9 details

Live `/api/collection-profile-audit` result on the host (Ubuntu 6.17,
virtio VM, 411-module fleet):

```
verdict: collection_slow · 375 timed · total 7714 ms
[expected] mem_bw_gauge                  2659.2 ms
[expected] bug_report_prep                973.1 ms
[expected] dkms_status                    869.3 ms
           bpf_program_inventory_audit    383.2 ms
           inotify_audit                  348.7 ms
           drm_fdinfo_engine_usage_audit  342.2 ms
           proc_maps_anomaly_audit        219.4 ms
           proc_smaps                      94.3 ms
           per_device_wakeup_attribution    77.0 ms
           numa_placement                  76.4 ms
```

No unflagged module exceeds the 500 ms per-module budget. The
`collection_slow` aggregate verdict still fires because the three
EXPECTED_SLOW modules alone account for ~4500 ms of the 7714 ms
total. That is honest — those modules are doing real intrinsic work
and the user cannot speed them up without sacrificing accuracy.

A future tweak worth considering (queued as H10): split
`total_ms` into `total_ms` and `optimizable_total_ms` (excluding
EXPECTED_SLOW). `collection_slow` would gate on the latter so the
verdict stops firing forever on fleets where the only cost is
intrinsic. Not shipped this sprint to avoid gold-plating.
