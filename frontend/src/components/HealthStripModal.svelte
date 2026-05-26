<script lang="ts">
  /**
   * F5.1 — Health Strip detail modal.
   *
   * Shows every check with its verdict + the elapsed_ms, plus
   * a refresh button. Grouped by severity (err first, then warn,
   * ok, unknown).
   */
  import { i18n } from "../lib/i18n/index.svelte";

  type Severity = "ok" | "warn" | "err" | "unknown";
  type Check = {
    id: string;
    label: string;
    severity: Severity;
    verdict: unknown;
    error?: string;
    elapsed_ms?: number;
  };
  type Aggregate = {
    ok: boolean;
    summary: Record<Severity | "total", number>;
    overall: Severity;
    checks: Check[];
    elapsed_ms: number;
  };

  let { open = $bindable(false), data = null, onRefresh } = $props<{
    open: boolean;
    data: Aggregate | null;
    onRefresh?: () => void;
  }>();

  function close() { open = false; }

  function sevColor(s: Severity): string {
    if (s === "err") return "var(--err)";
    if (s === "warn") return "var(--warn)";
    if (s === "ok") return "var(--ok)";
    return "var(--text-dim, #8a93a3)";
  }
  function sevIcon(s: Severity): string {
    if (s === "err") return "✗";
    if (s === "warn") return "⚠";
    if (s === "ok") return "✓";
    return "?";
  }
</script>

{#if open}
  <div class="modal-backdrop" onclick={close} role="presentation"></div>
  <div class="health-modal" role="dialog" aria-modal="true">
    <header>
      <h3>🏥 {i18n.t("health.modal_title") ?? "État système — détail"}</h3>
      <div style="display:flex;gap:6px;align-items:center">
        {#if onRefresh}
          <button class="btn btn-small" onclick={onRefresh}>↻</button>
        {/if}
        <button class="btn-close" onclick={close} aria-label="Close">✕</button>
      </div>
    </header>

    {#if !data}
      <p class="muted">⏳ {i18n.t("health.loading") ?? "Chargement..."}</p>
    {:else}
      <p class="meta muted small">
        {i18n.t("health.aggregated") ?? "Agrégé sur"}
        <b>{data.summary.total}</b>
        {i18n.t("health.checks_word") ?? "checks"}
        ({data.elapsed_ms.toFixed(0)}ms)
      </p>

      <ul class="checks">
        {#each data.checks as c}
          <li class="row sev-{c.severity}">
            <span class="row-icon" style:color={sevColor(c.severity)}>
              {sevIcon(c.severity)}
            </span>
            <span class="row-label">{c.label}</span>
            <span class="row-verdict muted">{String(c.verdict ?? "—")}</span>
            <span class="row-time muted small">
              {c.elapsed_ms?.toFixed(0) ?? "?"}ms
            </span>
            {#if c.error}
              <div class="row-error">{c.error}</div>
            {/if}
          </li>
        {/each}
      </ul>

      <p class="footer-hint muted small">
        {i18n.t("health.footer_hint") ??
          "Drill-down complet sur chaque module dans Settings → Integrations."}
      </p>
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
  .health-modal {
    position: fixed;
    top: 10vh;
    left: 50%;
    transform: translateX(-50%);
    width: min(94vw, 720px);
    max-height: 80vh;
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
    background: none; border: none;
    font-size: 1.4em;
    color: var(--text-dim);
    cursor: pointer;
  }
  .meta { margin: 0 0 12px; }
  .checks { list-style: none; padding: 0; margin: 0; }
  .row {
    display: grid;
    grid-template-columns: 1.5em 1fr 2fr auto;
    align-items: baseline;
    gap: 8px;
    padding: 6px 4px;
    border-bottom: 1px solid var(--border);
  }
  .row:last-child { border-bottom: none; }
  .row-icon { font-weight: 700; font-size: 1.1em; }
  .row-label { font-weight: 600; }
  .row-verdict { font-family: ui-monospace, monospace; font-size: 0.85em; }
  .row-time { font-size: 0.75em; }
  .row-error {
    grid-column: 1 / -1;
    color: var(--err);
    font-size: 0.78em;
    padding: 2px 0 0 1.7em;
  }
  .footer-hint { margin-top: 12px; text-align: center; }
</style>
