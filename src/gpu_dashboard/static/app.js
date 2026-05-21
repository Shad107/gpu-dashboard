// gpu-dashboard frontend — full UI with charts, modal, sliders, alerts, i18n.

const colorFan = f => f<40?"#4ade80":f<60?"#a3e635":f<80?"#fbbf24":"#f87171";
const tempColor = c => c<45?"#60a5fa":c<60?"#4ade80":c<72?"#a3e635":c<80?"#fbbf24":"#f87171";

function perfEstimate(w){
  if (w >= 340) return 100;
  if (w >= 300) return Math.round(100 - (350-w)*0.10);
  if (w >= 250) return Math.round(95  - (300-w)*0.12);
  if (w >= 220) return Math.round(89  - (250-w)*0.20);
  if (w >= 200) return Math.round(83  - (220-w)*0.35);
  if (w >= 150) return Math.round(76  - (200-w)*0.40);
  return Math.max(40, Math.round(56 - (150-w)*0.50));
}

function smoothPath(pts){
  if (pts.length < 2) return "";
  let d = `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++){
    const p0 = pts[Math.max(0, i-1)];
    const p1 = pts[i];
    const p2 = pts[i+1];
    const p3 = pts[Math.min(pts.length-1, i+2)];
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C ${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
  }
  return d;
}

function renderHistChart(hist){
  if(!hist.length) return `<div style="color:#7c8aa3;padding:1em;text-align:center">${t("chart.sampling")}</div>`;
  const W = 1200, H = 280, PAD_L = 50, PAD_R = 56, PAD_T = 20, PAD_B = 30;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const x = i => PAD_L + (hist.length>1 ? (i/(hist.length-1))*innerW : innerW/2);
  const tMin = 30, tMax = 90;
  const yTemp = v => PAD_T + (1 - (v-tMin)/(tMax-tMin)) * innerH;
  const yRpm = r => PAD_T + (1 - r/3000) * innerH;
  const tempPts = hist.map((h,i) => ({x: x(i), y: yTemp(h.temp)}));
  const f0Pts   = hist.map((h,i) => ({x: x(i), y: yRpm(h.fan0_rpm||0)}));
  const f1Pts   = hist.map((h,i) => ({x: x(i), y: yRpm(h.fan1_rpm||0)}));
  const tempD = smoothPath(tempPts);
  const f0D   = smoothPath(f0Pts);
  const f1D   = smoothPath(f1Pts);

  const gridRpm = [0,1000,2000,3000].map(v => {
    const yy = yRpm(v);
    return `<line x1="${PAD_L}" x2="${W-PAD_R}" y1="${yy}" y2="${yy}" stroke="#22262e" stroke-width="0.5"/>`
         + `<text x="${PAD_L-6}" y="${yy+3.5}" fill="#a3e635" font-size="10" text-anchor="end" opacity="0.75">${v}rpm</text>`;
  }).join("");
  const gridTemp = [40,60,80].map(v => {
    const yy = yTemp(v);
    return `<text x="${W-PAD_R+5}" y="${yy+3.5}" fill="#fbbf24" font-size="10" opacity="0.7">${v}°C</text>`;
  }).join("");

  const nTicks = Math.min(7, hist.length);
  let ticks = "";
  for (let k = 0; k < nTicks; k++){
    const idx = Math.round(k * (hist.length - 1) / (nTicks - 1));
    const xx = x(idx);
    const ts = (hist[idx].ts || "").substring(0, 5);
    ticks += `<line x1="${xx.toFixed(1)}" x2="${xx.toFixed(1)}" y1="${(PAD_T+innerH).toFixed(1)}" y2="${(PAD_T+innerH+4).toFixed(1)}" stroke="#3a3f4d" stroke-width="0.7"/>`
           + `<text x="${xx.toFixed(1)}" y="${(PAD_T+innerH+18).toFixed(1)}" fill="#7c8aa3" font-size="10" text-anchor="middle">${ts}</text>`;
  }

  const dots = hist.map((h,i) => {
    const xx = x(i).toFixed(1);
    const tt = `${h.ts}\nfan0: ${h.fan0_rpm||0} RPM\nfan1: ${h.fan1_rpm||0} RPM\ntemp: ${h.temp}°C`;
    return `<circle class="pt" cx="${xx}" cy="${yRpm(h.fan0_rpm||0).toFixed(1)}" r="1.6" fill="#4ade80" opacity="0.22"><title>${tt}</title></circle>`;
  }).join("");

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${gridRpm}${gridTemp}
    <path d="${tempD}" fill="none" stroke="#fbbf24" stroke-width="1.6" stroke-dasharray="5 3" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    <path d="${f1D}" fill="none" stroke="#a3e635" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke" opacity="0.85"/>
    <path d="${f0D}" fill="none" stroke="#4ade80" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    ${dots}
    ${ticks}
    <g font-size="11">
      <rect x="${W-PAD_R-260}" y="${PAD_T-12}" width="14" height="2.5" fill="#4ade80"/>
      <text x="${W-PAD_R-242}" y="${PAD_T-8}" fill="#4ade80">fan0 RPM</text>
      <rect x="${W-PAD_R-180}" y="${PAD_T-12}" width="14" height="2.5" fill="#a3e635"/>
      <text x="${W-PAD_R-162}" y="${PAD_T-8}" fill="#a3e635">fan1 RPM</text>
      <line x1="${W-PAD_R-100}" x2="${W-PAD_R-86}" y1="${PAD_T-11}" y2="${PAD_T-11}" stroke="#fbbf24" stroke-width="1.6" stroke-dasharray="4 2"/>
      <text x="${W-PAD_R-82}" y="${PAD_T-8}" fill="#fbbf24">temp °C</text>
    </g>
  </svg>`;
}

function renderPowerChart(hist, plimit){
  if(!hist.length) return `<div style="color:#7c8aa3;padding:1em;text-align:center">${t("chart.sampling")}</div>`;
  const W = 1200, H = 180, PAD_L = 50, PAD_R = 22, PAD_T = 20, PAD_B = 28;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const x = i => PAD_L + (hist.length>1 ? (i/(hist.length-1))*innerW : innerW/2);
  const yPow = w => PAD_T + (1 - w/350) * innerH;
  const powPts = hist.map((h,i) => ({x: x(i), y: yPow(h.power||0)}));
  const powD = smoothPath(powPts);
  const lastX  = powPts[powPts.length-1].x.toFixed(1);
  const firstX = powPts[0].x.toFixed(1);
  const bottomY = (PAD_T+innerH).toFixed(1);
  const areaD = powD + ` L ${lastX} ${bottomY} L ${firstX} ${bottomY} Z`;

  const gridPow = [0,50,100,150,200,250,300,350].map(v => {
    const yy = yPow(v);
    return `<line x1="${PAD_L}" x2="${W-PAD_R}" y1="${yy}" y2="${yy}" stroke="#22262e" stroke-width="0.5"/>`
         + `<text x="${PAD_L-6}" y="${yy+3.5}" fill="#7c8aa3" font-size="10" text-anchor="end">${v}W</text>`;
  }).join("");

  const limitY = yPow(plimit||350).toFixed(1);
  const limitLine = `<line x1="${PAD_L}" x2="${W-PAD_R}" y1="${limitY}" y2="${limitY}" stroke="#f87171" stroke-width="1.2" stroke-dasharray="5 4" opacity="0.7"/>`
                  + `<text x="${W-PAD_R-6}" y="${(parseFloat(limitY)-4).toFixed(1)}" fill="#f87171" font-size="10" text-anchor="end" opacity="0.85">${t("chart.cap")} ${plimit||"?"} W</text>`;

  const nTicks = Math.min(7, hist.length);
  let ticks = "";
  for (let k = 0; k < nTicks; k++){
    const idx = Math.round(k * (hist.length - 1) / (nTicks - 1));
    const xx = x(idx);
    const ts = (hist[idx].ts || "").substring(0, 5);
    ticks += `<line x1="${xx.toFixed(1)}" x2="${xx.toFixed(1)}" y1="${(PAD_T+innerH).toFixed(1)}" y2="${(PAD_T+innerH+4).toFixed(1)}" stroke="#3a3f4d" stroke-width="0.7"/>`
           + `<text x="${xx.toFixed(1)}" y="${(PAD_T+innerH+18).toFixed(1)}" fill="#7c8aa3" font-size="10" text-anchor="middle">${ts}</text>`;
  }

  const dots = hist.map((h,i) => {
    const xx = x(i).toFixed(1);
    const tt = `${h.ts}\npower: ${(h.power||0).toFixed(1)} W`;
    return `<circle class="pt" cx="${xx}" cy="${yPow(h.power||0).toFixed(1)}" r="1.6" fill="#22d3ee" opacity="0.25"><title>${tt}</title></circle>`;
  }).join("");

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${gridPow}
    <path d="${areaD}" fill="#22d3ee" opacity="0.10"/>
    ${limitLine}
    <path d="${powD}" fill="none" stroke="#22d3ee" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
    ${dots}
    ${ticks}
    <g font-size="11">
      <rect x="${W-PAD_R-200}" y="${PAD_T-12}" width="14" height="2.5" fill="#22d3ee"/>
      <text x="${W-PAD_R-182}" y="${PAD_T-8}" fill="#22d3ee">power draw W</text>
      <line x1="${W-PAD_R-110}" x2="${W-PAD_R-96}" y1="${PAD_T-11}" y2="${PAD_T-11}" stroke="#f87171" stroke-width="1.2" stroke-dasharray="4 3"/>
      <text x="${W-PAD_R-92}" y="${PAD_T-8}" fill="#f87171">${t("chart.cap")}</text>
    </g>
  </svg>`;
}

const card = (title,body) => `<div class="card"><h2>${title}</h2>${body}</div>`;

async function refresh(){
  try{
    const r = await fetch("/api/state",{cache:"no-store"});
    const d = await r.json();
    const g = d.gpu, w = d.watchdog || {available: false}, s = d.services || {};
    document.getElementById("ts").textContent = t("ts.updated") + " " + new Date().toLocaleTimeString();
    if(g.name) document.getElementById("gpu-name").textContent = g.name;

    let cards = "";
    if(g.alive){
      const memGiB = (g.mem_used_mib/1024).toFixed(1);
      const memTot = (g.mem_total_mib/1024).toFixed(1);
      cards += card(t("card.gpu"), `<div class="big" style="color:${tempColor(g.temp)}">${g.temp}°C</div><div class="sub">${t("gpu.util")} ${g.util_gpu}% · ${t("gpu.draw")} ${g.power.toFixed(0)} W</div>`);
      const plPerf = perfEstimate(g.power_limit);
      const plPerfCol = plPerf >= 95 ? "#4ade80" : plPerf >= 85 ? "#a3e635" : plPerf >= 75 ? "#fbbf24" : "#fb923c";
      cards += card(t("card.power_limit"), `<div class="big">${g.power_limit.toFixed(0)} <span class="sub" style="font-size:.55em">/ 350 W</span></div><div class="sub">~<span style="color:${plPerfCol}">${plPerf}%</span> ${t("perf.perf_short")} · ${t("perf.stock_pl")}</div>`);

      const mdiFan = `<path d="M12,11A1,1 0 0,0 11,12A1,1 0 0,0 12,13A1,1 0 0,0 13,12A1,1 0 0,0 12,11M12.5,2C17,2 17.11,5.57 14.75,6.75C13.76,7.24 13.32,8.29 13.13,9.22C13.61,9.42 14.03,9.73 14.35,10.13C18.05,8.13 22.03,8.92 22.03,12.5C22.03,17 18.46,17.1 17.28,14.73C16.78,13.74 15.72,13.3 14.79,13.11C14.59,13.59 14.28,14 13.87,14.34C15.87,18.04 15.08,22 11.5,22C7,22 6.91,18.42 9.27,17.24C10.25,16.75 10.69,15.71 10.89,14.79C10.4,14.59 9.97,14.27 9.65,13.87C5.95,15.87 2,15.08 2,11.5C2,7 5.56,6.91 6.74,9.27C7.24,10.25 8.29,10.69 9.22,10.88C9.41,10.4 9.73,9.97 10.14,9.65C8.14,5.96 8.91,2 12.5,2Z"/>`;
      const fanList = (d.fans && d.fans.length) ? d.fans : [{idx:0,rpm:0,pct:0,target:0}];
      const fansHtml = fanList.map(f => {
        const rpm = f.rpm ?? 0, pct = f.pct ?? 0, target = f.target ?? 0;
        const dur = rpm > 0 ? Math.max(0.08, 60/rpm*0.4).toFixed(2) : 0;
        const cls = rpm > 0 ? "spin" : "off";
        const col = pct >= 80 ? "#f87171" : pct >= 60 ? "#fbbf24" : pct >= 40 ? "#a3e635" : (pct > 0 ? "#4ade80" : "#7c8aa3");
        return `<div class="fan-cell">
          <svg class="fan-svg ${cls}" style="--fan-dur:${dur}s;color:${col}" viewBox="0 0 24 24" fill="currentColor"><title>Fan ${f.idx} — ${rpm} RPM · ${pct}% (target ${target}%)</title>${mdiFan}</svg>
          <div class="rpm">${rpm} RPM</div>
          <div class="pct"><b>${pct}%</b> <span style="color:#5a606c">/ ${target}%</span></div>
        </div>`;
      }).join("");
      cards += card(t("card.fans"), `<div class="fan-visual">${fansHtml}</div>`);
      cards += card(t("card.vram"), `<div class="big">${memGiB} <span class="sub" style="font-size:.55em">/ ${memTot} GiB</span></div>`);
    } else {
      cards += card(t("card.gpu"), `<div class="big bad">${t("gpu.off_bus")}</div><div class="sub">${t("gpu.no_response")}</div>`);
    }
    if(w.available){
      cards += card(t("card.oculink"), `<div class="big ${w.drops?"warn":"ok"}">${w.last_uptime}</div><div class="sub">${w.drops} ${t("oculink.drops")}</div>`);
    }
    if(d.llm_model){
      cards += card(t("card.llm_model"), `<div class="big" style="font-size:1em;word-break:break-all">${d.llm_model}</div>`);
    }

    if(d.tuning && (d.tuning.clocks || d.tuning.offsets)){
      const tu = d.tuning, c = tu.clocks || {}, o = tu.offsets || {};
      const sign = v => (v >= 0 ? "+" : "") + v;
      const offGpu = o.GPUGraphicsClockOffsetAllPerformanceLevels ?? o.GPUGraphicsClockOffset ?? 0;
      const offMem = o.GPUMemoryTransferRateOffsetAllPerformanceLevels ?? o.GPUMemoryTransferRateOffset ?? 0;
      const offGpuCol = offGpu !== 0 ? "#a3e635" : "#7c8aa3";
      const offMemCol = offMem !== 0 ? "#a3e635" : "#7c8aa3";
      const pct = (now, max) => max ? (now/max*100).toFixed(0) : 0;
      const pctGpu = pct(c.gr_now, c.gr_max), pctMem = pct(c.mem_now, c.mem_max);
      cards += card(t("card.tuning"), `
        <div class="tuning-row">
          <div class="tuning-lbl">${t("card.gpu")}</div>
          <div class="tuning-val"><b>${c.gr_now ?? "—"}</b> <span class="sub">/ ${c.gr_max ?? "—"} MHz</span></div>
          <div class="tuning-bar"><div style="width:${pctGpu}%;background:${tempColor(c.gr_now||0)}"></div></div>
        </div>
        <div class="tuning-row">
          <div class="tuning-lbl">${t("tuning.memory")}</div>
          <div class="tuning-val"><b>${c.mem_now ?? "—"}</b> <span class="sub">/ ${c.mem_max ?? "—"} MHz</span></div>
          <div class="tuning-bar"><div style="width:${pctMem}%;background:#60a5fa"></div></div>
        </div>
        <div class="tuning-row">
          <div class="tuning-lbl">${t("tuning.pstate")}</div>
          <div class="tuning-val"><b>${c.pstate || "—"}</b></div>
          <div class="tuning-bar" style="opacity:0"></div>
        </div>
        <div class="tuning-row" style="margin-top:.5em;border-top:1px solid #22262e;padding-top:.4em">
          <div class="tuning-lbl">${t("tuning.gpu_offset")}</div>
          <div class="tuning-val" style="color:${offGpuCol}"><b>${sign(offGpu)}</b> MHz</div>
          <div class="tuning-bar" style="opacity:0"></div>
        </div>
        <div class="tuning-row">
          <div class="tuning-lbl">${t("tuning.mem_offset")}</div>
          <div class="tuning-val" style="color:${offMemCol}"><b>${sign(offMem)}</b> MHz</div>
          <div class="tuning-bar" style="opacity:0"></div>
        </div>
      `);
    }
    document.getElementById("cards").innerHTML = cards;

    const series = d.metrics && d.metrics.length ? d.metrics : [];
    document.getElementById("hist").innerHTML = renderHistChart(series);
    document.getElementById("hist-power").innerHTML = renderPowerChart(series, d.gpu && d.gpu.power_limit);
    if(series.length){
      const fans = series.map(s=>s.fan), temps = series.map(s=>s.temp);
      const pwrs = series.map(s=>s.power||0);
      document.getElementById("hist-info").textContent =
        `fan ${Math.min(...fans)}-${Math.max(...fans)}% · temp ${Math.min(...temps)}-${Math.max(...temps)}°C · power ${Math.min(...pwrs).toFixed(0)}-${Math.max(...pwrs).toFixed(0)} W · ${series.length} ${t("chart.info_pts")} ${series[0].ts}`;
    } else {
      document.getElementById("hist-info").textContent = t("chart.buffer_filling");
    }

    const dist = d.fan_dist || {};
    const total = Object.values(dist).reduce((a,b)=>a+b,0) || 1;
    const keys = Object.keys(dist).sort((a,b)=>+a-+b);
    document.getElementById("dist").innerHTML = keys.map(k => {
      const n = dist[k]; const pct = (n/total*100);
      return `<tr><td>${k}%</td><td>${n} <span class="sub">(${pct.toFixed(1)}%)</span><br><span class="bar" style="width:${pct.toFixed(1)}%"><div style="width:100%;background:${colorFan(+k)}"></div></span></td></tr>`;
    }).join("");

    document.getElementById("svc").innerHTML = Object.entries(s).map(([k,v]) => {
      const cls = v==="active"?"ok":(v==="unknown"?"warn":"bad");
      return `<tr><td>${k}</td><td class="${cls}">${v}</td></tr>`;
    }).join("");
  }catch(e){
    document.getElementById("ts").textContent = t("ts.network_error") + ": " + e.message;
  }
}

window.gpuDashboardRefresh = refresh;
applyStaticTranslations();
refresh();
setInterval(refresh, 5000);


(function initPowerLimit(){
  const slider = document.getElementById("pl-slider");
  const valEl = document.getElementById("pl-val");
  const toastEl = document.getElementById("toast");
  if(!slider) return;

  const perfEl = document.getElementById("pl-perf");
  function refreshLocal(){ valEl.textContent = slider.value; perfEl.textContent = `(~${perfEstimate(+slider.value)}% ${t("perf.perf_short")})`; }
  slider.oninput = refreshLocal;
  refreshLocal();

  function showToast(msg, ok){
    toastEl.className = "toast show " + (ok ? "ok" : "err");
    toastEl.textContent = msg;
    setTimeout(() => toastEl.className = "toast", 3500);
  }

  async function apply(watts){
    try{
      const r = await fetch("/api/set-power-limit", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({watts})
      });
      const d = await r.json();
      if(d.ok){
        showToast("✓ " + t("toast.power_applied", {watts: d.watts, perf: perfEstimate(d.watts)}), true);
        slider.value = d.watts; refreshLocal();
      } else {
        showToast("✗ " + t("toast.error") + ": " + (d.error || t("toast.unknown")), false);
      }
    } catch(e){
      showToast("✗ " + t("ts.network_error") + ": " + e.message, false);
    }
  }

  document.getElementById("btn-pl-apply").onclick = () => apply(+slider.value);
  document.getElementById("btn-pl-250").onclick   = () => apply(250);
  document.getElementById("btn-pl-350").onclick   = () => apply(350);

  fetch("/api/state").then(r => r.json()).then(d => {
    const w = Math.round((d.gpu && d.gpu.power_limit) || 250);
    slider.value = w;
    refreshLocal();
  }).catch(() => {});
})();

(function initAlertsForm(){
  const enabled = document.getElementById("al-enabled");
  const token = document.getElementById("al-token");
  const chat = document.getElementById("al-chat");
  const onDrop = document.getElementById("al-on-drop");
  const onRecover = document.getElementById("al-on-recover");
  const btnSave = document.getElementById("btn-al-save");
  const btnTest = document.getElementById("btn-al-test");
  const toastEl = document.getElementById("toast");
  if (!enabled) return;

  function showToast(msg, ok){
    toastEl.className = "toast show " + (ok ? "ok" : "err");
    toastEl.textContent = msg;
    setTimeout(() => toastEl.className = "toast", 4000);
  }

  function load(){
    fetch("/api/alerts-config").then(r => r.json()).then(c => {
      enabled.checked   = !!c.enabled;
      token.value       = c.token || "";
      chat.value        = c.chat_id || "";
      onDrop.checked    = !!c.on_drop;
      onRecover.checked = !!c.on_recover;
    }).catch(() => {});
  }
  load();

  btnSave.onclick = async () => {
    try{
      const r = await fetch("/api/alerts-config", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
          enabled: enabled.checked,
          token: token.value.trim(),
          chat_id: chat.value.trim(),
          on_drop: onDrop.checked,
          on_recover: onRecover.checked,
        })
      });
      const d = await r.json();
      showToast(d.ok ? "✓ " + t("alerts.config_saved") : "✗ " + (d.error || t("toast.error")), d.ok);
    } catch(e){ showToast("✗ " + t("ts.network_error") + ": " + e.message, false); }
  };

  btnTest.onclick = async () => {
    try{
      const r = await fetch("/api/alerts-test", {method:"POST"});
      const d = await r.json();
      showToast(d.ok ? "✓ " + t("alerts.message_sent") : "✗ " + (d.msg||d.error||t("alerts.telegram_error")), d.ok);
    } catch(e){ showToast("✗ " + t("ts.network_error") + ": " + e.message, false); }
  };
})();

(function initModal(){
  const btn = document.getElementById("gear-btn");
  const overlay = document.getElementById("modal-overlay");
  const closeBtn = document.getElementById("modal-close");
  if (!btn || !overlay) return;

  function open(){ overlay.classList.add("show"); btn.classList.add("active"); }
  function close(){ overlay.classList.remove("show"); btn.classList.remove("active"); }

  btn.onclick = open;
  closeBtn.onclick = close;
  overlay.onclick = (e) => { if (e.target === overlay) close(); };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });

  document.querySelectorAll(".sidebar-item").forEach(item => {
    item.onclick = () => {
      document.querySelectorAll(".sidebar-item").forEach(i => i.classList.remove("active"));
      document.querySelectorAll(".modal-section").forEach(s => s.classList.remove("active"));
      item.classList.add("active");
      const section = document.getElementById("section-" + item.dataset.section);
      if (section) section.classList.add("active");
    };
  });
})();

(function initLanguage(){
  const en = document.getElementById("lang-en");
  const fr = document.getElementById("lang-fr");
  if (!en || !fr) return;
  const lang = window.currentLang();
  if (lang === "fr") fr.checked = true;
  else en.checked = true;
  en.onchange = () => en.checked && window.setLang("en");
  fr.onchange = () => fr.checked && window.setLang("fr");
})();

(function initOffsetControls(){
  const gpuS = document.getElementById("gpu-offset");
  const memS = document.getElementById("mem-offset");
  const gpuV = document.getElementById("gpu-offset-val");
  const memV = document.getElementById("mem-offset-val");
  const gpuZ = document.getElementById("gpu-zone");
  const memZ = document.getElementById("mem-zone");
  const adv  = document.getElementById("advanced-mode");
  const advMark = document.querySelector(".locked-mark");
  const toastEl = document.getElementById("toast");
  if(!gpuS) return;

  function classifyGpu(v){
    if (v <= 50)  return {n: t("zone.safe"),       c:"safe"};
    if (v <= 100) return {n: t("zone.moderate"),   c:"mod"};
    if (v <= 150) return {n: t("zone.aggressive"), c:"agg"};
    return            {n: t("zone.danger"),      c:"danger"};
  }
  function classifyMem(v){
    if (v <= 300)  return {n: t("zone.safe"),       c:"safe"};
    if (v <= 700)  return {n: t("zone.moderate"),   c:"mod"};
    if (v <= 1200) return {n: t("zone.aggressive"), c:"agg"};
    return             {n: t("zone.danger"),      c:"danger"};
  }

  function refreshGpu(){
    const v = +gpuS.value;
    gpuV.textContent = "+" + v;
    const z = classifyGpu(v);
    gpuZ.className = "zone " + z.c;
    gpuZ.textContent = z.n;
  }
  function refreshMem(){
    const v = +memS.value;
    memV.textContent = "+" + v;
    const z = classifyMem(v);
    memZ.className = "zone " + z.c;
    memZ.textContent = z.n;
  }

  gpuS.oninput = refreshGpu;
  memS.oninput = refreshMem;

  adv.onchange = () => {
    if (adv.checked){
      gpuS.max = 200;
      memS.max = 1500;
      advMark.textContent = t("clocks.unlocked");
      advMark.style.color = "#fb923c";
    } else {
      gpuS.max = 100;
      memS.max = 500;
      if (+gpuS.value > 100) gpuS.value = 100;
      if (+memS.value > 500) memS.value = 500;
      advMark.textContent = t("clocks.locked");
      advMark.style.color = "#5a606c";
    }
    refreshGpu();
    refreshMem();
  };

  function showToast(msg, ok){
    toastEl.className = "toast show " + (ok ? "ok" : "err");
    toastEl.textContent = msg;
    setTimeout(() => toastEl.className = "toast", 4000);
  }

  async function apply(gpu, mem){
    try{
      const r = await fetch("/api/set-offsets", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({gpu, mem})
      });
      const d = await r.json();
      if(d.ok){
        showToast("✓ " + t("clocks.applied", {gpu: d.gpu, mem: d.mem}), true);
        gpuS.value = d.gpu; refreshGpu();
        memS.value = d.mem; refreshMem();
      } else {
        showToast("✗ " + t("toast.error") + ": " + (d.error || t("toast.unknown")), false);
      }
    } catch(e){
      showToast("✗ " + t("ts.network_error") + ": " + e.message, false);
    }
  }

  document.getElementById("btn-apply").onclick = () => {
    const g = +gpuS.value, m = +memS.value;
    const gz = classifyGpu(g), mz = classifyMem(m);
    if (["agg","danger"].includes(gz.c) || ["agg","danger"].includes(mz.c)){
      const msg = t("clocks.confirm_dangerous", {gpu: g, mem: m, gz: gz.n, mz: mz.n});
      if (!confirm(msg)) return;
    }
    apply(g, m);
  };
  document.getElementById("btn-reset").onclick = () => apply(0, 0);

  fetch("/api/state").then(r => r.json()).then(d => {
    const o = (d.tuning && d.tuning.offsets) || {};
    const g = Math.max(0, o.GPUGraphicsClockOffsetAllPerformanceLevels || 0);
    const m = Math.max(0, o.GPUMemoryTransferRateOffsetAllPerformanceLevels || 0);
    if (g > 100 || m > 500){
      adv.checked = true;
      adv.onchange();
    }
    gpuS.value = Math.min(g, +gpuS.max);
    memS.value = Math.min(m, +memS.max);
    refreshGpu(); refreshMem();
  }).catch(() => {});
})();
