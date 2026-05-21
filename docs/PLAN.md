# gpu-dashboard — Living Plan

Plan vivant. Mis à jour à chaque cycle du loop autonome.
Source de vérité pour : ce qui est fait, en cours, à venir.

**Last updated** : 2026-05-22 01:18 (cycle 92 done — fan curve viz 1/8)
**Latest commit** : `064c59c` — fan curve SVG visualization
**Tests** : 497 passing · **CI** : ✅ green · **Bundle** : 72.74 KB gzip · CSS 5.30 KB

---

## 🔄 In progress

Nothing — between cycles. Wakeup soon will start **Cycle 93 : fan curve editor slice 2/8 — drag handling**.

---

## 📋 Next cycles (ordered)

Per user discussion 2026-05-21 22:30 : dashboard customization is the new priority.

### Cycle 93 (next) — Fan curve slice 2/8 — drag handling
- pointerdown on a circle → start drag
- pointermove updates the local curve array (constrained to [0,100] × [0,100])
- pointerup → finalize (still no persist yet — slice 4)
- Snap to integer grid (1°C and 1% steps)
- Visual feedback : circle radius +1 on hover, +2 on grab

### Cycle 94 — Slice 3/8 : add/remove control points
### Cycle 95 — Slice 4/8 : POST /api/fan-curve to persist edits
### Cycles 96-99 : per-fan curves, presets, tests, polish

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
| Tests | 497 passing on Py 3.9-3.13 |
| Test runtime | ~4s |
| Bundle JS | 215.31 KB raw / 72.74 KB gzip |
| Bundle CSS | 23.10 KB raw / 5.30 KB gzip |
| Commits since v0.1.0 | ~100 |
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
