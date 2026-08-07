# CharlyAudit

**Versión actual: 2.5.8b**

Suite de QA, session replay y auditoría de seguridad para Chrome (MV3).
Convierte cada sesión real de usuario en evidencia accionable y verificable
para QA, Performance y Security, con ingesta directa a bases vectoriales.

---

## Índice

1. [Arquitectura](#arquitectura)
2. [Estado funcional actual](#estado-funcional-actual)
3. [Criterio de automejora continua](#criterio-de-automejora-continua)
4. [Pendientes por prioridad](#pendientes-por-prioridad)
5. [Historial de versiones](#historial-de-versiones)

---

## Arquitectura

```
manifest.json           MV3 · sin content_scripts declarativos · inyección on-demand
src/background/         Service worker (orquestador)
  service-worker.js     Ciclo de vida + menú contextual; importa qa/background
src/lib/CharlyAPI.js    API de abstracción Chrome (storage, menús, tabs...);
                        superficie amplia, solo un subconjunto en uso activo
src/lib/reactive-store.js  Estándar de UI reactiva compartido por popup y
                        panel lateral: Store (pub/sub), Poller (polling
                        centralizado), captura/restauro de filas expandidas
src/qa/
  background.js         Ciclo de grabación, lifecycle de pestaña, telemetría,
                        webhook, cola serializada de replayJob (replayJobChain)
  content.js             Puente DOM↔SW (captura eventos, replay, banner de grabación)
  injected.js            MAIN world: perf/INP/waterfall, red, console, workers, globals
  report-engine.js       assembleReport, fingerprintEvent, attachInteractionLatency
  bundle-schema.js       validateBundle, stampIntegrity, redactBundle, chunkBundle,
                        computeKpis, partitionKeyOf, hmacHex
  exporters.js           toCypress, toPlaywright
  playwright-import.js   Interprete de un subconjunto de Playwright
                        (goto/click/fill/type/press/waitForTimeout) — NO
                        ejecuta Node/Playwright real (imposible en una
                        extension); traduce a pasos reproducibles por el
                        motor de replay existente
src/popup/               Popup de control rápido (grabar, exportar, atajo a Auditoría)
src/sidepanel/
  sidepanel.html/js/css  Panel lateral: 3 pestañas — Asistente / Auditoría / Reporte
  lib/
    openwebui-client.js  Cliente multi-proveedor (OpenWebUI/OpenAI/Gemini/Claude/custom)
    context-bridge.js    Puente de contexto QA→IA (12 scopes, budget, digest,
                        fuente Temporal/Importado)
    chat-cache.js        Caché de conversación en localStorage
    markdown.js          Render Markdown seguro (sin eval)
```

**Las tres pestañas del panel lateral:**
| Pestaña | Función |
|---|---|
| **Asistente** | Chat con IA sobre la sesión; selector de fuente Temporal/Importado; 12 ámbitos de contexto |
| **Auditoría** | Grabar, ver el timeline evento a evento, y reproducir (Temporal / Importado / Repetición) |
| **Reporte** | Único lugar para importar un archivo, descargar la sesión temporal (JSON/Cypress/Playwright) y gestionar el reporte importado |

---

## Estado funcional actual

### ✅ Captura
- Inyección 100% on-demand (sin `content_scripts` declarativos; el SW orquesta todo vía `ensureInjected()`)
- Grabación ligada a una pestaña: `recordingId`, `tabId`, `windowId`, `startUrl`, `startedAtMs`
- Grabación única (rechaza una segunda pestaña simultánea)
- Stop automático al cerrar la pestaña grabada (con telemetría `onclose`)
- Banner visible en la página grabada (Shadow DOM, no interfiere con el host)
- Badge del ícono anclado a la pestaña grabada (muestra dominio)
- Metadata de entorno al iniciar: CPU, RAM, OS, navegador, ventana, cookies (solo flags)
- Re-muestreo periódico (1/min) + dirigido por navegación (debounce 2s)
- `recState` rehidratado tras reinicio del SW (robustez MV3)
- Clicks, doble click, click central, drag&drop, tecleo coalescido (con atajos y modificadores), scroll, inputs vía `change`+`blur` con **validación de foco real**: si el evento llega desde un nodo distinto al campo enfocado (widgets personalizados como comboboxes o date-pickers), se usa el elemento realmente enfocado (rastreado por `focusin`) como fuente de verdad

### ✅ Performance (100% real, no aproximado)
- INP real por interacción: `PerformanceObserver("event")` → `interaction-timing` con `tsEvent` (reloj epoch) → correlación 1-a-1 con el click/input exacto vía `attachInteractionLatency`
- TBT real por navegación: `flushTbtSegment()` en cada `emitRoute` → `tbtSegmentMs` por segmento → `tbtPeorSegmento` en KPIs
- Waterfall completo: `waterfallFor()` por fetch/XHR + observer `"resource"` para recursos pasivos; fases DNS/TCP/TTFB/descarga, tamaño, protocolo, caché
- Dedup de `interaction-timing` por `interactionId` (evita triple emisión pointerdown+up+click)
- Dedup de `resource-timing` por URL+startTime (evita duplicados con `buffered:true`)
- `navInfo` enriquecida: tipo, referrer, redirects, TTFB, domListo, cargaMs, ttiApproxMs, docKb

### ✅ Datos y trazabilidad
- `cid` canónico determinista (`recordingId#seq`) por evento
- `tRel` monotónico por sesión (sobrevive reinicios del SW)
- `fp` fingerprint semántico por tipo (agrupa errores/endpoints/INP equivalentes entre sesiones)
- Esquema versionado `charlyaudit/audit-bundle@1` con migración y validación estricta
- `stampIntegrity`: `contentHash` FNV-1a + `ultimoCid` + nº eventos (idempotencia en ingesta)
- Redacción canónica pre-embedding (email, JWT, Bearer, api-keys, tarjetas, hashes)
- `chunkBundle`: unidades indexables por tipo con `partitionKey` por dominio/tenant
- `computeKpis`: INP p98, TBT peor segmento, red, seguridad por severidad, fidelidad de replay — función **pura**, calculable client-side o server-side sobre cualquier reporte (temporal o importado)
- HMAC-SHA256 del payload webhook (`X-CharlyAudit-Signature`)
- Reintentos con backoff exponencial (2→4→8→16→30 min, máx. 5) vía `replayJobChain` serializado — elimina condiciones de carrera al escribir `K.replayJob` desde múltiples mensajes concurrentes (`replayProgress`, `replayTrace`, `startReplay`, `stopReplay`, `clearImported`)

### ✅ Replay y Repetición
- Reproducir/Detener/Velocidad viven **exclusivamente** dentro del bloque de Repetición (Auditoría), visibles solo con esa fuente activa
- La vista de Repetición se auto-refresca sola (timer periódico, sin necesidad de cambiar de pestaña) mientras hay un replay en curso
- KPIs de Repetición se calculan sobre el reporte **realmente en reproducción** (el importado + su traza), nunca mezclados con el temporal
- Telemetría por paso: efecto esperado (grabación) vs. observado (replay), con diff estructural del DOM, capturada para **todo tipo de paso, incluido scroll**
- El denominador de progreso ("reproduciendo X/Y") refleja los pasos reales reproducibles, no el total de eventos del timeline
- Resolución de código bajo demanda: cada frame del stack de un error tiene un botón "Ver código" que resuelve el snippet real (con soporte de source maps) vía el canal `getSource` → `qa-source` → `get-source`
- Checkbox persistido: recargar (o no) el sitio en la URL de inicio al reproducir — útil para conservar el estado actual de la sesión en vez de forzar una navegación
- Reproducción de un Playwright importado (subconjunto interpretado de `goto/click/fill/type/press/waitForTimeout`) reutilizando el mismo motor de replay — ver `src/qa/playwright-import.js`

### ✅ Asistente IA
- **Conversación multi-turno real**: `messages: [{role:"system",...},{role:"user",...},{role:"assistant",...},...,{role:"user"}]`; el historial viaja como roles nativos, no como texto incrustado
- **Multi-proveedor**: OpenWebUI (propio), OpenAI/ChatGPT, Google Gemini, Anthropic Claude, endpoint personalizado. Claude separa `system` en su campo propio; Gemini usa el endpoint OpenAI-compatible
- **Parámetros configurables**: temperatura, tokens máximos, turnos de historial
- **Selector de fuente Temporal/Importado**: el asistente puede analizar la grabación en curso o un reporte importado, sin mezclar datos entre ambos (caché de contexto con clave por fuente)
- **12 ámbitos de contexto**, todos con conteo visible y actualizado según la fuente activa: Resumen, Errores, Red, Consola, Rutas, Funciones, Variables, Interacción, Estructura, Repetición, Performance, Seguridad
- El ámbito Resumen incluye entorno de grabación (CPU/RAM/navegador), identidad de sesión (`recordingId`/`startUrl`) y KPIs agregados completos

### ✅ Reporte (pestaña dedicada)
- Único lugar del panel para **importar** un archivo (`.json` propio, nunca Cypress/Playwright)
- Único lugar para **descargar o enviar** el reporte: JSON completo (bundle canónico validado/redactado/sellado), Cypress o Playwright — siempre de la sesión temporal
- Gestión del reporte importado: resumen (eventos, URL, versión del exportador) y vaciado independiente (`clearImported`, no afecta la grabación temporal)
- El resumen de ambas sesiones (temporal e importada) se refresca solo mientras la pestaña está activa, sin necesidad de salir y volver a entrar

### ✅ UI reactiva (`src/lib/reactive-store.js`)
- Estándar único para toda la UI (popup y panel lateral, sin excepción): `Store` (estado observable con no-op si el valor no cambió) y `Poller` (temporizador centralizado, pausa sola cuando la pestaña no es visible)
- Ningún render toca el DOM sin que el dato subyacente haya cambiado realmente — se compara una firma ligera de la página visible antes de reconstruir
- Las filas expandibles (`.tl-row.is-open`) sobreviven a un re-render mediante clave estable (`cid`/`seq`/índice), incluso cuando llegan datos nuevos en el mismo ciclo
- Límite visual con paginación por scroll: 150 filas iniciales, crecen de 150 en 150 al acercarse al final de la lista, en vez de renderizar cientos o miles de filas de una vez

### ✅ UX y accesibilidad
- Sistema de diseño con tokens (`--c-*`), jerarquía de 3 niveles de botón, mobile-first (breakpoints 340px/420px)
- Panel de KPIs colapsable (11 métricas: LCP/CLS/INP/TBT/errores/red/seguridad/replay)
- Indicador `qa:webhookPending` en panel y popup
- Known-issues del baseline inline (elementos sin nombre accesible)
- Validación del webhook antes de guardar (exige HTTPS/localhost)
- `aria-pressed`, `aria-expanded`, `aria-selected`, `aria-describedby`, `aria-live`
- Foco de teclado visible con `:focus-visible` en todos los controles
- Personalización de paleta de colores (tokens canónicos, aplica en vivo)

### ✅ Seguridad
- CSP/HSTS/XFO/XCTO ausentes, detectados por `webRequest.onHeadersReceived`
- Set-Cookie inseguro (sin `Secure`/`HttpOnly`) detectado por cabecera de respuesta
- Fugas de token/PII en requests (`Authorization`, JWT, cookies sensibles)
- Scanner pasivo de URLs (JWT, api-keys, mixed-content)

---

## Criterio de automejora continua

El criterio que rige el desarrollo de CharlyAudit:

> **Toda mejora debe ser medible, verificable y no debe degradar ninguna garantía existente.**

### Los tres invariantes que nunca deben romperse

1. **Sin inyección silenciosa.** Ningún código llega a una pestaña sin que el usuario haya iniciado una grabación o reproducción en ella. El manifest no tiene `content_scripts`. Cualquier cambio que requiera inyección debe pasar por `ensureInjected()` con su handshake.

2. **Sin pérdida de evidencia.** Un evento capturado siempre llega al timeline con `cid` canónico y `tRel` monotónico. Un bundle exportado siempre está validado (`validateBundle`), redactado (`redactBundle`) y sellado (`stampIntegrity`). El webhook tiene reintentos. Las escrituras concurrentes sobre una misma clave de storage (p. ej. `K.replayJob`) deben serializarse — nunca asumir que dos `get→modificar→set` independientes son seguros en paralelo.

3. **Sin regresión de módulos puros.** `report-engine.js`, `bundle-schema.js` y `exporters.js` son funciones puras. Cualquier cambio en ellas debe pasar los tests unitarios antes de tocar el SW o la UI. Cuando una vista de la UI necesita KPIs o un reporte, debe quedar explícito **de qué fuente** (temporal vs. importada) — nunca asumir una por defecto sin verificarlo contra el selector activo.

### Proceso de mejora

Antes de implementar cualquier cambio, responder:
- ¿Rompe alguno de los tres invariantes?
- ¿Tiene una forma de verificarse sin navegador (test puro en Node)?
- ¿Afecta el contrato del bundle (schema, campos obligatorios)? → incrementar versión de schema.
- ¿Hay más de una "fuente de datos" posible (temporal/importado)? → verificar que la UI lee la fuente correcta, no una fija por defecto.

Antes de empaquetar, ejecutar siempre:
```bash
# 1. Manifest válido
python3 -c "import json;json.load(open('manifest.json'))"

# 2. Sintaxis como módulo ES (igual que el navegador — no solo node --check)
for f in $(find src -name '*.js'); do
  node --input-type=module --check < "$f" 2>/dev/null || echo "FALLA: $f"
done

# 3. Service worker arranca sin errores con chrome mockeado

# 4. Tests de módulos puros (assembleReport, validateBundle, chunkBundle,
#    computeKpis, fingerprintEvent)

# 5. Verificación cruzada de IDs (todo id usado en JS existe en el HTML y viceversa)
```

### Escala de prioridad para nuevas mejoras

| Categoría | Criterio de inclusión |
|---|---|
| **P0 — Bug crítico** | Rompe un invariante, pierde evidencia o falla en el navegador |
| **P1 — Gap de integración** | Una característica ya implementada no fluye a la UI, al bundle o al contexto IA |
| **P2 — Fidelidad de evidencia** | Mejora la precisión de los datos (INP real, waterfall, replay) |
| **P3 — Ingesta / base vectorial** | Mejora la calidad de lo que se envía o almacena (fingerprint, chunking, redacción, webhook) |
| **P4 — UX / Accesibilidad / DX** | Mejora la comprensión o el mantenimiento sin cambiar el modelo de datos |
| **Backlog** | Valioso pero no urgente; suele requerir validación extensa en navegador real |

---

## Pendientes por prioridad

### P2 — Fidelidad de evidencia
- Screenshot diff (pixel) vía `captureVisibleTab` por paso de replay.
- Assertions de negocio inferidas del baseline en los exports (texto visible, conteos, estados).
- Persistir un resumen de *known-issues* de accesibilidad (elementos sin nombre accesible) dentro de `buildBundle` — hoy solo se calcula al renderizar la tabla en el panel, no viaja en el JSON exportado.

### P3 — Ingesta / base vectorial
- Gzip del cuerpo (CompressionStream) antes de enviar el webhook.
- Modo webhook que envíe chunks con idempotencia por `contentHash` (la acción `exportChunks` ya produce el artefacto; falta el modo de envío automático).
- Re-lectura de cookies tras `Set-Cookie` por request (correlación request↔cookie).

### P4 — UX / Accesibilidad / DX
- Panel de ajustes más completo para perfil, webhook y dominios.
- `prefers-reduced-motion` en el banner de grabación de la página auditada.
- Auditoría sistemática de contraste WCAG AA sobre el resto de combinaciones posibles de la paleta personalizable.
- `--c-brand-dim` (hovers, bordes sutiles) no se deriva automáticamente del `--c-brand` personalizado — sigue fijo al valor por defecto. Calcular un tono derivado, o añadirlo como sexto control en la paleta.
- El `digest()` del asistente (resumen en lenguaje natural que encabeza el contexto) aún no incorpora KPIs de performance/seguridad en su texto — solo cuenta eventos, duración, errores y red fallida.
- `ctx-src-imported` hace dos llamadas en cascada a `getReport("imported")` (una para verificar disponibilidad, otra dentro de `refreshState()`). Funciona correctamente pero es una ronda de red de más; se podría cachear el resultado de la primera.
- `CharlyAPI.js` mantiene una superficie amplia de métodos (tabs, ventanas, bookmarks, historial, cookies, descargas, debugger) sin consumidor activo más allá de `storageGet/Set`, `clearContextMenus/createContextMenu` y `notify`. No son código muerto en el sentido de inalcanzable — son métodos correctos y documentados — pero antes de construir una función nueva sobre ellos conviene confirmar el beneficio concreto en vez de asumir que "ya está soportado".
- El `Store` de `reactive-store.js` se usa hoy para centralizar el polling (`Poller`) y para la detección de cambios en las listas de Auditoría/Repetición; el resto del estado (grabación en curso, webhook pendiente, KPIs) sigue actualizándose por llamada directa dentro del callback del `Poller`, no por suscripción a `Store`. Migrar esos casos a `Store.subscribe()` sería más consistente con el estándar, aunque hoy no presentan el bug que sí tenía la lista de eventos.
- La paginación por scroll de `tl-list` solo *agrega* filas al hacer scroll (nunca libera las que salen de vista). Para reportes de varios miles de eventos, una ventana verdaderamente virtualizada (renderizar solo lo visible) sería más robusta que el tope actual de 1000 eventos + páginas de 150.
- El parser de Playwright (`playwright-import.js`) reconoce llamadas dentro de una sola línea; no soporta llamadas partidas en varias líneas, template literals con interpolación (`` `${variable}` ``), ni la API moderna de locators semánticos (`page.getByRole(...)`, `page.getByText(...)`, `page.getByLabel(...)`) — solo `page.locator(selectorCSS)` y las formas legadas (`page.click(selector)`, etc.). Es una limitación conocida y documentada en el propio módulo, no un intento fallido de cobertura total.
- `press`/`type` sin selector (`page.keyboard.press(...)`, `page.keyboard.type(...)` — acciones de teclado globales, sin apuntar a un elemento) no se traducen a un paso: el motor de replay actual siempre necesita un selector destino.
- El checkbox "Recargar el sitio al reproducir" vive físicamente en la pestaña Reporte, pero también controla el botón "▶ Playwright" de la barra de acciones y del popup — la relación no es obvia por la sola ubicación de los controles; un tooltip o nota cruzada ayudaría a descubrirla.

### Backlog
- Shadow DOM / iframes en captura y replay.
- Plugin-health (self-diagnóstico: si la inyección o un observer falla, emitir un evento propio).
- A11y del sitio auditado como categoría propia de auditoría (más allá del known-issue puntual ya existente).
- `installId` anónimo cross-sesión, para correlación por equipo/dispositivo.
- Suite de tests de los módulos puros integrada en CI.
- Diff entre dos reportes exportable (baseline vs. hoy).

---

## Historial de versiones

### Resumen rápido

| Versión | Foco principal |
|---|---|
| **2.5.8b** | Fix crítico: Repetición perdía toda su traza en grabaciones con scroll · validación de foco en captura de formularios · importación e interpretación de Playwright · checkbox persistido para no recargar el sitio al reproducir |
| **2.5.8a** | Estándar de UI reactiva (Store + Poller) en popup y panel lateral: corrige que un detalle expandido en Repetición se cerrara solo cada ciclo de refresco; paginación por scroll como límite visual |
| **2.5.8** | Auditoría de huérfanos: subsistema de mensajería sin punto de entrada eliminado, resolución de código bajo demanda conectada a la UI, pestaña Reporte con auto-refresco |
| **2.5.7** | Repetición sin auto-refresco, KPIs y contexto del asistente leyendo la fuente equivocada |
| **2.5.6** | Race condition que vaciaba Repetición · contexto del asistente completo (12/12 ámbitos) · Reproducir/Detener migrados a Repetición |
| **2.5.5** | Popup simplificado · pestaña Reporte nueva · Temporal/Importado gestionados de forma independiente |
| **2.5.4** | Personalización de colores rota · footer duplicado · Captura y Ajustes separados |
| **2.5.3** | Auditoría de 6 hallazgos visuales (4 ya resueltos, 2 corregidos) |
| **2.5.1** | Rediseño completo de UI/UX del panel lateral (sistema de tokens, mobile-first) |
| **2.5.0** | `web_accessible_resources` para inyección on-demand · rebrand CharlyPlugin→CharlyAudit · criterio de automejora continua |
| **2.4.1** | Asistente IA multi-proveedor · conversación multi-turno real con roles nativos |
| **2.4.0** | Performance 100% real: INP por interacción, TBT por navegación, waterfall completo |
| **2.3.x** | UX (KPIs, webhook pending, known-issues) · navegación fina · chunking+partitionKey · HMAC webhook |
| **2.2.x** | Inyección 100% on-demand · grabación ligada a pestaña · ancla semántica · fingerprint semántico |
| **2.1.x** | Replay fiel · exportadores Cypress/Playwright · panel de auditoría · perf/vitals |
| **2.0.x** | MV3 · panel lateral · núcleo de grabación/replay |

Detalle completo de cada versión desde 2.5.1 (documentación exhaustiva empezó
en ese punto; versiones anteriores solo tienen el resumen de la tabla).

---

### v2.5.8b — Repetición perdía la traza en grabaciones con scroll, validación de foco, importación de Playwright, checkbox de recarga

Cinco puntos marcados como críticos de prioridad alta, a partir de un reporte
real: se cargó un reporte importado (246 eventos, página de documentación
larga) y al reproducirlo la navegación avanzaba pero el panel de Repetición
quedaba vacío — "0 pasos, 0 inconsistencias" pese al banner de la página
mostrando progreso real.

**Hallazgo crítico — `reportTrace()` se omitía por completo en pasos de scroll.**
*Causa raíz:* en el bucle de reproducción de `content.js`, `reportProgress()`
(que informa el avance) se ejecutaba sin condición para cada paso, pero
`reportTrace()` (que llena la telemetría que ve "Repetición") vivía DENTRO
de un `if (e.type !== "scroll")`. Una grabación mayormente de scroll — muy
común en páginas de documentación largas — hacía avanzar el índice
correctamente mientras la traza se quedaba permanentemente vacía. Coincide
exactamente con el síntoma reportado.
*Fix:* `reportTrace()` ahora se ejecuta para **todo** tipo de paso; solo se
omite la costosa observación de consecuencias (`observeConsequences`, que no
aporta señal útil en scroll) para ese tipo específico, con valores neutros
por defecto. De paso se corrigió una inconsistencia relacionada: el panel
mostraba `índice/total-de-eventos-del-timeline` (p. ej. "4/246") mientras el
banner de la propia página siempre mostró correctamente `índice/pasos-
reales` (p. ej. "4/8") — ahora ambos coinciden, vía un nuevo campo
`job.total` comunicado desde `content.js`.
*Validado:* traza simulada mayormente-scroll contra el código real de render
— pasó de "0 pasos" a "4 pasos" con las filas visibles, y el denominador de
"4/246" a "4/8".

**Fidelidad de captura — validación de foco real en formularios.**
Se auditó exhaustivamente la captura existente (clicks, doble click, click
central, drag&drop, tecleo coalescido con atajos, scroll, navegación,
intercepción de funciones/callbacks vía `patchedFunctions`) y se confirmó
sólida en su mayoría. El gap real encontrado: `captureInput()` (disparado
por `change`/`blur`) confiaba ciegamente en `event.target`, que en widgets
personalizados (comboboxes, date-pickers estilizados) puede no coincidir
con el campo que el usuario realmente enfocó. Ahora se rastrea el elemento
enfocado vía `focusin` (`local.lastFocusedEl`) y se usa como fuente de
verdad cuando el evento llega desde un nodo distinto.

**Importación de Playwright (con límite técnico honesto).** Ejecutar un
script Playwright real es imposible dentro de una extensión de Chrome —
Playwright depende de APIs de Node.js (`chromium.launch()`, etc.) que no
existen en un navegador. En su lugar, `src/qa/playwright-import.js` (módulo
puro, con tests) interpreta un subconjunto reconocible del script —
`page.goto/click/dblclick/fill/type/press/waitForTimeout/waitForSelector` —
vía un parser ligero basado en patrones, y traduce la secuencia a la misma
forma de "reporte" que ya sabe reproducir el motor de replay existente,
reutilizando toda su infraestructura probada en vez de duplicarla. Nuevas
acciones del SW: `loadPlaywright`, `getPlaywrightScript`, `clearPlaywright`,
`startPlaywrightReplay`. Nueva sección en la pestaña Reporte para importar;
botón **"▶ Playwright"** junto a Grabar — en el panel lateral y en el popup
— visible únicamente cuando hay un script cargado (se oculta solo al
vaciarlo). El popup no importa (eso solo vive en Reporte); solo reproduce,
leyendo el mismo estado compartido.
*Validado:* parser probado con aserciones (goto/click/fill/press
reconocidos correctamente, `waitForTimeout` absorbido como delay del
siguiente paso); ciclo completo importar→consultar→reproducir→vaciar
probado contra el código real del service worker; visibilidad del botón
probada en ambas superficies (aparece tras importar, desaparece tras
vaciar), incluida la reacción a `chrome.storage.onChanged` en el popup.
Durante la validación se encontró que `toPlaywright()` (nuestro propio
exportador) genera clicks con el estilo moderno de locator-chain
(`page.locator(sel).click()`), que el parser inicial no reconocía —
únicamente el estilo legado `page.click(sel)`. Se extendió el parser para
reconocer ambos estilos, además de `dragAndDrop()` y `selectOption()`
(también generados por `toPlaywright()`), y se confirmó el *round-trip*
completo: un reporte exportado con nuestra propia herramienta y
reimportado con este parser reconoce el 100% de sus pasos.

**Checkbox persistido: recargar el sitio al reproducir.** Antes, reproducir
una repetición siempre navegaba la pestaña a la URL de inicio de la
grabación — incluso si el usuario ya estaba en la página correcta y quería
conservar su estado actual. Nuevo checkbox en Reporte ("Recargar el sitio
en la URL de inicio al reproducir"), persistido en `localStorage` (origen
compartido entre panel lateral y popup) y respetado tanto por la
repetición normal (`startReplay`) como por la de Playwright
(`startPlaywrightReplay`) vía un flag `reloadOnReplay`.
*Validado:* con el checkbox desactivado, `startPlaywrightReplay` recibe
`reloadOnReplay: false` y el SW confirma `navegando: false` en su
respuesta — no se llama a `chrome.tabs.update`, solo se asegura la
inyección del content script en la página actual.

**Validado (general):** sintaxis de los 16 JS como módulo ES, verificación
cruzada de IDs sin huérfanos en panel lateral y popup, arranque limpio del
service worker con las 4 acciones nuevas.

---

### v2.5.8a — Estándar de UI reactiva: Store + Poller en toda la interfaz

Mejora de mantenimiento, sin cambios en el modelo de datos. Nace de un
reporte concreto: en "Repetición", abrir el detalle de un paso lo cerraba
solo tras unos segundos — el temporizador periódico reconstruía la lista
completa sin condición, sin importar si había algo nuevo que mostrar.

**Hallazgo — Cada superficie reimplementaba su propio polling ad-hoc, sin ningún control de cambios real.**
El panel lateral tenía tres temporizadores independientes (`qaTimer` de
Auditoría/Repetición, el general de `init()`, y el propio ciclo de
`refreshReplayState`), cada uno reconstruyendo `innerHTML` sin comparar si
el contenido había cambiado. `renderReplayTrace()` en particular no tenía
ninguna guarda: se ejecutaba cada 2.5s durante una repetición activa y
reemplazaba la lista entera, arrastrando consigo cualquier fila que el
usuario hubiera expandido. El popup tenía su propio `setInterval` separado,
con el mismo patrón sin comparación de cambios (aunque ahí no había filas
expandibles que se vieran afectadas).

*Fix — nuevo módulo `src/lib/reactive-store.js`, estándar único para ambas superficies:*
- **`Store`** — estado observable minimalista: `set()` es un no-op si el
  valor no cambió (comparación estructural), así que ningún suscriptor se
  entera y ningún render ocurre sin motivo.
- **`Poller`** — temporizador centralizado con pausa automática cuando
  `document.visibilityState` no es `"visible"`, sustituyendo los
  `setInterval` repetidos de forma casi idéntica en cada superficie.
- **`captureOpenRows`/`restoreOpenRows`** — capturan qué filas (por clave
  estable: `cid`, `seq` o índice de paso) estaban expandidas antes de
  reemplazar el `innerHTML`, y las reabren después.

`renderTimeline()` y `renderReplayTrace()` ahora calculan una firma ligera
de la página visible (las claves de fila + el límite de paginación) antes
de tocar el DOM: si es idéntica a la última renderizada, la función retorna
sin hacer nada. Cuando sí hay cambios reales, el reemplazo de `innerHTML`
va acompañado de capturar y restaurar las filas abiertas. El popup adoptó
el mismo patrón en `renderLog()` (firma de cambio) aunque no tiene filas
expandibles hoy, para mantener el estándar sin excepción.

**Límite visual (paginación por scroll).** Antes, la lista de eventos
renderizaba hasta 400 filas de una sola vez en el DOM. Ahora arranca en 150
(`RENDER_PAGE`) y crece de 150 en 150 al acercarse el usuario al final de
la lista (`scroll` cerca del límite inferior), con un tope duro de
seguridad de 1000 eventos considerados independientemente de cuántos se
rendericen. Se aplica igual a la vista de Repetición.

**Validado:** simulación con datos reales — se abre una fila con
inconsistencia, llegan datos nuevos (un paso adicional) antes del siguiente
ciclo del `Poller`, y tras el ciclo automático la fila **sigue abierta** y
la lista **sí** incluye el paso nuevo. Por separado, con datos sin cambios
durante dos ciclos completos, se confirmó por marcador de DOM que el
`innerHTML` no se tocó en absoluto. Paginación probada con 500 eventos
simulados: 150 renderizados inicialmente, 300 tras hacer scroll al final.
Sintaxis de los 15 JS como módulo ES, verificación cruzada de IDs sin
huérfanos, arranque limpio del service worker.

**Nota de version.** El campo `version` del manifest de Chrome exige un
formato estrictamente numérico (hasta 4 enteros separados por punto); no
admite sufijos como `"2.5.8a"`. Se mantuvo `version: "2.5.8"` (válido para
Chrome) y se añadió `version_name: "2.5.8a"` (campo opcional de MV3 para la
etiqueta legible), que es lo que ahora muestra el popup.

---

### v2.5.8 — Auditoría de huérfanos: mensajería sin punto de entrada, código bajo demanda conectado, Reporte con auto-refresco

Este ciclo no partió de un reporte de bug, sino de una auditoría deliberada:
recorrer el proyecto en busca de funcionalidad construida pero nunca
utilizada, y de vistas que no se actualizan solas — el mismo tipo de defecto
que en v2.5.7 dejaba "Repetición" congelada, buscado ahora de forma
sistemática en el resto del panel.

**Hallazgo 1 — `page:getMetrics` era la punta de un subsistema completo sin punto de entrada.**
`src/content/content-script.js` no está declarado en el manifest (no se
inyecta nunca). Al rastrear qué lo invocaba, el hallazgo se amplió: el
router `ACTIONS` completo en `service-worker.js` (`ping`, `getActiveTab`,
`getPageInfo`, `getPageText`, `getPageLinks`, `getSystemSummary`,
`getDeviceProfile`, `getExtensionInfo`, `getTabs`, `captureScreenshot`,
`notify`) tampoco tiene ningún llamador interno (ni `popup.js` ni
`sidepanel.js` envían mensajes sin `channel`, que es lo único que ese router
atiende) y el manifest no declara `externally_connectable`, así que tampoco
es alcanzable desde fuera de la extensión.
*Postura de beneficio:* fusionar `page:getMetrics` dentro de `qa/content.js`
inyectaría capacidad sin consumidor en cada página, en contra del principio
de inyección mínima necesaria. La idea de fondo — que el asistente pueda
analizar la página actualmente abierta, no solo sesiones grabadas — es
válida como *feature* futura, pero es una decisión de diseño propia (qué
scope de contexto, qué redacción aplica, qué UI la dispara) y no algo para
resolver de paso dentro de una limpieza de huérfanos.
*Acción:* se eliminó `src/content/content-script.js` y el router `ACTIONS`
completo de `service-worker.js` (entrada inalcanzable). Se mantiene intacto
todo lo que sí tiene consumidor: `chrome.runtime.onInstalled`, el menú
contextual y las llamadas puntuales de `CharlyAPI.storageGet/Set`,
`clearContextMenus/createContextMenu` y `notify`.

**Hallazgo 2 — Resolución de código bajo demanda construida pero nunca disparada.**
El canal `getSource` (SW) → `qa-source` (content) → `get-source` (injected,
con soporte de source maps) existía completo y probado, pero ningún punto de
la UI lo invocaba — el usuario nunca podía pedir el código fuente de un
frame del stack que no tuviera ya un snippet auto-capturado.
*Acción:* cada frame del stack trace ahora se renderiza con un botón
"Ver código"; al pulsarlo, dispara `getSource` sobre la pestaña activa y
muestra el resultado inline. Validado en navegador: el botón resuelve y
muestra el snippet correcto, incluida la línea marcada.

**Hallazgo 3 — La pestaña Reporte no se auto-actualizaba.**
Mismo patrón que el hallazgo de v2.5.7 en "Repetición": `renderReportTab()`
solo se ejecutaba al entrar a la pestaña o tras una acción explícita
(importar, vaciar), nunca por un temporizador. Si el usuario permanecía en
Reporte mientras cambiaba el estado de la grabación en otra parte del panel,
el resumen quedaba desactualizado hasta salir y volver a entrar.
*Acción:* el ciclo de refresco periódico ya existente ahora también
refresca Reporte cuando esa pestaña está activa. Validado sin cambiar de
pestaña en ningún momento: el resumen se actualiza solo tras el ciclo.

**Hallazgo 4 — Función privada sin ningún uso.**
`joinPath()` en `context-bridge.js` estaba declarada, sin exportar y sin una
sola invocación en su propio archivo. Eliminada.

**Revisado y confirmado en uso (no huérfano, falso positivo del barrido inicial):**
`getReplayJob`, `replayProgress`, `replayTrace` (invocados desde
`content.js`, no desde la UI directamente); `getReport` (invocado vía el
helper `readSW()` de `context-bridge.js`, con nombre de wrapper distinto);
`exportChunks` (infraestructura para el modo de envío por webhook aún
pendiente — deliberadamente no expuesto como descarga manual, ver
Pendientes P3).

**Validado:** verificación cruzada de IDs sin huérfanos; sintaxis de los 17
JS como módulo ES; arranque limpio del service worker sin el router
eliminado; flujo de "Ver código" probado en navegador con Playwright
(resuelve y muestra el snippet correcto); auto-refresco de Reporte
confirmado sin cambiar de pestaña.

---

### v2.5.7 — Repetición sin auto-refresco, KPIs y contexto del asistente de la fuente equivocada

Se probó la v2.5.6 en un caso real (exportar, reimportar, reproducir) y el
síntoma parecía persistir — pero el fix del *race condition* de esa versión
**sí funcionó** (los datos se guardaban correctamente; visibles al cambiar
de pestaña y volver). El problema real de esta versión era distinto: tres
puntos de la UI nunca refrescaban solos, o leían la fuente de datos
equivocada.

**Hallazgo 1 — La vista de Repetición no se auto-actualizaba.**
*Causa raíz:* el temporizador periódico de la pestaña Auditoría (`qaTimer`,
cada 2.5s) tenía la condición `source === "live"` — solo refrescaba cuando
la fuente activa era "Temporal". Al ver "Repetición" (reproduciendo un
reporte importado), ese temporizador no hacía nada nunca. La única forma de
ver los datos actualizados era forzar un `renderTimeline(true)` manual, que
solo ocurre al entrar a la pestaña Auditoría (`switchTab`) — de ahí que
cambiar a Asistente o Reporte y volver "arreglara" la vista: no era magia,
era el único punto del código que disparaba un re-render.
*Fix:* la condición pasó a `source !== "imported"` — "Temporal" y
"Repetición" se refrescan solos (ambos cambian con el tiempo), "Importado"
sigue sin refrescarse innecesariamente (es una foto estática).

**Hallazgo 2 — Los KPIs mostraban el reporte temporal, no el que se estaba reproduciendo.**
*Causa raíz:* `renderKpis()` llamaba siempre a la acción `getKpis` del
service worker, que internamente **siempre** calcula sobre el reporte
temporal. Al reproducir un reporte importado, el panel de KPIs mostraba 0
eventos/errores/red (los del temporal, vacío) mezclados con la fidelidad de
replay correcta (esa sí calculada desde la traza real, independiente del
reporte "activo"). Resultado: un panel con números contradictorios entre sí.
*Fix:* `renderKpis()` calcula ahora client-side con `computeKpis` (la misma
función pura que usa el SW) sobre el reporte y la traza que **realmente**
corresponden a la fuente activa: Temporal usa el reporte vivo sin traza;
Importado usa el reporte importado sin traza; Repetición usa el reporte
importado **con** la traza del replay en curso. `updateSourceUI()`
sincroniza la fuente activa a `state.auditSource` para que `renderKpis()`
(fuera del cierre de la pestaña Auditoría) pueda leerla.

**Hallazgo 3 — Los chips de contexto del asistente no reflejaban el selector Temporal/Importado.**
*Causa raíz:* `refreshState()` — la función que llena `state.counts` y el
contador "N eventos" — llamaba siempre a `getState()` del SW (temporal), sin
mirar `state.contextSource`. El selector agregado en v2.5.6 sí cambiaba qué
se enviaba a la IA al preguntar, pero los chips visuales seguían mostrando
los números del reporte temporal sin importar cuál estuviera seleccionado.
*Fix:* `refreshState()` ahora lee `report.metadata.counts`/`eventCount` del
reporte importado cuando `state.contextSource === "imported"`. Los botones
del selector llaman a `refreshState()` de inmediato al hacer clic.

**Validado:** escenario reproducido exactamente como fue reportado (reporte
temporal vacío + reporte importado con 399 eventos + traza de replay con 5
pasos y 3 inconsistencias), confirmado en Playwright **sin cambiar de
pestaña en ningún momento**: la vista de Repetición se actualiza sola; el
panel de KPIs muestra "5 eventos" (el importado) en vez de "0" (el
temporal); el selector del asistente cambia "0 eventos" → "399 eventos" y
actualiza los 12 chips al alternar la fuente. Cero errores de página;
verificación cruzada de IDs sin huérfanos.

---

### v2.5.6 — Race condition en Repetición, contexto del asistente completo, Reproducir/Detener en su módulo

**Hallazgo 1 — Repetición quedaba vacía tras importar y reproducir (bug crítico).**
*Causa raíz — condición de carrera real:* durante un replay, `content.js`
envía dos mensajes independientes por cada paso — `replayProgress` (avance)
y `replayTrace` (telemetría) — cada uno `sendMessage` sin esperar respuesta
(fire-and-forget). En el service worker, cada acción hacía su propio ciclo
`get(K.replayJob) → modificar → set(K.replayJob)` de forma independiente.
Con cientos de pasos en rápida sucesión, dos ciclos podían solaparse: si A
lee, B lee (antes de que A escriba), A escribe, B escribe — el `set` de B
**pisa por completo** el objeto que dejó A, descartando su cambio en
silencio (*lost update* clásico). Con 291 pasos esto perdía casi toda la
telemetría de `trace`, mientras que `index` (el progreso) sobrevivía con más
frecuencia por la casualidad del orden — explica exactamente el síntoma: el
banner avanzaba ("5/291") pero Repetición mostraba "0 pasos, 0 inconsistencias".
*Fix:* nueva cola de escritura serializada `replayJobChain` (mismo patrón ya
probado que usa `writeChain` para los eventos del timeline). Todas las
mutaciones de `K.replayJob` — `replayProgress`, `replayTrace`,
`startReplay`, `stopReplay`, `clearImported` — pasan por `mutateReplayJob()`,
que garantiza que cada ciclo get→modificar→set se complete antes de que
empiece el siguiente.
*Validado:* simulación determinística del race confirma la pérdida con el
patrón viejo y su ausencia con la cola nueva; prueba de estrés con 291 pasos
concurrentes **contra el código real del service worker** — resultado
exacto: 291/291 progreso, 291/291 traza, sin pérdidas.

**Hallazgo 2 — Contexto del asistente desactualizado e incompleto.**
- *Selector de fuente (Temporal/Importado):* el asistente solo podía leer el
  reporte temporal; no había forma de analizar un reporte importado. Se
  agregó `ContextBridge.getReport(source)` (`"live"` o `"imported"`), un
  selector visual en la pestaña Asistente, caché de contexto con la fuente
  en su clave (para no mezclar sesiones), y el `systemPrompt` declara
  explícitamente qué fuente está analizando.
- *Variables de contexto incompletas:* `scopeCount()` solo mapeaba 6 de los
  12 ámbitos — Variables, Estructura, Repetición, Performance y Seguridad
  quedaban siempre en blanco aunque tuvieran datos. El ámbito Resumen no
  incluía entorno de grabación (CPU/RAM/navegador), identidad de sesión
  (`recordingId`/`startUrl`) ni KPIs agregados. Ahora los 12 ámbitos
  muestran su conteo real, y Resumen incluye todo lo anterior — los KPIs se
  calculan client-side con `computeKpis` sobre el reporte de la fuente
  activa, para no mezclar KPIs del temporal con los del importado.

**Hallazgo 3 — Reproducir/Detener migrados al módulo de Repetición.**
Antes vivían en la barra de acciones persistente de Auditoría, visibles sin
importar qué fuente estuviera activa. Ahora viven exclusivamente dentro de
`#replay-controls`, visible solo con la fuente "Repetición" activa. De paso
se eliminó `#act-replay-info`, que duplicaba la misma información que
`#replay-progress`.

**Validado:** verificación cruzada de IDs sin huérfanos; sintaxis de los 16
JS como módulo ES; los 12 chips de contexto muestran conteos correctos;
selector de fuente cambia solo cuando hay datos disponibles; Reproducir/
Detener ausentes de la barra persistente y presentes solo en Repetición —
todo sin errores de página en Playwright.

---

### v2.5.5 — Popup simplificado, pestaña Reporte, fuentes independientes

Reestructuración de flujo: import/export/replay quedan centralizados y cada
fuente de datos (temporal/importado) se gestiona de forma independiente.

**Popup:**
- *Exportar como dropdown + acceso a Auditoría:* la fila de 3 botones
  (JSON/Cypress/Playwright) se reemplazó por un único `<select>`
  "Exportar ▾". "Copiar JSON" y "Vaciar" se eliminaron; en su lugar, un
  botón **"Ver en Auditoria"** abre el panel lateral directo en esa pestaña
  (bandera transitoria `charlyaudit:openTab` en `chrome.storage.local`, leída
  una vez por `init()` del panel y luego borrada — mecanismo genérico,
  reutilizable para futuras aperturas dirigidas).
- *Sección Replay eliminada:* Importar/Reproducir/Detener/Velocidad ya no
  existen en el popup; toda la reproducción vive en el panel lateral.

**Panel lateral:**
- *Nueva pestaña "Reporte":* único lugar del panel para descargar la sesión
  temporal (JSON completo/Cypress/Playwright), ver un resumen de la sesión
  importada (eventos, URL, versión de quien exportó) y vaciarla de forma
  independiente. También el único lugar para **importar** (se eliminó
  `#tl-import` de Auditoría). La importación solo acepta el JSON propio del
  reporte, nunca Cypress/Playwright.
- *Análisis del "reporte completo":* se auditó `buildBundle()` en el
  service worker antes de dar la tarea por completa. Ya incluye, sin huecos
  relevantes: `schema` versionado, metadata de extensión, `settings`+
  `capture` del exportador (solo referencia), el `report` íntegro
  (metadata+timeline con `cid`/`tRel`/`fp` por evento), telemetría de la
  última repetición, errores destacados, conteos, KPIs agregados e
  integridad. Es el mismo artefacto que se firma y envía por webhook.
- *Vaciar por fuente, no global:* antes, "Vaciar" en Auditoría siempre
  llamaba a la misma acción (solo temporal) sin importar la fuente activa.
  Ahora bifurca: Temporal → `clear`; Importado/Repetición → nueva acción
  `clearImported` (vacía `qa:replay` y `qa:replayJob`, detiene cualquier
  replay activo primero). Efecto colateral corregido de paso: el reporte
  importado ahora se hidrata desde `storage` al abrir el panel (antes se
  perdía al cerrar y reabrir, aunque el dato seguía en el SW).
- *Controles de reproducción en Repetición:* la Velocidad (antes solo en el
  popup) se agregó como `<select>` visible únicamente con la fuente
  "Repetición" activa. Al pulsar Reproducir, la vista cambia automáticamente
  a esa fuente.

**Validado:** suite funcional en navegador real (Playwright, sin errores de
página): 3 pestañas presentes y conmutables; importar desde Reporte habilita
"Importado" en Auditoría; Vaciar en Temporal no afecta al importado y
viceversa; controles de repetición solo en esa fuente; cero elementos
huérfanos de import/export en Auditoría; cero IDs referenciados en JS
ausentes del HTML (y viceversa).

---

### v2.5.4 — Personalización de colores, footer duplicado, separación de formularios

**Hallazgo 1 — La personalización de colores no se aplicaba.**
*Causa raíz:* el diálogo "Personalizar paleta" seguía apuntando a los alias
legacy del sistema de tokens (`--brand`, `--ink`, `--panel`, `--line`,
`--text`), que desde el rediseño de v2.5.1 son solo `var(--c-*)` de un único
sentido — nada los lee de vuelta. Todos los componentes reales leen los
tokens canónicos (`--c-brand`, `--c-bg`...) directamente.
*Fix:* `VARS` en `setupPalette()` y los `data-var` del diálogo ahora apuntan
a los tokens canónicos. Verificado: cambiar el color de marca a naranja y
guardar recolorea el botón "Exportar" de inmediato.
*Nota:* `--c-brand-dim` (hovers, bordes sutiles) no se deriva automáticamente
del `--c-brand` personalizado — queda como pendiente (ver P4).

**Hallazgo 2 — "CharlyAudit" duplicado en el pie del popup.**
*Causa raíz:* el HTML tenía un `<span>` de relleno (sobrescrito por JS en
tiempo de ejecución) seguido de OTRO `<span>CharlyAudit</span>` estático que
nunca se tocaba — resultado: "CharlyAudit v2.5.3 • CharlyAudit".
*Fix:* se eliminó el `<span>` estático duplicado y su separador.

**Hallazgo 3 — CAPTURA y PERFIL Y DOMINIOS eran el mismo formulario.**
Un único botón "Captura ▾" abría un panel con tres grupos visuales
(Captura, Perfil y dominios, Telemetría) — visualmente separados pero
funcionalmente un solo formulario con un solo estado abierto/cerrado.
*Fix:* se dividió en dos secciones y dos botones independientes: **Captura ▾**
(qué capturar durante la grabación: config de sesión) y **Ajustes ▾**
(perfil, dominios, telemetría: config persistente). Todos los `id` de los
campos internos se conservaron; ningún binding de JS se rompió.

---

### v2.5.3 — Auditoría y cierre de los 6 hallazgos de UI/UX

Se revisaron los seis problemas del reporte visual de v2.5.1 con
verificación empírica en navegador (Playwright) **antes** de tocar código,
para no corregir nada que ya estuviera resuelto ni dejar sin corregir algo
real.

**Ya estaban corregidos (verificado, sin cambios adicionales):**
1. *KPIs no cerraban* — `.tl-kpis[hidden] { display: none; }` ya tenía mayor
   especificidad que `.tl-kpis { display: grid }` y ganaba correctamente.
2. *Modales no céntricos* — `dialog.settings { margin: auto; }` ya
   restauraba lo que el reset global (`* { margin: 0 }`) le quitaba al
   `margin: auto` nativo de `<dialog>`.
3. *Toast detrás de un modal abierto* — ya resuelto con `popover="manual"` +
   `showPopover()`/`hidePopover()`, que saca al toast del flujo normal y lo
   coloca en la capa superior por encima de cualquier `<dialog>`.
4. *Contraste del texto "Importado" (deshabilitado)* — ya no usaba
   `opacity` sobre `--c-muted` (caía a 1.78:1, ilegible); usaba un color
   sólido precalculado (5.26:1 sobre `--c-bg`).

**Corregidos en esta versión:**
5. *Toast podía solaparse con el dock envuelto* (paneles angostos) — el
   mecanismo `--dock-h` vía `ResizeObserver` ya existía pero
   `watchDockHeight()` estaba definida y **nunca se invocaba**; además, al
   adoptar la Popover API para el hallazgo #3, el toast heredó un `top: 0`
   por defecto que ganaba sobre `bottom`. *Fix:* se añadió la llamada a
   `watchDockHeight()` en `init()` y `top: auto;` explícito en `.toast`.
6. *Header desbordaba 2px en el ancho mínimo (280px)* — `.tabs` no tenía
   `min-width: 0`, así que no podía comprimirse dentro del `.bar` flex.
   *Fix:* `min-width: 0; flex-shrink: 1` en `.tabs` + ajuste fino del
   breakpoint `max-width: 340px`.

**Nota de proceso:** aplicar "fixes" sobre código que ya funciona introduce
riesgo sin beneficio — de ahí la verificación empírica previa. Cuatro de
seis hallazgos ya estaban resueltos; solo dos necesitaban trabajo real.

---

### v2.5.1 — Rediseño de UI/UX del panel lateral

Sistema de diseño reescrito desde cero. El CSS pasó de 813 líneas
acumuladas por parches a 1166 líneas organizadas en 11 secciones con un
sistema de tokens coherente. Ningún binding de JS se rompió (todos los `id`
permanecen intactos).

**Sistema de tokens:**
| Token | Descripción |
|---|---|
| `--c-*` | 10 colores funcionales (bg, surface, surface2, border, brand, brand-dim, danger, success, warn, text, muted) |
| `--t-xs/sm/base/lg` | 4 tamaños tipográficos (10/11.5/13/15px) en lugar de 11 |
| `--s-1 a --s-5` | Escala de espaciado ×4 (4/8/12/16/24px) en lugar de 12 valores arbitrarios |
| `--r-sm/md/lg/full` | 4 radios (6/8/12/999px) |
| Aliases retrocompatibles | `--ink`, `--panel`, `--brand`, etc. — solo para CSS legacy; los componentes reales leen los tokens `--c-*` directamente |

**Jerarquía de botones** (antes 7 estilos, ahora 3): Primario (`.act--brand`,
acción con consecuencia), Secundario (`.act`, acción reversible/neutra),
Ghost (`.act--ghost`, `.cache-btn`, acción de bajo perfil).

**Captura:** de un muro de campos a tres grupos legibles (CAPTURA · PERFIL Y
DOMINIOS · TELEMETRÍA), cada uno con encabezado, borde y espaciado propio.

**KPI cards:** grid predecible `repeat(3, 1fr)` en lugar de
`auto-fill, minmax(96px)` — 3 columnas en 300-419px, 4 columnas en ≥420px.

**Mobile-first:** dos breakpoints (`max-width: 340px` — nombre truncado,
tabs más pequeños, KPIs en 2 columnas; `min-width: 420px` — KPIs en 4
columnas, burbujas de chat más anchas).

**Accesibilidad:** `min-height: 28px` en chips/scopes (touch target),
`aria-selected` en pestañas, `role="list"`/`role="group"` donde corresponde,
`prefers-reduced-motion` en el indicador de escritura, colores funcionales
siempre vía tokens.

---

### v2.5.0 y anteriores

Documentación exhaustiva por hallazgo empezó en v2.5.1. Las versiones
anteriores solo cuentan con el resumen de la tabla al inicio de esta
sección: `web_accessible_resources` para inyección on-demand y rebrand
(2.5.0); asistente multi-proveedor con conversación multi-turno (2.4.1);
Performance 100% real (2.4.0); UX y chunking/partitionKey (2.3.x); inyección
on-demand y ancla semántica (2.2.x); replay fiel y exportadores (2.1.x);
núcleo MV3 (2.0.x).
