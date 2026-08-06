/**
 * sidepanel.js — Controlador del Asistente IA (CharlyAudit)
 * =========================================================
 * Orquesta la UI del panel: configuracion, contexto de QA (solo lectura),
 * flujo bloqueante de solicitud->respuesta, cache en localStorage y ajustes.
 *
 * Garantias de seguridad/flujo:
 *   - Un solo request en vuelo (estado "busy"): la entrada se bloquea hasta la
 *     respuesta -> no hay solapamiento ni saturacion de envios.
 *   - Anti-stale: cada peticion lleva un numero de secuencia; respuestas viejas
 *     se descartan.
 *   - El panel NO escucha mensajes de ventanas externas y solo habla con su
 *     propio service worker (acciones de SOLO lectura) y con Open WebUI.
 *   - La salida del modelo se renderiza con Markdown saneado (nunca se ejecuta).
 */
import { OpenWebUIClient, PROVIDERS } from "./lib/openwebui-client.js";
import { renderMarkdown, Markdown } from "./lib/markdown.js";
import { ContextBridge, SCOPES } from "./lib/context-bridge.js";
import { ChatCache } from "./lib/chat-cache.js";
import { computeKpis } from "../qa/bundle-schema.js";

const AI_CONFIG_KEY = "charlyplugin:ai:config";
const DEFAULT_AI = {
  provider: "openwebui",
  baseUrl: "https://assistant.service24gps.com",
  model: "charly-pt",
  apiKey: "sk-3e17ab34903e4e1fbe68c683fabbb67c",
  proxyUrl: "",
  temperature: 0.7,
  maxTokens: 1024,
  maxHistoryTurns: 6,
};
const MIN_INTERVAL = 1500; // ms minimo entre envios (anti-saturacion)
const CONTEXT_BUDGET = 12000; // chars de contexto (~3k tokens) por peticion

const $ = (id) => document.getElementById(id);
const bridge = new ContextBridge();

const state = {
  client: null,
  config: { ...DEFAULT_AI },
  history: [], // { role, content, scopes? }
  scopes: new Set(),
  contextSource: "live", // "live" (temporal) | "imported" — que reporte analiza el asistente (2.1)
  auditSource: "live", // "live" | "imported" | "replay" — fuente activa en la pestana Auditoria (para KPIs)
  busy: false,
  seq: 0,
  abort: null,
  lastSend: 0,
  counts: {},
  replayPasos: "",
  ctxCache: { key: null, build: null }, // evita reconstruir el contexto en cada mensaje
};

// --- Configuracion ----------------------------------------------------------
async function loadConfig() {
  try {
    const stored = await chrome.storage.local.get(AI_CONFIG_KEY);
    state.config = { ...DEFAULT_AI, ...(stored[AI_CONFIG_KEY] || {}) };
  } catch {
    state.config = { ...DEFAULT_AI };
  }
  state.client = new OpenWebUIClient(state.config);
}
async function saveConfig(patch) {
  state.config = { ...state.config, ...patch };
  await chrome.storage.local.set({ [AI_CONFIG_KEY]: state.config });
  state.client = new OpenWebUIClient(state.config);
}

// --- Estado de conexion -----------------------------------------------------
async function refreshConnection() {
  const el = $("status");
  const txt = $("status-text");
  txt.textContent = "Conectando…";
  el.className = "bar__status";
  const ok = await state.client.available();
  el.className = "bar__status " + (ok ? "ok" : "down");
  const prov = PROVIDERS[state.config.provider] || { label: state.config.provider || "IA" };
  txt.textContent = ok
    ? `${prov.label} · ${state.config.model}`
    : `Sin conexion · ${prov.label}`;
}

// --- Contexto: chips de ambito ---------------------------------------------
function scopeCount(id) {
  const c = state.counts || {};
  switch (id) {
    case "metadata": return "";
    case "errors": return (c.error || 0) + (c.unhandledrejection || 0);
    case "network": return c.network || 0;
    case "console": return c.console || 0;
    case "routes": return c.route || 0;
    case "functions": return c["function-call"] || 0;
    case "globals": return c["global-state"] || 0;
    case "interactions": return (c.click || 0) + (c.input || 0) + (c.key || 0) + (c.dblclick || 0) + (c.dragdrop || 0);
    case "audit": return c.focus || 0;
    case "security": return c.security || 0;
    case "performance": return (c["web-vitals"] || 0) + (c["interaction-timing"] || 0) + (c["resource-timing"] || 0);
    case "replay": return state.replayPasos || "";
    default: return "";
  }
}
function renderScopes() {
  const host = $("scopes");
  host.innerHTML = "";
  for (const s of SCOPES) {
    const n = scopeCount(s.id);
    const btn = document.createElement("button");
    btn.className = "scope";
    btn.type = "button";
    btn.title = s.desc;
    btn.setAttribute("aria-pressed", String(state.scopes.has(s.id)));
    btn.innerHTML = `${Markdown.escapeHtml(s.label)}${n !== "" ? ` <span class="scope__n">${n}</span>` : ""}`;
    btn.addEventListener("click", () => {
      if (state.scopes.has(s.id)) state.scopes.delete(s.id);
      else state.scopes.add(s.id);
      btn.setAttribute("aria-pressed", String(state.scopes.has(s.id)));
      previewContextSize(); // feedback de tamano al cambiar ambitos
    });
    host.appendChild(btn);
  }
}
async function refreshState() {
  const st = await bridge.getState();
  // Antes state.counts/eventCount SIEMPRE venian de getState() (temporal),
  // sin importar la fuente elegida en el selector Temporal/Importado del
  // asistente — por eso los chips (Errores, Red, Variables...) y el contador
  // "N eventos" nunca reflejaban el reporte importado aunque estuviera
  // seleccionado. Ahora, si la fuente activa es "imported", se leen los
  // conteos del reporte importado (K.replay) en su lugar.
  let counts = st.counts || {};
  let eventCount = st.count || 0;
  if (state.contextSource === "imported") {
    const imp = await bridge.getReport("imported");
    counts = (imp && imp.metadata && imp.metadata.counts) || {};
    eventCount = (imp && imp.metadata && imp.metadata.eventCount) || 0;
  }
  state.counts = counts;
  state.eventCount = eventCount;
  state.config = state.config || {};
  state.captureConfig = st.config || {};
  // Conteo ligero para el chip "Repeticion" (no viene en getState: vive en
  // K.replayJob, no en el timeline temporal; es global, no depende de la
  // fuente seleccionada arriba).
  try {
    const tr = await qaControl("getReplayTrace");
    state.replayPasos = tr && tr.resumen ? tr.resumen.pasos : "";
  } catch {
    state.replayPasos = "";
  }
  const rec = $("rec-state");
  // La grabacion en curso es un hecho global (no depende de la fuente
  // elegida para el contexto), pero el conteo mostrado si respeta la fuente.
  rec.textContent = st.isRecording ? `● grabando · ${st.count} ev` : `${eventCount} eventos`;
  rec.className = "context__rec" + (st.isRecording ? " live" : "");
  // Refleja el estado en el boton de accion del panel.
  const recBtn = $("act-record");
  if (recBtn) {
    recBtn.textContent = st.isRecording ? "■ Detener" : "● Grabar";
    recBtn.classList.toggle("on", !!st.isRecording);
    recBtn.setAttribute("aria-pressed", String(!!st.isRecording));
  }
  renderScopes();
}

// --- Acciones de captura (accesos directos a funciones del popup) -----------
/** Envia una accion de control al service worker (grabar/replay/config). */
function qaControl(action, extra = {}) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage({ channel: "qa-control", action, ...extra }, (res) => {
        if (chrome.runtime.lastError) return resolve(null);
        resolve(res || null);
      });
    } catch {
      resolve(null);
    }
  });
}
const linesToArr = (s) => String(s || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
const arrToLines = (a) => (Array.isArray(a) ? a.join("\n") : "");

// --- Panel de KPIs (colapsable): resumen de la sesion sin recorrer el timeline.
let kpisOpen = false;
function kpiCard(label, value, cls) {
  return `<div class="tl-kpi${cls ? " tl-kpi--" + cls : ""}"><div class="tl-kpi__v">${value}</div><div class="tl-kpi__l">${label}</div></div>`;
}
async function renderKpis() {
  const box = $("tl-kpis");
  if (!box || box.hidden) return;
  // Antes esto SIEMPRE pedia getKpis (SW), que solo conoce el reporte
  // temporal. Al ver "Repeticion" (reproduciendo un reporte importado), los
  // KPIs mostraban 0 eventos/errores/red del temporal (vacio) mezclados con
  // la fidelidad real del replay — confuso y erroneo. Ahora se calcula aqui
  // mismo, con la misma funcion pura que usa el SW, sobre el reporte y la
  // traza que realmente corresponden a la fuente activa.
  const wantsImported = state.auditSource === "imported" || state.auditSource === "replay";
  const report = await bridge.getReport(wantsImported ? "imported" : "live");
  if (!report) {
    box.innerHTML = `<div class="tl-empty">Sin datos aun.</div>`;
    return;
  }
  let trace = [];
  if (state.auditSource === "replay") {
    const tr = await qaControl("getReplayTrace");
    trace = (tr && tr.trace) || [];
  }
  const k = computeKpis(report, { trace });
  const p = k.performance || {};
  const cards = [
    kpiCard("Eventos", k.eventos),
    kpiCard("Errores", k.errores, k.errores ? "bad" : "ok"),
    kpiCard("LCP", p.lcpMs != null ? Math.round(p.lcpMs) + "ms" : "—", p.lcpMs > 2500 ? "warn" : p.lcpMs ? "ok" : ""),
    kpiCard("CLS", p.cls != null ? p.cls.toFixed ? p.cls.toFixed(2) : p.cls : "—", p.cls > 0.1 ? "warn" : ""),
    kpiCard("INP", p.inpMs != null ? Math.round(p.inpMs) + "ms" : "—", p.inpMs > 200 ? "warn" : ""),
    kpiCard("TBT", p.tbtMs != null ? Math.round(p.tbtMs) + "ms" : "—", p.tbtMs > 300 ? "warn" : ""),
    kpiCard("Red total", k.red.total),
    kpiCard("Red fallida", k.red.fallidas, k.red.fallidas ? "bad" : "ok"),
    kpiCard("Seguridad", k.seguridad.total, k.seguridad.porSeveridad.critica || k.seguridad.porSeveridad.alta ? "bad" : k.seguridad.total ? "warn" : "ok"),
    kpiCard("Interacciones", k.interacciones),
  ];
  if (k.replay) cards.push(kpiCard("Fidelidad replay", k.replay.fidelidad + "%", k.replay.fidelidad >= 98 ? "ok" : k.replay.fidelidad >= 90 ? "warn" : "bad"));
  box.innerHTML = cards.join("");
}

// --- Indicador de reintentos de webhook pendientes (qa:webhookPending). -----
async function renderWebhookPending() {
  const el = $("wh-pending");
  if (!el) return;
  const res = await qaControl("getWebhookStatus");
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

async function refreshReplayState() {
  const res = await qaControl("getReplay");
  const play = $("act-play");
  const progress = $("replay-progress");
  if (res && res.ok && res.report && Array.isArray(res.report.timeline)) {
    if (play) play.disabled = false;
    const active = res.job && res.job.active;
    const txt = active
      ? `reproduciendo ${res.job.index}/${res.report.timeline.length}`
      : `${res.report.timeline.length} eventos listos`;
    if (progress) progress.textContent = txt;
  } else {
    if (play) play.disabled = true;
    if (progress) progress.textContent = "";
  }
}

function wireActions() {
  // Grabar / Detener.
  $("act-record").addEventListener("click", async () => {
    await qaControl("toggle");
    refreshState();
  });
  // La importacion es unica y vive en la pestana Reporte (no en Auditoria).
  // Reproducir / Detener replay contra la pestana activa.
  $("act-play").addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || tab.id == null) return toast("Sin pestana activa.");
    const speedEl = $("replay-speed");
    const speed = speedEl ? Number(speedEl.value) || 1 : 1;
    const res = await qaControl("startReplay", { tabId: tab.id, options: { speed } });
    if (res && res.ok) {
      toast("Reproduciendo en la pestana…");
      // Lleva la vista a "Repeticion" para ver el avance en vivo (2.3).
      document.getElementById("src-replay")?.click();
    } else {
      toast("No se pudo iniciar el replay.");
    }
  });
  $("act-stop").addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    await qaControl("stopReplay", { tabId: tab && tab.id });
    toast("Replay detenido.");
  });
  // Configuracion de captura (desplegable).
  $("act-cfg").addEventListener("click", () => {
    const panel = $("capture-cfg");
    const open = panel.hasAttribute("hidden");
    if (open) {
      const c = state.captureConfig || {};
      $("cfg-mask").value = arrToLines(c.maskSelectors);
      $("cfg-globals").value = arrToLines(c.watchedGlobals);
      $("cfg-fns").value = arrToLines(c.patchedFunctions);
      panel.removeAttribute("hidden");
    } else {
      panel.setAttribute("hidden", "");
    }
    $("act-cfg").setAttribute("aria-expanded", String(open));
  });
  // Perfil, dominios y telemetria: configuracion persistente, independiente de
  // la sesion de captura — por eso vive en su propio panel con su propio boton.
  $("act-domains").addEventListener("click", async () => {
    const panel = $("domains-cfg");
    const open = panel.hasAttribute("hidden");
    if (open) {
      const s = (await qaControl("getSettings")) || {};
      const st = (s && s.settings) || {};
      state.settings = st;
      $("cfg-domains").value = arrToLines(st.allowedDomains);
      $("cfg-prof-name").value = (st.profile && st.profile.name) || "";
      $("cfg-prof-email").value = (st.profile && st.profile.email) || "";
      $("cfg-wh-url").value = (st.webhook && st.webhook.url) || "";
      $("cfg-wh-token").value = (st.webhook && st.webhook.token) || "";
      $("cfg-wh-mode").value = (st.webhook && st.webhook.mode) || "manual";
      $("cfg-wh-enabled").checked = !!(st.webhook && st.webhook.enabled);
      $("cfg-autostart").checked = !!st.autoStart;
      panel.removeAttribute("hidden");
    } else {
      panel.setAttribute("hidden", "");
    }
    $("act-domains").setAttribute("aria-expanded", String(open));
  });
  // Panel de KPIs (desplegable): resumen de la sesion sin recorrer el timeline.
  $("tl-kpis-toggle").addEventListener("click", () => {
    const panel = $("tl-kpis");
    const open = panel.hasAttribute("hidden");
    if (open) {
      panel.removeAttribute("hidden");
      renderKpis();
    } else {
      panel.setAttribute("hidden", "");
    }
    kpisOpen = open;
    $("tl-kpis-toggle").setAttribute("aria-expanded", String(open));
  });
  $("cfg-capture-apply").addEventListener("click", async () => {
    const config = {
      maskSelectors: linesToArr($("cfg-mask").value),
      watchedGlobals: linesToArr($("cfg-globals").value),
      patchedFunctions: linesToArr($("cfg-fns").value),
    };
    const res = await qaControl("setConfig", { config });
    if (res && res.ok) {
      state.captureConfig = res.config;
      toast("Configuracion aplicada.");
    } else {
      toast("No se pudo aplicar la configuracion.");
    }
  });
  // Guarda ajustes persistentes (dominios/perfil/webhook/auto-inicio).
  $("cfg-settings-save").addEventListener("click", async () => {
    const settings = {
      allowedDomains: linesToArr($("cfg-domains").value),
      profile: { name: $("cfg-prof-name").value.trim(), email: $("cfg-prof-email").value.trim() },
      webhook: {
        url: $("cfg-wh-url").value.trim(),
        token: $("cfg-wh-token").value.trim(),
        mode: $("cfg-wh-mode").value,
        enabled: $("cfg-wh-enabled").checked,
      },
      autoStart: $("cfg-autostart").checked,
    };
    // Validacion clara ANTES de guardar: el envio exige HTTPS (o localhost) y se
    // rechaza en silencio si no — avisar aqui evita confusion sobre por que no
    // llega la telemetria.
    if (settings.webhook.enabled && settings.webhook.url && !/^https:\/\//i.test(settings.webhook.url) && !/^https?:\/\/(localhost|127\.0\.0\.1)/i.test(settings.webhook.url)) {
      $("cfg-settings-msg").textContent = "El webhook debe usar HTTPS (o localhost) para poder enviarse.";
      $("cfg-wh-url").focus();
      return;
    }
    // Si hay webhook, pide permiso de host para poder enviarlo (gesto de usuario).
    if (settings.webhook.enabled && settings.webhook.url) {
      try {
        await chrome.permissions.request({ origins: ["<all_urls>"] });
      } catch {
        /* el usuario decide */
      }
    }
    const res = await qaControl("setSettings", { settings });
    $("cfg-settings-msg").textContent = res && res.ok
      ? `Ajustes guardados · ${settings.allowedDomains.length || "todos los"} dominio(s) · webhook ${settings.webhook.enabled ? "activo" : "inactivo"}.`
      : "Error al guardar.";
    if (res && res.ok) state.settings = res.settings;
  });
  // Envio manual de la telemetria acumulada de la grabacion en curso.
  $("cfg-tele-flush").addEventListener("click", async () => {
    await qaControl("flushTelemetry");
    $("cfg-settings-msg").textContent = "Telemetria enviada.";
  });
}

// --- Contexto: construccion presupuestada + cache ---------------------------
/** Devuelve el contexto (cacheado por ambitos + conteo de eventos). */
async function getContext(scopes) {
  const key = state.contextSource + ":" + [...scopes].sort().join("|") + ":" + (state.eventCount || 0);
  if (state.ctxCache.key === key && state.ctxCache.build) return state.ctxCache.build;
  const build = await bridge.buildContext(scopes, CONTEXT_BUDGET, state.contextSource);
  state.ctxCache = { key, build };
  updateCtxIndicator(build);
  return build;
}
function updateCtxIndicator(build) {
  const el = $("ctx-size");
  if (!el) return;
  if (!build || !build.snapshot) {
    el.textContent = "";
    el.className = "ctx-size";
    return;
  }
  const kb = Math.max(1, Math.round(build.chars / 1024));
  el.textContent = `~${kb} KB${build.trimmed ? " \u00b7 recortado" : ""}`;
  el.className = "ctx-size" + (build.trimmed ? " warn" : "");
}
let ctxPreviewTimer = null;
function previewContextSize() {
  clearTimeout(ctxPreviewTimer);
  ctxPreviewTimer = setTimeout(async () => {
    if (!state.scopes.size) return updateCtxIndicator(null);
    updateCtxIndicator(await getContext([...state.scopes]));
  }, 350);
}

// --- Render de mensajes -----------------------------------------------------
function scrollToEnd() {
  const t = $("thread");
  t.scrollTop = t.scrollHeight;
}
function hideEmpty() {
  const e = $("empty");
  if (e) e.remove();
}
function addUserMessage(text, scopes) {
  hideEmpty();
  const el = document.createElement("div");
  el.className = "msg msg--user";
  el.innerHTML = Markdown.escapeHtml(text).replace(/\n/g, "<br>");
  if (scopes && scopes.length) {
    const tags = document.createElement("div");
    tags.className = "msg__ctx";
    tags.innerHTML = scopes.map((s) => `<span>/${Markdown.escapeHtml(s)}</span>`).join("");
    el.appendChild(tags);
  }
  $("thread").appendChild(el);
  scrollToEnd();
}
function addAiMessage(markdown) {
  hideEmpty();
  const el = document.createElement("div");
  el.className = "msg msg--ai";
  el.innerHTML = renderMarkdown(markdown); // markdown saneado (escape-first)
  $("thread").appendChild(el);
  scrollToEnd();
  return el;
}
function addErrorMessage(text) {
  hideEmpty();
  const el = document.createElement("div");
  el.className = "msg msg--err";
  el.textContent = text;
  $("thread").appendChild(el);
  scrollToEnd();
}
function showTyping(on) {
  const existing = $("typing");
  if (on && !existing) {
    const el = document.createElement("div");
    el.id = "typing";
    el.className = "typing";
    el.innerHTML = "<i></i><i></i><i></i>";
    $("thread").appendChild(el);
    scrollToEnd();
  } else if (!on && existing) {
    existing.remove();
  }
}

// --- Bloqueo de la entrada (flujo fijo solicitud->respuesta) ----------------
function setBusy(busy) {
  state.busy = busy;
  $("input").disabled = busy;
  const send = $("send");
  send.classList.toggle("cancel", busy);
  send.textContent = busy ? "■" : "➤";
  send.title = busy ? "Cancelar" : "Enviar";
  send.disabled = false; // en "busy" sirve para cancelar
  showTyping(busy);
  if (!busy) $("input").focus();
}

// --- Envio ------------------------------------------------------------------
async function send() {
  if (state.busy) return; // un solo request en vuelo
  const now = Date.now();
  if (now - state.lastSend < MIN_INTERVAL) {
    toast("Espera un momento entre consultas.");
    return;
  }
  const input = $("input");
  const text = input.value.trim();
  if (!text) return;

  state.lastSend = now;
  const usedScopes = [...state.scopes];
  addUserMessage(text, usedScopes);
  input.value = "";
  updateCounter();
  autoGrow();

  setBusy(true);
  state.abort = new AbortController();
  const mySeq = ++state.seq;

  try {
    // Contexto presupuestado y cacheado (no se reconstruye si no cambio).
    const build = usedScopes.length ? await getContext(usedScopes) : null;
    const sysContent = build
      ? bridge.systemPrompt(build)
      : bridge.systemPrompt({ snapshot: null, digest: "", meta: {} });

    // CONVERSACION MULTI-TURNO: el historial viaja como mensajes reales con roles,
    // no como texto incrustado en el ultimo user message. El modelo puede recordar
    // lo dicho en el hilo y mantener coherencia entre turnos.
    // Estructura: [system, ...historial(user/assistant), user_actual]
    const recentPairs = state.history.slice(-((state.config.maxHistoryTurns || 6) * 2));
    const messages = [
      { role: "system", content: sysContent },
      ...recentPairs.map((m) => ({ role: m.role, content: m.content })),
      { role: "user", content: text },
    ];

    // Burbuja de la IA que se va rellenando con el streaming.
    showTyping(false);
    const aiEl = addAiMessage("");
    let acc = "";
    let lastRender = 0;
    const paint = (full, force) => {
      const now = Date.now();
      if (!force && now - lastRender < 90) return; // throttle del render de Markdown
      lastRender = now;
      aiEl.innerHTML = renderMarkdown(full);
      scrollToEnd();
    };

    let answer = "";
    try {
      answer = await state.client.chatStream(messages, state.abort.signal, (_d, full) => {
        if (mySeq !== state.seq) return;
        acc = full;
        paint(full, false);
      });
    } catch (streamErr) {
      if (state.abort && state.abort.signal.aborted) throw streamErr;
      answer = await state.client.chat(messages, state.abort.signal);
    }
    if (mySeq !== state.seq) return; // respuesta obsoleta: descartar

    answer = answer || acc;
    aiEl.innerHTML = renderMarkdown(answer || "_(respuesta vacia)_"); // render final completo
    scrollToEnd();
    state.history.push({ role: "user", content: text });
    state.history.push({ role: "assistant", content: answer });
    persist();
  } catch (err) {
    if (mySeq !== state.seq) return;
    if (state.abort && state.abort.signal.aborted) addErrorMessage("Consulta cancelada.");
    else addErrorMessage(mapError(err));
  } finally {
    if (mySeq === state.seq) {
      state.abort = null;
      setBusy(false);
    }
  }
}

function cancel() {
  if (state.busy && state.abort) state.abort.abort();
}

function mapError(err) {
  const m = (err && err.message) || String(err);
  if (/Failed to fetch|NetworkError|aborted/i.test(m))
    return "Sin respuesta del servidor. Revisa la conexion, que el host este accesible y los permisos del host. (" + m + ")";
  if (/HTTP 401|HTTP 403/.test(m)) return "Autenticacion rechazada: verifica la API key o el proxy. (" + m + ")";
  if (/HTTP 404/.test(m)) return "Endpoint o modelo no encontrado: revisa Base URL y modelo. (" + m + ")";
  return m;
}

// --- Cache ------------------------------------------------------------------
function persist() {
  if (ChatCache.getKeep()) ChatCache.saveCurrent(state.history);
  updateCacheSize();
}
function updateCacheSize() {
  const kb = Math.max(1, Math.round(ChatCache.sizeBytes() / 1024));
  $("cache-size").textContent = kb + " KB";
}
function loadHistory() {
  if (!ChatCache.getKeep()) return;
  const h = ChatCache.loadCurrent();
  if (!h || !h.length) return;
  state.history = h;
  for (const m of h) {
    if (m.role === "user") addUserMessage(m.content);
    else addAiMessage(m.content);
  }
}

// --- Contador y autogrow ----------------------------------------------------
function updateCounter() {
  const input = $("input");
  const c = $("counter");
  const n = input.value.length;
  c.textContent = `${n}/2000`;
  c.classList.toggle("warn", n > 1900);
}
function autoGrow() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 120) + "px";
}

// --- Aviso ------------------------------------------------------------------
let toastTimer = null;
/** Mantiene --dock-h sincronizada con la altura real del dock del compositor,
 *  para que el toast (posicionado por CSS relativo a esa variable) nunca quede
 *  tapado ni se solape cuando el dock crece (dock__meta se envuelve en anchos
 *  angostos) o cuando no esta visible (pestana Auditoria, sin dock en el flujo). */
function watchDockHeight() {
  const dock = $("dock") || document.querySelector(".dock");
  if (!dock) return;
  const apply = () => {
    const visible = dock.offsetParent !== null; // oculto si su tabpane no es .is-on
    document.documentElement.style.setProperty("--dock-h", visible ? `${dock.offsetHeight}px` : "16px");
  };
  apply();
  try {
    new ResizeObserver(apply).observe(dock);
  } catch {
    window.addEventListener("resize", apply); // navegador sin ResizeObserver
  }
  // El cambio de pestana no dispara resize del dock (solo cambia display); observarlo aparte.
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setTimeout(apply, 0)));
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  // Popover API: pone el aviso en el "top layer", por encima de cualquier
  // <dialog> abierto (z-index no tiene efecto contra la capa de un dialog).
  try {
    if (!t.matches(":popover-open")) t.showPopover();
  } catch {
    /* navegador sin soporte: sigue visible via posicion fixed + clase */
  }
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    t.classList.remove("on");
    try { t.hidePopover(); } catch { /* no critico */ }
  }, 2000);
}

// --- Ajustes ----------------------------------------------------------------
function openSettings() {
  const cfg = state.config;
  const prov = cfg.provider || "openwebui";
  $("cfg-provider").value = prov;
  $("cfg-base").value = cfg.baseUrl || "";
  $("cfg-model").value = cfg.model || "";
  $("cfg-key").value = cfg.apiKey || "";
  $("cfg-proxy").value = cfg.proxyUrl || "";
  $("cfg-temp").value = cfg.temperature != null ? cfg.temperature : 0.7;
  $("cfg-temp-val").textContent = $("cfg-temp").value;
  $("cfg-tokens").value = cfg.maxTokens || 1024;
  $("cfg-turns").value = cfg.maxHistoryTurns || 6;
  $("probe").textContent = "";
  $("probe").className = "probe";
  updateProviderFields(prov);
  $("settings").showModal();
}
function updateProviderFields(prov) {
  // Mostrar/ocultar Base URL segun el proveedor: OpenWebUI y custom la necesitan.
  const needsBase = prov === "openwebui" || prov === "custom";
  $("fld-base").style.display = needsBase ? "" : "none";
  // Actualizar el placeholder del modelo con el default del proveedor.
  const def = PROVIDERS[prov] || {};
  $("cfg-model").placeholder = def.defaultModel || "";
  // Sugerir la URL de base para proveedores conocidos.
  const baseEl = $("cfg-base");
  if (!needsBase && baseEl.value === "") {
    baseEl.value = def.baseUrl || "";
  }
}

// --- Cableado de eventos ----------------------------------------------------
function wire() {
  $("send").addEventListener("click", () => (state.busy ? cancel() : send()));
  const input = $("input");
  input.addEventListener("input", () => { updateCounter(); autoGrow(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!state.busy) send(); }
  });

  // Fuente del contexto: temporal (grabacion) vs reporte importado (2.1).
  // Cambiar la fuente invalida el cache de contexto (la clave incluye la
  // fuente) para no mezclar datos de una sesion con la otra.
  $("ctx-src-live").addEventListener("click", async () => {
    state.contextSource = "live";
    $("ctx-src-live").classList.add("is-on");
    $("ctx-src-live").setAttribute("aria-selected", "true");
    $("ctx-src-imported").classList.remove("is-on");
    $("ctx-src-imported").setAttribute("aria-selected", "false");
    await refreshState(); // refresca los chips YA (antes se veian las cuentas de la fuente anterior)
    previewContextSize();
  });
  $("ctx-src-imported").addEventListener("click", async () => {
    const report = await bridge.getReport("imported");
    if (!report) {
      toast("No hay ningun reporte importado. Ve a la pestana Reporte.");
      return;
    }
    state.contextSource = "imported";
    $("ctx-src-imported").classList.add("is-on");
    $("ctx-src-imported").setAttribute("aria-selected", "true");
    $("ctx-src-live").classList.remove("is-on");
    $("ctx-src-live").setAttribute("aria-selected", "false");
    await refreshState();
    previewContextSize();
  });

  // Cache
  const keepBtn = $("cache-keep");
  keepBtn.setAttribute("aria-pressed", String(ChatCache.getKeep()));
  keepBtn.addEventListener("click", () => {
    const next = !ChatCache.getKeep();
    ChatCache.setKeep(next);
    keepBtn.setAttribute("aria-pressed", String(next));
    if (next) persist();
    else ChatCache.clearCurrent();
    toast(next ? "La cache se conservara." : "La cache no se conservara al cerrar.");
    updateCacheSize();
  });
  $("cache-save").addEventListener("click", () => {
    if (!state.history.length) return toast("No hay conversacion que guardar.");
    const name = prompt("Nombre para la sesion guardada:", "Sesion " + new Date().toLocaleString());
    if (name == null) return;
    ChatCache.saveSession(name, state.history);
    toast("Sesion guardada.");
    updateCacheSize();
  });
  $("cache-clear").addEventListener("click", () => {
    if (!confirm("Vaciar la conversacion actual?")) return;
    state.history = [];
    ChatCache.clearCurrent();
    $("thread").innerHTML =
      '<div id="empty" class="empty"><h2>Analiza tu sesion de QA</h2><p>Activa los ambitos de contexto y pregunta.</p><p class="hint">No graba ni exporta: solo analiza.</p></div>';
    updateCacheSize();
    toast("Conversacion vaciada.");
  });

  // Ajustes
  $("open-settings").addEventListener("click", openSettings);
  $("close-settings").addEventListener("click", () => $("settings").close());
  // Proveedor: actualiza campos segun seleccion
  $("cfg-provider").addEventListener("change", () => updateProviderFields($("cfg-provider").value));
  // Temperatura: muestra el valor en tiempo real
  $("cfg-temp").addEventListener("input", () => { $("cfg-temp-val").textContent = $("cfg-temp").value; });
  $("cfg-save").addEventListener("click", async () => {
    const prov = $("cfg-provider").value;
    const pDef = PROVIDERS[prov] || {};
    const base = $("cfg-base").value.trim() || pDef.baseUrl || DEFAULT_AI.baseUrl;
    await saveConfig({
      provider: prov,
      baseUrl: base,
      model: $("cfg-model").value.trim() || pDef.defaultModel || DEFAULT_AI.model,
      apiKey: $("cfg-key").value.trim(),
      proxyUrl: $("cfg-proxy").value.trim(),
      temperature: parseFloat($("cfg-temp").value) || 0.7,
      maxTokens: parseInt($("cfg-tokens").value) || 1024,
      maxHistoryTurns: parseInt($("cfg-turns").value) || 6,
    });
    $("settings").close();
    toast("Configuracion guardada · " + (PROVIDERS[prov] ? PROVIDERS[prov].label : prov));
    refreshConnection();
  });
  $("cfg-test").addEventListener("click", async () => {
    const probe = $("probe");
    probe.textContent = "Probando conexion…";
    probe.className = "probe";
    const prov = $("cfg-provider").value;
    const pDef = PROVIDERS[prov] || {};
    const tmp = new OpenWebUIClient({
      provider: prov,
      baseUrl: $("cfg-base").value.trim() || pDef.baseUrl || "",
      model: $("cfg-model").value.trim() || pDef.defaultModel || "",
      apiKey: $("cfg-key").value.trim(),
      proxyUrl: $("cfg-proxy").value.trim(),
    });
    const ok = await tmp.available();
    probe.textContent = ok ? "Conexion correcta." : "Sin respuesta (revisa URL, API key o permisos del host).";
    probe.className = "probe " + (ok ? "ok" : "down");
  });

  window.addEventListener("pagehide", () => {
    if (ChatCache.getKeep()) ChatCache.saveCurrent(state.history);
    else ChatCache.clearCurrent();
  });
}

// --- Arranque ---------------------------------------------------------------
async function init() {
  await loadConfig();
  wire();
  loadHistory();
  updateCounter();
  updateCacheSize();
  await refreshState();
  wireActions();
  watchDockHeight(); // sincroniza --dock-h para que el toast nunca se solape ni quede tapado
  // Nota: la wiring de la pestana Auditoria (timeline, import/export, replay,
  // paleta) vive en la IIFE `setupQaTab` mas abajo, que se autoejecuta al cargar
  // el script. No existe una funcion `wireQA` — llamarla aqui lanzaba una
  // excepcion no capturada que impedia que el resto de esta funcion (conexion,
  // sincronizacion entre pestanas, indicador de webhook, sondeo periodico) se
  // ejecutara jamas.
  refreshReplayState();
  refreshConnection();
  // Si el popup pidio abrir el panel en una pestana especifica (p. ej. "Abrir
  // Auditoria" en la nueva barra de exportacion), respeta esa peticion una vez.
  try {
    const OPEN_TAB_KEY = "charlyaudit:openTab";
    const stored = await chrome.storage.local.get(OPEN_TAB_KEY);
    const wanted = stored[OPEN_TAB_KEY];
    if (wanted === "qa" || wanted === "report" || wanted === "assistant") {
      document.getElementById("tab-btn-" + wanted)?.click();
      await chrome.storage.local.remove(OPEN_TAB_KEY);
    }
  } catch {
    /* sin storage */
  }
  // Sincronia popup<->panel<->SW: al grabar/detener desde cualquier UI, el estado
  // compartido cambia y ambas interfaces se refrescan (sin inconsistencias).
  try {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "local") return;
      if (changes["qa:isRecording"] || changes["qa:timeline"] || changes["qa:meta"]) {
        refreshState();
        refreshReplayState();
      }
      if (changes["qa:replay"] || changes["qa:replayJob"]) refreshReplayState();
      if (changes["qa:webhookPending"]) renderWebhookPending();
      if (changes["qa:timeline"] && kpisOpen) renderKpis();
    });
  } catch {
    /* sin storage */
  }
  renderWebhookPending();
  // Refresco ligero de conteos/grabacion/replay mientras el panel este visible.
  setInterval(() => {
    if (document.visibilityState === "visible") {
      refreshState();
      refreshReplayState();
      renderWebhookPending();
      if (kpisOpen) renderKpis();
    }
  }, 4000);
}
init();

// ===========================================================================
// Pestañas (Asistente / QA·Timeline) + Vista premium de timeline
// ===========================================================================
(function setupQaTab() {
  const TL_META = {
    click: { c: "#5b6cff", l: "click" }, dblclick: { c: "#6d7cff", l: "dblclick" },
    middleclick: { c: "#818cf8", l: "centro" }, dragdrop: { c: "#c084fc", l: "drag" },
    key: { c: "#7dd3fc", l: "tecla" }, input: { c: "#38bdf8", l: "input" }, focus: { c: "#2dd4bf", l: "foco" },
    network: { c: "#a78bfa", l: "red" }, console: { c: "#94a3b8", l: "console" }, error: { c: "#ff6b5e", l: "error" },
    unhandledrejection: { c: "#fb7185", l: "reject" }, "code-block": { c: "#a3e635", l: "codigo" },
    route: { c: "#f5b544", l: "ruta" }, navigation: { c: "#64748b", l: "nav" },
    "global-state": { c: "#34d399", l: "estado" }, "function-call": { c: "#22d3ee", l: "fn" },
    scroll: { c: "#475569", l: "scroll" }, resize: { c: "#475569", l: "resize" }, meta: { c: "#475569", l: "meta" },
    "web-vitals": { c: "#f472b6", l: "vitals" }, "resource-timing": { c: "#fbbf24", l: "recurso" }, security: { c: "#ef4444", l: "seguridad" },
    "interaction-timing": { c: "#818cf8", l: "inp" },
    "response-headers": { c: "#0ea5e9", l: "cabeceras" }, worker: { c: "#14b8a6", l: "worker" },
  };
  const G = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const shortUrl = (u) => {
    if (!u) return "";
    try { const x = new URL(u, "http://x"); return (x.pathname + x.search).slice(0, 46); } catch { return String(u).slice(0, 46); }
  };
  const meta = (t) => TL_META[t] || { c: "#64748b", l: t };

  function tlDesc(e) {
    const d = e.data || {};
    switch (e.type) {
      case "network": return `${d.method || ""} ${d.status || d.error || ""} · ${shortUrl(d.url)}`;
      case "error": case "unhandledrejection": return d.message || d.reason || "error";
      case "route": return `${d.tipo === "spa" ? "SPA " : ""}${d.from || ""} → ${d.to || ""}`;
      case "navigation": return `${d.reason || ""}${d.tipo ? " (" + d.tipo + (d.redirects ? `, ${d.redirects} redir` : "") + ")" : ""} ${shortUrl(d.url)}`;
      case "input": return `${d.selector || ""} = ${d.value}`;
      case "key": return d.run ? `"${d.text}"` : (d.key || "");
      case "global-state": return d.changed && d.changed.length ? "cambio: " + d.changed.join(", ") : "snapshot";
      case "code-block": return d.ref || "";
      case "function-call": return `${d.path} (${d.durationMs}ms)`;
      case "web-vitals": return `LCP ${d.lcpMs}ms · CLS ${d.cls} · INP ${d.inpMs}ms · TBT ${d.tbtMs}ms`;
      case "resource-timing": return `${d.kb}KB · ${d.ms}ms · ${shortUrl(d.url)}`;
      case "interaction-timing": return `${d.tipo || "?"} · INP ${d.inpMs}ms${d.interactionId ? " #" + d.interactionId : ""}`;
      case "security": return `[${d.severidad}] ${d.kind} · ${d.donde}`;
      case "response-headers": { const s = d.seguridad || {}; return `${d.status || ""} · CSP:${s.csp ? "si" : "no"} HSTS:${s.hsts ? "si" : "no"} · ${shortUrl(d.url)}`; }
      case "worker": return `${d.clase}${d.existente ? " (existente)" : d.nuevo ? " (nuevo)" : ""} · ${shortUrl(d.script || d.scope)}`;
      default: return d.selector || d.text || "";
    }
  }
  function tlDetail(e) {
    const d = e.data || {};
    const rows = [];
    const add = (k, v) => { if (v != null && v !== "") rows.push(`<dt>${esc(k)}</dt><dd>${v}</dd>`); };
    add("hora", new Date(e.ts).toLocaleTimeString());
    if (e.delay != null) add("desde previo", `+${e.delay} ms`);
    if (d.selector) add("selector", `<span class="tl-tree">${esc(d.selector)}</span>`);
    if (d.path) add("arbol", `<span class="tl-tree">${esc(d.path)}</span>`);
    if (d.from || d.to) add("drag", esc(`${d.from || "?"} → ${d.to || "?"}`));
    if (d.value !== undefined) add("valor", esc(String(d.value)));
    if (d.text !== undefined) add("texto", esc(String(d.text)));
    if (d.key !== undefined) add("tecla", esc(String(d.key)));
    if (d.method) add("peticion", esc(`${d.method} ${d.status || d.error || ""} ${d.url || ""}`));
    // Waterfall completo: fases DNS/conexion/TTFB/descarga + tamano + protocolo.
    if (d.fases) {
      const f = d.fases;
      const wf = [f.dnsMs != null ? `DNS ${f.dnsMs}ms` : null, f.conexionMs != null ? `TCP ${f.conexionMs}ms` : null, f.ttfbMs != null ? `TTFB ${f.ttfbMs}ms` : null, f.descargaMs != null ? `↓ ${f.descargaMs}ms` : null].filter(Boolean).join(" · ");
      if (wf) add("waterfall", esc(wf));
    }
    if (d.kb != null) add("tamano", `${d.kb} KB${d.cache ? " (cache)" : ""}${d.protocolo ? " · " + esc(d.protocolo) : ""}`);
    if (d.inicioMs != null) add("inicio/fin", `+${d.inicioMs}ms → +${d.finMs || d.inicioMs + (d.durationMs || d.ms || 0)}ms`);
    // INP real: latencia medida por el navegador (PerformanceObserver "event").
    if (d.inpMs != null && ["click","input","key","dblclick"].includes(e.type)) add("INP medido", `${d.inpMs}ms${d.inpMs > 200 ? " ⚠ lento" : d.inpMs > 100 ? " · revisar" : " ✓"}`);
    if (e.type === "interaction-timing") { add("tipo", esc(d.tipo)); add("INP", `${d.inpMs}ms${d.inpMs > 200 ? " ⚠" : ""}`); if (d.interactionId) add("interactionId", esc(d.interactionId)); }
    if (e.type === "web-vitals") { add("TBT segmento", `${d.tbtSegmentMs || 0}ms`); add("long tasks", `${d.longTasksSegment || 0} en segmento · ${d.longTasks || 0} total`); }
    if (d.message || d.reason) add("mensaje", esc(d.message || d.reason));
    if (d.ref) add("origen", esc(d.ref));
    if (d.trigger) add("disparo", esc(d.trigger));
    if (Array.isArray(d.frames) && d.frames.length)
      add("stack", esc(d.frames.slice(0, 4).map((f) => `${f.fn || "?"} @ ${f.url}:${f.line}`).join("  ·  ")));
    if (Array.isArray(d.changed) && d.changed.length) add("mutaciones", esc(d.changed.join(", ")));
    if (d.values) add("valores", `<pre class="tl-code">${esc(JSON.stringify(d.values, null, 1))}</pre>`);
    if (Array.isArray(d.snippet))
      add("codigo", `<pre class="tl-code">${d.snippet.map((s) => `<span class="${s.hit ? "hit" : ""}">${esc((s.hit ? "\u203a " : "  ") + s.n + ": " + s.code)}</span>`).join("\n")}</pre>`);
    if (d.attributes) add("attrs", `<pre class="tl-code">${esc(JSON.stringify(d.attributes))}</pre>`);
    if (d.css) add("css", `<pre class="tl-code">${esc(JSON.stringify(d.css))}</pre>`);
    // Known-issue del baseline: la interaccion se capturo sobre un elemento SIN
    // nombre accesible (ni name/aria-label/texto/placeholder) — dificulta el
    // replay resiliente y es en si un hallazgo de accesibilidad del sitio auditado.
    const a = d.anchor;
    if (a && ["click", "dblclick", "middleclick", "input", "key"].includes(e.type)) {
      const sinNombre = !a.name && !a.aria && !a.text && !a.ph;
      if (sinNombre)
        rows.push(`<dt>known-issue</dt><dd class="tl-issue">⚠ elemento &lt;${esc(a.tag || "?")}&gt; sin nombre accesible (agrega aria-label o texto visible)</dd>`);
    }
    return `<dl class="tl-kv">${rows.join("")}</dl>`;
  }

  let lastReport = null;
  let lastCount = -1;
  let source = "live"; // "live" (temporal) | "imported" | "replay"
  let importedReport = null;
  let importedBundle = null;
  let activeType = null; // filtro por tipo al hacer clic en un chip

  // Recupera el reporte importado ya persistido en el SW (sobrevive a cerrar
  // y reabrir el panel; antes solo vivia en esta variable local).
  async function hydrateImported() {
    const res = await qaControl("getReplay");
    if (res && res.ok && res.report && Array.isArray(res.report.timeline)) {
      importedReport = res.report;
    }
  }

  async function getActiveReport() {
    return source === "imported" ? importedReport : await bridge.getReport();
  }
  function updateSourceUI() {
    // Sincroniza con el state compartido: renderKpis() (fuera de este closure)
    // necesita saber que fuente esta activa para no calcular siempre sobre el
    // reporte temporal — antes eso hacia que los KPIs de "Repeticion" mostraran
    // 0 eventos/errores/red (los del temporal, casi siempre vacio al reproducir
    // un reporte importado) con solo la fidelidad del replay correcta.
    state.auditSource = source;
    G("src-live").classList.toggle("is-on", source === "live");
    G("src-imported").classList.toggle("is-on", source === "imported");
    G("src-replay").classList.toggle("is-on", source === "replay");
    G("src-imported").disabled = !importedReport;
    const badge = G("tl-srcbadge");
    badge.classList.remove("imported");
    if (source === "imported") {
      badge.textContent = "reporte importado (solo lectura)";
      badge.classList.add("imported");
    } else if (source === "replay") {
      badge.textContent = "telemetria de la ultima repeticion";
      badge.classList.add("imported");
    } else {
      badge.textContent = "grabacion en curso (temporal)";
    }
    // Vaciar cambia de etiqueta segun que fuente se va a vaciar (2.2).
    const clearBtn = G("tl-clear");
    clearBtn.title = source === "live" ? "Vaciar la grabacion temporal" : "Vaciar el reporte importado (y su repeticion)";
    // Controles de repeticion (velocidad) solo tienen sentido en esa fuente.
    G("replay-controls").hidden = source !== "replay";
  }

  async function renderTimeline(force) {
    if (source === "replay") return renderReplayTrace();
    const report = await getActiveReport();
    const tl = (report && report.timeline) || [];
    // Perf: en la fuente temporal, no reconstruir si el conteo no cambio.
    if (!force && source === "live" && tl.length === lastCount && G("tl-list").children.length) return;
    lastCount = tl.length;
    lastReport = report;
    const list = G("tl-list");
    G("tl-ribbon").innerHTML = tl.slice(-120).map((e) => `<span class="tl-tick" style="background:${meta(e.type).c}"></span>`).join("");
    // Chips por tipo (clicables: filtran la lista).
    const counts = (report && report.metadata && report.metadata.counts) || {};
    G("tl-chips").innerHTML = Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([t, n]) => `<span class="tl-chip${activeType === t ? " is-on" : ""}" data-type="${esc(t)}"><span class="tl-dot" style="background:${meta(t).c}"></span><b>${n}</b> ${esc(meta(t).l)}</span>`)
      .join("");
    G("tl-chips").querySelectorAll(".tl-chip").forEach((c) =>
      c.addEventListener("click", () => {
        const t = c.getAttribute("data-type");
        activeType = activeType === t ? null : t;
        renderTimeline(true);
      })
    );
    // Lista (mas reciente arriba), con filtro por texto y por tipo activo.
    const filter = (G("tl-filter").value || "").toLowerCase().trim();
    const rows = tl
      .filter((e) => !activeType || e.type === activeType)
      .filter((e) => !filter || e.type.includes(filter) || JSON.stringify(e.data || {}).toLowerCase().includes(filter))
      .slice(-400).reverse();
    if (!rows.length) {
      list.innerHTML = `<div class="tl-empty">Sin eventos${filter || activeType ? " para el filtro" : ""}.</div>`;
      return;
    }
    list.innerHTML = rows
      .map((e) => {
        const m = meta(e.type);
        const a = e.data && e.data.anchor;
        const issue = a && ["click", "dblclick", "middleclick", "input", "key"].includes(e.type) && !a.name && !a.aria && !a.text && !a.ph;
        return `<div class="tl-row${issue ? " bad" : ""}"><div class="tl-row__head">
          <span class="tl-badge" style="background:${m.c}">${esc(m.l)}</span>
          <span class="tl-desc">${issue ? "⚠ " : ""}${esc(tlDesc(e))}</span>
          <span class="tl-delay">+${e.delay || 0}ms</span>
        </div><div class="tl-detail">${tlDetail(e)}</div></div>`;
      })
      .join("");
    list.querySelectorAll(".tl-row__head").forEach((h) =>
      h.addEventListener("click", () => h.parentElement.classList.toggle("is-open"))
    );
  }

  // Vista de la telemetria del ultimo replay: pasos, estado e inconsistencias.
  const EST_META = {
    ok: { c: "#34d399", l: "ok" },
    "no-encontrado": { c: "#ef4444", l: "no encontrado" },
    "no-visible": { c: "#f5b544", l: "no visible" },
    error: { c: "#ef4444", l: "error" },
    enmascarado: { c: "#64748b", l: "enmascarado" },
  };
  const estMeta = (e) => EST_META[e] || { c: "#64748b", l: e || "?" };
  async function renderReplayTrace() {
    const res = await qaControl("getReplayTrace");
    const list = G("tl-list");
    const trace = (res && res.trace) || [];
    const resumen = (res && res.resumen) || { pasos: 0, inconsistencias: 0 };
    // Ribbon: verde = fiel, rojo = con inconsistencia.
    G("tl-ribbon").innerHTML = trace
      .map((t) => `<span class="tl-tick" style="background:${t.inconsistencias && t.inconsistencias.length ? "#ef4444" : "#34d399"}"></span>`)
      .join("");
    // Chips: resumen + conteo por estado.
    const porEstado = {};
    for (const t of trace) porEstado[t.estado] = (porEstado[t.estado] || 0) + 1;
    const chips = [`<span class="tl-chip"><b>${resumen.pasos}</b> pasos</span>`, `<span class="tl-chip" style="border-color:#ef4444"><span class="tl-dot" style="background:#ef4444"></span><b>${resumen.inconsistencias}</b> inconsistencias</span>`];
    for (const [e, n] of Object.entries(porEstado))
      chips.push(`<span class="tl-chip"><span class="tl-dot" style="background:${estMeta(e).c}"></span><b>${n}</b> ${esc(estMeta(e).l)}</span>`);
    G("tl-chips").innerHTML = chips.join("");
    if (!trace.length) {
      list.innerHTML = `<div class="tl-empty">Aun no hay repeticion. Importa o graba un flujo y pulsa <strong>Reproducir</strong>.</div>`;
      return;
    }
    // Filtro por texto reutilizado; por defecto muestra primero las inconsistencias.
    const filter = (G("tl-filter").value || "").toLowerCase().trim();
    const rows = trace
      .filter((t) => !filter || (t.sel || "").toLowerCase().includes(filter) || (t.tipo || "").includes(filter) || (t.inconsistencias || []).join(" ").toLowerCase().includes(filter))
      .slice()
      .sort((a, b) => (b.inconsistencias?.length || 0) - (a.inconsistencias?.length || 0) || a.i - b.i);
    list.innerHTML = rows
      .map((t) => {
        const m = estMeta(t.estado);
        const bad = t.inconsistencias && t.inconsistencias.length;
        const exp = t.esperado || {};
        const detail =
          `<dl class="tl-kv">` +
          `<dt>paso</dt><dd>#${t.i} · ${esc(t.tipo)}</dd>` +
          (t.sel ? `<dt>selector</dt><dd><span class="tl-tree">${esc(t.sel)}</span></dd>` : "") +
          `<dt>estado</dt><dd>${esc(t.estado)}</dd>` +
          `<dt>esperado</dt><dd>nav:${exp.nav ? "si" : "no"} · red:${exp.net || 0} · err:${exp.err || 0}</dd>` +
          `<dt>observado</dt><dd>mutaciones:${t.mutaciones || 0} · navego:${t.navego ? "si" : "no"}</dd>` +
          (t.diff ? `<dt>diff DOM</dt><dd>nodos ${esc(t.diff.nodos)}${t.diff.tituloCambio ? " · titulo cambio" : ""}</dd>` : "") +
          (bad ? `<dt>problemas</dt><dd style="color:var(--live)">${esc(t.inconsistencias.join(" · "))}</dd>` : "") +
          `</dl>`;
        return `<div class="tl-row${bad ? " bad" : ""}"><div class="tl-row__head">
          <span class="tl-badge" style="background:${m.c}">#${t.i} ${esc(m.l)}</span>
          <span class="tl-desc">${esc(t.sel || t.tipo)}${bad ? " · " + esc(t.inconsistencias[0]) : ""}</span>
          <span class="tl-delay">${bad ? "\u26a0" : "\u2713"}</span>
        </div><div class="tl-detail">${detail}</div></div>`;
      })
      .join("");
    list.querySelectorAll(".tl-row__head").forEach((h) => h.addEventListener("click", () => h.parentElement.classList.toggle("is-open")));
  }

  function dl(name, text, mime) {
    const blob = new Blob([text], { type: mime || "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }

  let qaTimer = null;
  function switchTab(name) {
    for (const t of ["assistant", "qa", "report"]) {
      G("tab-" + t).classList.toggle("is-on", t === name);
      G("tab-btn-" + t).classList.toggle("is-on", t === name);
      G("tab-btn-" + t).setAttribute("aria-selected", String(t === name));
    }
    if (name === "qa") {
      updateSourceUI();
      renderTimeline(true);
      clearInterval(qaTimer);
      // "Importado" es una foto estatica del archivo cargado (no cambia sola);
      // "live" y "replay" SI cambian con el tiempo (grabacion en curso / pasos
      // de repeticion llegando) y deben refrescarse solos. Antes este timer
      // excluia todo lo que no fuera "live", asi que la vista de Repeticion
      // nunca se auto-actualizaba: solo se veia al salir de Auditoria y volver
      // (lo que fuerza un renderTimeline(true) manual via switchTab).
      qaTimer = setInterval(() => { if (document.visibilityState === "visible" && source !== "imported") renderTimeline(); }, 2500);
    } else if (name === "report") {
      clearInterval(qaTimer);
      renderReportTab();
    } else {
      clearInterval(qaTimer);
    }
  }
  G("tab-btn-assistant").addEventListener("click", () => switchTab("assistant"));
  G("tab-btn-qa").addEventListener("click", () => switchTab("qa"));
  G("tab-btn-report").addEventListener("click", () => switchTab("report"));
  G("tl-refresh").addEventListener("click", () => renderTimeline(true));
  G("tl-filter").addEventListener("input", () => renderTimeline(true));

  // Fuente del reporte: temporal (grabacion) vs importado vs repeticion.
  G("src-live").addEventListener("click", () => { source = "live"; activeType = null; updateSourceUI(); renderTimeline(true); if (kpisOpen) renderKpis(); });
  G("src-imported").addEventListener("click", () => { if (!importedReport) return; source = "imported"; activeType = null; updateSourceUI(); renderTimeline(true); if (kpisOpen) renderKpis(); });
  G("src-replay").addEventListener("click", () => { source = "replay"; activeType = null; updateSourceUI(); renderReplayTrace(); if (kpisOpen) renderKpis(); });

  // Importa un artefacto de auditoria: SOLO existe este flujo (pestana Reporte).
  // NO reemplaza la configuracion persistente: el bundle es solo metadata para
  // entender el contexto de quien lo exporto. Compartido por Auditoria (para
  // que "Importado"/"Repeticion" reflejen lo mismo que se importa aqui).
  async function importReportFile(file) {
    const data = JSON.parse(await file.text());
    // Acepta el bundle canonico (schema) o un reporte suelto (compatibilidad).
    // NUNCA acepta cypress/playwright: el input solo toma .json y el SW valida
    // que tenga la forma de nuestro reporte (report.timeline), no un script.
    const bundle = data && data.schema && String(data.schema).startsWith("charlyaudit/") ? data : null;
    const report = bundle ? bundle.report : data;
    if (!report || !Array.isArray(report.timeline)) throw new Error("formato");
    const res = await qaControl("loadReplay", { report });
    if (!res || !res.ok) throw new Error(res && res.error ? res.error : "invalido");
    importedBundle = bundle;
    importedReport = report;
    source = "imported";
    updateSourceUI();
    renderTimeline(true);
    await refreshReplayState(); // habilita el boton Reproducir de la barra de acciones
    renderReportTab();
    return bundle;
  }

  // Vaciar: SIEMPRE actua sobre la fuente activa, nunca sobre la otra (2.2.1/2.2.2).
  //  - "live"               -> vacia SOLO la grabacion temporal.
  //  - "imported"/"replay"  -> vacia el reporte importado Y su repeticion,
  //                            como si el archivo nunca se hubiera cargado.
  async function clearImportedSource() {
    await qaControl("clearImported");
    importedReport = null;
    importedBundle = null;
    if (source !== "live") source = "live";
    lastCount = -1;
    updateSourceUI();
    renderTimeline(true);
    await refreshReplayState();
    renderReportTab();
  }
  G("tl-clear").addEventListener("click", async () => {
    if (source === "live") {
      await qaControl("clear");
      lastCount = -1;
      renderTimeline(true);
      toast("Grabacion temporal vaciada.");
    } else {
      await clearImportedSource();
      toast("Reporte importado vaciado.");
    }
  });

  // ═══════════════════════════════════════════════════════════════════════
  // Pestana REPORTE: unico lugar del panel donde se puede importar, descargar
  // o enviar el reporte (temporal completo, o ver el importado). (2.1)
  // ═══════════════════════════════════════════════════════════════════════
  async function renderReportTab() {
    // Resumen de la sesion temporal (vive siempre; usa los KPIs ya calculados).
    const liveEl = G("report-live-summary");
    try {
      const res = await qaControl("getKpis");
      const k = res && res.kpis;
      liveEl.textContent = k && k.eventos
        ? `${k.eventos} eventos · ${k.errores} errores · fidelidad no aplica aqui`
        : "Aun sin eventos. Graba una sesion en la pestana Auditoria.";
    } catch {
      liveEl.textContent = "Sin datos disponibles.";
    }
    // Resumen del reporte importado.
    const impEl = G("report-imported-summary");
    const clearBtn = G("rep-clear-imported");
    if (importedReport) {
      const n = (importedReport.timeline || []).length;
      const url = (importedReport.metadata && importedReport.metadata.url) || "?";
      const ext = importedBundle && importedBundle.extension;
      impEl.textContent = `${n} eventos · ${url}${ext ? ` · ${ext.name || "CharlyAudit"} v${ext.version || "?"}` : ""}`;
      clearBtn.disabled = false;
    } else {
      impEl.textContent = "Sin reporte importado.";
      clearBtn.disabled = true;
    }
  }

  // Descargas de la sesion TEMPORAL (unico lugar permitido, 2.1.5/2.1.6).
  G("rep-dl-json").addEventListener("click", async () => {
    const res = await qaControl("exportBundle");
    if (res && res.bundle) {
      const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      dl(`charlyaudit-${ts}.json`, JSON.stringify(res.bundle, null, 2), "application/json");
    } else {
      toast("No se pudo exportar.");
    }
  });
  G("rep-dl-cy").addEventListener("click", async () => {
    const res = await qaControl("exportCypress");
    if (res && res.script) dl("charlyaudit-session.cy.js", res.script, "text/javascript");
  });
  G("rep-dl-pw").addEventListener("click", async () => {
    const res = await qaControl("exportPlaywright");
    if (res && res.script) dl("charlyaudit-session.spec.js", res.script, "text/javascript");
  });

  // Importar: SOLO existe aqui (2.1.3). Solo acepta nuestro JSON completo,
  // nunca cypress/playwright (2.1.4) — el input restringe a .json y el SW
  // valida la forma exacta del reporte antes de aceptarlo.
  G("rep-import").addEventListener("click", () => G("rep-import-file").click());
  G("rep-import-file").addEventListener("change", async (ev) => {
    const file = ev.target.files[0];
    if (!file) return;
    try {
      const bundle = await importReportFile(file);
      G("report-msg").textContent = bundle
        ? `Auditoria importada (${bundle.extension?.name || "CharlyAudit"} v${bundle.extension?.version || "?"}).`
        : "Reporte importado.";
      toast("Reporte importado.");
    } catch (e) {
      G("report-msg").textContent = "Archivo invalido: " + (e && e.message ? e.message : "formato desconocido");
      toast("Archivo invalido.");
    } finally {
      ev.target.value = "";
    }
  });
  G("rep-clear-imported").addEventListener("click", async () => {
    await clearImportedSource();
    G("report-msg").textContent = "";
    toast("Reporte importado vaciado.");
  });

  // Al terminar de hidratar desde storage, refleja el resumen si el usuario
  // ya esta viendo la pestana Reporte (o la abre despues).
  hydrateImported().then(() => {
    updateSourceUI();
    if (G("tab-report").classList.contains("is-on")) renderReportTab();
  });
})();

// ===========================================================================
// Personalizacion persistente de la paleta base (boton lapiz junto al engrane)
// ===========================================================================
(function setupPalette() {
  const G = (id) => document.getElementById(id);
  const KEY = "charlyaudit:palette";
  // Los componentes leen los tokens canonicos --c-* directamente (v2.5.1+).
  // Los alias legacy (--brand, --ink...) son solo `var(--c-*)` de un solo sentido:
  // sobreescribir el alias NO cambia el token que los estilos realmente usan.
  // Por eso la paleta debe apuntar a los tokens canonicos, no a los alias.
  const VARS = ["--c-brand", "--c-bg", "--c-surface", "--c-border", "--c-text"];
  const inputs = () => Array.from(document.querySelectorAll("#palette input[type=color]"));

  function rgbToHex(v) {
    v = (v || "").trim();
    if (v.startsWith("#")) return v.length === 4 ? "#" + [...v.slice(1)].map((c) => c + c).join("") : v;
    const m = v.match(/\d+/g);
    if (!m) return "#000000";
    return "#" + m.slice(0, 3).map((n) => (+n).toString(16).padStart(2, "0")).join("");
  }
  function apply(pal) {
    for (const k of VARS) if (pal[k]) document.documentElement.style.setProperty(k, pal[k]);
  }
  function load() {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; }
  }
  // Aplica la paleta guardada al iniciar.
  apply(load());

  function syncInputs() {
    const cs = getComputedStyle(document.documentElement);
    for (const inp of inputs()) inp.value = rgbToHex(cs.getPropertyValue(inp.dataset.var));
  }
  G("open-palette").addEventListener("click", () => { syncInputs(); G("palette").showModal(); });
  G("close-palette").addEventListener("click", () => G("palette").close());
  // Vista previa en vivo al mover un selector.
  for (const inp of inputs()) inp.addEventListener("input", () => document.documentElement.style.setProperty(inp.dataset.var, inp.value));
  G("pal-save").addEventListener("click", () => {
    const pal = {};
    for (const inp of inputs()) pal[inp.dataset.var] = inp.value;
    localStorage.setItem(KEY, JSON.stringify(pal));
    apply(pal);
    G("palette").close();
  });
  G("pal-reset").addEventListener("click", () => {
    localStorage.removeItem(KEY);
    for (const k of VARS) document.documentElement.style.removeProperty(k);
    syncInputs();
  });
})();
