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
- `prefers-reduced-motion` en el banner de grabación (la página auditada; ya
  aplicado al indicador "pensando" del asistente).
- Auditoría de contraste WCAG AA en el resto de combinaciones de la paleta
  personalizable (v2.5.3 corrigió el caso conocido de "Importado" deshabilitado;
  falta una pasada sistemática sobre todas las combinaciones posibles cuando el
  usuario personaliza colores en `#palette`).
- `--c-brand-dim` (hovers, bordes sutiles) no se deriva automáticamente del
  `--c-brand` personalizado — sigue fijo al valor por defecto. Calcular un tono
  derivado (o añadirlo como sexto control en la paleta) para consistencia total.

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
| **2.5.4** | Personalización de colores: la paleta apuntaba a alias que ya nadie leía, ahora apunta a los tokens canónicos · footer del popup sin "CharlyAudit" duplicado · CAPTURA y AJUSTES (perfil/dominios/telemetría) separados en paneles y botones independientes |
| **2.5.3** | Auditoría de los 6 hallazgos visuales de v2.5.1: 4 ya resueltos (verificados), 2 corregidos (toast solapado con dock envuelto, overflow del header a 280px) |
| **2.5.1** | Rediseño de UI/UX del panel lateral: sistema de tokens, jerarquía de 3 botones, grupos semánticos en captura, KPI grid predecible, mobile-first, accesibilidad |
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

---

## v2.5.3 — Auditoría y cierre de los 6 hallazgos de UI/UX

Se revisaron los seis problemas del reporte visual de v2.5.1 con verificación
empírica en navegador (Playwright) antes de tocar código, para no corregir
nada que ya estuviera resuelto ni dejar sin corregir algo real.

### Ya estaban corregidos (verificado, sin cambios adicionales)
1. **KPIs no cerraban** — `.tl-kpis[hidden] { display: none; }` ya tenía mayor
   especificidad (clase+atributo) que `.tl-kpis { display: grid }` y gana
   correctamente. Medido: `display: none` tras cerrar. Sin acción.
2. **Modales no céntricos** — `dialog.settings { margin: auto; }` ya restauraba
   explícitamente lo que el reset global (`* { margin: 0 }`) le quitaba al
   `margin: auto` nativo de `<dialog>`. Medido: centrado correcto en 380px y 280px.
3. **Toast detrás de un modal abierto** — ya resuelto con `popover="manual"` +
   `showPopover()`/`hidePopover()`, que saca al toast del flujo normal y lo pone
   en la capa superior (top layer), por encima de cualquier `<dialog>` sin
   importar `z-index`. Confirmado con captura de pixel: el toast se ve sobre
   el modal abierto.
4. **Contraste del texto "Importado" (deshabilitado)** — ya no usa `opacity`
   sobre `--c-muted` (que caía a 1.78:1, ilegible); usa un color sólido
   precalculado. Medido: 5.26:1 sobre `--c-bg`, 4.78:1 sobre `--c-surface`
   (ambos superan el mínimo AA de 4.5:1).

### Corregidos en esta versión
5. **Toast podía solaparse con el dock envuelto (paneles angostos)** — el
   mecanismo (`--dock-h` vía `ResizeObserver`) ya existía en CSS y JS pero
   tenía dos fallas:
   - `watchDockHeight()` estaba definida pero **nunca se invocaba** desde
     `init()` → `--dock-h` quedaba sin valor real. **Fix:** se añadió la
     llamada en `init()`.
   - Al adoptar la Popover API para el fix #3, el toast heredó el `top: 0`
     por defecto de un popover sin anclaje, que ganaba sobre `bottom` al no
     haber un `top` explícito en el CSS del autor. **Fix:** `top: auto;`
     explícito en `.toast`.
   - Verificado: `--dock-h` ahora resuelve a `108px` (altura real medida),
     el toast se posiciona justo encima del dock sin superposición.
6. **Header desbordaba 2px en el ancho mínimo (280px)** — `.tabs` no tenía
   `min-width: 0`, así que no podía comprimirse por debajo de su ancho de
   contenido dentro del `.bar` flex, forzando overflow horizontal aun con
   `.bar__name` ya truncado. **Fix:** `min-width: 0; flex-shrink: 1` en
   `.tabs`, más ajuste fino de padding/gap/tamaño de ícono en el breakpoint
   `max-width: 340px`. Verificado: `scrollWidth` del header = 280px exactos,
   sin overflow.

### Nota de proceso
Antes de aplicar cualquier corrección se reverificó cada uno de los 6 puntos
contra el código real (no contra la memoria del reporte anterior). Cuatro de
seis ya estaban resueltos correctamente; solo dos necesitaban trabajo real.
Aplicar "fixes" sobre código que ya funciona introduce riesgo sin beneficio,
así que se documenta la verificación en vez de tocar lo que no estaba roto.

---

## v2.5.4 — Personalización de colores, footer duplicado, separación de formularios

Tres hallazgos reportados tras revisión del producto final. Los tres tenían
causa raíz real (no falsos positivos esta vez) y se corrigieron.

### 1. La personalización de colores no se aplicaba

**Causa raíz:** el diálogo "Personalizar paleta" y su lógica en `setupPalette()`
seguían apuntando a los alias legacy del sistema de tokens (`--brand`, `--ink`,
`--panel`, `--line`, `--text`), que desde el rediseño de v2.5.1 son solo
`var(--c-*)` de un único sentido — sirven para que CSS *viejo* siga funcionando,
pero **nada los lee de vuelta**. Todos los componentes reales (`.act--brand`,
fondos, texto) leen los tokens canónicos `--c-brand`, `--c-bg`, `--c-surface`,
`--c-border`, `--c-text` directamente. Al guardar una paleta, el JS hacía
`setProperty('--brand', ...)`, que no tenía ningún efecto visual porque ese
alias no alimenta a `--c-brand` en sentido inverso.

**Fix:** `VARS` en `setupPalette()` y los `data-var` del diálogo ahora apuntan
a los tokens canónicos. Verificado: cambiar el color de marca a naranja
(`#f5a623`) y guardar recolorea el botón "Exportar" de `rgb(91,108,255)` a
`rgb(245,166,35)` de inmediato.

**Nota:** `--c-brand-dim` (usado en hovers y bordes sutiles) no se deriva
automáticamente del nuevo `--c-brand` — sigue siendo un valor fijo. No es el
bug reportado (los botones y superficies principales ya recolorean
correctamente), pero queda anotado como mejora futura en pendientes.

### 2. "CharlyAudit" duplicado en el pie del popup

**Causa raíz:** el HTML tenía `<span id="version">CharlyPlugin</span>` (texto
de relleno con el nombre de marca antiguo) seguido de `<span>CharlyAudit</span>`
estático. El JS sobrescribe el primer span con `"CharlyAudit v" + version` en
tiempo de ejecución, pero el segundo span nunca se tocó — resultado:
"CharlyAudit v2.5.3 • CharlyAudit".

**Fix:** se eliminó el `<span>` estático duplicado y el separador `•`. El pie
ahora muestra solo lo que el JS ya generaba correctamente: "CharlyAudit v2.5.4".

### 3. CAPTURA y PERFIL Y DOMINIOS eran el mismo formulario

**Antes:** un único botón "Captura ▾" abría un panel con tres grupos visuales
(Captura, Perfil y dominios, Telemetría) dentro de la misma sección
desplazable — visualmente separados por líneas, pero funcionalmente un solo
formulario con un solo estado abierto/cerrado.

**Fix:** se dividió en dos secciones y dos botones independientes en la barra
de acciones:
- **`Captura ▾`** — solo "qué capturar durante la grabación" (selectores a
  enmascarar, variables globales, funciones a interceptar, Aplicar). Config
  de sesión.
- **`Ajustes ▾`** — perfil, dominios permitidos y telemetría (webhook, modo de
  envío, auto-inicio). Config persistente, independiente de una grabación en
  curso.

Cada botón carga y muestra solo sus propios campos; abrir uno no afecta al
otro. Todos los `id` de los campos internos se conservaron exactamente
(ningún binding de JS se rompió). Verificado: abrir Captura no muestra
Ajustes y viceversa; los valores de dominios/perfil cargan correctamente en
su panel dedicado.

---

## v2.5.5 — Popup simplificado, pestaña Reporte, fuentes independientes

Reestructuración de flujo: import/export/replay quedan centralizados y cada
fuente de datos (temporal/importado) se gestiona de forma independiente.

### 1. Popup

**1.1/1.2 — Exportar como dropdown + acceso a Auditoría.** La fila de 3
botones (JSON/Cypress/Playwright) se reemplazó por un único `<select>`
"Exportar ▾" con las 3 opciones (columna 1). "Copiar JSON" y "Vaciar" se
eliminaron; en su lugar, columna 2 tiene **"Ver en Auditoria"**, que abre el
panel lateral directo en esa pestaña (usa una bandera transitoria
`charlyaudit:openTab` en `chrome.storage.local`, que `init()` del panel lee
una vez al arrancar y borra — mecanismo genérico, reutilizable para futuras
aperturas dirigidas).

**1.3 — Sección Replay eliminada.** Importar/Reproducir/Detener/Velocidad ya
no existen en el popup; toda la reproducción vive en el panel lateral
(Auditoría → Repetición), con el mismo botón "Ver en Auditoria" como puente.

### 2. Panel lateral

**2.1 — Nueva pestaña "Reporte".** Único lugar del panel donde se puede:
- Descargar la **sesión temporal** (JSON completo / Cypress / Playwright) —
  usa `exportBundle`/`exportCypress`/`exportPlaywright`, igual que antes pero
  reubicado.
- Ver un resumen de la **sesión importada** (eventos, URL, versión de quien
  exportó) y vaciarla de forma independiente.
- **Importar** (única entrada de archivo del panel completo — se eliminó
  `#tl-import` de Auditoría).
- La importación solo acepta el JSON propio del reporte (`report.timeline`
  validado por el SW vía `validateBundle`); nunca aceptó ni aceptará
  Cypress/Playwright, que son scripts de prueba, no datos de reporte.
- Exportar/enviar el reporte ya **no existe en ningún otro lugar** del panel
  (se quitaron `#exp-bundle`/`#exp-cy`/`#exp-pw` de la barra de Auditoría).

**Análisis del "reporte completo" (2.1.6).** Se auditó `buildBundle()` en el
service worker antes de dar por completa la tarea. Ya incluye, sin huecos
relevantes: `schema` versionado, metadata de extensión, `settings`+`capture`
del exportador (solo referencia, nunca se aplica al importar), el `report`
íntegro (metadata + timeline con `cid`/`tRel`/`fp` por evento), telemetría de
la última repetición (`trace`+`resumen`), errores destacados, conteos, KPIs
agregados (`computeKpis`) e integridad (`contentHash`, `ultimoCid`). Es el
mismo artefacto que se firma y envía por webhook — no hay una versión "más
completa" oculta en otro lugar del código. Un hallazgo no crítico para
`pendientes`: los *known-issues* de accesibilidad (elementos sin nombre
accesible) hoy solo se calculan al renderizar la tabla en el panel — no se
persisten como una lista resumen en el bundle. Ver pendientes.

**2.2 — Vaciar por fuente, no global.** Antes, el botón "Vaciar" de Auditoría
llamaba siempre a la misma acción (`clear`, solo temporal) sin importar qué
fuente estuviera activa — si estabas viendo "Importado", igual vaciaba la
grabación temporal (o simplemente no tenía efecto sobre el importado, que
persistía para siempre salvo cerrar sesión). Ahora el handler bifurca por
`source`:
- **Temporal** → `clear` (solo `qa:timeline`/`qa:meta`).
- **Importado / Repetición** → nueva acción de SW **`clearImported`**, que
  vacía `qa:replay` y `qa:replayJob` (detiene cualquier replay activo primero)
  — el reporte y su repetición desaparecen como si el archivo nunca se
  hubiera cargado, sin tocar la grabación temporal.

El botón de Importar se eliminó de Auditoría (solo vive en Reporte, 2.2.3).

Efecto colateral corregido de paso: el reporte importado ahora se **hidrata
desde `storage`** al abrir el panel (antes solo vivía en una variable en
memoria y se perdía al cerrar y reabrir el panel, aunque el dato seguía en el
SW).

**2.3 — Controles de reproducción en Repetición.** La Velocidad (antes solo
en el popup, ahora eliminada de ahí) se agregó como `<select>` visible
únicamente cuando la fuente activa es "Repetición". Al pulsar Reproducir, la
vista cambia automáticamente a esa fuente para ver el avance en vivo
(`replay-progress` muestra `índice/total` en tiempo real, reutilizando
`refreshReplayState()`). Reproducir/Detener ya vivían en la barra de acciones
de Auditoría (accesibles sin importar la sub-pestaña); ahora quedan
explícitamente conectados al contexto de Repetición.

### Validado
Suite funcional en navegador real (Playwright, sin errores de página):
3 pestañas presentes y conmutables; importar desde Reporte habilita
"Importado" en Auditoría y refleja los datos; Vaciar en Temporal no afecta al
importado y viceversa; controles de repetición se muestran solo en esa
fuente; velocidad se aplica y la vista cambia a Repetición al reproducir;
cero elementos huérfanos de import/export en Auditoría; cero IDs referenciados
en JS ausentes del HTML (y viceversa) en panel lateral y popup.

## Pendientes (por prioridad)

### P2 — Fidelidad de evidencia
- Screenshot diff (pixel) vía `captureVisibleTab` por paso de replay.
- Assertions de negocio inferidas del baseline en los exports.
- **Nuevo:** persistir un resumen de *known-issues* de accesibilidad
  (elementos sin nombre accesible) dentro de `buildBundle`, no solo calculado
  al renderizar en el panel — para que viaje también en el JSON exportado.

### P3 — Ingesta
- Gzip del cuerpo (CompressionStream) antes del webhook.
- Modo webhook que envíe chunks con idempotencia por `contentHash`.
- Re-lectura de cookies tras `Set-Cookie` por request.

### P4 — UX / Accesibilidad
- Panel de ajustes más completo para perfil/webhook/dominios.
- `prefers-reduced-motion` en el banner de grabación de la página auditada.
- Auditoría de contraste WCAG AA sobre el resto de combinaciones de paleta.
- `--c-brand-dim` no se deriva automáticamente del `--c-brand` personalizado.
