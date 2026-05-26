<script lang="ts">
  /**
   * F2.1 — Diff result modal.
   *
   * Fetches /api/witness/diff?before=A&after=B and shows the
   * structural changes grouped by section (driver/modules/pcie/
   * packages/systemd/kernel/gpu). No ranker yet — that's F2.2.
   */
  import { i18n } from "../lib/i18n/index.svelte";

  type Change = {
    path: string;
    kind: "added" | "removed" | "changed";
    before?: unknown;
    after?: unknown;
    score?: number;
    severity?: "critical" | "high" | "medium" | "low" | "info";
    reason?: string;
  };
  type DiffResult = {
    ok: boolean;
    error?: string;
    message?: string;
    before?: { id: string; taken_at: string };
    after?: { id: string; taken_at: string };
    changes?: Change[];
    change_count?: number;
  };

  let { open = $bindable(false),
        beforeId = null,
        afterId = null } = $props<{
    open: boolean;
    beforeId: string | null;
    afterId: string | null;
  }>();

  let result = $state<DiffResult | null>(null);
  let loading = $state(false);
  let hideNoise = $state(true); // hide score=0 (refcount drift, etc.)

  $effect(() => {
    if (open && beforeId && afterId) {
      loading = true;
      result = null;
      fetch(`/api/witness/diff?before=${encodeURIComponent(beforeId)}` +
            `&after=${encodeURIComponent(afterId)}`)
        .then((x) => x.json())
        .then((r) => (result = r))
        .catch((e) => (result = { ok: false, message: String(e) }))
        .finally(() => (loading = false));
    }
  });

  function close() {
    open = false;
    result = null;
  }

  // Filter + group changes by their top-level section.
  const filtered = $derived.by(() => {
    if (!result?.changes) return [] as Change[];
    if (!hideNoise) return result.changes;
    return result.changes.filter((c) => (c.score ?? 5) > 0);
  });
  const grouped = $derived.by(() => {
    const m = new Map<string, Change[]>();
    for (const c of filtered) {
      const section = c.path.split(".")[0];
      if (!m.has(section)) m.set(section, []);
      m.get(section)!.push(c);
    }
    return m;
  });
  const hiddenCount = $derived(
    (result?.change_count ?? 0) - filtered.length,
  );

  // Top-N changes summary at the top of the modal — the value
  // prop of the whole feature.
  const topN = $derived(filtered.slice(0, 5));

  function fmtVal(v: unknown): string {
    if (v === null || v === undefined) return "—";
    if (typeof v === "string") return v.length > 80 ? v.slice(0, 80) + "…" : v;
    if (typeof v === "object") {
      try {
        const j = JSON.stringify(v);
        return j.length > 80 ? j.slice(0, 80) + "…" : j;
      } catch {
        return String(v);
      }
    }
    return String(v);
  }

  // Sections in priority order matching what historically causes
  // regressions. Sections not in this list get appended after.
  const SECTION_ORDER = [
    "driver", "kernel", "modules", "pcie", "gpu", "packages", "systemd",
  ];
  const sortedSections = $derived.by(() => {
    const keys = Array.from(grouped.keys());
    keys.sort((a, b) => {
      const ai = SECTION_ORDER.indexOf(a);
      const bi = SECTION_ORDER.indexOf(b);
      if (ai === -1 && bi === -1) return a.localeCompare(b);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });
    return keys;
  });
</script>

{#if open}
  <div class="modal-backdrop" onclick={close} role="presentation"></div>
  <div class="diff-modal" role="dialog" aria-modal="true">
    <header>
      <h3>🔀 {i18n.t("witness.diff_title") ?? "Diff de snapshots"}</h3>
      <button class="btn-close" onclick={close} aria-label="Close">✕</button>
    </header>

    {#if loading}
      <p class="muted">⏳ {i18n.t("witness.computing") ?? "Calcul du diff..."}</p>
    {:else if !result}
      <p class="muted">—</p>
    {:else if !result.ok}
      <p style="color:var(--err)">✗ {result.message ?? result.error ?? "error"}</p>
    {:else}
      <p class="meta muted small">
        <b>{i18n.t("witness.before") ?? "Avant"}</b>: {result.before?.taken_at}<br/>
        <b>{i18n.t("witness.after") ?? "Après"}</b>: {result.after?.taken_at}<br/>
        <b>{result.change_count ?? 0}</b>
        {i18n.t("witness.changes") ?? "changement(s)"}
      </p>

      {#if (result.change_count ?? 0) === 0}
        <p class="empty">
          ✅ {i18n.t("witness.no_changes") ??
            "Aucune différence détectée. Le système est identique."}
        </p>
      {:else}
        {#if topN.length > 0}
          <section class="top-suspects">
            <h4>🎯 {i18n.t("witness.top_suspects") ?? "Suspects prioritaires"}</h4>
            <ol>
              {#each topN as c}
                <li class="suspect sev-{c.severity ?? 'info'}">
                  <span class="sev-tag">{c.severity ?? "info"}</span>
                  <code class="path">{c.path}</code>
                  <div class="reason muted small">{c.reason ?? ""}</div>
                </li>
              {/each}
            </ol>
          </section>
        {/if}

        <div class="filter-row">
          <label class="muted small">
            <input type="checkbox" bind:checked={hideNoise} />
            {i18n.t("witness.hide_noise") ??
              "Masquer le bruit (refcounts, etc.)"}
            {#if hideNoise && hiddenCount > 0}
              <span class="muted">({hiddenCount} masqué(s))</span>
            {/if}
          </label>
        </div>

        {#each sortedSections as section}
          {@const items = grouped.get(section) ?? []}
          <details open class="section">
            <summary>
              <b>{section}</b>
              <span class="muted small">({items.length})</span>
            </summary>
            <ul>
              {#each items as c}
                <li class="change {c.kind}">
                  <span class="kind-tag">{
                    c.kind === "added" ? "+" :
                    c.kind === "removed" ? "−" : "~"
                  }</span>
                  <code class="path">{c.path}</code>
                  {#if c.kind === "changed"}
                    <div class="vals">
                      <span class="before">{fmtVal(c.before)}</span>
                      <span class="arrow">→</span>
                      <span class="after">{fmtVal(c.after)}</span>
                    </div>
                  {:else if c.kind === "added"}
                    <div class="vals">
                      <span class="after">{fmtVal(c.after)}</span>
                    </div>
                  {:else}
                    <div class="vals">
                      <span class="before">{fmtVal(c.before)}</span>
                    </div>
                  {/if}
                </li>
              {/each}
            </ul>
          </details>
        {/each}
      {/if}
    {/if}
  </div>
{/if}

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.78);
    backdrop-filter: blur(4px);
    z-index: 2100;
  }
  .diff-modal {
    position: fixed;
    top: 6vh;
    left: 50%;
    transform: translateX(-50%);
    width: min(94vw, 880px);
    max-height: 88vh;
    overflow-y: auto;
    background: var(--bg-1, #14171b);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 12px;
    z-index: 2200;
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.75);
    padding: 18px 22px;
    isolation: isolate;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid var(--border);
    padding-bottom: 8px;
    margin-bottom: 12px;
  }
  header h3 { margin: 0; }
  .btn-close {
    background: none;
    border: none;
    font-size: 1.4em;
    color: var(--text-dim);
    cursor: pointer;
  }
  .meta {
    margin: 0 0 12px;
    line-height: 1.5;
  }
  .empty {
    text-align: center;
    padding: 24px;
    color: var(--ok);
    font-weight: 600;
  }
  .section {
    margin: 8px 0;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 10px;
  }
  .section summary {
    cursor: pointer;
    padding: 4px 0;
  }
  .section ul {
    list-style: none;
    padding: 0;
    margin: 6px 0 0;
  }
  .change {
    padding: 4px 0;
    border-bottom: 1px solid rgba(120, 120, 120, 0.1);
    font-size: 0.85em;
    word-break: break-word;
  }
  .change:last-child { border-bottom: none; }
  .kind-tag {
    display: inline-block;
    width: 1.2em;
    text-align: center;
    font-weight: 600;
    margin-right: 4px;
  }
  .change.added .kind-tag  { color: var(--ok, #22c55e); }
  .change.removed .kind-tag { color: var(--err, #ef4444); }
  .change.changed .kind-tag { color: var(--warn, #eab308); }
  .path {
    font-family: ui-monospace, monospace;
    font-size: 0.95em;
    color: var(--accent, #38bdf8);
  }
  .vals {
    margin-top: 2px;
    margin-left: 1.2em;
    font-family: ui-monospace, monospace;
    font-size: 0.85em;
  }
  .before {
    color: var(--err, #ef4444);
    text-decoration: line-through;
    text-decoration-color: rgba(239, 68, 68, 0.4);
  }
  .arrow {
    margin: 0 6px;
    color: var(--text-dim);
  }
  .after {
    color: var(--ok, #22c55e);
  }

  .top-suspects {
    background: rgba(56, 189, 248, 0.06);
    border-left: 3px solid var(--accent, #38bdf8);
    border-radius: 4px;
    padding: 8px 12px;
    margin: 0 0 14px;
  }
  .top-suspects h4 {
    margin: 0 0 6px;
  }
  .top-suspects ol {
    margin: 0;
    padding-left: 20px;
  }
  .suspect {
    padding: 4px 0;
    line-height: 1.45;
  }
  .suspect .sev-tag {
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 0.7em;
    font-weight: 700;
    text-transform: uppercase;
    margin-right: 6px;
    vertical-align: middle;
  }
  .sev-critical .sev-tag {
    background: rgba(239, 68, 68, 0.18);
    color: var(--err, #ef4444);
  }
  .sev-high .sev-tag {
    background: rgba(234, 179, 8, 0.18);
    color: var(--warn, #eab308);
  }
  .sev-medium .sev-tag {
    background: rgba(56, 189, 248, 0.18);
    color: var(--accent, #38bdf8);
  }
  .sev-low .sev-tag, .sev-info .sev-tag {
    background: rgba(120, 120, 120, 0.18);
    color: var(--text-dim, #8a93a3);
  }
  .suspect .reason {
    margin-left: 0.2em;
  }
  .filter-row {
    margin: 4px 0 12px;
  }
  .filter-row label {
    cursor: pointer;
  }
</style>
