// Pure SVG chart renderers (Catmull-Rom smooth paths). No DOM access — returns
// SVG markup as a string for {@html ...} in Svelte.

import type { Sample } from "./api";

type Pt = { x: number; y: number };

export function smoothPath(pts: Pt[]): string {
  if (pts.length < 2) return "";
  let d = `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C ${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  }
  return d;
}

export function renderCoolingChart(hist: Sample[], samplingText: string): string {
  if (!hist.length) {
    return `<div style="color:#7c8aa3;padding:1em;text-align:center">${samplingText}</div>`;
  }
  const W = 1200, H = 280, PAD_L = 50, PAD_R = 56, PAD_T = 20, PAD_B = 30;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const x = (i: number) => PAD_L + (hist.length > 1 ? (i / (hist.length - 1)) * innerW : innerW / 2);

  // Auto-scale Y axes against the actual data + 10% headroom,
  // rounded to nice values, so peaks never clip outside the chart
  // area. Static 0-3000 / 30-90 floors were leaking the top of
  // 24h-range curves past the legend labels when fans/temp briefly
  // exceeded the static caps.
  const ceilTo = (v: number, step: number) =>
    Math.max(step, Math.ceil(v / step) * step);
  const dataMaxRpm = Math.max(
    ...hist.map((h) => Math.max(h.fan0_rpm || 0, h.fan1_rpm || 0)),
  );
  const rpmMax = ceilTo(dataMaxRpm * 1.1, 1000);
  const dataMaxTemp = Math.max(...hist.map((h) => h.temp ?? 0));
  const dataMinTemp = Math.min(...hist.map((h) => h.temp ?? 100));
  const tMin = Math.max(0, Math.floor(dataMinTemp / 10) * 10 - 5);
  const tMax = Math.max(tMin + 20, Math.ceil(dataMaxTemp / 10) * 10 + 5);
  const yTemp = (v: number) => PAD_T + (1 - (v - tMin) / (tMax - tMin)) * innerH;
  const yRpm = (r: number) => PAD_T + (1 - r / rpmMax) * innerH;

  const tempPts = hist.map((h, i) => ({ x: x(i), y: yTemp(h.temp) }));
  const f0Pts = hist.map((h, i) => ({ x: x(i), y: yRpm(h.fan0_rpm || 0) }));
  const f1Pts = hist.map((h, i) => ({ x: x(i), y: yRpm(h.fan1_rpm || 0) }));
  const tempD = smoothPath(tempPts);
  const f0D = smoothPath(f0Pts);
  const f1D = smoothPath(f1Pts);

  // Dynamic grid ticks — 4-5 nice steps across the y range so the
  // labels line up with the curves regardless of dataMaxRpm.
  const rpmStep = rpmMax / 4;
  const rpmGridVals: number[] = [];
  for (let v = 0; v <= rpmMax + 1; v += rpmStep) rpmGridVals.push(Math.round(v));
  const tempStep = Math.max(10, Math.round((tMax - tMin) / 4 / 5) * 5);
  const tempGridVals: number[] = [];
  for (let v = Math.ceil(tMin / tempStep) * tempStep;
       v <= tMax; v += tempStep) tempGridVals.push(v);

  const gridRpm = rpmGridVals.map(v => {
    const yy = yRpm(v);
    return `<line x1="${PAD_L}" x2="${W - PAD_R}" y1="${yy}" y2="${yy}" stroke="#22262e" stroke-width="0.5"/>`
      + `<text x="${PAD_L - 6}" y="${yy + 3.5}" fill="#a3e635" font-size="10" text-anchor="end" opacity="0.75">${v}rpm</text>`;
  }).join("");
  const gridTemp = tempGridVals.map(v => {
    const yy = yTemp(v);
    return `<text x="${W - PAD_R + 5}" y="${yy + 3.5}" fill="#fbbf24" font-size="10" opacity="0.7">${v}°C</text>`;
  }).join("");

  const nTicks = Math.min(7, hist.length);
  let ticks = "";
  for (let k = 0; k < nTicks; k++) {
    const idx = Math.round(k * (hist.length - 1) / (nTicks - 1));
    const xx = x(idx);
    const ts = (hist[idx].ts || "").substring(0, 5);
    ticks += `<line x1="${xx.toFixed(1)}" x2="${xx.toFixed(1)}" y1="${(PAD_T + innerH).toFixed(1)}" y2="${(PAD_T + innerH + 4).toFixed(1)}" stroke="#3a3f4d" stroke-width="0.7"/>`
      + `<text x="${xx.toFixed(1)}" y="${(PAD_T + innerH + 18).toFixed(1)}" fill="#7c8aa3" font-size="10" text-anchor="middle">${ts}</text>`;
  }

  const dots = hist.map((h, i) => {
    const xx = x(i).toFixed(1);
    const tt = `${h.ts}\nfan0: ${h.fan0_rpm || 0} RPM\nfan1: ${h.fan1_rpm || 0} RPM\ntemp: ${h.temp}°C`;
    return `<circle class="pt" cx="${xx}" cy="${yRpm(h.fan0_rpm || 0).toFixed(1)}" r="1.6" fill="#4ade80" opacity="0.22"><title>${tt}</title></circle>`;
  }).join("");

  // Clip-path so any peak that overshoots the y-axis cap (rare
  // but happens when fan RPM > 3000 or temp > 90°C) gets cleanly
  // clipped to the chart's inner rectangle instead of leaking
  // above/below the card edge.
  const clipId = `cool-clip-${Math.random().toString(36).slice(2, 8)}`;
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs>
      <clipPath id="${clipId}">
        <rect x="${PAD_L}" y="${PAD_T}" width="${innerW}" height="${innerH}"/>
      </clipPath>
    </defs>
    ${gridRpm}${gridTemp}
    <g clip-path="url(#${clipId})">
    <path d="${tempD}" fill="none" stroke="#fbbf24" stroke-width="1.6" stroke-dasharray="5 3" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    <path d="${f1D}" fill="none" stroke="#a3e635" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" opacity="0.85"/>
    <path d="${f0D}" fill="none" stroke="#4ade80" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    ${dots}
    </g>
    ${ticks}
    <g font-size="11">
      <rect x="${W - PAD_R - 260}" y="${PAD_T - 12}" width="14" height="2.5" fill="#4ade80"/>
      <text x="${W - PAD_R - 242}" y="${PAD_T - 8}" fill="#4ade80">fan0 RPM</text>
      <rect x="${W - PAD_R - 180}" y="${PAD_T - 12}" width="14" height="2.5" fill="#a3e635"/>
      <text x="${W - PAD_R - 162}" y="${PAD_T - 8}" fill="#a3e635">fan1 RPM</text>
      <line x1="${W - PAD_R - 100}" x2="${W - PAD_R - 86}" y1="${PAD_T - 11}" y2="${PAD_T - 11}" stroke="#fbbf24" stroke-width="1.6" stroke-dasharray="4 2"/>
      <text x="${W - PAD_R - 82}" y="${PAD_T - 8}" fill="#fbbf24">temp °C</text>
    </g>
  </svg>`;
}

export function renderPowerChart(hist: Sample[], plimit: number | undefined, samplingText: string, capText: string): string {
  if (!hist.length) {
    return `<div style="color:#7c8aa3;padding:1em;text-align:center">${samplingText}</div>`;
  }
  const W = 1200, H = 180, PAD_L = 50, PAD_R = 22, PAD_T = 20, PAD_B = 28;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const x = (i: number) => PAD_L + (hist.length > 1 ? (i / (hist.length - 1)) * innerW : innerW / 2);
  const yPow = (w: number) => PAD_T + (1 - w / 350) * innerH;
  const powPts = hist.map((h, i) => ({ x: x(i), y: yPow(h.power || 0) }));
  const powD = smoothPath(powPts);
  const lastX = powPts[powPts.length - 1].x.toFixed(1);
  const firstX = powPts[0].x.toFixed(1);
  const bottomY = (PAD_T + innerH).toFixed(1);
  const areaD = powD + ` L ${lastX} ${bottomY} L ${firstX} ${bottomY} Z`;

  const gridPow = [0, 50, 100, 150, 200, 250, 300, 350].map(v => {
    const yy = yPow(v);
    return `<line x1="${PAD_L}" x2="${W - PAD_R}" y1="${yy}" y2="${yy}" stroke="#22262e" stroke-width="0.5"/>`
      + `<text x="${PAD_L - 6}" y="${yy + 3.5}" fill="#7c8aa3" font-size="10" text-anchor="end">${v}W</text>`;
  }).join("");

  const limitY = yPow(plimit || 350).toFixed(1);
  const limitLine = `<line x1="${PAD_L}" x2="${W - PAD_R}" y1="${limitY}" y2="${limitY}" stroke="#f87171" stroke-width="1.2" stroke-dasharray="5 4" opacity="0.7"/>`
    + `<text x="${W - PAD_R - 6}" y="${(parseFloat(limitY) - 4).toFixed(1)}" fill="#f87171" font-size="10" text-anchor="end" opacity="0.85">${capText} ${plimit || "?"} W</text>`;

  const nTicks = Math.min(7, hist.length);
  let ticks = "";
  for (let k = 0; k < nTicks; k++) {
    const idx = Math.round(k * (hist.length - 1) / (nTicks - 1));
    const xx = x(idx);
    const ts = (hist[idx].ts || "").substring(0, 5);
    ticks += `<line x1="${xx.toFixed(1)}" x2="${xx.toFixed(1)}" y1="${(PAD_T + innerH).toFixed(1)}" y2="${(PAD_T + innerH + 4).toFixed(1)}" stroke="#3a3f4d" stroke-width="0.7"/>`
      + `<text x="${xx.toFixed(1)}" y="${(PAD_T + innerH + 18).toFixed(1)}" fill="#7c8aa3" font-size="10" text-anchor="middle">${ts}</text>`;
  }

  const dots = hist.map((h, i) => {
    const xx = x(i).toFixed(1);
    const tt = `${h.ts}\npower: ${(h.power || 0).toFixed(1)} W`;
    return `<circle class="pt" cx="${xx}" cy="${yPow(h.power || 0).toFixed(1)}" r="1.6" fill="#22d3ee" opacity="0.25"><title>${tt}</title></circle>`;
  }).join("");

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${gridPow}
    <path d="${areaD}" fill="#22d3ee" opacity="0.10"/>
    ${limitLine}
    <path d="${powD}" fill="none" stroke="#22d3ee" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    ${dots}
    ${ticks}
    <g font-size="11">
      <rect x="${W - PAD_R - 200}" y="${PAD_T - 12}" width="14" height="2.5" fill="#22d3ee"/>
      <text x="${W - PAD_R - 182}" y="${PAD_T - 8}" fill="#22d3ee">power draw W</text>
      <line x1="${W - PAD_R - 110}" x2="${W - PAD_R - 96}" y1="${PAD_T - 11}" y2="${PAD_T - 11}" stroke="#f87171" stroke-width="1.2" stroke-dasharray="4 3"/>
      <text x="${W - PAD_R - 92}" y="${PAD_T - 8}" fill="#f87171">${capText}</text>
    </g>
  </svg>`;
}

export function perfEstimate(w: number): number {
  if (w >= 340) return 100;
  if (w >= 300) return Math.round(100 - (350 - w) * 0.10);
  if (w >= 250) return Math.round(95 - (300 - w) * 0.12);
  if (w >= 220) return Math.round(89 - (250 - w) * 0.20);
  if (w >= 200) return Math.round(83 - (220 - w) * 0.35);
  if (w >= 150) return Math.round(76 - (200 - w) * 0.40);
  return Math.max(40, Math.round(56 - (150 - w) * 0.50));
}

export const tempColor = (t: number) =>
  t < 45 ? "#60a5fa" : t < 60 ? "#4ade80" : t < 72 ? "#a3e635" : t < 80 ? "#fbbf24" : "#f87171";

export const colorFan = (f: number) =>
  f < 40 ? "#4ade80" : f < 60 ? "#a3e635" : f < 80 ? "#fbbf24" : "#f87171";

/** Compute rolling μ ± k*σ band for an array of numeric values.
 *
 * For each index i, the window is the [i-w/2, i+w/2] slice (clamped at edges).
 * Returns parallel arrays {mean, upper, lower} of same length as `values`.
 * Caller maps them to SVG x/y coordinates.
 *
 * k = 2 (default) gives the ~95% confidence band — points outside are
 * statistical outliers worth investigating. */
export function rollingBand(values: number[], window = 20, k = 2): { mean: number[]; upper: number[]; lower: number[] } {
  const n = values.length;
  const mean = new Array<number>(n);
  const upper = new Array<number>(n);
  const lower = new Array<number>(n);
  const half = Math.max(1, Math.floor(window / 2));

  for (let i = 0; i < n; i++) {
    const lo = Math.max(0, i - half);
    const hi = Math.min(n, i + half + 1);
    let sum = 0, cnt = 0;
    for (let j = lo; j < hi; j++) {
      const v = values[j];
      if (typeof v === "number" && Number.isFinite(v)) { sum += v; cnt++; }
    }
    if (cnt === 0) {
      mean[i] = values[i] ?? 0;
      upper[i] = mean[i];
      lower[i] = mean[i];
      continue;
    }
    const m = sum / cnt;
    let varsum = 0;
    for (let j = lo; j < hi; j++) {
      const v = values[j];
      if (typeof v === "number" && Number.isFinite(v)) varsum += (v - m) * (v - m);
    }
    const sd = Math.sqrt(varsum / cnt);
    mean[i] = m;
    upper[i] = m + k * sd;
    lower[i] = m - k * sd;
  }
  return { mean, upper, lower };
}

/** Serialize an SVGElement to a standalone .svg file and trigger a download.
 *
 * Inlines the page's `--bg-page` / `--text-muted` etc. computed colors so
 * the saved file renders correctly outside the dashboard CSS context.
 *
 * Pure DOM API : no fetch, no backend round-trip. The user gets the file
 * instantly. */
export function exportSvgAsFile(svgEl: SVGSVGElement, filename = "chart.svg"): void {
  if (!svgEl) return;
  // Clone so we don't mutate the live DOM
  const clone = svgEl.cloneNode(true) as SVGSVGElement;

  // Ensure xmlns is set (the original lives inside the HTML doc so may not have it)
  if (!clone.getAttribute("xmlns")) {
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  }

  // Add a white-on-dark background <rect> first so the chart is readable
  // when opened standalone in a browser (which has no theme stylesheet).
  const viewBox = clone.getAttribute("viewBox");
  if (viewBox) {
    const [vx, vy, vw, vh] = viewBox.split(/\s+/).map(parseFloat);
    const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    bg.setAttribute("x", String(vx));
    bg.setAttribute("y", String(vy));
    bg.setAttribute("width", String(vw));
    bg.setAttribute("height", String(vh));
    bg.setAttribute("fill", "#10131a");
    clone.insertBefore(bg, clone.firstChild);
  }

  const xml = new XMLSerializer().serializeToString(clone);
  // Prepend the standard SVG preamble for downloaded files
  const fullSvg = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml;
  const blob = new Blob([fullSvg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Defer revocation so the click() handoff completes
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
