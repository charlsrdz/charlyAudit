/**
 * popup.js — Consola de QA
 * Controla la grabacion, refleja el estado del service worker y exporta el
 * reporte. Toda la verdad vive en el SW; el popup solo consulta y ordena.
 */

// Color y etiqueta por tipo de evento (coherente con el resto de la suite).
const TYPE_META = {
  click: { c: "#5b6cff", l: "click" },
  dblclick: { c: "#6d7cff", l: "dblclick" },
  middleclick: { c: "#818cf8", l: "centro" },
  dragdrop: { c: "#c084fc", l: "drag" },
  key: { c: "#7dd3fc", l: "tecla" },
  input: { c: "#38bdf8", l: "input" },
  focus: { c: "#2dd4bf", l: "foco" },
  network: { c: "#a78bfa", l: "red" },
  console: { c: "#94a3b8", l: "console" },
  error: { c: "#ff6b5e", l: "error" },
  unhandledrejection: { c: "#fb7185", l: "reject" },
  "code-block": { c: "#a3e635", l: "codigo" },
  route: { c: "#f5b544", l: "ruta" },
  navigation: { c: "#64748b", l: "nav" },
  "global-state": { c: "#34d399", l: "estado" },
  "function-call": { c: "#22d3ee", l: "fn" },
  scroll: { c: "#475569", l: "scroll" },
  resize: { c: "#475569", l: "resize" },
  meta: { c: "#475569", l: "meta" },
};
const metaFor = (t) => TYPE_META[t] || { c: "#475569", l: t };

const $ = (id) => document.getElementById(id);
const control = (action, extra = {}) =>
  chrome.runtime.sendMessage({ channel: "qa-control", action, ...extra });

let pollTimer = null;

// --- Descripcion legible por tipo ------------------------------------------
function describe(e) {
  const d = e.data || {};
  switch (e.type) {
    case "click":
      return d.selector || "(elemento)";
    case "dblclick":
      return `2x ${d.selector || "(elemento)"}`;
    case "middleclick":
      return `centro ${d.selector || "(elemento)"}`;
    case "dragdrop":
      return `${(d.from || "?").slice(0, 24)} → ${(d.to || "?").slice(0, 24)}`;
    case "key":
      return `${[d.ctrl && "Ctrl", d.alt && "Alt", d.shift && "Shift", d.meta && "Meta"].filter(Boolean).join("+")}${
        d.key ? (d.ctrl || d.alt || d.meta ? "+" : "") + d.key : ""
      }`;
    case "focus":
      return `${d.selector || d.tag} ${d.rect ? `(${d.rect.w}×${d.rect.h})` : ""}`;
    case "input":
      return `${d.selector} = ${d.value}`;
    case "network":
      return `${d.method} ${d.status ?? d.error ?? "?"} · ${shortUrl(d.url)}`;
    case "console":
      return `${d.level}: ${(d.args || []).map(String).join(" ")}`;
    case "error":
      return `${d.trigger === "user-action" ? "↩ " : ""}${d.message || d.url || "error"}`;
    case "code-block":
      return d.ref || "(bloque)";
    case "unhandledrejection":
      return d.reason || "promesa rechazada";
    case "route":
      return `${d.method}: ${shortUrl(d.to)}`;
    case "navigation":
      return `${d.reason} · ${shortUrl(d.url)}`;
    case "global-state":
      return Object.keys(d.values || {}).join(", ") || "(sin variables)";
    case "function-call":
      return `${d.path}() ${d.error ? "✗" : "✓"} ${d.durationMs}ms`;
    case "scroll":
      return `x:${d.x} y:${d.y}`;
    case "resize":
      return `${d.width}×${d.height}`;
    default:
      return JSON.stringify(d).slice(0, 80);
  }
}
function shortUrl(u) {
  if (!u) return "";
  try {
    const url = new URL(u, location.href);
    return (url.pathname + url.search).slice(0, 48) || url.host;
  } catch {
    return String(u).slice(0, 48);
  }
}

// --- Render -----------------------------------------------------------------
function renderState(rec, count) {
  const btn = $("rec");
  const label = $("rec-label");
  const state = $("state");
  btn.classList.toggle("is-live", rec);
  btn.setAttribute("aria-pressed", String(rec));
  label.textContent = rec ? "Detener" : "Grabar";
  state.textContent = rec ? "Grabando" : "En espera";
  state.classList.toggle("is-live", rec);
  $("count").textContent = `${count} ${count === 1 ? "evento" : "eventos"}`;
}

function renderChips(counts) {
  const host = $("chips");
  const entries = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
  host.innerHTML = "";
  if (!entries.length) return;
  for (const [type, n] of entries) {
    const m = metaFor(type);
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML =
      `<span class="chip__dot" style="background:${m.c}"></span>` +
      `<span class="chip__n">${n}</span><span class="chip__label">${m.l}</span>`;
    host.appendChild(chip);
  }
}

function renderRibbon(timeline) {
  const host = $("ribbon");
  host.querySelectorAll(".ribbon__tick").forEach((n) => n.remove());
  const empty = $("ribbon-empty");
  const ticks = timeline.filter((e) => e.type !== "meta");
  if (!ticks.length) {
    empty.style.display = "";
    return;
  }
  empty.style.display = "none";
  const start = ticks[0].ts;
  const end = ticks[ticks.length - 1].ts;
  const span = Math.max(1, end - start);
  // Limita el numero de marcas dibujadas por rendimiento.
  const sample = ticks.length > 300 ? ticks.slice(-300) : ticks;
  for (const e of sample) {
    const tick = document.createElement("div");
    tick.className = "ribbon__tick";
    const pct = ((e.ts - start) / span) * 100;
    tick.style.left = `${pct}%`;
    tick.style.background = metaFor(e.type).c;
    tick.title = `${e.type} · ${new Date(e.ts).toLocaleTimeString()}`;
    host.appendChild(tick);
  }
}

function renderLog(timeline) {
  const host = $("log");
  const items = timeline.filter((e) => e.type !== "meta");
  if (!items.length) {
    host.innerHTML =
      '<div class="log__empty">Sin eventos todavia.<br />Pulsa <strong>Grabar</strong> e interactua con la pagina.</div>';
    return;
  }
  // Calcula delays y muestra los mas recientes primero (max 50).
  const recent = items.slice(-50).reverse();
  host.innerHTML = "";
  recent.forEach((e, i) => {
    const m = metaFor(e.type);
    // delay respecto al evento cronologicamente anterior
    const all = items;
    const idx = all.indexOf(e);
    const delay = idx > 0 ? e.ts - all[idx - 1].ts : 0;
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML =
      `<span class="row__bar" style="background:${m.c}"></span>` +
      `<span class="row__type">${m.l}</span>` +
      `<span class="row__desc"></span>` +
      `<span class="row__delay">+${delay}ms</span>`;
    row.querySelector(".row__desc").textContent = describe(e);
    host.appendChild(row);
  });
}

// --- Ciclo de actualizacion -------------------------------------------------
async function refresh() {
  try {
    const st = await control("getState");
    if (!st || !st.ok) return;
    renderState(st.isRecording, st.count);
    renderChips(st.counts);
    fillConfig(st.config);
    const tl = await control("getTimeline");
    if (tl && tl.ok) {
      renderRibbon(tl.timeline);
      renderLog(tl.timeline);
    }
  } catch {
    /* el SW puede estar despertando */
  }
}

let configFilled = false;
function fillConfig(config) {
  if (configFilled || !config) return; // no pisar lo que el usuario escribe
  $("globals").value = (config.watchedGlobals || []).join("\n");
  $("functions").value = (config.patchedFunctions || []).join("\n");
  $("masks").value = (config.maskSelectors || []).join("\n");
  configFilled = true;
}

// --- Acciones ---------------------------------------------------------------
$("rec").addEventListener("click", async () => {
  const res = await control("toggle");
  if (res && res.ok) {
    renderState(res.isRecording, Number($("count").textContent) || 0);
    refresh();
    toast(res.isRecording ? "Grabacion iniciada" : "Grabacion detenida");
  }
});

$("save-config").addEventListener("click", async () => {
  const lines = (id) =>
    $(id)
      .value.split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  const config = {
    watchedGlobals: lines("globals"),
    patchedFunctions: lines("functions"),
    maskSelectors: lines("masks").length ? lines("masks") : [".private", "[data-private]"],
  };
  const res = await control("setConfig", { config });
  toast(res && res.ok ? "Configuracion guardada" : "No se pudo guardar");
});

$("clear").addEventListener("click", async () => {
  if (!confirm("Vaciar todos los eventos grabados?")) return;
  await control("clear");
  refresh();
  toast("Timeline vaciado");
});

// Exportaciones (descarga de archivo)
$("exp-json").addEventListener("click", async () => {
  const res = await control("getReport");
  if (res && res.ok) download("charly-qa-report.json", JSON.stringify(res.report, null, 2), "application/json");
});
$("exp-cypress").addEventListener("click", async () => {
  const res = await control("exportCypress");
  if (res && res.ok) download("charly-qa.cy.js", res.script, "text/javascript");
});
$("exp-playwright").addEventListener("click", async () => {
  const res = await control("exportPlaywright");
  if (res && res.ok) download("charly-qa.spec.js", res.script, "text/javascript");
});
$("copy-json").addEventListener("click", async () => {
  const res = await control("getReport");
  if (res && res.ok) {
    const ok = await copyText(JSON.stringify(res.report, null, 2));
    toast(ok ? "Reporte copiado al portapapeles" : "No se pudo copiar");
  }
});

// Abrir el asistente IA en el panel lateral (el clic es un gesto de usuario).
$("open-assistant").addEventListener("click", async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.id != null) {
      await chrome.sidePanel.open({ tabId: tab.id });
      window.close(); // cede el foco al panel
    }
  } catch (e) {
    toast("No se pudo abrir el panel");
  }
});

// --- Replay: importar y ejecutar (persistente, sobrevive al cierre) ----------
let replayLoaded = false;
async function refreshReplay() {
  // Restaura el reporte importado desde el SW (no se pierde al cerrar el popup).
  const res = await control("getReplay");
  if (res && res.ok && res.report && Array.isArray(res.report.timeline)) {
    replayLoaded = true;
    $("play-replay").disabled = false;
    const active = res.job && res.job.active;
    $("replay-info").textContent = active
      ? `reproduciendo · ${res.job.index}/${res.report.timeline.length}`
      : `${res.report.timeline.length} eventos · listo`;
  } else {
    replayLoaded = false;
    $("play-replay").disabled = true;
    $("replay-info").textContent = "Sin replay cargado";
  }
}
$("imp-replay").addEventListener("click", () => $("replay-file").click());
$("replay-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (!data || !Array.isArray(data.timeline)) throw new Error("formato");
    // Persiste en el SW de inmediato: si el popup se cierra, no se pierde.
    await control("loadReplay", { report: data });
    await refreshReplay();
    toast("Replay importado y guardado.");
  } catch {
    toast("Archivo de replay invalido.");
  } finally {
    e.target.value = ""; // permite reimportar el mismo archivo
  }
});
$("play-replay").addEventListener("click", async () => {
  if (!replayLoaded) return;
  const speed = Number($("replay-speed").value) || 1;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || tab.id == null) return toast("Sin pestana activa.");
  const res = await control("startReplay", { tabId: tab.id, options: { speed } });
  if (res && res.ok) toast("Reproduciendo en la pestana…"); // el popup NO se cierra
  else toast("No se pudo iniciar el replay.");
});
$("stop-replay").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  await control("stopReplay", { tabId: tab && tab.id });
  toast("Replay detenido.");
});

// --- Utilidades de salida ---------------------------------------------------
function download(filename, text, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast(`Descargado ${filename}`);
}

async function copyText(text) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fallback abajo */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("is-on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("is-on"), 1800);
}

// --- Arranque ---------------------------------------------------------------
$("version").textContent = `CharlyAudit v${chrome.runtime.getManifest().version}`;
async function renderWebhookPending() {
  const el = $("wh-pending");
  if (!el) return;
  const res = await control("getWebhookStatus");
  const p = res && res.pending;
  if (!p) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.textContent = p.agotado
    ? `Webhook: envio agotado tras ${p.intentos} intentos`
    : `Webhook: reintentando (${p.intentos}, prox. ${p.proximoMin}min)`;
}
refresh();
refreshReplay();
renderWebhookPending();
pollTimer = setInterval(refresh, 1200);
// Sincronia popup<->panel<->SW: reacciona al estado compartido para que grabar/
// detener desde el panel lateral (o el SW) se refleje aqui, y viceversa.
try {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    if (changes["qa:isRecording"] || changes["qa:timeline"] || changes["qa:meta"]) refresh();
    if (changes["qa:replay"] || changes["qa:replayJob"]) refreshReplay();
    if (changes["qa:webhookPending"]) renderWebhookPending();
  });
} catch {
  /* sin storage */
}
window.addEventListener("pagehide", () => clearInterval(pollTimer));
