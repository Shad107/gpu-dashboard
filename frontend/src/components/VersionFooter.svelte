<script lang="ts">
  // Cycle 144 — visible version + update status in the footer.
  // User feedback : 'tu peux affiché un numéro de version sur le dashboard,
  //  et indiqué si une version plus récente existe, par exemple la je ne
  //  suis pas a jour comment je procéde'.
  import { onMount } from "svelte";
  import { api } from "../lib/api";
  import { i18n } from "../lib/i18n/index.svelte";
  import { toast } from "../lib/stores.svelte";

  let version = $state<string>("…");
  let info = $state<Awaited<ReturnType<typeof api.updateCheck>> | null>(null);
  let pulling = $state(false);

  async function reload() {
    try {
      const v = await api.versionInfo();
      version = v.version;
    } catch {}
    try { info = await api.updateCheck(); } catch {}
  }

  async function pullUpdate() {
    if (!confirm(i18n.t("update.confirm_pull") ?? "Mettre à jour et redémarrer ?")) return;
    pulling = true;
    try {
      const r = await fetch("/api/update/pull", { method: "POST" });
      const j = await r.json();
      if (!j.ok) {
        toast.emit("✗ " + (j.error || "pull failed"), "err");
        pulling = false;
        return;
      }
      toast.emit("⏳ " + (i18n.t("update.restarting") ?? "Mise à jour appliquée, redémarrage…"), "ok");
      // Poll /api/version until back, then reload
      const deadline = Date.now() + 60_000;
      while (Date.now() < deadline) {
        await new Promise(r => setTimeout(r, 1500));
        try {
          const r = await fetch("/api/version", { cache: "no-store" });
          if (r.ok) {
            location.reload();
            return;
          }
        } catch {}
      }
      toast.emit(i18n.t("update.timeout") ?? "Timeout, recharge la page", "err");
    } catch (e: any) {
      toast.emit("✗ " + (e?.message || "pull failed"), "err");
    } finally {
      pulling = false;
    }
  }

  onMount(() => {
    reload();
    // Re-check every 10 min — quick enough to catch a push without
    // hammering GitHub's rate limit.
    const t = setInterval(reload, 600_000);
    return () => clearInterval(t);
  });

  const updateAvailable = $derived(info?.behind != null && info.behind > 0);
</script>

<div class="version-footer" class:has-update={updateAvailable}>
  <span class="vf-left">
    <span class="vf-pill" title={info?.current_sha ?? ""}>
      v<b>{version}</b>
      {#if info?.current_sha}<span class="vf-sha">({info.current_sha.slice(0, 7)})</span>{/if}
    </span>
    {#if updateAvailable}
      <span class="vf-update-line">
        🔔 <b>{info?.behind}</b> {i18n.t("update.commits_behind") ?? "commits de retard"}
        {#if info?.last_remote_msg}
          <span class="vf-remote-msg">— {info.last_remote_msg}</span>
        {/if}
      </span>
      <button class="btn btn-primary vf-pull" disabled={pulling} onclick={pullUpdate}>
        {pulling ? "⏳ …" : "⬇️ " + (i18n.t("update.pull_btn") ?? "Mettre à jour")}
      </button>
    {:else if info?.ok}
      <span class="vf-uptodate">✓ {i18n.t("update.up_to_date") ?? "à jour"}</span>
    {/if}
  </span>
  <span class="vf-right">
    {i18n.t("footer.refresh")} ·
    <a href="https://github.com/Shad107/gpu-dashboard" target="_blank" rel="noopener">github</a>
  </span>
</div>

<style>
  .version-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.6em;
    padding: 0.6em 0.2em 0.4em;
    margin-top: 1em;
    border-top: 1px solid var(--border-subtle);
    font-size: 0.82em;
    color: var(--text-dim);
  }
  .version-footer.has-update {
    background: linear-gradient(180deg, rgba(96,165,250,0.06), transparent);
    border-top-color: rgba(96,165,250,0.35);
    padding-top: 0.7em;
  }
  .vf-left, .vf-right { display: inline-flex; align-items: center; gap: 0.5em; flex-wrap: wrap; }
  .vf-pill {
    padding: 0.2em 0.6em;
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 999px;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }
  .vf-pill b { color: var(--accent); }
  .vf-sha { color: var(--text-dim); margin-left: 0.3em; font-family: ui-monospace, monospace; font-size: 0.9em; }
  .vf-update-line { color: var(--accent-cool); }
  .vf-update-line b { color: var(--accent-cool); }
  .vf-remote-msg { color: var(--text-dim); font-style: italic; }
  .vf-pull {
    padding: 0.25em 0.8em !important;
    font-size: 0.9em !important;
  }
  .vf-uptodate { color: var(--accent); }
  @media (max-width: 600px) {
    .vf-remote-msg { display: none; }
  }
</style>
