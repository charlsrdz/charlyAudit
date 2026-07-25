/**
 * background.js — Service Worker (cerebro de la suite de QA)
 * =========================================================
 * - Mantiene el estado de grabacion y la configuracion.
 * - Recibe el timeline de los content scripts y lo persiste en chrome.storage
 *   (el SW es efimero en MV3: el estado NO puede vivir en variables de modulo).
 * - Difunde cambios de estado/config a todas las pestanas.
 * - Motor de Reportes (Herramienta 8): ensambla el JSON y genera scripts
 *   Cypress / Playwright mediante los modulos importados.
 *
 * Se importa desde el service worker principal (src/background/service-worker.js).
 *
 * Canales:
 *   { channel:"qa", entry, resumed }      eventos entrantes desde content.js
 *   { channel:"qa-control", action }       ordenes desde la UI (popup)
 *   { channel:"qa-config", recording, config }  difusion del SW hacia las pestanas
 */
import { assembleReport } from "./report-engine.js";
import { toCypress, toPlaywright } from "./exporters.js";
import { SCHEMA_CURRENT, validateBundle, stampIntegrity, redactBundle, chunkBundle, computeKpis } from "./bundle-schema.js";

const K = {
  recording: "qa:isRecording",
  config: "qa:config",
  timeline: "qa:timeline",
  meta: "qa:meta",
  replay: "qa:replay", // reporte importado (persiste al cerrar el popup)
  replayJob: "qa:replayJob", // { active, index, options, tabId } para reanudar
  settings: "qa:settings", // dominios permitidos, perfil, webhook, auto-inicio
  webhookPending: "qa:webhookPending", // estado de reintentos pendientes del webhook
};

const MAX_EVENTS = 5000;

const DEFAULT_CONFIG = {
  watchedGlobals: [], // Herramienta 4
  patchedFunctions: [], // Herramienta 7
  sensitiveParams: ["token", "access_token", "auth", "key", "apikey", "password", "secret"],
  maskSelectors: [".private", "[data-private]"],
  throttleScrollMs: 250,
  debounceResizeMs: 300,
};

// Serializa escrituras del timeline e ids vistos (dedupe de reenvios).
let writeChain = Promise.resolve();
let seenIds = null; // Set perezoso, reconstruido tras reinicios del SW

// --- Helpers de estado ------------------------------------------------------

async function isRecording() {
  return Boolean((await chrome.storage.local.get(K.recording))[K.recording]);
}

async function getConfig() {
  const stored = (await chrome.storage.local.get(K.config))[K.config];
  return { ...DEFAULT_CONFIG, ...(stored || {}) };
}

// --- Ajustes persistentes: dominios, perfil, webhook, auto-inicio ------------
const DEFAULT_SETTINGS = {
  allowedDomains: [], // [] = todos los dominios; si tiene items, solo esos
  profile: { name: "", email: "" }, // identidad del usuario (distinta del asistente)
  webhook: { url: "", token: "", enabled: false, mode: "manual" }, // manual|chunks|onclose
  autoStart: false, // iniciar grabacion automaticamente en dominios permitidos
};
async function getSettings() {
  const stored = (await chrome.storage.local.get(K.settings))[K.settings];
  const s = { ...DEFAULT_SETTINGS, ...(stored || {}) };
  s.profile = { ...DEFAULT_SETTINGS.profile, ...(s.profile || {}) };
  s.webhook = { ...DEFAULT_SETTINGS.webhook, ...(s.webhook || {}) };
  s.allowedDomains = Array.isArray(s.allowedDomains) ? s.allowedDomains : [];
  return s;
}
/** Un dominio esta permitido si la lista esta vacia o si coincide (o es subdominio). */
function domainAllowed(host, list) {
  if (!Array.isArray(list) || !list.length) return true;
  host = String(host || "").toLowerCase();
  return list.some((d) => {
    d = String(d || "").toLowerCase().trim();
    return d && (host === d || host.endsWith("." + d));
  });
}

// --- Telemetria por webhook (solo mientras hay grabacion en curso) -----------
const TELE_CHUNK = 25; // eventos por lote en modo "chunks"
let teleBuffer = []; // se descarta al detener/limpiar (no se envia data detenida)
let teleProfileId = null;

function newProfileId() {
  return "sess-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
}
/** Firma HMAC-SHA256 en hexadecimal (WebCrypto; disponible en el SW). */
async function hmacHex(message, secret) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(String(secret)), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(String(message)));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

let webhookAttempts = 0;
const WEBHOOK_MAX_RETRY = 5;

async function sendTelemetry(reason) {
  const isRetry = reason === "retry";
  // En un reintento re-enviamos el snapshot completo aunque el buffer este vacio.
  if (!isRetry && !teleBuffer.length) return;
  const s = await getSettings();
  if (!s.webhook.enabled || !s.webhook.url) {
    teleBuffer = [];
    return;
  }
  teleBuffer = []; // el bundle ya contiene todo el reporte (snapshot)
  // Seguridad: no enviar datos de sesion en claro (exige HTTPS, salvo localhost).
  const isHttps = /^https:\/\//i.test(s.webhook.url);
  const isLocal = /^https?:\/\/(localhost|127\.0\.0\.1)/i.test(s.webhook.url);
  if (!isHttps && !isLocal) {
    console.warn("[CharlyAudit] Webhook rechazado: usa HTTPS.");
    return;
  }
  const headers = { "Content-Type": "application/json" };
  if (s.webhook.token) headers["Authorization"] = `Bearer ${s.webhook.token}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15000); // no acumular peticiones colgadas
  let okSend = false;
  try {
    const bundle = await buildBundle(reason); // mismo contenido que el export
    const body = JSON.stringify(bundle);
    // Integridad/autenticidad: firma HMAC-SHA256 del cuerpo con el token del
    // webhook como secreto compartido, y el contentHash para idempotencia. El
    // servidor puede verificar que la evidencia es autentica y no fue manipulada.
    if (s.webhook.token) {
      try {
        headers["X-CharlyAudit-Signature"] = "sha256=" + (await hmacHex(body, s.webhook.token));
      } catch {
        /* crypto no disponible */
      }
    }
    if (bundle.integrity && bundle.integrity.contentHash) headers["X-CharlyAudit-Content-Hash"] = bundle.integrity.contentHash;
    const res = await fetch(s.webhook.url, { method: "POST", headers, signal: ctrl.signal, body });
    okSend = !!(res && res.ok);
    if (!okSend) throw new Error("HTTP " + (res && res.status));
  } catch (e) {
    console.warn("[CharlyAudit] Telemetria fallida:", e && e.message);
  } finally {
    clearTimeout(timer);
  }

  // Cola de reenvio: en fallo, reintenta con backoff exponencial (no se pierde la
  // evidencia en silencio); en exito, limpia el estado pendiente.
  try {
    if (okSend) {
      webhookAttempts = 0;
      chrome.alarms.clear("qa-webhook-retry");
      await chrome.storage.local.set({ [K.webhookPending]: null });
    } else if (webhookAttempts < WEBHOOK_MAX_RETRY) {
      webhookAttempts++;
      const mins = Math.min(30, Math.pow(2, webhookAttempts)); // 2,4,8,16,30 min
      chrome.alarms.create("qa-webhook-retry", { delayInMinutes: mins });
      await chrome.storage.local.set({ [K.webhookPending]: { intentos: webhookAttempts, proximoMin: mins, at: Date.now() } });
    } else {
      await chrome.storage.local.set({ [K.webhookPending]: { intentos: webhookAttempts, agotado: true, at: Date.now() } });
    }
  } catch {
    /* alarms/storage no criticos */
  }
}
/** Encola un evento para telemetria SOLO si hay grabacion activa y webhook on. */
async function telemetryOnEvent(entry) {
  const s = await getSettings();
  if (!s.webhook.enabled || !s.webhook.url || s.webhook.mode === "manual") return;
  if (!(await isRecording())) return; // limitado a grabaciones en curso
  teleBuffer.push(entry);
  if (s.webhook.mode === "chunks" && teleBuffer.length >= TELE_CHUNK) sendTelemetry("chunk");
}

// Al cerrar una pestana: si el modo es "onclose", vuelca lo acumulado.
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const rec = (await getMeta()).recording || {};
  // Si se cierra la pestana que se estaba grabando: detener y (si aplica) enviar.
  if (rec.tabId === tabId && (await isRecording())) {
    await stopRecordingBound("tab-close");
    return;
  }
  const s = await getSettings();
  if (s.webhook.enabled && s.webhook.mode === "onclose") sendTelemetry("tab-close");
});

async function getTimeline() {
  return (await chrome.storage.local.get(K.timeline))[K.timeline] || [];
}

async function getMeta() {
  return (await chrome.storage.local.get(K.meta))[K.meta] || {};
}

// === Ciclo de grabacion ligado a la pestana (tarea 1) =======================
/** Recaba metadata de entorno: sistema, navegador, ventana, cookies (flags). */
async function collectEnvironment(tab) {
  const env = { capturedAt: new Date().toISOString(), fechaHora: new Date().toString() };
  try {
    const cpu = await chrome.system.cpu.getInfo().catch(() => null);
    const mem = await chrome.system.memory.getInfo().catch(() => null);
    env.sistema = {
      cpu: cpu ? cpu.modelName : undefined,
      nucleos: cpu ? cpu.numOfProcessors : undefined,
      ramGB: mem ? +(mem.capacity / 1073741824).toFixed(1) : undefined,
    };
  } catch {
    /* sin system.* */
  }
  try {
    let hi = {};
    if (self.navigator.userAgentData && self.navigator.userAgentData.getHighEntropyValues)
      hi = await self.navigator.userAgentData.getHighEntropyValues(["platform", "platformVersion", "architecture", "uaFullVersion"]).catch(() => ({}));
    env.navegador = { ua: self.navigator.userAgent, plataforma: self.navigator.platform, idioma: self.navigator.language, ...hi };
  } catch {
    env.navegador = { ua: self.navigator.userAgent };
  }
  if (tab) {
    env.tabId = tab.id;
    env.windowId = tab.windowId;
    env.startUrl = tab.url;
    env.titulo = tab.title;
    try {
      const w = await chrome.windows.get(tab.windowId);
      env.ventana = { w: w.width, h: w.height, estado: w.state, incognito: w.incognito };
    } catch {
      /* sin windows */
    }
    try {
      // Solo metadata de cookies (nombre + flags), NUNCA el valor (seguridad).
      const cks = await chrome.cookies.getAll({ url: tab.url });
      env.cookies = cks.map((c) => ({ name: c.name, secure: c.secure, httpOnly: c.httpOnly, sameSite: c.sameSite, domain: c.domain, session: c.session }));
    } catch {
      /* sin cookies perm/host */
    }
  }
  return env;
}
/** Garantiza que el codigo de tracking este inyectado y responda ANTES de grabar.
 *  1) hace ping; 2) si no responde, inyecta programaticamente (host permission). */
async function ensureInjected(tab) {
  if (!tab || tab.id == null) return false;
  try {
    const res = await chrome.tabs.sendMessage(tab.id, { channel: "qa-control", action: "ping" });
    if (res && res.ready) return true;
  } catch {
    /* aun no presente */
  }
  try {
    // ORDEN IMPORTANTE: primero content.js (ISOLATED) para que registre su
    // listener, y luego injected.js (MAIN) que emite "injected-ready". Asi el
    // content captura el handshake y libera la config pendiente al mundo MAIN.
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "ISOLATED", files: ["src/qa/content.js"] });
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "MAIN", files: ["src/qa/injected.js"] });
    const res = await chrome.tabs.sendMessage(tab.id, { channel: "qa-control", action: "ping" }).catch(() => null);
    return !!(res && res.ready);
  } catch (e) {
    console.warn("[CharlyAudit] Inyeccion programatica no disponible (host permission):", e.message);
    return false;
  }
}

/** Re-muestreo dirigido por evento: en cada ruta/navegacion (debounce 2s). */
let _resampleAt = 0;
function maybeResampleOnNav(entry) {
  if (!recState.recording || !entry) return;
  if (entry.type !== "route" && entry.type !== "navigation") return;
  const now = Date.now();
  if (now - _resampleAt < 2000) return;
  _resampleAt = now;
  resampleEnvironment();
}

/** Re-muestreo periodico de metadata durante la grabacion (1.4). */
async function resampleEnvironment() {
  if (!(await isRecording())) return;
  const meta = await getMeta();
  const rec = meta.recording || {};
  if (rec.tabId == null) return;
  try {
    const tab = await chrome.tabs.get(rec.tabId);
    const env = meta.entorno || {};
    env.urlActual = tab.url;
    try {
      const cks = await chrome.cookies.getAll({ url: tab.url });
      env.cookies = cks.map((c) => ({ name: c.name, secure: c.secure, httpOnly: c.httpOnly, sameSite: c.sameSite, domain: c.domain, session: c.session }));
    } catch {
      /* sin cookies */
    }
    env.ultimoMuestreo = new Date().toISOString();
    await chrome.storage.local.set({ [K.meta]: { ...meta, entorno: env } });
    reportCache.dirty = true;
  } catch {
    /* pestana cerrada: onRemoved detendra la grabacion */
  }
}
chrome.alarms.onAlarm.addListener((a) => {
  if (!a) return;
  if (a.name === "qa-resample") resampleEnvironment();
  if (a.name === "qa-webhook-retry") sendTelemetry("retry");
});

// Nota: las actualizaciones las gestiona la Chrome Web Store de forma nativa.
// No se contemplan mecanismos externos (update_url / version.xml / crx propio).

// Estado en memoria de la grabacion (para listeners sincronos como webRequest).
// En MV3 el service worker se suspende/reinicia; este cache se pierde. Por eso se
// REHIDRATA desde el estado persistido al arrancar, para que webRequest/onUpdated
// sigan gateando bien una grabacion en curso tras un reinicio del SW.
let recState = { recording: false, tabId: null };
let recStateReady = null;
async function hydrateRecState() {
  try {
    const [recording, meta] = await Promise.all([isRecording(), getMeta()]);
    const tabId = meta.recording && meta.recording.tabId != null ? meta.recording.tabId : null;
    recState = { recording: !!recording, tabId };
  } catch {
    /* storage no disponible aun */
  }
  return recState;
}
// Rehidrata en cuanto el SW (re)arranca; los listeners lo esperan si aun no esta listo.
recStateReady = hydrateRecState();

// Contexto canonico de la sesion: recordingId + epoch + contador de secuencia.
// Se usa para asignar a cada evento un id canonico determinista (recordingId#seq)
// y un tiempo normalizado monotonico (tRel) por sesion. Robusto ante reinicios
// del SW: se reconstruye desde meta + la maxima secuencia ya persistida.
let recCtx = null;
async function ensureRecCtx(timeline) {
  if (recCtx) return recCtx;
  const rec = (await getMeta()).recording || {};
  const tl = timeline || (await getTimeline());
  const maxSeq = tl.reduce((x, e) => (e.seq > x ? e.seq : x), 0);
  const maxT = tl.reduce((x, e) => (typeof e.tRel === "number" && e.tRel > x ? e.tRel : x), -1);
  recCtx = {
    recordingId: rec.recordingId || "rec",
    epoch: rec.startedAtMs || (tl[0] && tl[0].ts) || Date.now(),
    seq: maxSeq,
    lastT: maxT, // ultimo tiempo normalizado, para garantizar orden monotonico
  };
  return recCtx;
}
const auditedOrigins = new Set(); // dedup de auditoria de cabeceras por origen

// Auditoria de CABECERAS DE RESPUESTA durante la grabacion (tarea 1.4 + 3).
// Usa datos del servidor (no sensibles del usuario) para detectar CSP/HSTS/
// X-Content-Type-Options/X-Frame-Options ausentes (alineado a ISO 27001).
try {
  chrome.webRequest.onHeadersReceived.addListener(
    (d) => {
      if (!recState.recording || d.tabId !== recState.tabId) return;
      let origin;
      try {
        origin = new URL(d.url).origin;
      } catch {
        return;
      }
      if (auditedOrigins.has(origin)) return;
      auditedOrigins.add(origin);
      const H = {};
      for (const h of d.responseHeaders || []) H[(h.name || "").toLowerCase()] = h.value || "";
      const isHttps = origin.startsWith("https:");
      const csp = !!H["content-security-policy"];
      const hsts = !!H["strict-transport-security"];
      const xcto = !!H["x-content-type-options"];
      const xfo = !!H["x-frame-options"];
      const ts = Date.now();
      appendEntry({
        id: `hdr-${d.requestId}`,
        type: "response-headers",
        ts,
        data: { url: d.url, status: d.statusCode, seguridad: { csp, hsts, xcto, xfo }, servidor: H["server"] || undefined },
      });
      const findings = [];
      if (!csp) findings.push(["csp-header-ausente", "alta"]);
      if (isHttps && !hsts) findings.push(["hsts-ausente", "media"]);
      if (!xcto) findings.push(["x-content-type-options-ausente", "baja"]);
      if (!xfo && !/frame-ancestors/i.test(H["content-security-policy"] || "")) findings.push(["clickjacking-sin-x-frame-options", "media"]);
      for (const [kind, sev] of findings)
        appendEntry({ id: `sechdr-${origin}-${kind}`, type: "security", ts, data: { kind, severidad: sev, detalle: origin, donde: "cabeceras" } });

      // Set-Cookie del servidor: flags de seguridad (autoritativo vs document.cookie).
      for (const h of d.responseHeaders || []) {
        if ((h.name || "").toLowerCase() !== "set-cookie") continue;
        const cookie = String(h.value || "");
        const name = (cookie.split("=")[0] || "").trim();
        const secure = /;\s*secure/i.test(cookie);
        const httpOnly = /;\s*httponly/i.test(cookie);
        const sameSite = (cookie.match(/;\s*samesite=([^;]+)/i) || [])[1];
        if (!secure || !httpOnly) {
          appendEntry({
            id: `setcookie-${origin}-${name}`,
            type: "security",
            ts,
            data: { kind: "set-cookie-insegura", severidad: secure ? "media" : "alta", detalle: `${name} (secure:${secure} httpOnly:${httpOnly} sameSite:${sameSite || "?"})`, donde: "set-cookie" },
          });
        }
      }
    },
    { urls: ["<all_urls>"], types: ["main_frame", "sub_frame"] },
    ["responseHeaders", "extraHeaders"]
  );

  // Auditoria de CABECERAS DE SOLICITUD: detecta fugas de token/PII en los
  // requests (Authorization, Cookie, x-api-key...). Reporta la CLAVE, no el valor.
  const REQ_TOKEN = /^(authorization|x-api-key|x-auth-token|x-access-token|api-key|proxy-authorization)$/i;
  const seenReqLeak = new Set();
  chrome.webRequest.onBeforeSendHeaders.addListener(
    (d) => {
      if (!recState.recording || d.tabId !== recState.tabId) return;
      let origin;
      try {
        origin = new URL(d.url).origin;
      } catch {
        return;
      }
      for (const h of d.requestHeaders || []) {
        const name = (h.name || "").toLowerCase();
        const val = String(h.value || "");
        let kind = null;
        let sev = "media";
        if (REQ_TOKEN.test(name)) {
          kind = "token-en-request";
          sev = "alta";
        } else if (name === "cookie" && /(sess|token|auth|sid|jwt)=/i.test(val)) {
          kind = "cookie-sensible-en-request";
        } else if (/eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\./.test(val)) {
          kind = "jwt-en-request";
          sev = "alta";
        }
        if (kind) {
          const key = `${origin}|${kind}|${name}`;
          if (seenReqLeak.has(key)) continue;
          seenReqLeak.add(key);
          appendEntry({
            id: `secreq-${key}`,
            type: "security",
            ts: Date.now(),
            data: { kind, severidad: sev, detalle: `cabecera "${name}" hacia ${new URL(d.url).pathname}`, donde: "request" },
          });
        }
      }
    },
    { urls: ["<all_urls>"], types: ["main_frame", "sub_frame", "xmlhttprequest"] },
    ["requestHeaders", "extraHeaders"]
  );
} catch (e) {
  console.warn("[CharlyAudit] webRequest no disponible:", e && e.message);
}

// === Orquestacion de inyeccion 100% on-demand ==============================
// Sin content scripts declarativos: el SW inyecta el codigo SOLO cuando hace
// falta (grabar/reproducir). Este listener mantiene viva la grabacion tras una
// navegacion, reanuda el replay y realiza el auto-inicio en dominios permitidos.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete" || !tab || !tab.url) return;
  if (!/^https?:/i.test(tab.url)) return; // ignora chrome://, about:, extensiones
  await recStateReady; // asegura el estado tras un reinicio del SW

  // 1) La pestana que se esta grabando navego: re-inyectar para no perder captura.
  if (recState.recording && tabId === recState.tabId) {
    await ensureInjected(tab);
    return;
  }
  // 2) Hay un replay activo en esta pestana: re-inyectar para reanudarlo tras cargar.
  try {
    const job = (await chrome.storage.local.get(K.replayJob))[K.replayJob];
    if (job && job.active && tabId === job.tabId) {
      await ensureInjected(tab);
      return;
    }
  } catch {
    /* storage */
  }
  // 3) Auto-inicio: dominio permitido + autoStart + sin grabacion en curso.
  //    (Reemplaza el auto-start que antes hacia el content script en su bootstrap.)
  const s = await getSettings();
  if (s.autoStart && !recState.recording) {
    let host;
    try {
      host = new URL(tab.url).hostname;
    } catch {
      return;
    }
    if (domainAllowed(host, s.allowedDomains)) await startRecordingBound(tab);
  }
});

/** Inicia grabacion ligada a UNA pestana. Rechaza si ya hay otra en curso. */
async function startRecordingBound(tab) {
  const t = tab || (await chrome.tabs.query({ active: true, currentWindow: true }))[0];
  if (await isRecording()) {
    const cur = (await getMeta()).recording || {};
    if (cur.tabId != null && t && cur.tabId !== t.id)
      return { ok: false, error: "Ya hay una grabacion en curso en otra pestana. Solo se permite una a la vez." };
  }
  // Garantia de inyeccion ANTES de grabar (tarea 1.1): esperamos a que el codigo
  // de tracking este presente y responda antes de activar la grabacion.
  const inyectado = await ensureInjected(t);
  const recordingId = "rec-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 7);
  const env = await collectEnvironment(t);
  const meta = {
    ...(await getMeta()),
    url: env.startUrl || (t && t.url), // URL desde donde se comenzo la grabacion (1.6)
    recording: { recordingId, tabId: t && t.id, windowId: t && t.windowId, startUrl: env.startUrl, startedAt: env.capturedAt, startedAtMs: Date.now(), inyectado },
    entorno: env, // sistema, navegador, ventana, cookies (1.4/1.5)
  };
  await chrome.storage.local.set({ [K.meta]: meta });
  let hostLabel = "esta pestana";
  try {
    hostLabel = new URL(env.startUrl || (t && t.url)).hostname;
  } catch {
    /* url invalida */
  }
  await setRecording(true, t && t.id, hostLabel);
  reportCache.dirty = true;
  recState = { recording: true, tabId: t && t.id };
  recCtx = null; // se reconstruye con el nuevo recordingId/epoch
  auditedOrigins.clear();
  teleProfileId = newProfileId();
  teleBuffer = [];
  // Re-muestreo periodico de metadata mientras dure la grabacion (1.4).
  chrome.alarms.create("qa-resample", { periodInMinutes: 1 });
  return { ok: true, isRecording: true, recordingId, tabId: t && t.id, inyectado, profileId: teleProfileId };
}
/** Detiene la grabacion; si es por cierre de pestana, vuelca telemetria si aplica. */
async function stopRecordingBound(reason) {
  const wasRec = await isRecording();
  const prevTabId = recState.tabId;
  await setRecording(false, prevTabId);
  recState = { recording: false, tabId: null };
  recCtx = null;
  chrome.alarms.clear("qa-resample");
  if (wasRec && reason === "tab-close") {
    const s = await getSettings();
    if (s.webhook.enabled && s.webhook.mode === "onclose") await sendTelemetry("tab-close");
  }
  teleBuffer = [];
}

async function ensureSeenIds() {
  if (seenIds) return seenIds;
  seenIds = new Set((await getTimeline()).map((e) => e.id).filter(Boolean));
  return seenIds;
}

async function updateBadge(on, tabId, label) {
  try {
    const opts = tabId != null ? { tabId } : {};
    await chrome.action.setBadgeText({ text: on ? "REC" : "", ...opts });
    if (on) {
      await chrome.action.setBadgeBackgroundColor({ color: "#FF6B5E", ...opts });
      await chrome.action.setTitle({ title: `CharlyAudit · grabando ${label || "esta pestana"}`, ...opts });
    } else if (tabId != null) {
      await chrome.action.setTitle({ title: "CharlyAudit", tabId });
    }
  } catch {
    /* no critico */
  }
}

/** Difunde estado y config a todas las pestanas con content script. */
async function broadcast() {
  const [recording, config] = await Promise.all([isRecording(), getConfig()]);
  const msg = { channel: "qa-config", recording, config };
  const tabs = await chrome.tabs.query({});
  for (const t of tabs) {
    if (t.id) chrome.tabs.sendMessage(t.id, msg).catch(() => {});
  }
}

async function setRecording(value, tabId, label) {
  await chrome.storage.local.set({ [K.recording]: value });
  await updateBadge(value, tabId, label);
  await broadcast();
}

// --- Timeline ---------------------------------------------------------------

function appendEntry(entry) {
  writeChain = writeChain
    .then(async () => {
      const ids = await ensureSeenIds();
      if (entry.id && ids.has(entry.id)) return; // dedupe (reenvios tras recarga)
      const timeline = await getTimeline();
      // Id CANONICO determinista + reloj normalizado por sesion. Solo se asigna
      // a eventos nuevos (los reenvios ya traen su cid y se dedupean por id).
      if (entry.cid == null) {
        const ctx = await ensureRecCtx(timeline);
        entry.seq = ++ctx.seq;
        entry.cid = `${ctx.recordingId}#${entry.seq}`;
        // Reloj normalizado, estrictamente creciente (evita empates/desorden por
        // relojes de content/SW/pagina): clave para diffs y patrones temporales.
        let tRel = Math.max(0, (entry.ts || Date.now()) - ctx.epoch);
        if (!(tRel > ctx.lastT)) tRel = ctx.lastT + 0.001;
        ctx.lastT = tRel;
        entry.tRel = Math.round(tRel * 1000) / 1000;
      }
      timeline.push(entry);
      if (timeline.length > MAX_EVENTS) timeline.splice(0, timeline.length - MAX_EVENTS);
      if (entry.id) ids.add(entry.id);
      await chrome.storage.local.set({ [K.timeline]: timeline });
      reportCache.dirty = true; // invalida el reporte cacheado
      telemetryOnEvent(entry); // envia por webhook si hay grabacion+webhook activos
      maybeResampleOnNav(entry); // re-muestreo dirigido por evento (ruta/navegacion)
    })
    .catch((e) => console.warn("[CharlyQA] Error guardando entrada:", e));
  return writeChain;
}

async function clearAll() {
  seenIds = new Set();
  reportCache = { dirty: true, report: null };
  recCtx = null;
  teleBuffer = []; // no enviar data tras limpiar
  await chrome.storage.local.set({ [K.timeline]: [], [K.meta]: {} });
}

// --- Motor de Reportes (Herramienta 8) --------------------------------------

// Cache del reporte ensamblado: evita reconstruirlo en cada consulta del panel.
// Se invalida al anadir eventos o vaciar (y al reiniciar el SW, que arranca dirty).
let reportCache = { dirty: true, report: null };

/**
 * Ensambla el reporte estructurado completo (metadata + timeline con delays).
 * Usa cache mientras el timeline no cambie. Expuesta en globalThis para depurar.
 * @returns {Promise<{metadata:object, timeline:Array}>}
 */
async function buildReport() {
  if (!reportCache.dirty && reportCache.report) return reportCache.report;
  const [timeline, meta] = await Promise.all([getTimeline(), getMeta()]);
  const report = assembleReport(timeline, meta);
  reportCache = { dirty: false, report };
  return report;
}
globalThis.charlyBuildReport = buildReport;

/**
 * Artefacto canonico de auditoria (tarea: export unico y completo).
 * Es EXACTAMENTE lo que se descarga al exportar, lo que se envia por webhook y
 * la base de contexto para el asistente. No excluye metadatos.
 */
async function buildBundle(reason) {
  const [report, settings, capture, replayData] = await Promise.all([
    buildReport(),
    getSettings(),
    getConfig(),
    chrome.storage.local.get(K.replayJob),
  ]);
  const job = replayData[K.replayJob] || {};
  const trace = job.trace || [];
  const errores = report.timeline
    .filter((e) => e.type === "error" || e.type === "unhandledrejection")
    .map((e) => ({ ts: e.ts, ...e.data }));
  return stampIntegrity(
    redactBundle({
      schema: SCHEMA_CURRENT,
    exportedAt: new Date().toISOString(),
    reason: reason || "export",
    // Metadata de la extension (version, etc.).
    extension: { name: "CharlyAudit", version: chrome.runtime.getManifest().version },
    profileId: teleProfileId,
    // Config persistente del EXPORTADOR (solo metadata; al importar NO se aplica).
    settings,
    capture,
    // Contexto COMPLETO de auditoria + interaccion (reproduccion fiel).
    report,
    // Telemetria de la ultima repeticion.
    replay: {
      trace,
      resumen: {
        pasos: trace.length,
        inconsistencias: trace.filter((t) => t.inconsistencias && t.inconsistencias.length).length,
        activo: !!job.active,
      },
    },
    // Errores destacados (subconjunto del report; el report ya los incluye entero).
    errores,
    counts: report.metadata.counts,
    // KPIs agregados por sesion (consultables sin recorrer el timeline).
    kpis: computeKpis(report, { trace }),
    })
  );
}
globalThis.charlyBuildBundle = buildBundle;

// --- Router de mensajes -----------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // 1) Eventos entrantes del timeline.
  if (message && message.channel === "qa") {
    (async () => {
      const entry = message.entry;
      if (!entry) return;
      if (entry.type === "meta") {
        // La metadata se guarda aparte (no satura el timeline).
        await chrome.storage.local.set({ [K.meta]: entry.data });
        return;
      }
      if (!(await isRecording())) return; // se reenvia todo, se guarda solo al grabar
      // Adjunta contexto de la pestana.
      entry.data = entry.data || {};
      entry.tabId = sender.tab ? sender.tab.id : null;
      await appendEntry(entry);
    })();
    return false;
  }

  // 2) Ordenes de control desde la UI.
  if (message && message.channel === "qa-control") {
    (async () => {
      switch (message.action) {
        case "start": {
          const res = await startRecordingBound(sender.tab);
          sendResponse(res);
          break;
        }
        case "stop":
          await stopRecordingBound("manual");
          sendResponse({ ok: true, isRecording: false });
          break;
        case "toggle": {
          if (await isRecording()) {
            await stopRecordingBound("manual");
            sendResponse({ ok: true, isRecording: false, profileId: null });
          } else {
            const res = await startRecordingBound(sender.tab);
            sendResponse(res);
          }
          break;
        }
        case "getSettings":
          sendResponse({ ok: true, settings: await getSettings() });
          break;
        case "setSettings": {
          const merged = { ...(await getSettings()), ...(message.settings || {}) };
          await chrome.storage.local.set({ [K.settings]: merged });
          sendResponse({ ok: true, settings: merged });
          break;
        }
        case "domainAllowed": {
          const s = await getSettings();
          sendResponse({ ok: true, allowed: domainAllowed(message.host, s.allowedDomains), autoStart: s.autoStart });
          break;
        }
        case "flushTelemetry":
          await sendTelemetry("manual");
          sendResponse({ ok: true });
          break;
        case "getWebhookStatus": {
          const pending = (await chrome.storage.local.get(K.webhookPending))[K.webhookPending] || null;
          sendResponse({ ok: true, pending });
          break;
        }
        case "getKpis": {
          // KPIs de la sesion activa (fuente "Temporal"): reporte + telemetria de replay.
          const rep = await buildReport();
          const job = (await chrome.storage.local.get(K.replayJob))[K.replayJob] || {};
          sendResponse({ ok: true, kpis: computeKpis(rep, { trace: job.trace || [] }) });
          break;
        }
        case "getState": {
          const [rec, config, timeline, meta] = await Promise.all([
            isRecording(),
            getConfig(),
            getTimeline(),
            getMeta(),
          ]);
          const counts = {};
          for (const e of timeline) counts[e.type] = (counts[e.type] || 0) + 1;
          sendResponse({ ok: true, isRecording: rec, config, count: timeline.length, counts, meta });
          break;
        }
        case "getTimeline":
          sendResponse({ ok: true, timeline: await getTimeline() });
          break;
        case "getReport":
          sendResponse({ ok: true, report: await buildReport() });
          break;
        case "exportBundle":
          // Export unico y completo (mismo contenido que webhook y contexto IA).
          sendResponse({ ok: true, bundle: await buildBundle("export") });
          break;
        case "exportChunks":
          // Unidades indexables (chunks) para ingesta en base vectorial.
          sendResponse({ ok: true, ...chunkBundle(await buildBundle("chunks")) });
          break;
        case "exportCypress":
          sendResponse({ ok: true, script: toCypress(await buildReport()) });
          break;
        case "exportPlaywright":
          sendResponse({ ok: true, script: toPlaywright(await buildReport()) });
          break;
        case "setConfig": {
          const merged = { ...(await getConfig()), ...(message.config || {}) };
          await chrome.storage.local.set({ [K.config]: merged });
          await broadcast();
          sendResponse({ ok: true, config: merged });
          break;
        }
        case "clear":
          await clearAll();
          sendResponse({ ok: true });
          break;

        // --- Replay persistente (sobrevive al cierre del popup y a navegar) ---
        case "loadReplay": {
          // Validacion estricta: un reporte mal formado no debe cargarse.
          const rep = message.report || null;
          const check = validateBundle(rep && rep.timeline ? { schema: SCHEMA_CURRENT, report: rep } : rep);
          if (!check.ok) {
            sendResponse({ ok: false, error: "Reporte invalido: " + check.errors.join("; ") });
            break;
          }
          const finalReport = check.bundle.report;
          await chrome.storage.local.set({ [K.replay]: finalReport });
          sendResponse({ ok: true, count: finalReport.timeline.length, warnings: check.warnings });
          break;
        }
        case "getReplay": {
          const data = await chrome.storage.local.get([K.replay, K.replayJob]);
          sendResponse({ ok: true, report: data[K.replay] || null, job: data[K.replayJob] || null });
          break;
        }
        case "startReplay": {
          const data = await chrome.storage.local.get(K.replay);
          const report = data[K.replay];
          const tabId = message.tabId;
          if (!report || tabId == null) {
            sendResponse({ ok: false, error: "Sin reporte cargado o pestana invalida" });
            break;
          }
          const job = { active: true, index: 0, options: message.options || {}, tabId, trace: [], startedAt: Date.now() };
          await chrome.storage.local.set({ [K.replayJob]: job });
          // Tarea 2: reproducir desde la URL donde se comenzo la grabacion, como si
          // el usuario entrara al sitio. La carga del documento dispara el replay
          // (el content script resuelve el job en su bootstrap) y permite capturar
          // la metadata de red/navegacion del ingreso.
          const startUrl = report.metadata && report.metadata.url;
          if (startUrl) {
            chrome.tabs.update(tabId, { url: startUrl }).catch(async () => {
              const t = await chrome.tabs.get(tabId).catch(() => null);
              if (t) await ensureInjected(t);
            });
          } else {
            // Sin URL de inicio: inyectar directamente; el bootstrap llama maybeReplay.
            const t = await chrome.tabs.get(tabId).catch(() => null);
            if (t) await ensureInjected(t);
          }
          sendResponse({ ok: true, navegando: !!startUrl });
          break;
        }
        case "replayProgress": {
          // El content script reporta el avance para poder reanudar tras navegar.
          const data = await chrome.storage.local.get(K.replayJob);
          const job = data[K.replayJob];
          if (job && job.active && sender.tab && job.tabId === sender.tab.id) {
            job.index = message.index;
            if (message.done) job.active = false;
            await chrome.storage.local.set({ [K.replayJob]: job });
          }
          sendResponse({ ok: true });
          break;
        }
        case "replayTrace": {
          // Telemetria por paso (efecto esperado vs observado) para hallar
          // inconsistencias entre la grabacion y la repeticion (1.3).
          const data = await chrome.storage.local.get(K.replayJob);
          const job = data[K.replayJob];
          if (job && sender.tab && job.tabId === sender.tab.id && message.entry) {
            job.trace = job.trace || [];
            job.trace.push(message.entry);
            if (job.trace.length > MAX_EVENTS) job.trace.splice(0, job.trace.length - MAX_EVENTS);
            await chrome.storage.local.set({ [K.replayJob]: job });
          }
          sendResponse({ ok: true });
          break;
        }
        case "getReplayTrace": {
          const data = await chrome.storage.local.get(K.replayJob);
          const job = data[K.replayJob] || {};
          const trace = job.trace || [];
          const inconsist = trace.filter((t) => t.inconsistencias && t.inconsistencias.length);
          sendResponse({
            ok: true,
            trace,
            resumen: { pasos: trace.length, inconsistencias: inconsist.length, activo: !!job.active },
          });
          break;
        }
        case "getReplayJob": {
          // Lo consulta el content script al cargar para reanudar si toca.
          const data = await chrome.storage.local.get([K.replayJob, K.replay]);
          const job = data[K.replayJob];
          const mine = job && job.active && sender.tab && job.tabId === sender.tab.id;
          sendResponse({ ok: true, resume: !!mine, job: mine ? job : null, report: mine ? data[K.replay] : null });
          break;
        }
        case "stopReplay": {
          const data = await chrome.storage.local.get(K.replayJob);
          const job = data[K.replayJob] || {};
          job.active = false;
          await chrome.storage.local.set({ [K.replayJob]: job });
          if (job.tabId != null) chrome.tabs.sendMessage(job.tabId, { channel: "qa-replay", action: "stop" }).catch(() => {});
          else if (message.tabId != null) chrome.tabs.sendMessage(message.tabId, { channel: "qa-replay", action: "stop" }).catch(() => {});
          sendResponse({ ok: true });
          break;
        }
        case "getSource": {
          // Relevo de bloque de codigo: lo resuelve injected.js en la pestana.
          const tabId = message.tabId != null ? message.tabId : sender.tab && sender.tab.id;
          if (tabId == null) {
            sendResponse({ ok: false });
            break;
          }
          chrome.tabs.sendMessage(
            tabId,
            { channel: "qa-source", url: message.url, line: message.line, ctx: message.ctx },
            (res) => sendResponse(chrome.runtime.lastError ? { ok: false } : res || { ok: false })
          );
          break;
        }
        default:
          sendResponse({ ok: false, error: `Accion desconocida: ${message.action}` });
      }
    })();
    return true; // respuesta asincrona
  }

  return false;
});

// Restaura el badge tras un reinicio del navegador.
chrome.runtime.onStartup.addListener(async () => {
  await hydrateRecState();
  updateBadge(await isRecording(), recState.tabId);
});

console.info("[CharlyQA] Suite de QA cargada (background).");
