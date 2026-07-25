/**
 * sidepanel.js — Controlador del Asistente IA (CharlyPlugin)
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
import { OpenWebUIClient } from "./lib/openwebui-client.js";
import { renderMarkdown, Markdown } from "./lib/markdown.js";
import { ContextBridge, SCOPES } from "./lib/context-bridge.js";
import { ChatCache } from "./lib/chat-cache.js";

const AI_CONFIG_KEY = "charlyplugin:ai:config";
const DEFAULT_AI = {
  baseUrl: "https://assistant.service24gps.com",
  model: "charly-pt",
  apiKey: "sk-3e17ab34903e4e1fbe68c683fabbb67c",
  proxyUrl: "",
};
const MIN_INTERVAL = 1500; // ms minimo entre envios (anti-saturacion)
const MAX_HISTORY_TURNS = 6; // pares usuario/asistente reenviados (un solo turno)
const CONTEXT_BUDGET = 12000; // chars de contexto (~3k tokens) por peticion
const HISTORY_BUDGET = 2500; // chars de historial reenviado por peticion

const $ = (id) => document.getElementById(id);
const bridge = new ContextBridge();

const state = {
  client: null,
  config: { ...DEFAULT_AI },
  history: [], // { role, content, scopes? }
  scopes: new Set(),
  busy: false,
  seq: 0,
  abort: null,
  lastSend: 0,
  counts: {},
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
  txt.textContent = ok ? "Conectado · " + state.config.model : "Sin conexion";
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
    case "interactions": return (c.click || 0) + (c.input || 0);
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
  state.counts = st.counts || {};
  state.eventCount = st.count || 0;
  state.config = state.config || {};
  state.captureConfig = st.config || {};
  const rec = $("rec-state");
  rec.textContent = st.isRecording ? `● grabando · ${st.count} ev` : `${st.count} eventos`;
  rec.className = "context__rec" + (st.isRecording ? " live" : "");
  // Refleja el estado en el boton de accion del panel.
  const recBtn = $("act-record");
  if (recBtn) {
    recBtn.textContent = st.isRecording ? "■ Detener" : "● Grabar";
    recBtn.classList.toggle("on", !!st.isRecording);
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

async function refreshReplayState() {
  const res = await qaControl("getReplay");
  const info = $("act-replay-info");
  const play = $("act-play");
  if (res && res.ok && res.report && Array.isArray(res.report.timeline)) {
    if (play) play.disabled = false;
    if (info) {
      const active = res.job && res.job.active;
      info.textContent = active
        ? `reproduciendo ${res.job.index}/${res.report.timeline.length}`
        : `${res.report.timeline.length} eventos listos`;
    }
  } else {
    if (play) play.disabled = true;
    if (info) info.textContent = "Sin replay cargado";
  }
}

function wireActions() {
  // Grabar / Detener.
  $("act-record").addEventListener("click", async () => {
    await qaControl("toggle");
    refreshState();
  });
  // La importacion es unica y vive en la pestana Auditoria (tl-import).
  // Reproducir / Detener replay contra la pestana activa.
  $("act-play").addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || tab.id == null) return toast("Sin pestana activa.");
    const res = await qaControl("startReplay", { tabId: tab.id, options: { speed: 1 } });
    toast(res && res.ok ? "Reproduciendo en la pestana…" : "No se pudo iniciar el replay.");
  });
  $("act-stop").addEventListener("click", async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    await qaControl("stopReplay", { tabId: tab && tab.id });
    toast("Replay detenido.");
  });
  // Configuracion de captura (desplegable).
  $("act-cfg").addEventListener("click", async () => {
    const panel = $("capture-cfg");
    const open = panel.hasAttribute("hidden");
    if (open) {
      const c = state.captureConfig || {};
      $("cfg-mask").value = arrToLines(c.maskSelectors);
      $("cfg-globals").value = arrToLines(c.watchedGlobals);
      $("cfg-fns").value = arrToLines(c.patchedFunctions);
      // Carga los ajustes persistentes (dominios, perfil, webhook, auto-inicio).
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
    $("act-cfg").setAttribute("aria-expanded", String(open));
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
    // Si hay webhook, pide permiso de host para poder enviarlo (gesto de usuario).
    if (settings.webhook.enabled && settings.webhook.url) {
      try {
        await chrome.permissions.request({ origins: ["<all_urls>"] });
      } catch {
        /* el usuario decide */
      }
    }
    const res = await qaControl("setSettings", { settings });
    $("cfg-settings-msg").textContent = res && res.ok ? "Ajustes guardados." : "Error al guardar.";
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
  const key = [...scopes].sort().join("|") + ":" + (state.eventCount || 0);
  if (state.ctxCache.key === key && state.ctxCache.build) return state.ctxCache.build;
  const build = await bridge.buildContext(scopes, CONTEXT_BUDGET);
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
    const sys = build ? bridge.systemPrompt(build) : bridge.systemPrompt({ snapshot: null, digest: "", meta: {} });

    // Modelo de un solo turno: instrucciones + historial reciente (acotado) + consulta.
    const recent = state.history.slice(-MAX_HISTORY_TURNS * 2);
    let transcript = recent.map((m) => (m.role === "user" ? "Usuario" : "Charly") + ": " + m.content).join("\n");
    if (transcript.length > HISTORY_BUDGET) transcript = "\u2026" + transcript.slice(-HISTORY_BUDGET); // prioriza lo reciente
    const content =
      sys +
      (transcript ? "\n\n[Historial reciente]:\n" + transcript : "") +
      "\n\n[Consulta del usuario] (responde solo a esto, sin reproducir el contexto):\n" +
      text;

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
      answer = await state.client.chatStream([{ role: "user", content }], state.abort.signal, (_d, full) => {
        if (mySeq !== state.seq) return;
        acc = full;
        paint(full, false);
      });
    } catch (streamErr) {
      // Fallback a no-stream si el streaming no esta disponible.
      if (state.abort && state.abort.signal.aborted) throw streamErr;
      answer = await state.client.chat([{ role: "user", content }], state.abort.signal);
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
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), 2000);
}

// --- Ajustes ----------------------------------------------------------------
function openSettings() {
  $("cfg-base").value = state.config.baseUrl;
  $("cfg-model").value = state.config.model;
  $("cfg-key").value = state.config.apiKey;
  $("cfg-proxy").value = state.config.proxyUrl;
  $("probe").textContent = "";
  $("probe").className = "probe";
  $("settings").showModal();
}

// --- Cableado de eventos ----------------------------------------------------
function wire() {
  $("send").addEventListener("click", () => (state.busy ? cancel() : send()));
  const input = $("input");
  input.addEventListener("input", () => {
    updateCounter();
    autoGrow();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!state.busy) send();
    }
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
  $("cfg-save").addEventListener("click", async () => {
    await saveConfig({
      baseUrl: $("cfg-base").value.trim() || DEFAULT_AI.baseUrl,
      model: $("cfg-model").value.trim() || DEFAULT_AI.model,
      apiKey: $("cfg-key").value.trim(),
      proxyUrl: $("cfg-proxy").value.trim(),
    });
    $("settings").close();
    toast("Conexion actualizada.");
    refreshConnection();
  });
  $("cfg-test").addEventListener("click", async () => {
    const probe = $("probe");
    probe.textContent = "Probando…";
    probe.className = "probe";
    const tmp = new OpenWebUIClient({
      baseUrl: $("cfg-base").value.trim(),
      model: $("cfg-model").value.trim(),
      apiKey: $("cfg-key").value.trim(),
      proxyUrl: $("cfg-proxy").value.trim(),
    });
    const ok = await tmp.available();
    probe.textContent = ok ? "Conexion correcta." : "No se pudo conectar (revisa URL, key o permisos del host).";
    probe.className = "probe " + (ok ? "ok" : "down");
  });

  // Al cerrar el panel: respeta la preferencia de cache.
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
  wireQA();
  refreshReplayState();
  refreshConnection();
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
    });
  } catch {
    /* sin storage */
  }
  // Refresco ligero de conteos/grabacion/replay mientras el panel este visible.
  setInterval(() => {
    if (document.visibilityState === "visible") {
      refreshState();
      refreshReplayState();
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
    return `<dl class="tl-kv">${rows.join("")}</dl>`;
  }

  let lastReport = null;
  let lastCount = -1;
  let source = "live"; // "live" (temporal) | "imported"
  let importedReport = null;
  let activeType = null; // filtro por tipo al hacer clic en un chip

  async function getActiveReport() {
    return source === "imported" ? importedReport : await bridge.getReport();
  }
  function updateSourceUI() {
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
        return `<div class="tl-row"><div class="tl-row__head">
          <span class="tl-badge" style="background:${m.c}">${esc(m.l)}</span>
          <span class="tl-desc">${esc(tlDesc(e))}</span>
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
    for (const t of ["assistant", "qa"]) {
      G("tab-" + t).classList.toggle("is-on", t === name);
      G("tab-btn-" + t).classList.toggle("is-on", t === name);
    }
    if (name === "qa") {
      updateSourceUI();
      renderTimeline(true);
      clearInterval(qaTimer);
      qaTimer = setInterval(() => { if (document.visibilityState === "visible" && source === "live") renderTimeline(); }, 2500);
    } else {
      clearInterval(qaTimer);
    }
  }
  G("tab-btn-assistant").addEventListener("click", () => switchTab("assistant"));
  G("tab-btn-qa").addEventListener("click", () => switchTab("qa"));
  G("tl-refresh").addEventListener("click", () => renderTimeline(true));
  G("tl-filter").addEventListener("input", () => renderTimeline(true));

  // Fuente del reporte: temporal (grabacion) vs importado.
  G("src-live").addEventListener("click", () => { source = "live"; activeType = null; updateSourceUI(); renderTimeline(true); });
  G("src-imported").addEventListener("click", () => { if (!importedReport) return; source = "imported"; activeType = null; updateSourceUI(); renderTimeline(true); });
  G("src-replay").addEventListener("click", () => { source = "replay"; activeType = null; updateSourceUI(); renderReplayTrace(); });

  // Importar un artefacto de auditoria (bundle completo) o un reporte suelto.
  // NO reemplaza la configuracion persistente: el bundle es solo metadata para
  // entender el contexto de quien lo exporto.
  let importedBundle = null;
  G("tl-import").addEventListener("click", () => G("tl-import-file").click());
  G("tl-import-file").addEventListener("change", async (ev) => {
    const file = ev.target.files[0];
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      // Acepta el bundle canonico (schema) o un reporte suelto (compatibilidad).
      const bundle = data && data.schema && String(data.schema).startsWith("charlyaudit/") ? data : null;
      const report = bundle ? bundle.report : data;
      if (!report || !Array.isArray(report.timeline)) throw new Error("formato");
      // Validacion estricta en el SW: si el reporte esta mal formado, no se carga.
      const res = await qaControl("loadReplay", { report });
      if (!res || !res.ok) {
        toast("Reporte rechazado: " + ((res && res.error) || "invalido"));
        return;
      }
      importedBundle = bundle; // metadata del exportador (extension/settings), NO se aplica
      importedReport = report;
      source = "imported";
      updateSourceUI();
      renderTimeline(true);
      await refreshReplayState(); // habilita el boton Reproducir de la barra de acciones
      toast(bundle ? `Auditoria importada (${bundle.extension?.name || "?"} v${bundle.extension?.version || "?"}).` : "Reporte importado.");
    } catch {
      toast("Artefacto de auditoria invalido.");
    } finally {
      ev.target.value = "";
    }
  });

  // Vaciar el reporte temporal (equivalente al control del popup).
  G("tl-clear").addEventListener("click", async () => {
    await qaControl("clear");
    lastCount = -1;
    if (source === "live") renderTimeline(true);
    toast("Reporte temporal vaciado.");
  });

  // Export UNICO y completo: el mismo artefacto que se envia por webhook.
  G("exp-bundle").addEventListener("click", async () => {
    const res = await qaControl("exportBundle");
    if (res && res.bundle) {
      const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      dl(`charlyaudit-${ts}.json`, JSON.stringify(res.bundle, null, 2), "application/json");
    } else {
      toast("No se pudo exportar.");
    }
  });
  // Generadores de prueba (artefacto distinto: codigo de test ejecutable).
  G("exp-cy").addEventListener("click", async () => {
    const res = await qaControl("exportCypress");
    if (res && res.script) dl("charlyaudit-session.cy.js", res.script, "text/javascript");
  });
  G("exp-pw").addEventListener("click", async () => {
    const res = await qaControl("exportPlaywright");
    if (res && res.script) dl("charlyaudit-session.spec.js", res.script, "text/javascript");
  });
})();

// ===========================================================================
// Personalizacion persistente de la paleta base (boton lapiz junto al engrane)
// ===========================================================================
(function setupPalette() {
  const G = (id) => document.getElementById(id);
  const KEY = "charlyaudit:palette";
  const VARS = ["--brand", "--ink", "--panel", "--line", "--text"];
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
