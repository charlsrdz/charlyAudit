# CharlyAudit v2.5.0

Suite de QA, session replay y auditoría de seguridad para Chrome (MV3).
Convierte cada sesión real de usuario en evidencia accionable y verificable
para QA, Performance y Security, con ingesta directa a bases vectoriales.

---

## Arquitectura

```
manifest.json           MV3 · sin content_scripts declarativos · inyección on-demand
src/background/         Service worker (orquestador)
  service-worker.js     Importa CharlyAPI + qa/background
src/lib/CharlyAPI.js    API de abstracción Chrome (storage, menus, screenshots...)
src/qa/
  background.js         Ciclo de grabación, lifecycle de pestaña, telemetría, webhook
  content.js            Puente DOM↔SW (captura eventos, replay, banner de grabación)
  injected.js           MAIN world: perf/INP/waterfall, red, console, workers, globals
  report-engine.js      assembleReport, fingerprintEvent, attachInteractionLatency
  bundle-schema.js      validateBundle, stampIntegrity, redactBundle, chunkBundle,
                        computeKpis, partitionKeyOf, hmacHex
  exporters.js          toCypress, toPlaywright
src/popup/              Popup de control rápido (grabar, importar, reproducir)
src/sidepanel/
  sidepanel.html/js/css Panel lateral: asistente IA + auditoría
  lib/
    openwebui-client.js Cliente multi-proveedor (OpenWebUI/OpenAI/Gemini/Claude/custom)
    context-bridge.js   Puente de contexto QA→IA (12 scopes, budget, digest)
    chat-cache.js       Caché de conversación en localStorage
    markdown.js         Render Markdown seguro (sin eval)
```

---

## Estado de implementación

### ✅ Completado y validado

**Captura**
- Inyección 100% on-demand (sin content_scripts declarativos; SW orquesta todo)
- Grabación ligada a una pestaña: `recordingId`, `tabId`, `windowId`, `startUrl`, `startedAtMs`
- Grabación única (rechaza segunda pestaña simultánea)
- Stop automático al cerrar la pestaña grabada (con telemetría onclose)
- Banner visible en la página grabada (Shadow DOM, no interfiere con el host)
- Badge del ícono anclado a la pestaña grabada (muestra dominio)
- Metadata de entorno al iniciar: CPU, RAM, OS, navegador, ventana, cookies (flags)
- Re-muestreo periódico (1/min) + dirigido por navegación (debounce 2s)
- `recState` rehidratado tras reinicio del SW (MV3 robustez)

**Performance (100% real)**
- INP real por interacción: PerformanceObserver `"event"` → `interaction-timing` con `tsEvent` (reloj epoch) → correlación 1-a-1 con el click/input exacto en `attachInteractionLatency`
- TBT real por navegación: `flushTbtSegment()` en cada `emitRoute` → `tbtSegmentMs` por segmento → `tbtPeorSegmento` en KPIs
- Waterfall completo: `waterfallFor()` por fetch/XHR + observer `"resource"` por recursos pasivos; fases DNS/TCP/TTFB/descarga, tamaño, protocolo, caché
- Dedup de `interaction-timing` por `interactionId` (evita triple emisión pointerdown+up+click)
- Dedup de `resource-timing` por URL+startTime (evita duplicados con `buffered:true`)
- `navInfo` enriquecida: tipo, referrer, redirects, TTFB, domListo, cargaMs, ttiApproxMs, docKb

**Datos y trazabilidad**
- `cid` canónico determinista (`recordingId#seq`) por evento
- `tRel` monotónico por sesión (sobrevive reinicios del SW)
- `fp` fingerprint semántico por tipo (agrupa errores/endpoints/INP equivalentes entre sesiones)
- Esquema versionado `charlyaudit/audit-bundle@1` con migración y validación estricta
- `stampIntegrity`: `contentHash` FNV-1a + `ultimoCid` + nº eventos (idempotencia en ingesta)
- Redacción canónica pre-embedding (email, JWT, Bearer, api-keys, tarjetas, hashes)
- `chunkBundle`: unidades indexables por tipo con `partitionKey` por dominio/tenant
- `computeKpis`: INP p98, TBT peor segmento, red, seguridad por severidad, fidelidad replay
- HMAC-SHA256 del payload webhook (`X-CharlyAudit-Signature`)
- Reintentos con backoff exponencial (2→4→8→16→30min, max 5)

**Asistente IA (v2.4.1 → v2.5.0)**
- **Conversación multi-turno real**: `messages: [{role:"system",...},{role:"user",...},{role:"assistant",...},...,{role:"user"}]`; el historial viaja como roles nativos, no como texto incrustado
- **Multi-proveedor**: OpenWebUI (propio), OpenAI/ChatGPT, Google Gemini, Anthropic Claude, endpoint personalizado. Claude separa `system` en su campo propio; Gemini usa el endpoint OpenAI-compatible
- **Parámetros configurables**: temperatura, tokens máximos, turnos de historial
- **Contexto enriquecido**: INP real p98, TBT por segmento, waterfall con TTFB y caché

**UX y accesibilidad**
- Panel de KPIs colapsable (11 métricas: LCP/CLS/INP/TBT/errores/red/seguridad/replay)
- Indicador `qa:webhookPending` en panel y popup
- Known-issues del baseline inline (elementos sin nombre accesible)
- Validación del webhook antes de guardar (exige HTTPS/localhost)
- `aria-pressed`, `aria-expanded`, `aria-selected`, `aria-describedby`, `aria-live`
- Foco de teclado visible con `:focus-visible` en todos los controles

**Seguridad**
- CSP/HSTS/XFO/XCTO ausentes detectados por `webRequest.onHeadersReceived`
- Set-Cookie inseguro (sin `Secure`/`HttpOnly`) detectado por cabecera de respuesta
- Fugas de token/PII en requests (`Authorization`, JWT, cookies sensibles)
- Scanner pasivo de URLs (JWT, api-keys, mixed-content)

---

## Criterio de automejora continua

El criterio que rige el desarrollo de CharlyAudit es:

> **Toda mejora debe ser medible, verificable y no debe degradar ninguna garantía existente.**

### Los tres invariantes que nunca deben romperse

1. **Sin inyección silenciosa.** Ningún código llega a una pestaña sin que el usuario haya iniciado una grabación o reproducción en ella. El manifest no tiene `content_scripts`. Cualquier cambio que requiera inyección debe pasar por `ensureInjected()` con su handshake.

2. **Sin pérdida de evidencia.** Un evento capturado siempre llega al timeline con `cid` canónico y `tRel` monotónico. Un bundle exportado siempre está validado (`validateBundle`), redactado (`redactBundle`) y sellado (`stampIntegrity`). El webhook tiene reintentos.

3. **Sin regresión de módulos puros.** `report-engine.js`, `bundle-schema.js` y `exporters.js` son funciones puras. Cualquier cambio en ellas debe pasar los tests unitarios antes de tocar el SW o la UI.

### Proceso de mejora

Antes de implementar cualquier cambio, responder:
- ¿Rompe alguno de los tres invariantes?
- ¿Tiene una forma de verificarse sin navegador (test puro en Node)?
- ¿Afecta el contrato del bundle (schema, campos obligatorios)? → incrementar versión de schema.

Antes de empaquetar, ejecutar siempre:
```bash
# 1. Manifest válido
python3 -c "import json;json.load(open('manifest.json'))"

# 2. Sintaxis como módulo ES (igual que el navegador — no solo node --check)
for f in $(find src -name '*.js'); do
  node --input-type=module --check < "$f" 2>/dev/null || echo "FALLA: $f"
done

# 3. Service worker arranca sin errores con chrome mockeado

# 4. Tests de módulos puros (assembleReport, validateBundle, chunkBundle, computeKpis, fingerprintEvent)
```

### Escala de prioridad para nuevas mejoras

| Categoría | Criterio de inclusión |
|---|---|
| **P0 — Bug crítico** | Rompe un invariante, pierde evidencia o falla en el navegador |
| **P1 — Gap de integración** | Una característica ya implementada no fluye a la UI, al bundle o al contexto IA |
| **P2 — Fidelidad de evidencia** | Mejora la precisión de los datos (INP real, waterfall, replay) |
| **P3 — Base vectorial** | Mejora la calidad de lo que se ingesta (fingerprint, chunking, redacción) |
| **P4 — UX / DX** | Mejora la comprensión sin cambiar el modelo de datos |
| **Backlog** | Valioso pero no urgente; requiere validación en navegador real |

---

## Pendientes (por prioridad)

### P1 — Gaps de integración detectados en el análisis de v2.5.0
- **`src/content/content-script.js`** existe pero no está declarado en el manifest (no tiene efecto). Evaluar si su lógica (`page:getMetrics`) debe fusionarse en `src/qa/content.js` o eliminarse.
- **`web_accessible_resources`** añadido en v2.5.0 (faltaba para que `executeScript` con `world:MAIN` funcione en páginas de terceros en Chrome 116+).

### P2 — Fidelidad de evidencia
- Screenshot diff (pixel) via `captureVisibleTab` por paso de replay.
- Assertions de negocio inferidas del baseline en los exports (texto visible, conteos, estados).

### P3 — Ingesta
- Gzip del cuerpo (CompressionStream) antes del webhook.
- Modo webhook que envíe chunks con idempotencia por `contentHash`.
- Re-lectura de cookies tras `Set-Cookie` por request (correlación request↔cookie).

### P4 — UX
- Panel de ajustes más completo para perfil/webhook/dominios.
- `prefers-reduced-motion` en el banner de grabación.
- Contraste WCAG AA formal sobre la paleta personalizable.

### Backlog
- Shadow DOM/iframes en captura y replay.
- Plugin-health (self-diagnóstico: si la inyección o un observer falla, emitir evento).
- A11y del sitio auditado como categoría propia de auditoría (más allá del known-issue puntual).
- `installId` anónimo cross-sesión para correlación por equipo/dispositivo.
- Suite de tests de módulos puros en CI.
- Diff entre dos reportes exportable (baseline vs hoy).

---

## Changelog

| Versión | Cambios principales |
|---|---|
| **2.5.0** | Análisis y validación completa · `web_accessible_resources` para inyección on-demand · rebrand CharlyPlugin→CharlyAudit en lib · criterio de automejora continua |
| **2.4.1** | Asistente IA multi-proveedor (OpenWebUI/OpenAI/Gemini/Claude) · conversación multi-turno real con roles nativos · parámetros configurables · contexto performance con INP p98 y TBT por segmento |
| **2.4.0** | Performance 100%: INP real por interacción, TBT por navegación, waterfall completo · dedup de interaction-timing y resource-timing · navInfo enriquecida |
| **2.3.x** | UX (KPIs, webhook pending, known-issues baseline) · indicador de grabación · navegación fina · chunking+partitionKey · HMAC webhook · multi-proveedor base |
| **2.2.x** | Inyección 100% on-demand · grabación ligada a pestaña · ancla semántica · validación estricta del bundle · fingerprint semántico |
| **2.1.x** | Replay fiel · exportadores Cypress/Playwright · panel de auditoría · perf/vitals |
| **2.0.x** | MV3 · panel lateral · grabación/replay core |

---

## v2.5.1 — Rediseño de UI/UX del panel lateral

**Sistema de diseño reescrito desde cero.** El CSS pasó de 813 líneas acumuladas
por parches a 1166 líneas organizadas en 11 secciones con un sistema de tokens
coherente. No se rompió ningún binding de JS (todos los `id` permanecen intactos).

### Sistema de tokens
| Token | Descripción |
|---|---|
| `--c-*` | 10 colores funcionales (bg, surface, surface2, border, brand, brand-dim, danger, success, warn, text, muted) |
| `--t-xs/sm/base/lg` | 4 tamaños tipográficos (10/11.5/13/15px) en lugar de 11 |
| `--s-1 a --s-5` | Escala de espaciado ×4 (4/8/12/16/24px) en lugar de 12 valores arbitrarios |
| `--r-sm/md/lg/full` | 4 radios (6/8/12/999px) |
| Aliases retrocompatibles | `--ink`, `--panel`, `--brand`, etc. — el JS y la paleta del usuario siguen funcionando |

### Jerarquía de botones (antes 7 estilos, ahora 3)
- **Primario** (`.act--brand`): acción con consecuencia — Exportar, Guardar, Aplicar
- **Secundario** (`.act`): acción reversible o neutra — Importar, Cy, PW
- **Ghost** (`.act--ghost`, `.cache-btn`): acción de bajo perfil — KPIs ▾, Captura ▾

### Captura: de muro de campos a tres grupos legibles
`CAPTURA` · `PERFIL Y DOMINIOS` · `TELEMETRÍA` — cada uno con encabezado, borde
y espaciado propio. La jerarquía visual guía el ojo y reduce el tiempo de lectura.

### KPI cards: grid predecible
`repeat(3, 1fr)` fijo en lugar de `auto-fill, minmax(96px)` — 3 columnas en
300-419px, 4 columnas en ≥420px vía media query.

### Mobile-first: dos breakpoints
- `max-width: 340px`: nombre truncado, tabs más pequeños, KPIs en 2 columnas,
  `tl-srcbar` en columna
- `min-width: 420px`: KPIs en 4 columnas, burbujas de chat más anchas

### Accesibilidad
- `min-height: 28px` en todos los chips/scopes (touch target)
- `aria-selected` en pestañas `role="tab"`
- `role="list"` en `tl-list`
- `role="group"` en chips de filtro
- `prefers-reduced-motion` en el indicador de escritura
- Colores funcionales via tokens (nunca hardcodeados en componentes)
