# gpu-dashboard — Living Plan

Plan vivant. Mis à jour à chaque cycle du loop autonome.
Source de vérité pour : ce qui est fait, en cours, à venir.

**Last updated** : 2026-05-21 22:36 (cycle 63 done)
**Latest commit** : `668971a` — Phase A card hide/show toggle
**Tests** : 428 passing · **CI** : ✅ green · **Bundle** : 51.90 KB gzip

---

## 🔄 In progress

Nothing — between cycles. Wakeup soon will start **Phase B : Drag-and-drop reorder**.

---

## 📋 Next cycles (ordered)

Per user discussion 2026-05-21 22:30 : dashboard customization is the new priority.

### Cycle 64 (next) — Phase B : Drag-and-drop card reorder (~2-3h)
- Install `svelte-dnd-action` (~5 KB gzip)
- Drag handle on each card (top-right tiny icon)
- Order persisted in localStorage alongside visibility
- "Reset layout" button in Layout tab
- Tests : reorder persists, reset works, mobile not broken
- Screenshot

### Cycle 65 — Phase C : Custom URL card (iframe embed) (~2-3h)
- Allow user to add an arbitrary URL card (Grafana panel, Home Assistant card, an image, etc.)
- Sandboxed iframe (sandbox="allow-scripts" — no parent navigation)
- Multiple custom cards possible
- Add/remove from Layout tab
- Tests : URL validation (must be http/https), iframe attrs correct
- Screenshot

### Cycle 66+ — Original feature backlog continues
1. Lifetime LLM stats (total tokens since install, ~1h)
2. Power cost heatmap by hour-of-day (~1h)
3. Compare-to-7d / 30d toggle in History (~30 min)
4. Theme toggle light/dark (~2h)
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
| Tests | 428 passing on Py 3.9-3.13 |
| Test runtime | ~4s |
| Bundle JS | 150 KB raw / 51.90 KB gzip |
| Bundle CSS | 15.28 KB raw / 3.76 KB gzip |
| Commits since v0.1.0 | ~66 |
| API endpoints | 30+ |
| Opt-in modules | 8 (power_limit, clock_offsets, telegram_alerts, fan_curve, auto_profile, alert_monitor, webhook, oculink_watchdog) |
| Background daemons | 5 (sampler, retention, fan_curve, auto_profile, alert_monitor) |
| Modal tabs | 11 (grouped in 6 sections) |
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
