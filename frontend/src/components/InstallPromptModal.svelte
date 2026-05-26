<script lang="ts">
  /**
   * F6 — generalized one-click installer modal.
   *
   * Usage:
   *   import { installPrompt } from "../lib/stores.svelte";
   *   ...
   *   installPrompt.request("oculink_watchdog", () => refreshSomething());
   *
   * The modal handles the password input + the POST + the result
   * toast. Caller gets a callback when install succeeds, can refresh
   * its own state.
   */
  import { installPrompt, toast } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";

  type ScriptInfo = {
    id: string;
    label: string;
    label_key?: string | null;
    description: string;
    description_key?: string | null;
    installed: boolean;
    script_path: string | null;
    script_exists: boolean;
  };

  // Resolve an i18n key with a fallback to the backend-provided
  // English string. The English string IS the fallback baked into
  // the i18n.t call (the runtime falls back to en.json then to the
  // key itself if neither has a translation), so we explicitly
  // override with the spec value when the lookup returned the raw
  // key.
  function trWithFallback(key: string | null | undefined,
                          fallback: string): string {
    if (!key) return fallback;
    const v = i18n.t(key as any);
    return (v === key) ? fallback : v;
  }

  let scripts = $state<ScriptInfo[]>([]);
  let password = $state("");
  let installing = $state(false);
  let installError = $state<string | null>(null);

  // Reset state when modal opens for a new script.
  $effect(() => {
    if (installPrompt.open) {
      password = "";
      installError = null;
      // Lazy-load the inventory once per open.
      if (scripts.length === 0) {
        fetch("/api/install/list")
          .then((r) => r.json())
          .then((d) => (scripts = d.scripts ?? []))
          .catch(() => (scripts = []));
      }
    }
  });

  const spec = $derived(scripts.find((s) => s.id === installPrompt.scriptId));

  async function runInstall(e: Event) {
    e.preventDefault();
    if (!password || installing || !installPrompt.scriptId) return;
    installing = true;
    installError = null;
    try {
      const r = await fetch("/api/install/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script_id: installPrompt.scriptId,
          password,
        }),
      }).then((x) => x.json());
      password = ""; // scrub client-side
      if (r.ok) {
        toast.emit("✓ " + (spec?.label ?? "Script") +
          " " + (i18n.t("install.installed") ?? "installé"), "ok");
        const cb = installPrompt.onInstalled;
        installPrompt.close();
        cb?.();
      } else {
        installError = r.message ?? r.error ?? "install failed";
        if (r.error === "wrong_password") {
          toast.emit("✗ " + (i18n.t("install.wrong_password") ?? "Mot de passe incorrect"), "err");
        } else {
          toast.emit("✗ " + installError, "err");
        }
      }
    } catch (e) {
      installError = (e as Error).message;
      toast.emit("✗ " + installError, "err");
    } finally {
      installing = false;
    }
  }

  function close() {
    password = "";
    installPrompt.close();
  }
</script>

{#if installPrompt.open}
  <div class="modal-backdrop" onclick={close} role="presentation"></div>
  <div class="install-modal" role="dialog" aria-modal="true">
    <header>
      <h3>🔧 {i18n.t("install.title") ?? "Installation"}</h3>
      <button class="btn-close" onclick={close} aria-label="Close">✕</button>
    </header>
    {#if spec}
      <p>
        <b>{trWithFallback(spec.label_key, spec.label)}</b>
      </p>
      <p class="muted small">{trWithFallback(spec.description_key, spec.description)}</p>
      {#if spec.script_path}
        <p class="muted small" style="font-family:monospace">
          {spec.script_path}
        </p>
      {/if}
    {:else if installPrompt.scriptId}
      <p class="muted">{installPrompt.scriptId}</p>
    {/if}

    <form onsubmit={runInstall}>
      <label class="form-row" style="display:block;margin:12px 0 4px">
        <span class="form-lbl">{i18n.t("install.password") ?? "Mot de passe sudo"}</span>
        <input
          type="password"
          bind:value={password}
          disabled={installing}
          autocomplete="current-password"
          autofocus
          style="width:100%;padding:8px 10px;
                 background:var(--bg-1);color:var(--text);
                 border:1px solid var(--border);border-radius:4px;"
        />
      </label>

      {#if installError}
        <p style="color:var(--err);font-size:0.85em;margin:6px 0;">
          ✗ {installError}
        </p>
      {/if}

      <div style="display:flex;gap:8px;margin-top:12px;justify-content:flex-end;">
        <button type="button" class="btn btn-small" onclick={close}>
          {i18n.t("install.cancel") ?? "Annuler"}
        </button>
        <button
          type="submit"
          class="btn"
          disabled={installing || !password}
          style="background:var(--accent);color:var(--bg-1);font-weight:600;"
        >
          {installing
            ? "⏳ " + (i18n.t("install.running") ?? "Installation...")
            : "🔧 " + (i18n.t("install.do_install") ?? "Installer")}
        </button>
      </div>
    </form>
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
  .install-modal {
    position: fixed;
    top: 25%;
    left: 50%;
    transform: translateX(-50%);
    width: min(92vw, 480px);
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
  header h3 {
    margin: 0;
  }
  .btn-close {
    background: none;
    border: none;
    font-size: 1.4em;
    color: var(--text-dim);
    cursor: pointer;
  }
  .small {
    font-size: 0.85em;
  }
</style>
