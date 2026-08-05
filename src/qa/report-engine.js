/**
 * report-engine.js — Motor de Reportes
 * ====================================
 * Modulo puro (sin dependencias de chrome.*) que ensambla el reporte final a
 * partir del timeline crudo y la metadata de la sesion. Importable tanto por el
 * service worker como por pruebas unitarias.
 *
 * Entrada: array de entradas { id, type, ts, data } y un objeto `meta`.
 * Salida: { metadata, timeline } donde cada entrada del timeline lleva su
 * `delay` (ms transcurridos desde la entrada anterior).
 */

import { fnv1a } from "./bundle-schema.js";

/** Tipos de evento conocidos (las 7 herramientas). */
export const EVENT_TYPES = [
  "click",
  "dblclick",
  "middleclick",
  "dragdrop",
  "key",
  "input",
  "focus",
  "console",
  "error",
  "unhandledrejection",
  "code-block",
  "network",
  "route",
  "navigation",
  "global-state",
  "function-call",
  "scroll",
  "resize",
  "web-vitals",
  "resource-timing",
  "security",
  "response-headers",
  "worker",
  "interaction-timing",
];

/**
 * Fingerprint SEMANTICO por evento: firma estable y normalizada que agrupa
 * eventos "equivalentes" entre sesiones/usuarios (clave para la base vectorial).
 * Ignora partes volatiles (numeros, ids, urls, query) y usa el ancla semantica
 * en interacciones (rol/nombre), no la posicion.
 */
function normStr(s) {
  return String(s == null ? "" : s)
    .replace(/https?:\/\/[^\s'")]+/gi, "URL")
    .replace(/\b[0-9a-f]{8}-[0-9a-f-]{20,}\b/gi, "UUID")
    .replace(/0x[0-9a-f]+/gi, "HEX")
    .replace(/\b\d[\d.,:]*\b/g, "N")
    .replace(/['"][^'"]*['"]/g, "S")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
}
function pathOf(u) {
  try {
    return new URL(u, "http://x").pathname.replace(/\/\d+(?=\/|$)/g, "/:id");
  } catch {
    return String(u || "");
  }
}
export function fingerprintEvent(e) {
  const d = (e && e.data) || {};
  let sig;
  switch (e && e.type) {
    case "error":
    case "unhandledrejection":
      sig = `err|${normStr(d.message || d.reason)}|${d.ref || ""}`;
      break;
    case "network":
      sig = `net|${d.method || "GET"}|${pathOf(d.url)}|${Math.floor((d.status || 0) / 100)}xx`;
      break;
    case "response-headers":
      sig = `hdr|${pathOf(d.url)}`;
      break;
    case "security":
      sig = `sec|${d.kind || ""}|${d.donde || ""}`;
      break;
    case "route":
    case "navigation":
      sig = `nav|${pathOf(d.to || d.url)}`;
      break;
    case "web-vitals":
      sig = "vitals";
      break;
    case "interaction-timing":
      sig = `inp|${d.tipo || "?"}|${d.inpMs > 200 ? "lento" : d.inpMs > 100 ? "medio" : "rapido"}`;
      break;
    case "worker":
      sig = `worker|${d.clase || ""}`;
      break;
    case "click":
    case "dblclick":
    case "middleclick":
    case "input":
    case "key": {
      const a = d.anchor || {};
      const label = (a.name || a.aria || a.text || a.ph || "").toString().toLowerCase().slice(0, 40);
      sig = `${e.type}|${a.role || a.tag || ""}|${label}`;
      break;
    }
    default:
      sig = `${(e && e.type) || "?"}`;
  }
  return "fp-" + fnv1a(sig);
}

const INTERACTIVE_TYPES = new Set(["click", "dblclick", "middleclick", "input", "key"]);

/**
 * INP real por interaccion (no aproximado): correlaciona cada evento
 * "interaction-timing" (emitido por el PerformanceObserver de tipo "event" en
 * injected.js, con su reloj epoch propio) con el evento de interaccion
 * (click/input/tecla) que realmente lo origino, usando el MISMO reloj epoch
 * (Event.timeStamp y performance.timeOrigin comparten base) — no una
 * aproximacion por cercania arbitraria, sino la correlacion por el reloj real
 * del navegador. Adjunta `data.inpMs`/`data.interactionId` al evento original y
 * deja tambien la entrada `interaction-timing` cruda para trazabilidad.
 * @param {Array} timeline ya ordenado y con `ts` presente.
 */
function attachInteractionLatency(timeline) {
  const timed = timeline.filter((e) => e.type === "interaction-timing");
  if (!timed.length) return timeline;
  const candidates = timeline.filter((e) => INTERACTIVE_TYPES.has(e.type));
  for (const t of timed) {
    const tsEvent = t.data && t.data.tsEvent;
    if (tsEvent == null) continue;
    let best = null;
    let bestDiff = Infinity;
    for (const c of candidates) {
      if (c.data && c.data.inpMs != null) continue; // ya correlacionado (no reasignar)
      const diff = Math.abs((c.ts || 0) - tsEvent);
      if (diff < bestDiff && diff <= 1000) {
        best = c;
        bestDiff = diff;
      }
    }
    if (best) {
      best.data = { ...best.data, inpMs: t.data.inpMs, interactionId: t.data.interactionId };
    }
  }
  return timeline;
}

/**
 * Ensambla el reporte estructurado.
 * @param {Array<{id?:string,type:string,ts:number,data:object}>} rawTimeline
 * @param {object} [meta]  metadata capturada de la pagina (url, ua, resolucion...)
 * @returns {{metadata:object, timeline:Array}}
 */
export function assembleReport(rawTimeline, meta = {}) {
  // Orden CANONICO por secuencia de sesion (monotonica, asignada por el SW);
  // si falta seq (datos antiguos), se ordena por ts como respaldo.
  const sorted = [...rawTimeline].sort((a, b) => {
    if (a.seq != null && b.seq != null) return a.seq - b.seq;
    return a.ts - b.ts;
  });

  const counts = {};
  for (const e of sorted) counts[e.type] = (counts[e.type] || 0) + 1;

  // Reloj normalizado por sesion: epoch = inicio de la grabacion (o primer ts).
  const epoch = (meta.recording && meta.recording.startedAtMs) || (sorted.length ? sorted[0].ts : 0);
  let prevRel = 0;
  const timeline = sorted.map((entry, index) => {
    // tRel = ms desde el inicio de sesion, forzado NO decreciente (monotonico)
    // para eliminar desordenes por relojes de content/SW/pagina.
    let tRel = entry.tRel != null ? entry.tRel : (entry.ts || 0) - epoch;
    if (tRel < prevRel) tRel = prevRel;
    const delay = index === 0 ? 0 : tRel - prevRel;
    prevRel = tRel;
    return {
      index,
      seq: entry.seq != null ? entry.seq : index, // orden canonico de sesion
      cid: entry.cid || null, // id canonico determinista (recordingId#seq)
      fp: fingerprintEvent(entry), // fingerprint semantico (agrupacion vector DB)
      type: entry.type,
      ts: entry.ts,
      tRel, // tiempo normalizado monotonico (ms desde inicio de sesion)
      iso: new Date(entry.ts).toISOString(),
      delay, // ms desde el evento anterior (siempre >= 0)
      data: entry.data,
    };
  });
  attachInteractionLatency(timeline); // INP real por interaccion (correlacion pura)

  const startedAt = sorted.length ? sorted[0].ts : null;
  const endedAt = sorted.length ? sorted[sorted.length - 1].ts : null;

  return {
    metadata: {
      tool: "CharlyAudit",
      url: meta.url || null,
      capturedAt: new Date().toISOString(),
      startedAt: startedAt ? new Date(startedAt).toISOString() : null,
      endedAt: endedAt ? new Date(endedAt).toISOString() : null,
      durationMs: startedAt && endedAt ? endedAt - startedAt : 0,
      userAgent: meta.userAgent || null,
      language: meta.language || null,
      resolution: meta.resolution || null, // pantalla
      viewport: meta.viewport || null, // ventana
      recording: meta.recording || null, // recordingId, tabId, windowId, startUrl (tarea 1)
      entorno: meta.entorno || null, // sistema, navegador, ventana, cookies (1.4/1.5)
      eventCount: sorted.length,
      counts,
    },
    timeline,
  };
}
