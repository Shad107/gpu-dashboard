<script lang="ts">
  import { view, type View } from "../lib/view.svelte";
  import { i18n } from "../lib/i18n/index.svelte";

  type NavTab = { id: View; labelKey: string; emoji: string };
  // 'About' stays in the Settings modal (per user feedback 2026-05-21 23:25 :
  // 'Remet le à-propos dans le paramétrage a la fin'). Top-level keeps only
  // recurring views.
  // Order : Dashboard (live) → Stats (perf overview) → History (deep dive)
  // Stats is more "at-a-glance" than History → goes second per user 23:39.
  const tabs: NavTab[] = [
    { id: "dashboard", labelKey: "nav.dashboard", emoji: "🏠" },
    { id: "stats",     labelKey: "nav.stats",     emoji: "📈" },
    { id: "history",   labelKey: "nav.history",   emoji: "📊" },
  ];
</script>

<nav class="top-nav">
  {#each tabs as t}
    <button
      class="top-nav-btn"
      class:active={view.current === t.id}
      onclick={() => view.set(t.id)}
    >
      <span class="top-nav-emoji">{t.emoji}</span>
      <span class="top-nav-label">{i18n.t(t.labelKey as any)}</span>
    </button>
  {/each}
</nav>
