<script lang="ts">
  import { api } from "../lib/api";
  import { toast, installPrompt } from "../lib/stores.svelte";
  import { i18n } from "../lib/i18n/index.svelte";

  type Step = {
    id: string;
    label: string;
    command: string;
    scope: "guest" | "host" | "physical";
    safety: "safe" | "kills_workloads" | "needs_host_access" | "manual";
    why: string;
  };
  type AdvisorState = Awaited<ReturnType<typeof api.pcieRecoveryAdvisorStatus>>;
  type StepResult = {
    ok: boolean;
    stdout: string;
    stderr: string;
    elapsed_ms: number;
    link_recovered: boolean | null;
    error?: string;
  };

  let { open = $bindable(false), advisor = $bindable<AdvisorState | null>(null) } = $props();

  let wrapperAvailable = $state<boolean | null>(null);
  let running = $state(false);
  let runningStep = $state<string | null>(null);
  let results = $state<Record<string, StepResult>>({});
  let recovered = $state<boolean | null>(null);

  // F5.3 — auto-run live progress
  let autoRunStartedAt = $state<number | null>(null);
  let autoRunStepIdx = $state(0);
  let autoRunStepTotal = $state(0);

  // F5.4 — pre-warning banner before destructive steps. The
  // browser tab running the dashboard may freeze when /dev/nvidia*
  // consumers are killed; the user needs an out-of-band channel
  // (a terminal tailing the log file) to know what's happening.
  let preWarningOpen = $state(false);
  const RECOVERY_LOG_PATH = "/tmp/gpu-dashboard-recovery.log";
  const TAIL_CMD = `tail -f ${RECOVERY_LOG_PATH}`;

  // tick the elapsed clock every 200ms while auto-running
  let nowTick = $state(Date.now());
  $effect(() => {
    if (!autoRunning) return;
    const id = setInterval(() => (nowTick = Date.now()), 200);
    return () => clearInterval(id);
  });
  // scroll the currently-running step into view
  $effect(() => {
    if (!runningStep) return;
    queueMicrotask(() => {
      const el = document.querySelector(
        `[data-step-id="${runningStep}"]`,
      ) as HTMLElement | null;
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });

  // Steps that the wrapper can actually execute. The advisor's full
  // plan includes host-side and manual steps which we cannot run from
  // the dashboard.
  const EXECUTABLE_STEPS = new Set(["persistence_restart", "module_reload", "pcie_rescan", "flr"]);

  $effect(() => {
    if (open && wrapperAvailable === null) {
      checkWrapper();
    }
  });

  async function checkWrapper() {
    try {
      const r = await fetch("/api/pcie-recovery/check-wrapper").then((x) => x.json());
      wrapperAvailable = !!r.available;
    } catch (e) {
      wrapperAvailable = false;
    }
  }

  function safetyBadge(s: Step["safety"]): { label: string; color: string } {
    if (s === "safe") return { label: "✓ safe", color: "var(--ok)" };
    if (s === "kills_workloads") return { label: "⚠ tue les workloads", color: "var(--warn)" };
    if (s === "needs_host_access") return { label: "🔑 accès host", color: "var(--accent)" };
    return { label: "✋ manuel", color: "var(--text-dim)" };
  }

  // F5.2b — "Tout essayer" auto-escalation. Walks every executable
  // step in plan order, stops as soon as the link comes back or
  // the last step fails. Asks ONCE upfront if the user accepts
  // kills_workloads steps (instead of confirming each one).
  let autoRunning = $state(false);
  function onClickRunAll() {
    if (!advisor?.plan || autoRunning) return;
    const executable = advisor.plan.filter((s: Step) => EXECUTABLE_STEPS.has(s.id));
    if (!executable.length) return;
    const hasKills = executable.some((s) => s.safety === "kills_workloads");
    if (hasKills) {
      // F5.4 — show the pre-warning panel inside the modal instead
      // of a browser confirm(), so the user can copy the tail
      // command and open a terminal BEFORE we kill anything.
      preWarningOpen = true;
    } else {
      void runAll();
    }
  }
  async function runAll() {
    if (!advisor?.plan || autoRunning) return;
    preWarningOpen = false;
    const executable = advisor.plan.filter((s: Step) => EXECUTABLE_STEPS.has(s.id));
    if (!executable.length) return;
    autoRunning = true;
    autoRunStartedAt = Date.now();
    autoRunStepIdx = 0;
    autoRunStepTotal = executable.length;
    try {
      for (const step of executable) {
        autoRunStepIdx += 1;
        runningStep = step.id;
        try {
          const body: Record<string, string> = { step_id: step.id };
          if (advisor?.bdf && (step.id === "pcie_rescan" || step.id === "flr")) {
            body.bdf = advisor.bdf;
          }
          const r: StepResult = await fetch("/api/pcie-recovery/run-step", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }).then((x) => x.json());
          results = { ...results, [step.id]: r };
          if (r.link_recovered === true) {
            recovered = true;
            toast.emit(`✓ Lien récupéré via ${step.label} (${(r.elapsed_ms / 1000).toFixed(1)}s)`, "ok");
            return;
          }
        } catch (e) {
          toast.emit("✗ " + (e as Error).message, "err");
          break;
        }
      }
      // Loop exited without recovery — final re-check just in case.
      await rerunCheck();
      if (recovered !== true) {
        toast.emit("⚠ Auto-run terminé — lien toujours down. Étapes host nécessaires.", "warn");
      }
    } finally {
      autoRunning = false;
      runningStep = null;
      autoRunStartedAt = null;
    }
  }

  function stepStatus(stepId: string): "pending" | "running" | "done" | "failed" {
    if (results[stepId]) {
      return results[stepId].ok ? "done" : "failed";
    }
    if (runningStep === stepId) return "running";
    return "pending";
  }

  async function runStep(step: Step) {
    if (!EXECUTABLE_STEPS.has(step.id)) {
      toast.emit("✗ ce step n'est pas exécutable depuis le dashboard", "err");
      return;
    }
    if (step.safety === "kills_workloads") {
      const ok = confirm(
        `⚠ ${step.label}\n\nCe step va tuer Chrome, VS Code, et tout process utilisant la GPU. Continuer ?`,
      );
      if (!ok) return;
    }
    running = true;
    runningStep = step.id;
    try {
      const body: Record<string, string> = { step_id: step.id };
      if (advisor?.bdf && (step.id === "pcie_rescan" || step.id === "flr")) {
        body.bdf = advisor.bdf;
      }
      const r: StepResult = await fetch("/api/pcie-recovery/run-step", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then((x) => x.json());
      results = { ...results, [step.id]: r };
      if (r.link_recovered === true) {
        recovered = true;
        toast.emit(`✓ ${i18n.t("pcierec.recovered_in") ?? "Lien récupéré en"} ${(r.elapsed_ms / 1000).toFixed(1)}s`, "ok");
      } else if (r.ok) {
        recovered = false;
        toast.emit(`✓ ${step.label} (${(r.elapsed_ms / 1000).toFixed(1)}s) — lien toujours down`, "warn");
      } else {
        toast.emit(`✗ ${step.label} a échoué`, "err");
      }
    } catch (e) {
      toast.emit("✗ " + (e as Error).message, "err");
    } finally {
      running = false;
      runningStep = null;
    }
  }

  async function rerunCheck() {
    try {
      const r: AdvisorState = await fetch("/api/pcie-recovery/check-link").then((x) => x.json());
      advisor = r;
      if (r.verdict?.verdict === "ok") {
        recovered = true;
      }
    } catch {}
  }

  function close() {
    open = false;
  }

  function copyCmd(cmd: string) {
    navigator.clipboard.writeText(cmd).then(
      () => toast.emit("✓ " + (i18n.t("pcierec.copied") ?? "copié"), "ok"),
      () => toast.emit("✗ clipboard", "err"),
    );
  }
</script>

{#if open}
  <div class="modal-backdrop" onclick={close} role="presentation"></div>
  <div class="recovery-modal" role="dialog" aria-modal="true">
    <header>
      <h3>🔧 {i18n.t("pcierec.modal_title") ?? "Récupération du lien PCIe"}</h3>
      <button class="btn-close" onclick={close} aria-label="Close">✕</button>
    </header>

    {#if advisor}
      <section class="diag">
        <div class="diag-line">
          <b>{advisor.bdf ?? "—"}</b>
          <span class="muted">virt={advisor.virt ?? "?"}</span>
          <span class="muted">{i18n.t("pcierec.verdict") ?? "verdict"}: {advisor.verdict.verdict}</span>
        </div>
        {#if advisor.diagnosis?.signals && advisor.diagnosis.signals.length > 0}
          <div class="muted small">
            {i18n.t("pcierec.signals") ?? "signaux"}: {advisor.diagnosis.signals.join(", ")}
          </div>
        {/if}
        {#if advisor.pci_state}
          <div class="muted small">
            link: {advisor.pci_state.current_link_speed ?? "?"} × {advisor.pci_state.current_link_width ?? "?"}
            (max {advisor.pci_state.max_link_speed ?? "?"} × {advisor.pci_state.max_link_width ?? "?"})
            · power {advisor.pci_state.power_state ?? "?"}
            · FLR {advisor.pci_state.flr_supported ? "yes" : "no"}
          </div>
        {/if}
      </section>
    {/if}

    {#if wrapperAvailable === true && recovered !== true}
      {#if autoRunning && autoRunStartedAt !== null}
        {@const elapsed = ((nowTick - autoRunStartedAt) / 1000).toFixed(1)}
        {@const currentStep = advisor?.plan?.find((s: Step) => s.id === runningStep)}
        <section class="auto-progress">
          <div class="progress-header">
            <span class="spinner">⏳</span>
            <b>{i18n.t("pcierec.running_step") ?? "Étape en cours"} {autoRunStepIdx}/{autoRunStepTotal}</b>
            <span class="muted small" style="margin-left:auto">{elapsed}s</span>
          </div>
          <div class="progress-bar">
            <div class="progress-fill" style:width="{(autoRunStepIdx / autoRunStepTotal) * 100}%"></div>
          </div>
          <div class="progress-current">
            <b>{currentStep?.label ?? runningStep}</b>
            <div class="muted small">{currentStep?.why ?? ""}</div>
          </div>
          {#if advisor?.plan}
            <div class="step-pills">
              {#each advisor.plan.filter((s: Step) => EXECUTABLE_STEPS.has(s.id)) as step, i}
                {@const st = stepStatus(step.id)}
                <span class="pill" class:pill-running={st === "running"}
                                    class:pill-done={st === "done"}
                                    class:pill-failed={st === "failed"}>
                  {st === "done" ? "✓" : st === "failed" ? "✗" : st === "running" ? "⏳" : "·"}
                  {i + 1}. {step.label}
                </span>
              {/each}
            </div>
          {/if}
        </section>
      {:else if preWarningOpen}
        <section class="pre-warning">
          <p style="font-weight:600;margin:0 0 6px;color:var(--warn)">
            ⚠ {i18n.t("pcierec.kills_warning_title") ??
              "Étapes destructives détectées"}
          </p>
          <p class="muted small" style="margin:0 0 10px;">
            {i18n.t("pcierec.kills_warning_body") ??
              "L'auto-escalade contient une étape qui tue les workloads consommant /dev/nvidia* (module_reload). Le navigateur peut geler quelques secondes pendant l'opération — c'est normal. Pour suivre ce qui se passe en parallèle, ouvre un terminal sur l'hôte et lance :"}
          </p>
          <pre style="font-size:0.78em;padding:6px 8px;
                      background:var(--bg-1);border-radius:4px;
                      margin:6px 0;border:1px solid var(--border);
                      user-select:all">{TAIL_CMD}</pre>
          <button class="btn btn-small"
                  onclick={() => copyCmd(TAIL_CMD)}
                  style="margin-bottom:10px">
            📋 {i18n.t("pcierec.copy_tail") ?? "Copier la commande"}
          </button>
          <div style="display:flex;gap:8px;margin-top:6px;
                       justify-content:flex-end;flex-wrap:wrap">
            <button type="button" class="btn btn-small"
                    onclick={() => (preWarningOpen = false)}>
              {i18n.t("pcierec.cancel") ?? "Annuler"}
            </button>
            <button type="button" class="btn"
                    style="background:var(--warn);color:var(--bg-1);
                           font-weight:600"
                    onclick={() => void runAll()}>
              ▶▶ {i18n.t("pcierec.kills_confirm") ??
                "Je comprends, lancer quand même"}
            </button>
          </div>
        </section>
      {:else}
        <section class="auto-run">
          <button
            class="btn auto-run-btn"
            disabled={running}
            onclick={onClickRunAll}
          >
            ▶▶ {i18n.t("pcierec.run_all") ?? "Tout essayer (auto-escalade)"}
          </button>
          <p class="muted small" style="margin: 4px 0 0;">
            {i18n.t("pcierec.run_all_help") ??
              "Lance les 4 étapes guest dans l'ordre, s'arrête dès que le lien revient. Demande confirmation une fois si des étapes tuent les workloads."}
          </p>
        </section>
      {/if}
    {/if}

    {#if wrapperAvailable === false}
      <section class="install-cta">
        <p style="font-weight:600;margin:0 0 8px;">
          ⚠ {i18n.t("pcierec.wrapper_missing") ?? "Le wrapper sudoers n'est pas installé."}
        </p>
        <p class="muted small" style="margin:0 0 10px;">
          {i18n.t("pcierec.install_explain") ?? "Une étape root est nécessaire une fois. Installe le wrapper sudoers maintenant — l'invite te demandera ton mot de passe sudo."}
        </p>

        <button
          type="button"
          class="btn"
          style="background:var(--accent);color:var(--bg-1);font-weight:600;"
          onclick={() => installPrompt.request("pcie_recovery_wrapper", checkWrapper)}
        >
          🔧 {i18n.t("pcierec.install_now") ?? "Installer le wrapper"}
        </button>

        <details style="margin-top:8px;">
          <summary class="muted small">
            {i18n.t("pcierec.install_terminal_fallback") ?? "Ou installer depuis un terminal"}
          </summary>
          <pre style="font-size:0.78em;padding:6px;background:var(--bg-1);border-radius:4px;margin:6px 0;">sudo bash scripts/install-pcie-recovery-wrapper.sh --user $USER</pre>
          <button class="btn btn-small"
                  onclick={() => copyCmd("sudo bash scripts/install-pcie-recovery-wrapper.sh --user $USER")}
            >📋 Copy</button>
          <button class="btn btn-small" onclick={checkWrapper}>{i18n.t("pcierec.recheck") ?? "Re-vérifier"}</button>
        </details>
      </section>
    {/if}

    {#if recovered === true}
      <section class="banner ok">
        ✅ {i18n.t("pcierec.recovered_banner") ?? "Lien récupéré"} —
        {advisor?.pci_state?.current_link_speed ?? "?"} × {advisor?.pci_state?.current_link_width ?? "?"}
      </section>
    {/if}

    <section class="plan">
      <h4>{i18n.t("pcierec.plan_header") ?? "Plan d'escalade"}</h4>
      <ol>
        {#each advisor?.plan ?? [] as step, i}
          {@const r = results[step.id]}
          {@const isExec = EXECUTABLE_STEPS.has(step.id)}
          {@const badge = safetyBadge(step.safety)}
          {@const st = stepStatus(step.id)}
          <li data-step-id={step.id}
              class:done={r?.ok}
              class:fail={r && !r.ok}
              class:running={runningStep === step.id}>
            <div class="step-head">
              <span class="status-icon"
                style:color={st === "done" ? "var(--ok)" :
                             st === "failed" ? "var(--err)" :
                             st === "running" ? "var(--accent)" :
                             "var(--text-dim)"}>
                {st === "done" ? "✓" : st === "failed" ? "✗" : st === "running" ? "⏳" : "·"}
              </span>
              <span class="num">{i + 1}.</span>
              <b>{step.label}</b>
              <span class="badge" style:color={badge.color}>[{step.scope}] {badge.label}</span>
            </div>
            <div class="muted small">{step.why}</div>
            <pre class="cmd">{step.command}</pre>
            <div class="actions">
              {#if isExec}
                <button
                  class="btn btn-small"
                  disabled={running || wrapperAvailable === false || recovered === true}
                  onclick={() => runStep(step)}
                >
                  {runningStep === step.id ? "⏳ ..." : (i18n.t("pcierec.run") ?? "▶ Exécuter")}
                </button>
              {/if}
              <button class="btn btn-small" onclick={() => copyCmd(step.command)}>📋 Copy</button>
            </div>
            {#if r}
              <div class="result" class:fail={!r.ok}>
                <div class="muted small">
                  {r.ok ? "✓" : "✗"} {(r.elapsed_ms / 1000).toFixed(1)}s
                  {#if r.link_recovered === true}<span style="color:var(--ok)">· lien UP ✅</span>{:else if r.link_recovered === false}<span style="color:var(--warn)">· lien toujours down</span>{/if}
                </div>
                {#if r.stdout}
                  <pre class="output">{r.stdout}</pre>
                {/if}
                {#if r.stderr}
                  <pre class="output err">{r.stderr}</pre>
                {/if}
              </div>
            {/if}
          </li>
        {/each}
      </ol>
    </section>

    <footer>
      <button class="btn btn-small" onclick={rerunCheck} disabled={running}>{i18n.t("pcierec.recheck_link") ?? "Re-vérifier le lien"}</button>
      <button class="btn" onclick={close}>{i18n.t("pcierec.close") ?? "Fermer"}</button>
    </footer>
  </div>
{/if}

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.78);
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
    z-index: 1900;
  }
  .recovery-modal {
    position: fixed;
    top: 5%;
    left: 50%;
    transform: translateX(-50%);
    width: min(92vw, 760px);
    max-height: 90vh;
    overflow-y: auto;
    /* Explicit opaque fallback in case --bg-1 itself has alpha. */
    background: var(--bg-1, #14171b);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 12px;
    z-index: 2000;
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.75);
    padding: 18px 22px 14px;
    /* Belt-and-suspenders against any background-image bleed */
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
    font-size: 1.05em;
  }
  .btn-close {
    background: none;
    border: none;
    font-size: 1.4em;
    color: var(--text-dim);
    cursor: pointer;
  }
  section {
    margin-bottom: 12px;
  }
  .diag-line {
    display: flex;
    gap: 12px;
    align-items: baseline;
    flex-wrap: wrap;
  }
  .small {
    font-size: 0.85em;
  }
  .auto-run {
    border-left: 3px solid var(--accent);
    padding-left: 10px;
    margin-bottom: 12px;
  }
  .auto-progress {
    position: sticky;
    top: -18px;
    z-index: 10;
    margin: 0 -22px 12px;
    padding: 12px 22px;
    background: var(--bg-2);
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
  }
  .progress-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }
  .progress-header .spinner {
    display: inline-block;
    animation: spin 1.4s linear infinite;
  }
  @keyframes spin {
    from { transform: rotate(0deg); }
    to   { transform: rotate(360deg); }
  }
  .progress-bar {
    height: 6px;
    background: var(--bg-1);
    border-radius: 3px;
    overflow: hidden;
    margin-bottom: 8px;
  }
  .progress-fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.3s ease;
  }
  .progress-current {
    margin: 6px 0;
    padding: 6px 10px;
    background: var(--bg-1);
    border-radius: 4px;
    font-size: 0.9em;
  }
  .step-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 8px;
  }
  .pill {
    font-size: 0.75em;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--bg-1);
    color: var(--text-dim);
    border: 1px solid var(--border);
    white-space: nowrap;
  }
  .pill-running {
    background: var(--accent);
    color: var(--bg-1);
    font-weight: 600;
    animation: pulse 1.2s ease-in-out infinite;
  }
  .pill-done {
    background: var(--ok);
    color: var(--bg-1);
    border-color: var(--ok);
  }
  .pill-failed {
    background: var(--err);
    color: var(--bg-1);
    border-color: var(--err);
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50%      { opacity: 0.65; }
  }
  .status-icon {
    font-weight: 700;
    width: 1.1em;
    text-align: center;
    display: inline-block;
  }
  .plan li.running {
    border-color: var(--accent);
    border-left-width: 4px;
    background: color-mix(in srgb, var(--accent) 8%, var(--bg-2));
  }
  .auto-run-btn {
    width: 100%;
    padding: 10px;
    font-weight: 600;
    background: var(--accent);
    color: var(--bg-1);
    border: none;
    border-radius: 6px;
    cursor: pointer;
  }
  .auto-run-btn:disabled {
    opacity: 0.6;
    cursor: progress;
  }
  .install-cta {
    border-left: 3px solid var(--warn);
    padding-left: 10px;
  }
  .pre-warning {
    border-left: 3px solid var(--warn);
    background: rgba(234, 179, 8, 0.08);
    padding: 10px 12px;
    border-radius: 4px;
  }
  .install-cta pre {
    background: var(--bg-2);
    padding: 6px 10px;
    border-radius: 4px;
    margin: 4px 0;
  }
  .banner {
    padding: 10px;
    border-radius: 6px;
    font-weight: 600;
    text-align: center;
  }
  .banner.ok {
    background: rgba(34, 197, 94, 0.15);
    color: var(--ok);
    border: 1px solid var(--ok);
  }
  .plan h4 {
    margin: 8px 0 6px;
  }
  .plan ol {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  .plan li {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 8px;
    background: var(--bg-2);
  }
  .plan li.done {
    border-left: 4px solid var(--ok);
  }
  .plan li.fail {
    border-left: 4px solid var(--err);
  }
  .step-head {
    display: flex;
    gap: 8px;
    align-items: baseline;
    flex-wrap: wrap;
  }
  .num {
    color: var(--text-dim);
  }
  .badge {
    font-size: 0.78em;
    font-family: monospace;
  }
  .cmd {
    background: var(--bg-1);
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 0.82em;
    margin: 6px 0 4px;
    overflow-x: auto;
  }
  .actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .result {
    margin-top: 6px;
  }
  .output {
    background: var(--bg-1);
    padding: 6px 10px;
    border-radius: 4px;
    font-size: 0.78em;
    max-height: 140px;
    overflow-y: auto;
    margin: 4px 0;
  }
  .output.err {
    border-left: 3px solid var(--err);
  }
  footer {
    display: flex;
    justify-content: space-between;
    border-top: 1px solid var(--border);
    padding-top: 10px;
    gap: 10px;
  }
</style>
