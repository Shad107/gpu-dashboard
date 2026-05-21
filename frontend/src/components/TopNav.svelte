<script lang="ts">
  import { view, type View } from "../lib/view.svelte";
  import { i18n } from "../lib/i18n/index.svelte";

  type NavTab = { id: View; labelKey: string; emoji: string };
  const tabs: NavTab[] = [
    { id: "dashboard", labelKey: "nav.dashboard", emoji: "🏠" },
    { id: "history",   labelKey: "nav.history",   emoji: "📊" },
    { id: "stats",     labelKey: "nav.stats",     emoji: "📈" },
    { id: "about",     labelKey: "nav.about",     emoji: "ℹ️" },
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
