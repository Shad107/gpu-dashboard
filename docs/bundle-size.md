# Frontend bundle size — Hardening #8 analysis

## Current state (HEAD = df814f7)

| Metric | Value |
|---|---|
| Total JS bundle | 1.75 MB |
| Gzipped | 403 kB |
| Total CSS | 38 kB / 8 kB gzip |
| Build time | ~28 s |

## What dominates the bundle

```
21953  src/components/SettingsModal.svelte  (1.39 MB raw)
 6055  src/lib/api.ts                       (227 kB raw)
 2723  src/lib/i18n/en.json
 2723  src/lib/i18n/fr.json
  132  src/App.svelte
```

`SettingsModal.svelte` is the single component carrying **~70% of
the JS bundle**. It contains **389 `.card-form` divs**, of which
**386 live in the `integrations` section** — one per audit module
shipped across R&D #1 through #112 and hardening sprints.

Each card includes:

- markup (~50–200 lines)
- a `$state` holder
- a load function (`loadFooBarAudit()`)
- the autoload hook inside the section's first-open effect
- the verdict-styled badge, recovery snippet, and copy button

The integration cards use `hidden={modal.section !== "integrations"}`
rather than `{#if section === "integrations"}`. That CSS-gates the
DOM but the markup still ships in the bundle (and renders to the DOM
on first mount). Switching to `{#if}` would reduce first-render DOM
work but **would not reduce bundle size** — the markup is still in
the JS chunk either way.

## Code-splitting feasibility

The natural seam is to extract integration cards into a separate
`IntegrationCards.svelte` component dynamic-imported only when the
user navigates to the integrations section:

```svelte
{#if modal.section === "integrations"}
  {#await import("./IntegrationCards.svelte") then m}
    <m.default {...props} />
  {/await}
{/if}
```

Expected gain: initial bundle drops to **~50 kB shell + ~350 kB
integrations chunk**. The integrations chunk loads lazily on first
section navigation.

### Why we are not doing this now

1. **LAN-only deployment context.** The dashboard runs from a
   systemd user service on the homelab desktop itself. There is no
   CDN cost, no mobile-network cost, no cold-start penalty per
   page visit. The bundle is fetched once over loopback per browser
   session.

2. **Refactor cost.** Extracting 386 cards out of a single 22 k-line
   Svelte file means touching every `$state` declaration, every
   autoloader, every `$effect`, every i18n key reference. The diff
   would be ~1.5 MB of churn — high risk of regression for marginal
   gain on a non-public dashboard.

3. **403 kB gzip is acceptable for the target audience.** Homelab
   operators on gigabit LANs see no noticeable load delay. Real-world
   measurement of `DOMContentLoaded` on the running instance is
   ~120 ms, dominated by Svelte hydration not network fetch.

4. **The vite warning is cosmetic.** Vite warns at 500 kB by
   default; bumping `chunkSizeWarningLimit` to 2000 reflects the
   intended deployment shape rather than papering over a problem.

## What we did ship in hardening #8

- Bumped `chunkSizeWarningLimit` from the 500 kB default to 2000.
- Documented the analysis here.
- Left the integration-card extraction as future work, gated on
  real-user latency data (which we currently don't collect).

## When to revisit

Reconsider code-splitting if:

- The dashboard is ever served over the public internet (would
  warrant lazy section loading).
- A user reports first-paint latency on slow hardware (low-end ARM
  homelab boards with weak JS engines).
- The integration card count crosses ~600+ and parse-time on a Pi-
  class CPU exceeds 500 ms.
