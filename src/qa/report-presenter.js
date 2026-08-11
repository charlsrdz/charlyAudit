/**
 * report-presenter.js — Traductor de un JSON exportado por CharlyAudit a una
 * estructura de presentación (secciones con pares label/value) lista para
 * maquetar en cualquier UI, sin que quien la consuma necesite conocer los
 * nombres de campo internos del esquema (`schema`, `report.metadata`, etc).
 * ===========================================================================
 * Módulo puro: sin dependencias de `chrome.*` ni del DOM. Acepta tanto el
 * bundle completo (`{schema, report, kpis, replay, ...}`, el que se firma y
 * se envía por webhook) como un reporte suelto (`{metadata, timeline}`), y
 * es defensivo ante campos ausentes — nunca lanza por datos incompletos,
 * simplemente omite la sección o el campo que no puede resolver.
 *
 * Uso:
 *   import { ReportPresenter } from "./report-presenter.js";
 *   const vista = ReportPresenter.from(bundleOJson).toJSON();
 *   // vista.sections = [{ id, title, items: [{ label, value, raw }] }, ...]
 */

// --- Formateadores de valor (texto listo para mostrar) ----------------------
function fmtMs(ms) {
  if (ms == null || Number.isNaN(ms)) return null;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)} s`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s - m * 60);
  return `${m}m ${rest}s`;
}
function fmtDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString();
}
function fmtNum(n) {
  if (n == null || Number.isNaN(n)) return null;
  return Number(n).toLocaleString();
}
function fmtPct(n, digits = 0) {
  if (n == null || Number.isNaN(n)) return null;
  return `${Number(n).toFixed(digits)}%`;
}
function fmtKb(kb) {
  if (kb == null || Number.isNaN(kb)) return null;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}
function fmtBool(b, si = "Sí", no = "No") {
  if (b == null) return null;
  return b ? si : no;
}

/** Construye la estructura de presentación a partir de un bundle o reporte. */
export class ReportPresenter {
  /**
   * @param {object} input Bundle completo (`{report, kpis, replay, ...}`) o
   *   un reporte suelto (`{metadata, timeline}`).
   */
  constructor(input) {
    const data = input || {};
    // Acepta ambas formas sin que el resto de la clase tenga que distinguirlas.
    this.bundle = data.report ? data : { report: data };
    this.report = this.bundle.report || {};
    this.meta = this.report.metadata || {};
    this.kpis = this.bundle.kpis || null;
    this.replay = this.bundle.replay || null;
    this.errores = this.bundle.errores || null;
    this.integrity = this.bundle.integrity || null;
    this.extension = this.bundle.extension || null;
  }

  /** Atajo: `ReportPresenter.from(json).toJSON()`. */
  static from(input) {
    return new ReportPresenter(input);
  }

  /**
   * @returns {Array<{id:string, title:string, items:Array<{label:string, value:string, raw:*}>}>}
   * Solo incluye secciones y campos que efectivamente tienen dato — nunca
   * placeholders vacíos, para que quien maqueta no tenga que filtrar.
   */
  toSections() {
    const sections = [];
    /** Agrega una seccion solo si al menos un campo tiene valor resoluble. */
    const section = (id, title, fields) => {
      const items = fields
        .filter((f) => f.value !== null && f.value !== undefined && f.value !== "")
        .map((f) => ({ label: f.label, value: String(f.value), raw: f.raw !== undefined ? f.raw : f.value }));
      if (items.length) sections.push({ id, title, items });
    };
    const field = (label, value, raw) => ({ label, value, raw });

    const m = this.meta;
    const ent = m.entorno || {};
    const rec = m.recording || {};

    section("resumen", "Resumen de la sesión", [
      field("URL", m.url, m.url),
      field("Iniciada en", rec.startUrl, rec.startUrl),
      field("Fecha de exportación", fmtDate(this.bundle.exportedAt), this.bundle.exportedAt),
      field("Duración", fmtMs(m.durationMs), m.durationMs),
      field("Eventos totales", fmtNum(m.eventCount), m.eventCount),
      field("Extensión", this.extension && `${this.extension.name || "CharlyAudit"} v${this.extension.version || "?"}`, this.extension),
      field("Motivo de exportación", this.bundle.reason, this.bundle.reason),
    ]);

    section("entorno", "Entorno de grabación", [
      field("CPU", ent.sistema && ent.sistema.cpu, ent.sistema && ent.sistema.cpu),
      field("RAM", ent.sistema && ent.sistema.ramGB != null ? `${ent.sistema.ramGB} GB` : null, ent.sistema && ent.sistema.ramGB),
      field("Núcleos", ent.sistema && ent.sistema.nucleos, ent.sistema && ent.sistema.nucleos),
      field("Navegador", ent.navegador && ent.navegador.ua, ent.navegador && ent.navegador.ua),
      field("Plataforma", ent.navegador && ent.navegador.plataforma, ent.navegador && ent.navegador.plataforma),
      field("Resolución", m.resolution, m.resolution),
      field("Viewport", m.viewport, m.viewport),
    ]);

    if (this.kpis) {
      const p = this.kpis.performance || {};
      section("performance", "Rendimiento (Core Web Vitals)", [
        field("LCP", fmtMs(p.lcpMs), p.lcpMs),
        field("CLS", p.cls != null ? p.cls.toFixed(3) : null, p.cls),
        field("INP", fmtMs(p.inpMs), p.inpMs),
        field("TBT", fmtMs(p.tbtMs), p.tbtMs),
        field("TBT peor segmento", fmtMs(p.tbtSegmentMaxMs), p.tbtSegmentMaxMs),
        field("Tareas largas", fmtNum(p.longTasks), p.longTasks),
      ]);

      const r = this.kpis.red || {};
      section("red", "Red", [
        field("Peticiones totales", fmtNum(r.total), r.total),
        field("Peticiones fallidas", fmtNum(r.fallidas), r.fallidas),
        field("Más lenta", fmtMs(r.masLentaMs), r.masLentaMs),
        field("Peso total descargado", fmtKb(r.kbTotal), r.kbTotal),
      ]);

      const s = this.kpis.seguridad || {};
      const sev = s.porSeveridad || {};
      section("seguridad", "Seguridad", [
        field("Hallazgos totales", fmtNum(s.total), s.total),
        field("Críticos", fmtNum(sev.critica), sev.critica),
        field("Altos", fmtNum(sev.alta), sev.alta),
        field("Medios", fmtNum(sev.media), sev.media),
        field("Bajos", fmtNum(sev.baja), sev.baja),
      ]);

      section("interaccion", "Interacción", [field("Interacciones capturadas", fmtNum(this.kpis.interacciones), this.kpis.interacciones)]);
    }

    // Repeticion: prioriza el resumen del bundle.replay; si no esta, usa el
    // que ya trae kpis.replay (calculado junto al resto de metricas).
    const repRes = (this.replay && this.replay.resumen) || (this.kpis && this.kpis.replay) || null;
    if (repRes) {
      section("repeticion", "Última repetición", [
        field("Pasos reproducidos", fmtNum(repRes.pasos), repRes.pasos),
        field("Inconsistencias", fmtNum(repRes.inconsistencias), repRes.inconsistencias),
        field("Fidelidad", fmtPct(repRes.fidelidad), repRes.fidelidad),
        field("Activo al exportar", fmtBool(repRes.activo), repRes.activo),
      ]);
    }

    if (Array.isArray(this.errores) && this.errores.length) {
      section(
        "errores",
        "Errores destacados",
        this.errores.map((e, i) => field(`Error ${i + 1}`, (e && (e.message || e.reason || e.detalle)) || String(e), e))
      );
    }

    if (m.counts && typeof m.counts === "object") {
      section(
        "conteos",
        "Eventos por tipo",
        Object.entries(m.counts).map(([tipo, n]) => field(tipo, fmtNum(n), n))
      );
    }

    if (this.integrity) {
      section("integridad", "Integridad del artefacto", [
        field("Esquema", this.bundle.schema, this.bundle.schema),
        field("Hash de contenido", this.integrity.contentHash, this.integrity.contentHash),
        field("Último evento (cid)", this.integrity.ultimoCid, this.integrity.ultimoCid),
        field("Eventos sellados", fmtNum(this.integrity.eventos), this.integrity.eventos),
      ]);
    }

    return sections;
  }

  /** Todas las secciones aplanadas en una sola lista (útil para tablas simples). */
  toFlat() {
    return this.toSections().flatMap((s) => s.items.map((it) => ({ section: s.title, ...it })));
  }

  /** Estructura final lista para serializar/consumir desde una plantilla. */
  toJSON() {
    return { generatedAt: new Date().toISOString(), sections: this.toSections() };
  }
}
