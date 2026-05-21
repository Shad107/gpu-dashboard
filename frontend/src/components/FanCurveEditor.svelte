<script lang="ts">
  // Fan curve editor — drag-and-drop slice 2/8 (cycle 93).
  // Slice 1 (cycle 92) added the read-only viz.
  // This slice adds : pointer drag of control points + reset button.
  // Slice 3-4 (next) : add/remove points + persist via POST.
  import { onMount, onDestroy } from "svelte";
  import { i18n } from "../lib/i18n/index.svelte";
  import { toast } from "../lib/stores.svelte";

  type CurvePoint = [number, number]; // [temp_°C, fan_%]
  type FanCurveData = {
    enabled: boolean;
    running: boolean;
    curve: CurvePoint[];
    current_target_pct: number | null;
  };

  let data = $state<FanCurveData | null>(null);
  let editedCurve = $state<CurvePoint[]>([]);
  let loading = $state(false);
  let error = $state<string>("");
  let draggingIdx = $state<number | null>(null);

  async function load() {
    loading = true;
    error = "";
    try {
      const r = await fetch("/api/fan-curve");
      data = await r.json();
      // Only sync editedCurve from server if user isn't mid-edit
      if (draggingIdx === null && !isDirty) {
        editedCurve = (data?.curve ?? []).map(p => [p[0], p[1]] as CurvePoint);
      }
    } catch (e: any) {
      error = e?.message ?? String(e);
    } finally {
      loading = false;
    }
  }

  function resetEdit() {
    editedCurve = (data?.curve ?? []).map(p => [p[0], p[1]] as CurvePoint);
  }

  let saving = $state(false);
  async function save() {
    saving = true;
    try {
      const r = await fetch("/api/fan-curve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ curve: editedCurve }),
      });
      const j = await r.json();
      if (j.ok) {
        toast.emit("✓ " + i18n.t("fancurve.saved"), "ok");
        await load();  // refresh from server
      } else {
        toast.emit("✗ " + (j.error || i18n.t("fancurve.save_failed")), "err");
      }
    } catch (e: any) {
      toast.emit("✗ " + (e?.message || "save failed"), "err");
    } finally {
      saving = false;
    }
  }

  const isDirty = $derived(
    data !== null && JSON.stringify(editedCurve) !== JSON.stringify(data.curve)
  );

  let timer: ReturnType<typeof setInterval> | null = null;
  onMount(() => {
    load();
    timer = setInterval(load, 5000);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
  });
  onDestroy(() => {
    if (timer) clearInterval(timer);
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
  });

  // SVG geometry
  const W = 460;
  const H = 240;
  const PAD_L = 40;
  const PAD_R = 12;
  const PAD_T = 10;
  const PAD_B = 30;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  function xOf(temp: number): number { return PAD_L + (temp / 100) * innerW; }
  function yOf(fan: number):  number { return PAD_T + (1 - fan / 100) * innerH; }

  // Inverse : SVG pixel coords → curve domain. Used during drag.
  let svgEl: SVGSVGElement | null = $state(null);
  function eventToCurve(e: PointerEvent): CurvePoint | null {
    if (!svgEl) return null;
    const rect = svgEl.getBoundingClientRect();
    // SVG viewBox is W×H but rendered size may differ — scale via rect
    const sx = (e.clientX - rect.left) / rect.width * W;
    const sy = (e.clientY - rect.top) / rect.height * H;
    let temp = Math.round(((sx - PAD_L) / innerW) * 100);
    let fan = Math.round((1 - (sy - PAD_T) / innerH) * 100);
    temp = Math.max(0, Math.min(100, temp));
    fan = Math.max(0, Math.min(100, fan));
    return [temp, fan];
  }

  function onPointerDown(idx: number, e: PointerEvent) {
    draggingIdx = idx;
    e.preventDefault();
  }
  function onPointerMove(e: PointerEvent) {
    if (draggingIdx === null) return;
    const p = eventToCurve(e);
    if (!p) return;
    // Keep curve ordered by temp : neighbors set the temp bounds
    const idx = draggingIdx;
    const prevT = idx > 0 ? editedCurve[idx - 1][0] : 0;
    const nextT = idx < editedCurve.length - 1 ? editedCurve[idx + 1][0] : 100;
    const clampedTemp = Math.max(prevT + 1, Math.min(nextT - 1, p[0]));
    editedCurve = editedCurve.map((pp, i) =>
      i === idx ? [clampedTemp, p[1]] as CurvePoint : pp
    );
  }
  function onPointerUp() {
    if (draggingIdx !== null) draggingIdx = null;
  }

  /** Double-click on empty SVG area → insert new point at that temp/fan,
   * sorted by temp. Ignored if it would create a duplicate temp. */
  function onSvgDoubleClick(e: MouseEvent) {
    // Ignore if user double-clicked an existing point (let circle handler win)
    if ((e.target as Element)?.tagName === "circle") return;
    if (!svgEl) return;
    const rect = svgEl.getBoundingClientRect();
    const sx = (e.clientX - rect.left) / rect.width * W;
    const sy = (e.clientY - rect.top) / rect.height * H;
    let temp = Math.round(((sx - PAD_L) / innerW) * 100);
    let fan = Math.round((1 - (sy - PAD_T) / innerH) * 100);
    if (temp < 0 || temp > 100 || fan < 0 || fan > 100) return;
    // Find sorted insertion index
    let idx = editedCurve.findIndex(p => p[0] >= temp);
    if (idx < 0) idx = editedCurve.length;
    // Refuse if exact temp already exists
    if (editedCurve.some(p => p[0] === temp)) return;
    editedCurve = [...editedCurve.slice(0, idx), [temp, fan], ...editedCurve.slice(idx)];
  }

  /** Right-click on a point → confirm + remove. Min 2 points enforced. */
  function onPointContextMenu(idx: number, e: MouseEvent) {
    e.preventDefault();
    if (editedCurve.length <= 2) {
      alert(i18n.t("fancurve.min_points_warning"));
      return;
    }
    if (!confirm(i18n.t("fancurve.remove_point_confirm"))) return;
    editedCurve = editedCurve.filter((_, i) => i !== idx);
  }

  const path = $derived(
    editedCurve.length >= 2
      ? editedCurve.map((p, i) => `${i === 0 ? "M" : "L"}${xOf(p[0])},${yOf(p[1])}`).join(" ")
      : ""
  );
  const targetY = $derived(
    data?.current_target_pct != null ? yOf(data.current_target_pct) : null
  );
</script>

<div class="fancurve">
  <h3>🌀 {i18n.t("fancurve.title")}</h3>
  <p class="sub" style="margin:0 0 .8em;font-size:.82em">{i18n.t("fancurve.description_editable")}</p>

  {#if loading && !data}
    <p class="sub">{i18n.t("fancurve.loading")}</p>
  {:else if error}
    <p class="sub" style="color:var(--accent-bad)">{error}</p>
  {:else if data}
    <div class="status-row">
      <span class:on={data.enabled} class:off={!data.enabled}>
        {data.enabled ? "✓" : "·"} {i18n.t("fancurve.module")}
      </span>
      <span class:on={data.running} class:off={!data.running}>
        {data.running ? "✓" : "·"} {i18n.t("fancurve.daemon")}
      </span>
      {#if data.current_target_pct != null}
        <span style="color:var(--accent-cool)">
          🌀 {i18n.t("fancurve.current_target")}: <b>{data.current_target_pct}%</b>
        </span>
      {/if}
      {#if isDirty}
        <span style="color:var(--accent-warn)">● {i18n.t("fancurve.unsaved")}</span>
      {/if}
    </div>

    <svg viewBox="0 0 {W} {H}" class="curve-svg" preserveAspectRatio="xMidYMid meet"
         bind:this={svgEl} class:dragging={draggingIdx !== null}
         ondblclick={onSvgDoubleClick}>
      {#each [0,20,40,60,80,100] as t}
        <line x1={xOf(t)} x2={xOf(t)} y1={PAD_T} y2={PAD_T + innerH}
          stroke="var(--border-subtle)" stroke-width="0.5" />
        <text x={xOf(t)} y={H - 10} text-anchor="middle"
          fill="var(--text-faint)" font-size="11">{t}°</text>
      {/each}
      {#each [0,25,50,75,100] as f}
        <line x1={PAD_L} x2={PAD_L + innerW} y1={yOf(f)} y2={yOf(f)}
          stroke="var(--border-subtle)" stroke-width="0.5" />
        <text x={PAD_L - 6} y={yOf(f) + 4} text-anchor="end"
          fill="var(--text-faint)" font-size="11">{f}%</text>
      {/each}

      {#if targetY != null}
        <line x1={PAD_L} x2={PAD_L + innerW} y1={targetY} y2={targetY}
          stroke="var(--accent-cool)" stroke-width="1" stroke-dasharray="4 3" opacity="0.5" />
      {/if}

      {#if path}
        <path d={path} fill="none" stroke="var(--accent)" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round" />
      {/if}

      {#each editedCurve as p, i}
        <circle
          cx={xOf(p[0])}
          cy={yOf(p[1])}
          r={draggingIdx === i ? 8 : 6}
          fill="var(--accent)"
          stroke="var(--bg-card)"
          stroke-width="2"
          class="point"
          class:dragging={draggingIdx === i}
          onpointerdown={(e) => onPointerDown(i, e)}
          oncontextmenu={(e) => onPointContextMenu(i, e)}
        >
          <title>{p[0]}°C → {p[1]}% · {i18n.t("fancurve.right_click_to_delete")}</title>
        </circle>
      {/each}
    </svg>

    <div class="btn-row" style="margin-top:.6em">
      <button class="btn btn-primary" onclick={save} disabled={!isDirty || saving}>
        💾 {saving ? i18n.t("fancurve.saving") : i18n.t("fancurve.save")}
      </button>
      <button class="btn" onclick={resetEdit} disabled={!isDirty || saving}>
        ↺ {i18n.t("fancurve.reset")}
      </button>
      <span class="sub" style="font-size:.78em">
        {i18n.t("fancurve.edit_hint")}
      </span>
    </div>
  {/if}
</div>

<style>
  .fancurve { padding: 0.4em 0 0; }
  .fancurve h3 { color: var(--text-muted); margin: 0 0 .4em; font-size: 0.95em; font-weight: 600; }
  .status-row {
    display: flex;
    gap: 1em;
    flex-wrap: wrap;
    margin-bottom: 0.6em;
    font-size: 0.82em;
  }
  .status-row .on { color: var(--accent); }
  .status-row .off { color: var(--text-dim); }
  .curve-svg {
    width: 100%;
    max-width: 460px;
    height: auto;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px;
    touch-action: none;  /* prevent scroll on touch drag */
  }
  .curve-svg { cursor: copy; }  /* dbl-click empty area = add point */
  .curve-svg.dragging { cursor: grabbing; }
  circle.point { cursor: grab; transition: r 0.1s; }
  circle.point:hover { r: 7; }
  circle.point.dragging { cursor: grabbing; }
</style>
