/**
 * bundle-schema.js — Versionado, migracion y validacion estricta del bundle.
 * ========================================================================
 * El bundle es el artefacto que se exporta, se envia por webhook y se ingesta
 * en la base vectorial. Un reporte mal formado NO debe contaminar la base, por
 * eso todo import/ingesta pasa por `validateBundle`. Modulo puro (sin DOM/chrome).
 */

/**
 * Agrupaciones canonicas de tipos de evento — FUENTE UNICA DE VERDAD.
 * Este modulo es puro y lo importan tanto el service worker (background.js,
 * computeKpis) como el panel lateral (context-bridge.js, sidepanel.js), asi
 * que es el unico lugar correcto para definir "que tipos de evento cuentan
 * como interaccion / como navegacion" sin duplicar la lista en cada sitio
 * que la necesita. Antes existian TRES copias independientes de "que es una
 * interaccion" (el contador de la pestana Auditoria, el builder de contexto
 * del Asistente, y computeKpis) que se fueron desincronizando con el tiempo
 * — "routes" no sumaba navegaciones completas de pagina en dos de los tres
 * lugares, "interactions" no sumaba middleclick en uno de ellos. Con esto,
 * un cambio se hace UNA vez y se refleja en Auditoria, Asistente y KPIs por
 * igual, sea la sesion temporal o importada.
 *
 * NOTA: `INTERACTIVE_TYPES` en report-engine.js es DISTINTO a proposito (no
 * incluye dragdrop/scroll/resize) — sirve para correlacionar INP real por
 * interaccion, un concepto mas angosto que "que cuenta como interaccion" en
 * general. No debe unificarse con esto.
 */
export const INTERACTION_TYPES = ["click", "dblclick", "middleclick", "input", "key", "dragdrop", "scroll", "resize"];
export const ROUTE_TYPES = ["route", "navigation"];

/**
 * Redaccion canonica pre-embedding: elimina PII/tokens del texto libre ANTES de
 * que el bundle salga (export/webhook/ingesta), para no contaminar la base
 * vectorial. Pura y determinista (no depende del orden de los patrones sensibles).
 */
const REDACTIONS = [
  [/eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}/g, "[JWT]"],
  [/\bBearer\s+[A-Za-z0-9._~+/-]{10,}=*/gi, "Bearer [TOKEN]"],
  [/\b(?:sk|pk|api|key|secret|token|access)[-_][A-Za-z0-9]{8,}\b/gi, "[KEY]"],
  [/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/gi, "[EMAIL]"],
  [/\b(?:\d[ -]?){13,16}\b/g, "[CARD]"],
  [/\b[A-Fa-f0-9]{32,}\b/g, "[HASH]"],
];
export function redactText(str) {
  let s = String(str == null ? "" : str);
  for (const [re, to] of REDACTIONS) s = s.replace(re, to);
  return s;
}
/**
 * Devuelve el bundle con el texto libre redactado, SIN mutar el reporte original
 * (crea nuevos objetos), para que el asistente conserve la fidelidad en vivo.
 */
export function redactBundle(bundle) {
  try {
    const tl = bundle && bundle.report && bundle.report.timeline;
    if (Array.isArray(tl)) {
      const redacted = tl.map((e) => {
        const d = e.data || {};
        const rd = {};
        if (d.message != null) rd.message = redactText(d.message);
        if (d.reason != null) rd.reason = redactText(d.reason);
        if (d.detalle != null) rd.detalle = redactText(d.detalle);
        if (d.text != null && !d.masked) rd.text = redactText(d.text);
        return Object.keys(rd).length ? { ...e, data: { ...d, ...rd } } : e;
      });
      bundle.report = { ...bundle.report, timeline: redacted };
    }
    // Redacta tambien los errores destacados del envelope.
    if (Array.isArray(bundle.errores)) {
      bundle.errores = bundle.errores.map((x) => ({ ...x, message: x.message != null ? redactText(x.message) : x.message, reason: x.reason != null ? redactText(x.reason) : x.reason }));
    }
    bundle.redactado = true;
  } catch {
    /* no critico */
  }
  return bundle;
}

export const SCHEMA_CURRENT = "charlyaudit/audit-bundle@1";
const KNOWN_SCHEMAS = new Set(["charlyaudit/audit-bundle@1"]);

/** Hash estable (FNV-1a, 32 bits) para idempotencia/dedup en la ingesta. */
export function fnv1a(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return ("00000000" + h.toString(16)).slice(-8);
}

/**
 * Migra formatos previos al esquema actual. Envuelve un "reporte suelto"
 * (sin envelope) como bundle minimo para compatibilidad hacia atras.
 */
export function migrateBundle(input) {
  if (!input || typeof input !== "object") return input;
  // Reporte suelto (metadata+timeline) sin envelope de bundle.
  if (!input.schema && Array.isArray(input.timeline) && input.metadata) {
    return {
      schema: SCHEMA_CURRENT,
      exportedAt: new Date().toISOString(),
      migratedFrom: "reporte-suelto",
      report: input,
    };
  }
  // Aqui irian migraciones por version (ej. @0 -> @1) en el futuro.
  return input;
}

/**
 * Valida estrictamente un bundle (tras migrar). Devuelve
 * { ok, errors[], warnings[], bundle }. Si !ok, bundle = null (no se ingesta).
 */
export function validateBundle(input) {
  const errors = [];
  const warnings = [];
  const bundle = migrateBundle(input);

  if (!bundle || typeof bundle !== "object") {
    return { ok: false, errors: ["el contenido no es un objeto JSON valido"], warnings, bundle: null };
  }
  if (!KNOWN_SCHEMAS.has(bundle.schema)) {
    errors.push(`schema desconocido o ausente: ${JSON.stringify(bundle.schema)}`);
  }

  const report = bundle.report;
  if (!report || typeof report !== "object") {
    errors.push("falta 'report'");
  } else {
    if (!report.metadata || typeof report.metadata !== "object") errors.push("falta 'report.metadata'");
    if (!Array.isArray(report.timeline)) {
      errors.push("'report.timeline' no es un arreglo");
    } else {
      // Estructura minima por evento.
      let bad = 0;
      for (const e of report.timeline) {
        if (!e || typeof e.type !== "string" || (e.ts == null && e.tRel == null)) bad++;
      }
      if (bad) errors.push(`${bad} evento(s) mal formados (sin 'type' o sin 'ts'/'tRel')`);

      // Orden monotonico por clave canonica (seq preferido, luego tRel, luego ts).
      const keyOf = (e) => (e.seq != null ? e.seq : e.tRel != null ? e.tRel : e.ts);
      let prev = -Infinity;
      let monotonic = true;
      for (const e of report.timeline) {
        const k = keyOf(e);
        if (typeof k !== "number") continue;
        if (k < prev) {
          monotonic = false;
          break;
        }
        prev = k;
      }
      if (!monotonic) errors.push("orden no monotonico (seq/tRel/ts decreciente): reloj no normalizado");

      // Advertencias (no bloquean, pero avisan de perdida de trazabilidad).
      if (report.timeline.length && report.timeline.some((e) => e && e.cid == null))
        warnings.push("hay eventos sin 'cid' canonico (correlacion 1-a-1 parcial)");
    }
    if (!report.metadata || !report.metadata.url) warnings.push("sin URL de sesion en metadata");
  }

  if (!bundle.extension || !bundle.extension.version) warnings.push("sin version de extension");

  const ok = errors.length === 0;
  return { ok, errors, warnings, bundle: ok ? bundle : null };
}

/**
 * Sella el bundle con integridad para idempotencia en la ingesta:
 * numero de eventos, ultimo cid y contentHash del timeline canonico.
 */
export function stampIntegrity(bundle) {
  try {
    const tl = (bundle.report && bundle.report.timeline) || [];
    const canon = tl.map((e) => `${e.cid || e.seq || ""}:${e.type}:${e.tRel != null ? e.tRel : e.ts}`).join("|");
    bundle.integrity = {
      eventos: tl.length,
      ultimoCid: tl.length ? tl[tl.length - 1].cid || null : null,
      contentHash: fnv1a(canon),
      schema: bundle.schema,
    };
  } catch {
    /* no critico */
  }
  return bundle;
}

/**
 * Deriva la clave de particion (tenant/dominio) para la base vectorial: separa
 * los datos por dominio de la sesion, evitando mezclar tenants en la ingesta.
 */
export function partitionKeyOf(bundle) {
  const url = bundle && bundle.report && bundle.report.metadata && bundle.report.metadata.url;
  try {
    return "domain:" + new URL(url).hostname;
  } catch {
    return "domain:unknown";
  }
}

// Tipos indexables (los que aportan patron; se excluye el ruido: scroll, resize,
// focus, console no-error, movimientos de raton, etc.).
const INDEXABLE = new Set([
  "error", "unhandledrejection", "security", "route", "navigation",
  "web-vitals", "worker", "code-block", "click", "input", "key",
  "interaction-timing", // INP real por interaccion (latencia medida por el navegador)
]);

/** Texto compacto y REDACTADO por evento, listo para embeddings. */
function chunkText(e) {
  const d = e.data || {};
  switch (e.type) {
    case "error":
    case "unhandledrejection":
      return redactText(`Error: ${d.message || d.reason || ""} @ ${d.ref || "?"}`);
    case "network":
      return redactText(`${d.method || "GET"} ${d.status || d.error || ""} ${d.url || ""}`);
    case "security":
      return `Seguridad [${d.severidad || ""}] ${d.kind || ""} en ${d.donde || ""} (${redactText(d.detalle || "")})`;
    case "route":
    case "navigation":
      return redactText(`Navegacion a ${d.to || d.url || ""}`);
    case "web-vitals":
      return `Vitals LCP ${d.lcpMs}ms CLS ${d.cls} INP ${d.inpMs}ms TBT ${d.tbtMs}ms longtasks ${d.longTasks}`;
    case "interaction-timing":
      return `INP ${d.tipo || "?"} ${d.inpMs}ms${d.inpMs > 200 ? " lento" : ""}${d.interactionId ? " id:" + d.interactionId : ""}`;
    case "worker":
      return redactText(`${d.clase || "worker"} ${d.script || d.scope || ""}`);
    case "resource-timing":
      return redactText(`Recurso ${d.tipo || ""} ${d.ms}ms ${d.kb}KB ${d.url || ""}`);
    case "code-block":
      return `Codigo culpable en ${d.ref || "?"}`;
    case "click":
    case "input":
    case "key": {
      const a = d.anchor || {};
      const etq = a.name || a.aria || (d.masked ? "***" : a.text) || a.role || a.tag || "";
      return redactText(`${e.type} sobre "${etq}"`);
    }
    default:
      return e.type;
  }
}

/**
 * Fragmenta el bundle en UNIDADES INDEXABLES para la base vectorial. Cada chunk
 * es autocontenido: clave de particion, identidad canonica (cid/fp/seq/tRel),
 * texto redactado para embedding, contexto minimo (url + entorno) y su propio
 * contentHash para idempotencia. Devuelve { partitionKey, count, chunks }.
 */
export function chunkBundle(bundle) {
  const partitionKey = partitionKeyOf(bundle);
  const report = (bundle && bundle.report) || {};
  const meta = report.metadata || {};
  const ent = meta.entorno || {};
  const contexto = {
    url: meta.url || null,
    sistema: ent.sistema || null,
    navegador: ent.navegador ? { ua: ent.navegador.ua, plataforma: ent.navegador.plataforma } : null,
  };
  const base = {
    schema: bundle.schema,
    recordingId: (meta.recording && meta.recording.recordingId) || null,
    profileId: bundle.profileId || null,
    exportedAt: bundle.exportedAt || null,
    partitionKey,
  };
  const chunks = [];
  for (const e of report.timeline || []) {
    // Red: solo indexa fallos o peticiones lentas (evita inflar con ruido 2xx).
    if (e.type === "network") {
      const slow = (e.data && e.data.durationMs) > 1000;
      const failed = e.data && (e.data.ok === false || (e.data.status || 0) >= 400);
      if (!slow && !failed) continue;
    } else if (e.type === "resource-timing") {
      // Idem para recursos pasivos: ahora se captura el waterfall COMPLETO (no
      // solo pesados) en el reporte crudo, pero para la vector DB solo indexamos
      // los que aportan senal (lentos/pesados), igual que "network".
      const slow = (e.data && e.data.ms) > 800;
      const heavy = (e.data && e.data.kb) > 120;
      if (!slow && !heavy) continue;
    } else if (!INDEXABLE.has(e.type)) {
      continue;
    }
    const texto = chunkText(e);
    const unit = {
      ...base,
      tipo: e.type,
      cid: e.cid || null,
      fp: e.fp || null,
      seq: e.seq != null ? e.seq : null,
      tRel: e.tRel != null ? e.tRel : null,
      texto,
      contexto,
    };
    unit.contentHash = fnv1a(`${partitionKey}|${unit.cid || ""}|${unit.fp || ""}|${texto}`);
    chunks.push(unit);
  }
  return { partitionKey, count: chunks.length, chunks };
}

/**
 * Firma HMAC-SHA256 (hex) del cuerpo, para que el servidor de ingesta verifique
 * AUTENTICIDAD e INTEGRIDAD del bundle/chunk. Usa Web Crypto (disponible en el
 * service worker MV3 y en Node webcrypto). Pura dado (secret, message).
 */
export async function hmacHex(secret, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode(String(secret)), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(String(message)));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * KPIs agregados por sesion: metricas listas para consulta/dashboard y para
 * agrupar/filtrar en la base vectorial sin recorrer el timeline. Puros (numeros
 * y conteos; sin PII).
 */
export function computeKpis(report, replay) {
  const tl = (report && report.timeline) || [];
  const meta = (report && report.metadata) || {};
  const by = (t) => tl.filter((e) => e.type === t);
  const vitals = by("web-vitals").slice(-1)[0];
  const net = by("network");
  const netFail = net.filter((e) => e.data && (e.data.ok === false || (e.data.status || 0) >= 400));
  const res = by("resource-timing");
  const kbTotal = [...net, ...res].reduce((s, e) => s + ((e.data && e.data.kb) || 0), 0);
  const sec = by("security");
  const porSeveridad = { critica: 0, alta: 0, media: 0, baja: 0 };
  for (const e of sec) {
    const s = (e.data && e.data.severidad) || "";
    if (porSeveridad[s] != null) porSeveridad[s]++;
  }
  const trace = (replay && replay.trace) || [];
  const incons = trace.filter((t) => t.inconsistencias && t.inconsistencias.length).length;
  const interKinds = new Set(INTERACTION_TYPES);
  // INP real por interaccion (p98 de las latencias YA correlacionadas a cada
  // click/input/tecla concreto — no el aproximado de web-vitals) siguiendo la
  // metodologia estandar (percentil 98, no el maximo absoluto de una sola vez).
  const interLatencies = tl.filter((e) => e.data && e.data.inpMs != null).map((e) => e.data.inpMs).sort((a, b) => a - b);
  const inpP98 = interLatencies.length ? interLatencies[Math.min(interLatencies.length - 1, Math.floor(interLatencies.length * 0.98))] : null;
  // TBT por navegacion: el segmento (entre rutas) con mas bloqueo — util para
  // localizar QUE vista/pagina especifica concentra el problema de performance.
  const segs = ROUTE_TYPES.flatMap((t) => by(t)).map((e) => (e.data && e.data.tbtSegmentMs) || 0);
  const tbtSegmentMax = segs.length ? Math.max(...segs) : null;
  return {
    eventos: tl.length,
    duracionMs: meta.durationMs || 0,
    errores: by("error").length + by("unhandledrejection").length,
    performance: vitals
      ? {
          lcpMs: vitals.data.lcpMs,
          cls: vitals.data.cls,
          inpMs: inpP98 != null ? inpP98 : vitals.data.inpMs, // real por interaccion (p98) si hay datos
          tbtMs: vitals.data.tbtMs,
          tbtSegmentMaxMs: tbtSegmentMax, // peor navegacion/vista de la sesion
          longTasks: vitals.data.longTasks,
        }
      : null,
    red: {
      total: net.length,
      fallidas: netFail.length,
      masLentaMs: net.reduce((m, e) => Math.max(m, (e.data && e.data.durationMs) || 0), 0),
      kbTotal: Math.round(kbTotal),
    },
    seguridad: { total: sec.length, porSeveridad },
    interacciones: tl.filter((e) => interKinds.has(e.type)).length,
    replay: trace.length ? { pasos: trace.length, inconsistencias: incons, fidelidad: Math.round(((trace.length - incons) / trace.length) * 100) } : null,
  };
}
