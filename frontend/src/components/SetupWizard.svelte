<script lang="ts">
  import { onMount } from "svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { toast, wizard } from "../lib/stores.svelte";
  import { api, type SetupDetect, type ModuleRec } from "../lib/api";

  type Props = { dismissable?: boolean };
  const { dismissable = false }: Props = $props();

  const TOTAL_STEPS = 5;
  // Support ?step=N in the URL for screenshot tooling + bookmarking.
  function initialStep(): number {
    const m = (typeof location !== "undefined" ? location.search : "").match(/[?&]step=(\d+)/);
    if (!m) return 1;
    const n = parseInt(m[1], 10);
    return n >= 1 && n <= TOTAL_STEPS ? n : 1;
  }
  let step = $state(initialStep());
  let detect = $state<SetupDetect | null>(null);
  let loading = $state(true);
  let saving = $state(false);
  let restarting = $state(false);

  // Click "Redémarrer et ouvrir le dashboard" on step 5 — POSTs /api/restart,
  // then polls /api/state until it comes back, then reloads.
  async function restartAndOpen() {
    restarting = true;
    try {
      await api.restart();
    } catch {
      // ignore — the connection drop is expected when systemd kills the process
    }
    // Poll every 1s for up to 30s
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const r = await fetch("/api/version", { cache: "no-store" });
        if (r.ok) {
          location.replace("/");
          return;
        }
      } catch {
        // still down, keep polling
      }
    }
    // Give up after 30s — force a reload anyway
    location.replace("/");
  }

  // Selected modules (initialized from detect recommendations)
  let selected = $state<Record<string, boolean>>({});

  // Module install status: tracks ok/pending per module after sudo
  let installStatus = $state<Record<string, { ok: boolean; reason: string }>>({});

  // Final config
  let port = $state(9999);
  let powerDefault = $state(250);
  // Cycle 77 : Simple mode vs LLM rig (user feedback 23:32)
  let llmMode = $state<"standard" | "llm">("standard");
  let llmServerUrl = $state("http://127.0.0.1:8080");

  async function loadDetect() {
    loading = true;
    try {
      detect = await api.setupDetect();
      // Initialize selected modules from recommendations
      for (const m of detect.modules) {
        if (selected[m.name] === undefined) {
          selected[m.name] = m.available && m.recommend;
        }
        installStatus[m.name] = { ok: m.available, reason: m.reason };
      }
    } catch (e) {
      toast.emit("✗ " + i18n.t("ts.network_error") + ": " + (e as Error).message, "err");
    } finally {
      loading = false;
    }
  }

  async function recheck(moduleName: string) {
    try {
      const r = await api.setupRecheck(moduleName);
      installStatus[moduleName] = { ok: r.ok, reason: r.reason };
      toast.emit(r.ok ? "✓ " + i18n.t("setup.status_ok") : "⏳ " + r.reason, r.ok ? "ok" : "err");
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    }
  }

  async function copyToClipboard(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.emit("✓ " + i18n.t("setup.copied"), "ok");
    } catch {
      toast.emit("✗ Clipboard unavailable — select and copy manually", "err");
    }
  }

  function sudoCommandFor(moduleName: string): string {
    // Each script lives in scripts/ in the repo, after `git clone`
    // The user runs them from the repo root or via absolute path.
    const repoHint = "~/gpu-dashboard";  // typical clone location
    if (moduleName === "power_limit") {
      return `sudo bash ${repoHint}/scripts/install-power-limit-wrapper.sh --user $USER`;
    }
    if (moduleName === "clock_offsets") {
      const headless = detect?.env.virt.is_vm || detect?.env.external_gpu.likely_external;
      return headless
        ? `sudo bash ${repoHint}/scripts/install-coolbits-xorg.sh --headless`
        : `sudo bash ${repoHint}/scripts/install-coolbits-xorg.sh`;
    }
    if (moduleName === "oculink_watchdog") {
      return `sudo bash ${repoHint}/scripts/install-oculink-watchdog.sh`;
    }
    if (moduleName === "telegram_alerts") {
      return "# No sudo needed — configure your bot token in the Alerts tab after setup.";
    }
    return "# Unknown module";
  }

  function needsSudo(moduleName: string): boolean {
    return ["power_limit", "clock_offsets", "oculink_watchdog"].includes(moduleName);
  }

  const enabledModulesNeedingSudo = $derived(
    detect ? detect.modules.filter(m => selected[m.name] && needsSudo(m.name)) : []
  );

  async function save() {
    saving = true;
    try {
      const r = await api.setupSave({
        modules: selected,
        port,
        power_default: powerDefault,
        llm_server_url: llmMode === "llm" ? llmServerUrl.trim() : "",
      });
      if (r.ok) {
        step = 5;
        toast.emit("✓ Saved to " + r.path, "ok");
      } else {
        toast.emit("✗ " + (r.error || "save failed"), "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally {
      saving = false;
    }
  }

  function next() { if (step < TOTAL_STEPS) step++; }
  function back() { if (step > 1) step--; }

  onMount(loadDetect);
</script>

<div class="setup-overlay">
  <div class="setup-container">
    <div class="setup-header">
      <h1>🎛️ gpu-dashboard</h1>
      {#if dismissable}
        <button class="setup-close" aria-label="Close" onclick={() => wizard.dismiss()}>×</button>
      {/if}
      <div class="setup-step">{i18n.t("setup.step_label", { n: step, total: TOTAL_STEPS })}</div>
      <div class="setup-progress">
        {#each Array(TOTAL_STEPS) as _, i}
          <div class="dot" class:done={i + 1 < step} class:active={i + 1 === step}></div>
        {/each}
      </div>
    </div>

    <div class="setup-body">
      {#if loading}
        <p style="text-align:center;color:#7c8aa3">{i18n.t("history.loading")}</p>
      {:else if !detect}
        <p style="text-align:center;color:#f87171">Detection failed. Reload the page.</p>
      {:else}
        <!-- Step 1: Welcome -->
        {#if step === 1}
          <h2>{i18n.t("setup.welcome_title")}</h2>
          <p class="sub">{i18n.t("setup.welcome_intro")}</p>

          <h3 style="margin-top:1.5em">{i18n.t("setup.detected_title")}</h3>
          <table class="detect-table">
            <tbody>
              <tr><td>OS</td><td>{detect.env.os.pretty_name ?? "?"} <span class="muted">({detect.env.os.package_manager ?? "?"})</span></td></tr>
              <tr><td>GPU</td>
                <td>
                  {#if detect.env.nvidia.gpus[0]}
                    {detect.env.nvidia.gpus[0].name}
                    <span class="muted">({detect.env.nvidia.gpus[0].vram_mib} MiB, driver {detect.env.nvidia.driver_version})</span>
                  {:else}
                    <span style="color:#f87171">none detected</span>
                  {/if}
                </td>
              </tr>
              <tr><td>Profile</td><td>{detect.profile?.model ?? "_generic"}</td></tr>
              <tr><td>PCIe link</td>
                <td>
                  {#if detect.env.external_gpu.link_width}
                    x{detect.env.external_gpu.link_width}
                    {#if detect.env.external_gpu.likely_external}<span class="muted">(likely eGPU / OcuLink)</span>{/if}
                  {:else}—{/if}
                </td>
              </tr>
              <tr><td>Coolbits</td><td>{detect.env.coolbits.enabled ? `configured (${detect.env.coolbits.value})` : "not configured"}</td></tr>
              {#if detect.env.virt.is_vm}
                <tr><td>Virt</td><td>{detect.env.virt.type} (VM)</td></tr>
              {/if}
            </tbody>
          </table>
        {/if}

        <!-- Step 2: Modules -->
        {#if step === 2}
          <h2>{i18n.t("setup.modules_title")}</h2>
          <p class="sub">{i18n.t("setup.modules_intro")}</p>
          <div style="margin-top:1em">
            {#each detect.modules as m}
              <label class="module-row" class:disabled={!m.available}>
                <input
                  type="checkbox"
                  bind:checked={selected[m.name]}
                  disabled={!m.available}
                />
                <div class="module-info">
                  <div class="module-name">
                    {m.name}
                    {#if m.available}
                      <span class="tag ok">{i18n.t("setup.module_available")}</span>
                    {:else}
                      <span class="tag bad">{i18n.t("setup.module_unavailable")}</span>
                    {/if}
                    {#if m.recommend}
                      <span class="tag rec">{i18n.t("setup.module_recommended")}</span>
                    {/if}
                  </div>
                  <div class="module-reason">{m.reason}</div>
                </div>
              </label>
            {/each}
          </div>
        {/if}

        <!-- Step 3: Sudo commands -->
        {#if step === 3}
          <h2>{i18n.t("setup.sudo_title")}</h2>
          <p class="sub">{i18n.t("setup.sudo_intro")}</p>

          {#if enabledModulesNeedingSudo.length === 0}
            <p style="margin-top:1em;color:#4ade80">No sudo commands needed for the selected modules.</p>
          {:else}
            {#each enabledModulesNeedingSudo as m}
              <div class="sudo-block">
                <div class="sudo-head">
                  <span class="module-name">{m.name}</span>
                  {#if installStatus[m.name]?.ok}
                    <span class="tag ok">{i18n.t("setup.status_ok")}</span>
                  {:else}
                    <span class="tag warn">{i18n.t("setup.status_pending")}</span>
                  {/if}
                </div>
                <div class="sudo-cmd">
                  <code>{sudoCommandFor(m.name)}</code>
                  <button class="btn" onclick={() => copyToClipboard(sudoCommandFor(m.name))}>
                    {i18n.t("setup.copy")}
                  </button>
                </div>
                <div class="sudo-actions">
                  <button class="btn btn-primary" onclick={() => recheck(m.name)}>
                    {i18n.t("setup.recheck")}
                  </button>
                  <span class="muted">{installStatus[m.name]?.reason ?? ""}</span>
                </div>
              </div>
            {/each}
          {/if}
        {/if}

        <!-- Step 4: Final config -->
        {#if step === 4}
          <h2>{i18n.t("setup.config_title")}</h2>
          <div style="margin-top:1em">
            <label class="form-row">
              <span class="form-lbl">{i18n.t("setup.config_port")}</span>
              <input class="al-input" type="number" bind:value={port} min="1024" max="65535" />
            </label>
            <label class="form-row">
              <span class="form-lbl">{i18n.t("setup.config_power_default")}</span>
              <input class="al-input" type="number" bind:value={powerDefault} min="100" max="600" />
            </label>
          </div>

          <h3 style="margin-top:1.4em;color:#cdd2da;font-size:.95em;font-weight:600">
            {i18n.t("setup.llm_mode_title")}
          </h3>
          <p class="sub" style="margin:0 0 .8em">{i18n.t("setup.llm_mode_description")}</p>
          <div class="mode-tiles">
            <button
              class="mode-tile"
              class:active={llmMode === "standard"}
              onclick={() => llmMode = "standard"}
            >
              <div class="mode-tile-emoji">🖥️</div>
              <div class="mode-tile-name">{i18n.t("setup.llm_mode_standard")}</div>
              <div class="mode-tile-desc">{i18n.t("setup.llm_mode_standard_desc")}</div>
            </button>
            <button
              class="mode-tile"
              class:active={llmMode === "llm"}
              onclick={() => llmMode = "llm"}
            >
              <div class="mode-tile-emoji">🤖</div>
              <div class="mode-tile-name">{i18n.t("setup.llm_mode_llm")}</div>
              <div class="mode-tile-desc">{i18n.t("setup.llm_mode_llm_desc")}</div>
            </button>
          </div>
          {#if llmMode === "llm"}
            <label class="form-row" style="margin-top:.8em">
              <span class="form-lbl">{i18n.t("setup.llm_url_label")}</span>
              <input class="al-input" type="url" bind:value={llmServerUrl}
                placeholder="http://127.0.0.1:8080" />
            </label>
            <p class="sub" style="font-size:.78em;margin:.2em 0 0 130px">
              {i18n.t("setup.llm_url_hint")}
            </p>
          {/if}
        {/if}

        <!-- Step 5: Done -->
        {#if step === 5}
          {@const restartingNow = restarting}
          <h2 style="color:#4ade80">✓ {i18n.t("setup.done_title")}</h2>
          <p>{i18n.t("setup.done_intro_short") ?? "Configuration sauvée dans ~/.config/gpu-dashboard/config.env."}</p>

          <div class="btn-row" style="margin-top:1.2em;gap:.8em">
            <button class="btn btn-primary" disabled={restartingNow} onclick={restartAndOpen}>
              {restartingNow
                ? "⏳ " + (i18n.t("setup.restarting") ?? "Redémarrage…")
                : "🔄 " + (i18n.t("setup.restart_and_open") ?? "Redémarrer et ouvrir le dashboard")}
            </button>
            {#if dismissable}
              <button class="btn" onclick={() => wizard.dismiss()}>{i18n.t("modal.close")}</button>
            {/if}
          </div>

          <details style="margin-top:1.4em">
            <summary class="sub" style="cursor:pointer;font-size:.85em">
              {i18n.t("setup.manual_restart_hint") ?? "Sinon, en ligne de commande :"}
            </summary>
            <code style="display:block;margin-top:.6em;padding:.7em;background:#0e1014;border-radius:6px;font-size:.85em">
              systemctl --user restart gpu-dashboard.service
            </code>
            <p class="sub" style="margin-top:.4em;font-size:.78em">
              {i18n.t("setup.manual_restart_hint_2") ?? "(ou Ctrl+C puis relancer si tu tournes en foreground)"}
            </p>
          </details>
        {/if}
      {/if}
    </div>

    <div class="setup-footer">
      <button class="btn" disabled={step === 1 || step === 5} onclick={back}>
        {i18n.t("setup.back")}
      </button>
      <div style="flex:1"></div>
      {#if step < 4}
        <button class="btn btn-primary" disabled={loading || !detect} onclick={next}>
          {i18n.t("setup.next")}
        </button>
      {:else if step === 4}
        <button class="btn btn-primary" disabled={saving} onclick={save}>
          {saving ? i18n.t("setup.saving") : i18n.t("setup.save_and_finish")}
        </button>
      {/if}
    </div>
  </div>
</div>

<style>
  .setup-overlay {
    position: fixed; inset: 0;
    background: #0e1014;
    z-index: 3000;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 2em 1em;
    overflow-y: auto;
  }
  .setup-container {
    background: #14171f;
    border: 1px solid #2a2e38;
    border-radius: 12px;
    width: min(880px, 96vw);
    min-height: min(620px, 90vh);
    display: flex;
    flex-direction: column;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.6);
  }
  .setup-header {
    padding: 1.4em 2em 0.8em;
    border-bottom: 1px solid #2a2e38;
  }
  .setup-header { position: relative; }
  .setup-header h1 {
    margin: 0 0 0.3em;
    font-size: 1.4em;
  }
  .setup-close {
    position: absolute;
    top: 1.4em;
    right: 1.6em;
    background: none;
    border: none;
    color: #7c8aa3;
    cursor: pointer;
    font-size: 1.6em;
    line-height: 1;
    padding: 0.1em 0.45em;
    border-radius: 6px;
    transition: all 0.15s;
  }
  .setup-close:hover { background: #22262e; color: #e6e6ee; }
  .setup-step {
    color: #7c8aa3;
    font-size: 0.8em;
    margin-bottom: 0.6em;
  }
  .setup-progress {
    display: flex;
    gap: 0.4em;
  }
  .setup-progress .dot {
    flex: 1;
    height: 4px;
    border-radius: 2px;
    background: #2a2e38;
  }
  .setup-progress .dot.done { background: #4ade80; }
  .setup-progress .dot.active { background: #fbbf24; }
  .setup-body {
    flex: 1;
    padding: 1.5em 2em;
    overflow-y: auto;
  }
  .setup-body h2 { margin: 0 0 0.4em; font-size: 1.2em; font-weight: 500; }
  .setup-body h3 { margin: 0 0 0.4em; font-size: 0.95em; color: #cdd2da; font-weight: 600; }
  .detect-table { width: 100%; margin-top: 0.5em; }
  .detect-table td { padding: 0.4em 0; vertical-align: top; }
  .detect-table td:first-child { width: 120px; color: #7c8aa3; }
  .muted { color: #7c8aa3; font-size: 0.85em; }
  .module-row {
    display: flex;
    align-items: flex-start;
    gap: 0.8em;
    padding: 0.7em 0.8em;
    border: 1px solid #2a2e38;
    border-radius: 8px;
    margin-bottom: 0.5em;
    cursor: pointer;
  }
  .module-row.disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .module-row input[type=checkbox] { margin-top: 4px; }
  .module-info { flex: 1; }
  .module-name { font-weight: 600; color: #e6e6ee; }
  .module-reason { color: #8a8f9a; font-size: 0.85em; margin-top: 0.25em; }
  .tag {
    display: inline-block;
    padding: 0.1em 0.5em;
    border-radius: 3px;
    font-size: 0.7em;
    font-weight: 600;
    margin-left: 0.5em;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .tag.ok { background: rgba(74,222,128,0.15); color: #4ade80; border: 1px solid rgba(74,222,128,0.4); }
  .tag.bad { background: rgba(248,113,113,0.18); color: #f87171; border: 1px solid rgba(248,113,113,0.4); }
  .tag.rec { background: rgba(251,191,36,0.15); color: #fbbf24; border: 1px solid rgba(251,191,36,0.4); }
  .tag.warn { background: rgba(251,146,60,0.15); color: #fb923c; border: 1px solid rgba(251,146,60,0.4); }
  .sudo-block {
    border: 1px solid #2a2e38;
    border-radius: 8px;
    padding: 0.8em 1em;
    margin-bottom: 0.8em;
    background: #0e1014;
  }
  .sudo-head { display: flex; align-items: center; gap: 0.5em; margin-bottom: 0.6em; }
  .sudo-cmd { display: flex; gap: 0.4em; align-items: center; margin-bottom: 0.5em; }
  .sudo-cmd code {
    flex: 1;
    background: #1a1d24;
    padding: 0.5em 0.7em;
    border-radius: 4px;
    color: #a3e635;
    font-size: 0.82em;
    word-break: break-all;
    user-select: all;
  }
  .sudo-actions { display: flex; align-items: center; gap: 0.8em; font-size: 0.82em; }
  .setup-footer {
    padding: 1em 2em;
    border-top: 1px solid #2a2e38;
    display: flex;
    align-items: center;
    gap: 0.6em;
  }
</style>
