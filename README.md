# CharlyAudit

**Versión actual: 2.6.0**

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
                        computeKpis, partitionKeyOf, hmacHex, INTERACTION_TYPES,
                        ROUTE_TYPES (fuente única de qué tipos de evento cuentan
                        como interacción/ruta — la usan SW, Auditoría y Asistente)
  exporters.js           toCypress, toPlaywright
src/popup/               Popup de control rápido (grabar, exportar, atajo a Auditoría)
src/sidepanel/
  sidepanel.html/js/css  Panel lateral: 3 pestañas — Asistente / Auditoría / Reporte
  lib/
    openwebui-client.js  Cliente multi-proveedor (OpenWebUI/OpenAI/Gemini/Claude/custom)
    context-bridge.js    Puente de contexto QA→IA (15 scopes, budget, digest,
                        fuente Temporal/Importado)
    chat-cache.js        Caché de conversación en localStorage
    markdown.js          Render Markdown seguro (sin eval)
```

**Las tres pestañas del panel lateral:**
| Pestaña | Función |
|---|---|
| **Asistente** | Chat con IA sobre la sesión; selector de fuente Temporal/Importado; 15 ámbitos de contexto (uno por cada tipo de evento visible en Auditoría) |
| **Auditoría** | Grabar, ver el timeline evento a evento, y reproducir (Temporal / Importado / Repetición) |
| **Reporte** | Único lugar para importar un archivo, descargar la sesión temporal (JSON/Cypress/Playwright), gestionar el reporte importado, y tarjetas de Perfil/Dominios/Telemetría e Icono personalizado |

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
- Agrupaciones canónicas de tipos de evento (`INTERACTION_TYPES`, `ROUTE_TYPES` en `bundle-schema.js`): única fuente de verdad para "qué cuenta como interacción/ruta", compartida por el contador de Auditoría, el contexto del Asistente y `computeKpis` — un reporte temporal o importado se ve exactamente igual de completo desde cualquiera de las tres pantallas
- HMAC-SHA256 del payload webhook (`X-CharlyAudit-Signature`)
- Reintentos con backoff exponencial (2→4→8→16→30 min, máx. 5) vía `replayJobChain` serializado — elimina condiciones de carrera al escribir `K.replayJob` desde múltiples mensajes concurrentes (`replayProgress`, `replayTrace`, `startReplay`, `stopReplay`, `clearImported`)

### ✅ Rendimiento y memoria durante la grabación
- Escritura del timeline en **lote** (no por evento): los eventos capturados se acumulan en memoria y se persisten cada 400ms o cada 40 eventos, lo que ocurra primero — evita el patrón O(n) por evento / O(n²) por sesión que antes hacía releer y reescribir el timeline completo en cada captura
- Ninguna lectura (KPIs, exportar, conteos en vivo, webhook) pierde visibilidad de eventos aún no persistidos — `getTimeline()` fusiona el buffer en memoria con lo ya guardado
- Volcado forzado del buffer pendiente en los puntos de durabilidad crítica: al detener una grabación y como red de seguridad en `chrome.runtime.onSuspend`
- Gestión activa del buffer nativo de Resource Timing del navegador (`clearResourceTimings()` cada 30s + tamaño ampliado) — antes crecía sin límite durante toda la sesión, a nivel de memoria de proceso, no solo del heap de JS de la extensión
- Deduplicación de recursos de red acotada por tiempo (se limpia junto al buffer nativo) y por tamaño (límite de seguridad ante ráfagas extremas)
- Configuración del asistente cacheada en memoria del service worker (se invalida solo ante un cambio real, no en cada evento capturado)

### ✅ Replay y Repetición
- Reproducir/Detener/Velocidad viven **exclusivamente** dentro del bloque de Repetición (Auditoría), visibles solo con esa fuente activa
- La vista de Repetición se auto-refresca sola (timer periódico, sin necesidad de cambiar de pestaña) mientras hay un replay en curso
- KPIs de Repetición se calculan sobre el reporte **realmente en reproducción** (el importado + su traza), nunca mezclados con el temporal
- Telemetría por paso: efecto esperado (grabación) vs. observado (replay), con diff estructural del DOM, capturada para **todo tipo de paso, incluido scroll**
- El denominador de progreso ("reproduciendo X/Y") refleja los pasos reales reproducibles, no el total de eventos del timeline
- Resolución de código bajo demanda: cada frame del stack de un error tiene un botón "Ver código" que resuelve el snippet real (con soporte de source maps) vía el canal `getSource` → `qa-source` → `get-source`
- Checkbox persistido: recargar (o no) el sitio en la URL de inicio al reproducir — útil para conservar el estado actual de la sesión en vez de forzar una navegación
- Fidelidad de reproducción: cada click dispara la secuencia real de eventos del navegador (`PointerEvent` + `MouseEvent`, no solo uno de los dos), el doble click reproduce dos ciclos completos down/up/click antes del `dblclick`, y el llenado de inputs dispara `beforeinput` antes del cambio de valor — la misma secuencia que produce una interacción humana real, no una síntesis parcial
- Detener responde casi de inmediato, en el banner flotante de la página o en Auditoría — antes las esperas internas entre pasos (hasta 2.5s + 1.2s) no comprobaban si se había pedido detener, así que en páginas muy dinámicas (mutación de DOM constante) el clic en "Detener" podía tardar varios segundos en tener efecto real
- Botón "▶ Reproducir" en el popup, junto a Grabar — visible únicamente cuando hay un reporte importado

### ✅ Asistente IA
- **Conversación multi-turno real**: `messages: [{role:"system",...},{role:"user",...},{role:"assistant",...},...,{role:"user"}]`; el historial viaja como roles nativos, no como texto incrustado
- **Multi-proveedor**: OpenWebUI (propio), OpenAI/ChatGPT, Google Gemini, Anthropic Claude, endpoint personalizado. Claude separa `system` en su campo propio; Gemini usa el endpoint OpenAI-compatible. Los proveedores con URL fija (OpenAI/Gemini/Claude) **siempre** usan esa URL, sin importar qué haya quedado guardado en el campo de un proveedor previo (p. ej. OpenWebUI) — el campo Base URL solo aplica, y solo se muestra, para OpenWebUI y endpoint personalizado
- **Parámetros configurables**: temperatura, tokens máximos, turnos de historial
- **Selector de fuente Temporal/Importado**: el asistente puede analizar la grabación en curso o un reporte importado, sin mezclar datos entre ambos (caché de contexto con clave por fuente)
- **15 ámbitos de contexto**, todos con conteo visible y actualizado según la fuente activa, uno por cada tipo de evento que Auditoría puede mostrar como chip: Resumen, Errores, Red, Consola, Rutas, Funciones, Variables, Interacción, Estructura, Repetición, Performance, Seguridad, **Recursos, Código, Cabeceras**
- **Todo lo que se ve en Auditoría es elegible como contexto — sin excepción**: Recursos expone el listado completo de recursos cargados (no solo los más pesados, que siguen disponibles agregados en Performance); Código expone todos los bloques de código fuente resueltos, no solo los correlacionados a errores actualmente mostrados; Cabeceras expone el detalle técnico completo por petición auditada (sus hallazgos de seguridad ya llegaban antes vía el ámbito Seguridad); Performance incluye ahora el listado completo de mediciones INP individuales, no solo el percentil 98 agregado
- **Mismo nivel de detalle que Auditoría, no una versión resumida**: Red envía la lista completa de peticiones con su waterfall (DNS/TCP/TTFB/descarga), no solo las fallidas; Consola incluye todos los niveles (log/info/warn/error), no solo warn/error; Interacción incluye el INP medido por evento y marca si el elemento no tiene nombre accesible (el mismo *known-issue* que resalta Auditoría visualmente); Rutas incluye el timing completo de cada navegación (TTFB, DOM listo, carga total, TTI aproximado, referrer, redirects)
- El ámbito Resumen incluye entorno de grabación (CPU/RAM/navegador), identidad de sesión (`recordingId`/`startUrl`), KPIs agregados completos, *workers* detectados y resumen de cabeceras de seguridad auditadas
- Si el contexto excede el presupuesto de tokens, el recorte automático reduce cuántos elementos se **listan en detalle** por ámbito — el **total real nunca se pierde ni se oculta**, sigue visible aunque el detalle completo no quepa (p. ej. "213 recursos en total" aunque solo se listen 25)
- El reporte exportado (JSON completo) siempre fue, y sigue siendo, más completo que cualquier ámbito de contexto: incluye el `timeline` íntegro sin filtrar por tipo — los ámbitos del Asistente son un recorte pensado para caber en una conversación con presupuesto de tokens, no el límite real de lo que se captura

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
- Personalización de paleta de colores (tokens canónicos, aplica en vivo) — **compartida con el popup**: antes el popup tenía su propio conjunto de variables CSS (`--ink`/`--panel`/`--brand`...) sin ningún puente con la paleta guardada, así que sus colores nunca reflejaban lo que el usuario personalizara; ahora ambos leen del mismo `localStorage` compartido
- Icono personalizado: el usuario puede cargar una imagen (se redimensiona en el navegador, nunca se sube a ningún lado) para reemplazar el logo del popup, del panel lateral y de la barra de herramientas. Si el dato guardado resulta corrupto o ilegible, ambas superficies vuelven solas al logo original — nunca queda sin icono
- Perfil, Dominios y Telemetría viven ahora en su propia tarjeta dentro de la pestaña Reporte (antes detrás de un botón "Ajustes ▾" en Auditoría) — es configuración persistente, no algo que se ajuste durante una grabación

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

3. **Sin regresión de módulos puros.** `report-engine.js`, `bundle-schema.js` y `exporters.js` son funciones puras. Cualquier cambio en ellas debe pasar los tests unitarios antes de tocar el SW o la UI. Cuando una vista de la UI necesita KPIs o un reporte, debe quedar explícito **de qué fuente** (temporal vs. importada) — nunca asumir una por defecto sin verificarlo contra el selector activo. Cuando dos o más superficies (Auditoría, Asistente, KPIs, exportación) necesitan "qué tipos de evento cuentan como X", esa lista vive **una sola vez** en el módulo puro compartido (`bundle-schema.js`) — nunca se copia ni se redefine por separado en cada consumidor, porque copias independientes se desincronizan con el tiempo sin que ningún test lo detecte.

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
- `prefers-reduced-motion` en el banner de grabación de la página auditada.
- Auditoría sistemática de contraste WCAG AA sobre el resto de combinaciones posibles de la paleta personalizable.
- `--c-brand-dim` (hovers, bordes sutiles) no se deriva automáticamente del `--c-brand` personalizado — sigue fijo al valor por defecto. Calcular un tono derivado, o añadirlo como sexto control en la paleta.
- El `digest()` del asistente (resumen en lenguaje natural que encabeza el contexto) aún no incorpora KPIs de performance/seguridad en su texto — solo cuenta eventos, duración, errores y red fallida.
- `ctx-src-imported` hace dos llamadas en cascada a `getReport("imported")` (una para verificar disponibilidad, otra dentro de `refreshState()`). Funciona correctamente pero es una ronda de red de más; se podría cachear el resultado de la primera.
- `CharlyAPI.js` mantiene una superficie amplia de métodos (tabs, ventanas, bookmarks, historial, cookies, descargas, debugger) sin consumidor activo más allá de `storageGet/Set`, `clearContextMenus/createContextMenu` y `notify`. No son código muerto en el sentido de inalcanzable — son métodos correctos y documentados — pero antes de construir una función nueva sobre ellos conviene confirmar el beneficio concreto en vez de asumir que "ya está soportado".
- El `Store` de `reactive-store.js` se usa hoy para centralizar el polling (`Poller`) y para la detección de cambios en las listas de Auditoría/Repetición; el resto del estado (grabación en curso, webhook pendiente, KPIs) sigue actualizándose por llamada directa dentro del callback del `Poller`, no por suscripción a `Store`. Migrar esos casos a `Store.subscribe()` sería más consistente con el estándar, aunque hoy no presentan el bug que sí tenía la lista de eventos.
- La paginación por scroll de `tl-list` solo *agrega* filas al hacer scroll (nunca libera las que salen de vista). Para reportes de varios miles de eventos, una ventana verdaderamente virtualizada (renderizar solo lo visible) sería más robusta que el tope actual de 1000 eventos + páginas de 150.
- La secuencia de click ahora dispara `pointerdown`/`pointerup`, pero no `pointermove`/`pointerover`/`pointerenter` — UI dependiente de hover real (tooltips, menús que se abren al pasar el cursor) no se activa durante la repetición.
- El drag&drop de la repetición usa únicamente la API nativa `DragEvent` (`dragstart`/`dragover`/`drop`/`dragend`). Muchas librerías modernas de listas ordenables (dnd-kit, react-beautiful-dnd y similares) no usan esa API — simulan arrastre con una secuencia de `pointerdown`/`pointermove`/`pointerup`, que hoy no se reproduce.
- `perf._timer` (el snapshot periódico de KPIs cada 5s en `injected.js`) sigue corriendo indefinidamente después de detener una grabación — tiene una guarda que lo vuelve no-operativo (`if (!state.recording) return`), pero el propio `setInterval` nunca se cancela hasta que se navega o se cierra la pestaña. No es una fuga de memoria (no crece nada), pero es trabajo innecesario que podría evitarse limpiando el intervalo explícitamente al detener.
- El buffer de escritura diferida del timeline (`pendingEvents`, ver Rendimiento y memoria) reduce drásticamente las escrituras a storage, pero introduce una ventana de riesgo real y acotada: si el service worker terminara de forma abrupta (no vía `onSuspend`, que sí se atiende) dentro de la ventana de 400ms, los eventos aún no volcados podrían perderse. Se mitigó con un intervalo corto y volcados forzados en los puntos de mayor riesgo (detener grabación, `onSuspend`), pero el riesgo teórico no es cero — vale la pena vigilarlo si en el futuro se reportan sesiones con eventos faltantes justo al final.
- `worker`: el Asistente ve los últimos 10 *workers*/service workers detectados (en el ámbito Resumen), no el listado completo si hubiera más — a diferencia de `resource-timing`/`code-block`/`response-headers` (2.5.9c), que ya tienen su propio ámbito con listado completo, `worker` sigue siendo un resumen recortado dentro de Resumen.
- Con la unificación de `INTERACTION_TYPES` (2.5.9a), la cifra de "Interacciones" en KPIs ahora incluye `scroll`/`resize`, que antes no contaba — es la definición correcta y consistente con Auditoría/Asistente, pero si algún reporte histórico se comparaba contra ese número, el valor absoluto puede diferir ligeramente de sesiones grabadas con versiones anteriores.
- El ámbito Interacción sigue sin el árbol de ancestros completo (`path`) de cada elemento — se excluyó deliberadamente por inflar demasiado el contexto (cadenas largas por cada evento). Sí incluye el selector compacto, el INP medido y el aviso de accesibilidad (2.5.9b), que cubren la señal de QA más accionable sin pagar el costo del árbol completo.
- En sesiones muy intensas en red o en recursos (cientos de peticiones/tiles), el sistema de presupuesto sigue recortando el **detalle por elemento** disponible para el Asistente, aunque el **total** (conteo agregado) nunca se pierde — es un límite de diseño consciente (tokens/costo de la conversación), no un descuido, pero vale la pena revisar si un resumen estadístico (percentiles de duración, top dominios) sería más útil que una lista truncada cuando el recorte es agresivo.
- Los ámbitos "Recursos" y "Performance → recursosPesados" ambos leen de `resource-timing` con vistas distintas (listado completo vs. los más pesados) — es una duplicación intencional (cada uno responde una pregunta distinta: "qué cargó" vs. "qué pesa más"), pero vale la pena revisar si conviene fusionarlas en una sola vista con ambos criterios de orden disponibles, para no hacer que el Asistente reciba el mismo dato dos veces si ambos ámbitos están activos a la vez.
- El icono personalizado no valida el tamaño del archivo antes de leerlo (`FileReader` carga el original completo en memoria antes de redimensionarlo) — un archivo extremadamente pesado podría tardar o consumir memoria de forma innecesaria antes de llegar al canvas de 128×128. Un tope razonable (p. ej. 5-10MB) evitaría ese caso sin afectar el uso normal.
- La sincronización de paleta entre panel lateral y popup (2.6.0) usa el evento nativo `storage`, que solo se dispara si el popup ya está abierto en el momento exacto en que el panel guarda la paleta — dado que el popup normalmente está cerrado, en la práctica el popup solo ve la paleta actualizada la próxima vez que se abre (que es el caso común), no en vivo mientras ambos coexisten. Correcto para el uso típico, pero vale la pena documentarlo como una sincronización "al abrir", no en tiempo real.
- El icono de la barra de herramientas se reaplica en `onStartup` a partir de lo guardado en `storage.local`; si ese dato guardado estuviera corrupto (no el archivo original al subirlo, que sí se valida, sino una corrupción posterior del propio storage), el intento de reaplicarlo fallaría silenciosamente y la barra se quedaría con el último icono que Chrome tenía cargado — no necesariamente el logo por defecto. Los logos del popup/panel sí garantizan la reversión (vía el `onerror` del propio `<img>`); la barra de herramientas depende de que `applyToolbarIcon` nunca reciba un dato corrupto en primer lugar.

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
| **2.6.0** | Ajustes migrados a Reporte (Perfil/Dominios/Telemetría) · fix real de responsividad del botón Detener (esperas internas que ignoraban la solicitud hasta 3.7s por paso) · botón Reproducir en el popup · icono personalizado con redimensionado y fallback garantizado · paleta de colores ahora compartida con el popup |
| **2.5.9c** | Tres tipos de evento (Recursos, Código, Cabeceras) nunca tuvieron un ámbito propio en el Asistente — solo aparecían recortados dentro de otros. Se agregan como ámbitos dedicados (12→15), llevando el total a un ámbito por cada tipo de evento visible en Auditoría; se confirma que el reporte exportado ya era completo desde antes |
| **2.5.9b** | Corrección de alcance sobre 2.5.9a: la auditoría de consistencia se amplía a nivel de *detalle* (no solo conteos) en los 12 ámbitos — Red ahora envía la lista completa con waterfall (antes solo fallidas/lentas), Consola incluye todos los niveles (antes solo warn/error), Interacción incluye INP medido y aviso de accesibilidad por evento, Rutas incluye el timing completo de cada navegación |
| **2.5.9a** | Consistencia total entre Auditoría/Asistente/KPIs: "Rutas" excluía navegaciones completas de página en el contexto del Asistente (mostraba 0 aunque Auditoría mostrara eventos reales) — causa raíz: tres listas independientes de "qué es una interacción/ruta" desincronizadas; se unifican en una sola fuente compartida |
| **2.5.9** | Fix crítico de memoria/rendimiento: escritura del timeline en lote (antes O(n) por evento) · gestión del buffer nativo de Resource Timing · caché de ajustes en el SW · fix de la URL fija del asistente para proveedores con endpoint conocido (OpenAI/Gemini/Claude) |
| **2.5.8c** | Corrección de rumbo: se elimina la importación de Playwright (imposible de ejecutar con alta fidelidad dentro de una extensión) a favor de pulir el motor de replay propio — eventos de puntero, doble click real, `beforeinput` en formularios |
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

### v2.6.0 — Ajustes en Reporte, fix real del botón Detener, icono personalizado, popup con Reproducir, paleta compartida

Seis objetivos, tratados con el mismo criterio de siempre: diagnóstico con
evidencia real antes de tocar código, y validación contra el código real
(no solo revisado) antes de dar cada punto por cerrado.

#### 1 — Perfil, Dominios y Telemetría migran a Reporte

Vivían detrás de un botón "Ajustes ▾" en Auditoría, junto al resto de la
configuración de captura de una sesión — pero son ajustes persistentes,
no algo que se toque durante una grabación. Se movieron a su propia
tarjeta, siempre visible, dentro de la pestaña Reporte (junto a Sesión
actual, Sesión importada e Icono personalizado). Los mismos campos, los
mismos `id`, ningún binding de JavaScript se rompió en el traslado — solo
cambió dónde vive la tarjeta y que ya no depende de un botón para
mostrarse. Validado en navegador real: el botón "Ajustes" ya no existe en
Auditoría, y los tres datos de prueba (dominios, nombre, URL de webhook) se
cargan automáticamente al abrir Reporte.

#### 2 — El botón "Detener" del banner flotante: diagnóstico con evidencia, no teoría

El síntoma reportado: al reproducir una repetición, el botón "Detener" del
banner flotante en la página hacía desaparecer la etiqueta pero la
repetición seguía corriendo — mientras que el botón "Detener" de Auditoría
sí funcionaba, pese a usar el mismo mecanismo de fondo.

En vez de teorizar sobre la causa, se construyó una reproducción **literal**
del mecanismo real (el mismo bucle, el mismo banner, el mismo cierre
`stop()`) ejecutada en un navegador de verdad vía Playwright. Esa prueba
confirmó que el mecanismo central — el bucle de reproducción, el banner, el
cierre `stop()` — funciona correctamente: al hacer clic, el bucle se
detiene en la siguiente iteración.

El hallazgo real estaba un nivel más abajo: **`waitForStable()`** (hasta
2.5s por paso, esperando a que el DOM deje de mutar) y
**`observeConsequences()`** (hasta 1.2s por paso, observando las
consecuencias de una interacción) **nunca comprobaban si se había pedido
detener** durante su propia espera interna. En una página con mutación de
DOM constante — el caso típico de un mapa en vivo con marcadores
moviéndose — `waitForStable()` casi nunca llegaba a su condición de
"quietud" (350ms sin mutaciones) y corría hasta su tope completo en
**cada** paso. Un clic en "Detener" durante esa ventana no tenía ningún
efecto hasta que el temporizador terminara por su cuenta — hasta 3.7
segundos de retraso por paso, acumulándose mientras quedaran pasos por
reproducir. Esto explica el patrón reportado: no es que un botón funcione y
el otro no (ambos comparten el mismo mecanismo de fondo) — es que la
respuesta real dependía de en qué momento exacto del ciclo cayera el clic,
lo que se siente exactamente como "a veces funciona, a veces no".

*Fix:* ambas funciones ahora comprueban cada 150ms si se pidió detener, y
salen de inmediato si es así. Además, el clic del banner ahora quita la
etiqueta al instante (antes esperaba la ida y vuelta al service worker para
el mismo efecto visual).

*Validado con medición real:* en una página que muta el DOM cada 50ms sin
parar (simulando un mapa en vivo), la versión anterior de `waitForStable()`
ignoraba una solicitud de detener durante **2.55 segundos**; la corregida
responde en **0.47 segundos** — más de 5 veces más rápido, medido, no
estimado.

#### 3 — Botón "Reproducir" en el popup

Junto a Grabar, visible únicamente cuando hay un reporte importado —
oculto el resto del tiempo. Reacciona a cambios reales de storage
(`qa:replay`), no a un sondeo periódico. Validado en navegador: aparece al
importar, permanece oculto sin importación, y dispara `startReplay`
correctamente al hacer clic, respetando la misma preferencia de recarga
(`localStorage`) que ya usa el panel lateral.

#### 4 — Icono personalizado

Nueva tarjeta en Reporte: el usuario carga una imagen, que se redimensiona
**en el navegador** (recorte tipo "cover" a 128×128 vía un `<canvas>`
oculto) antes de guardarse — el archivo original nunca sale de la máquina
del usuario ni se sube a ningún lado. El resultado reemplaza el logo del
popup, del panel lateral, y de la barra de herramientas de Chrome (esta
última vía `chrome.action.setIcon`, redimensionando de nuevo con
`OffscreenCanvas` en el service worker a los tamaños que Chrome exige:
16/32/48/128).

*Garantía central del pedido:* si el dato guardado resulta corrupto o
ilegible, la extensión **nunca** debe quedar sin icono. Para los logos del
popup y del panel, esto se implementó de la forma más robusta posible: el
propio evento `error` del elemento `<img>` revierte al logo original
automáticamente, sin ninguna lógica adicional que pueda fallar. Para la
barra de herramientas, `applyToolbarIcon()` nunca relanza una excepción —
cualquier fallo se registra y se descarta, dejando el icono tal como
estaba.

*Validado con el caso crítico, no solo el camino feliz:* se guardó
deliberadamente un dato corrupto (base64 inválido) como si fuera el icono
guardado, y se confirmó que el logo revierte automáticamente al original
sin ningún error de página. Por separado, se validó el flujo completo
feliz — cargar una imagen real de prueba, confirmar el redimensionado a
128×128, la actualización inmediata de ambas superficies, y el botón
"Restablecer" devolviendo el logo original.

#### 5 — Elementos no vinculados a la paleta de colores

Auditoría de los dos documentos de la extensión (panel lateral y popup) en
busca de colores hardcodeados que ignoraran la personalización. El CSS del
panel lateral resultó limpio (cada color hexadecimal aparece una sola vez,
en su propia declaración de variable, sin duplicados sueltos). El hallazgo
real fue estructural: **el popup tiene su propio conjunto de variables CSS**
(`--ink`/`--panel`/`--brand`/`--line`/`--text`), completamente separado de
los tokens canónicos (`--c-*`) que usa el panel lateral — son dos
documentos distintos, cada uno con su propio `:root`. La paleta
personalizada (botón lápiz, en Reporte) solo tocaba el
`document.documentElement` del panel lateral; el popup nunca la leía ni la
aplicaba, así que sus colores **nunca** reflejaban lo que el usuario
personalizara, sin importar cuántas veces la guardara.

*Fix:* un mapa de equivalencia entre los nombres canónicos y los del popup,
y una función que lee el mismo `localStorage` compartido (mismo origen de
extensión) y aplica los valores correspondientes al cargar el popup —
además de escuchar el evento `storage` para mantenerse sincronizado si
ambas superficies llegan a coexistir abiertas al mismo tiempo.

*Validado:* se guardó una paleta de prueba en `localStorage` (colores
claramente distintos a los por defecto) y se confirmó, leyendo los valores
computados reales de las variables del popup tras cargar, que ambas
coinciden exactamente con lo guardado.

**Validado (general):** sintaxis de los 16 JS como módulo ES; verificación cruzada de IDs sin
huérfanos en panel lateral y popup; arranque limpio del service worker con
las tres acciones nuevas (`setCustomIcon`/`getCustomIcon`/
`clearCustomIcon`), incluida la validación de formato rechazando
correctamente un dato de icono inválido.

---

### v2.5.9c — Tres tipos de evento sin ámbito propio: Recursos, Código, Cabeceras

Tras entregar v2.5.9b, la observación directa fue: la corrección seguía
enfocada en ajustar el *contenido* de ámbitos que ya existían, pero no
verificaba que **todos** los tipos de evento visibles como chip en
Auditoría tuvieran, cada uno, una forma de llegar al Asistente. Esta
versión hace esa verificación de manera literal y exhaustiva: se listaron
los 23 tipos de evento que `TL_META` (la fuente de verdad de los chips de
Auditoría) puede mostrar, y se confirmó, tipo por tipo, si tenían un ámbito
de contexto que los expusiera.

**Resultado de la verificación:** 20 de los 23 tipos ya tenían representación
adecuada (directa o agrupada dentro de un ámbito existente: `click`/`key`/
`input`/etc. en Interacción, `route`/`navigation` en Rutas, `security` en
Seguridad, etc.). Tres no la tenían — aparecían **recortados** dentro de
otro ámbito, nunca como su propio listado completo y navegable:

- **`resource-timing`** ("Recursos" en Auditoría, 213 eventos en el caso
  reportado) — solo aparecía como los N recursos más pesados dentro de
  Performance. El resto, la inmensa mayoría, era invisible para el
  Asistente.
- **`code-block`** ("Código") — solo llegaba si su `ref` coincidía con un
  error actualmente incluido en el ámbito Errores. Un bloque resuelto bajo
  demanda (botón "Ver código" sobre un frame que no es un error) o
  correlacionado a un error recortado por el tope de cap era invisible.
- **`response-headers`** ("Cabeceras") — solo se resumía de forma agregada
  dentro de Resumen (cuántas cabeceras se auditaron, cuántas con CSP). Sus
  *hallazgos* de seguridad ya llegaban completos por otra vía (se reflejan
  como eventos `security` propios), pero el registro técnico crudo por
  petición nunca se exponía.

**Fix:** se agregaron tres ámbitos nuevos — Recursos, Código, Cabeceras —
con nombres que coinciden literalmente con la etiqueta del chip
correspondiente en Auditoría, para que la correspondencia sea obvia sin
tener que adivinar a qué ámbito pertenece cada tipo. Cada uno sigue el
mismo patrón `{total, items}` que ya se usaba en Red (2.5.9b): el listado
completo se acota por el mismo sistema de presupuesto que protege a los
demás ámbitos, pero el conteo **total** nunca se pierde ni se oculta,
aunque el detalle completo no quepa. De paso, Performance ganó el listado
completo de mediciones INP individuales (antes solo se usaban para calcular
el percentil 98 agregado, nunca se exponía la lista).

**Se confirmó, sin necesidad de cambios, que el reporte exportado ya era
completo.** `buildBundle()` incluye el `report.timeline` íntegro sin
filtrar por tipo; `redactBundle()` solo sanea texto sensible en cuatro
campos específicos (`message`/`reason`/`detalle`/`text`) cuando existen,
nunca elimina un evento ni un tipo completo. El límite de detalle que se
corrigió en esta versión existía únicamente en los ámbitos de contexto del
Asistente (un recorte deliberado para caber en el presupuesto de tokens de
una conversación) — nunca en la exportación, que no tiene esa restricción.

**Validado contra el código real:** se replicaron las cifras exactas
reportadas (213 recursos, 22 mediciones INP, 4 bloques de código, 1
cabecera auditada, entre otros tipos) y se confirmó, aserción por
aserción contra `ContextBridge.buildContext()`, que los tres ámbitos
nuevos devuelven los totales correctos y que Performance expone el total
real de mediciones INP (22) aunque el detalle liste menos por el tope de
presupuesto. Prueba adicional en navegador real: los chips "Recursos 213",
"Código 4" y "Cabeceras 1" aparecen correctamente en la UI del Asistente,
sin errores de página. Verificación cruzada de IDs sin huérfanos (los
chips se generan dinámicamente desde la configuración, sin tocar HTML);
sintaxis de los 16 JS como módulo ES.

---

### v2.5.9b — Corrección de alcance: el mismo nivel de *detalle*, no solo el mismo *conteo*

v2.5.9a corrigió que "Rutas" mostrara 0 cuando existían navegaciones reales
— pero el pedido original era más amplio: garantizar el mismo nivel de
**detalle** de la información en las tres áreas (Asistente, Auditoría,
Reporte exportado) para **todos** los ámbitos, no solo arreglar el conteo
de uno. v2.5.9a resolvió la causa estructural (una fuente única para "qué
tipos de evento pertenecen a cada ámbito") pero se quedó corta en un nivel
distinto del mismo problema: aunque el *conteo* ya coincidía en los 12
ámbitos, el *contenido* real que recibía el Asistente seguía siendo, en
varios casos, una versión reducida de lo que Auditoría muestra al detalle
de cada fila. Esta versión completa esa auditoría, ámbito por ámbito,
comparando explícitamente contra `tlDetail()` — la función que arma lo que
un humano ve al expandir un evento en Auditoría — en vez de solo comparar
números de chips.

**Hallazgo — Red enviaba solo un subconjunto, nunca la lista completa.**
El ámbito "Red" del Asistente devolvía únicamente las peticiones fallidas y
las 8 más lentas — cualquier petición exitosa y de duración normal
simplemente no existía para el Asistente, aunque Auditoría la mostrara con
su *waterfall* completo (fases DNS/TCP/TTFB/descarga). *Fix:* se agregó
`todas` con la lista completa de peticiones (acotada por el mismo sistema
de presupuesto que ya protege el resto de ámbitos) y el desglose completo
de fases en cada entrada; `fallidas`/`masLentas` se mantienen como vistas
adicionales de conveniencia, no como el único contenido disponible.

**Hallazgo — Consola excluía silenciosamente `log`/`info`.**
El *builder* de contexto filtraba solo `warn`/`error`, pero el contador de
Auditoría (el chip "Consola") cuenta **todos** los niveles — el número que
el usuario ve nunca coincidía con lo que el Asistente realmente podía leer.
*Fix:* se incluyen todos los niveles, igual que el conteo.

**Hallazgo — Interacción no incluía la señal de accesibilidad ni el INP por evento.**
Auditoría resalta visualmente cuando un elemento interactuado no tiene
nombre accesible (ni `name`/`aria-label`/texto/`placeholder`) — un hallazgo
de QA genuino — y muestra el INP medido de esa interacción específica.
Ninguno de los dos llegaba al Asistente. *Fix:* se agregó `inpMs` y
`sinNombreAccesible` a cada entrada de interacción (campos ligeros, sin
reintroducir el árbol de ancestros completo, que se había excluido antes
deliberadamente por inflar demasiado el contexto — ese trade-off se
mantiene sin cambios).

**Hallazgo — Rutas seguía incompleta incluso tras el fix de v2.5.9a.**
El fix anterior hizo que las navegaciones completas de página **aparecieran**
en el ámbito Rutas, pero solo con `tipo` y `url` — el resto de lo que
`navInfo()` captura (referrer, redirects, TTFB, DOM listo, carga total, TTI
aproximado, tamaño del documento) seguía sin representarse, aunque Auditoría
lo muestra íntegro. *Fix:* se incluye el timing completo de cada navegación.

**Validado contra el código real (no mocks de UI):** se construyó un
timeline con datos que ejercitan cada campo nuevo — una navegación con
timing completo, dos peticiones de red (una fallida, una normal), un
mensaje de consola de nivel `log` y otro `error`, y un click sobre un
elemento sin nombre accesible con INP medido — y se confirmó, aserción por
aserción contra `ContextBridge.buildContext()` real, que los cuatro campos
antes ausentes ahora llegan correctamente. Por separado, se confirmó que el
sistema de recorte automático por presupuesto sigue funcionando sin
degradarse: con 60 peticiones de red y un presupuesto reducido a propósito,
el detalle se acota a 12 elementos pero el **total real (60) nunca se
pierde** — sigue visible en `red.total` aunque el detalle completo no quepa.

**Rendimiento y seguridad (punto 6 del pedido original):** el crecimiento
de contexto que introduce este cambio está acotado por el mismo sistema de
`CAPS`/presupuesto que ya existía — no se añadió ningún mecanismo nuevo de
control de tamaño porque el existente ya cubre este caso correctamente
(confirmado con la prueba de 60 peticiones). `sanitize()` sigue truncando
strings largos y redactando claves sensibles en cada campo nuevo, igual que
en los campos que ya existían.

---

### v2.5.9a — Consistencia total entre Auditoría, Asistente y KPIs

Parte de una comparación directa, imagen contra imagen, entre la pestaña
Auditoría y la pestaña Asistente sobre el **mismo** reporte importado:
Auditoría mostraba `4 nav` en sus chips de tipo, pero el Asistente mostraba
`Rutas 0` para ese mismo reporte — el usuario nunca podía preguntarle al
Asistente sobre las navegaciones de página que sí veía con sus propios ojos
en Auditoría.

**Hallazgo — "Rutas" excluía las navegaciones completas de página, no solo en el contador.**
*Causa raíz:* existen dos tipos de evento relacionados con navegación —
`route` (cambios de ruta dentro de una SPA, sin recargar) y `navigation`
(cargas completas de página, capturadas por separado). El ámbito "Rutas"
del Asistente (`context-bridge.js`) filtraba únicamente `e.type === "route"`
— si una sesión no tenía enrutamiento SPA pero sí navegaciones completas
(el caso exacto reportado), el Asistente recibía el ámbito Rutas
**completamente vacío**, aunque los datos estuvieran íntegros en el
timeline y visibles en Auditoría.

Profundizando, esto no era un bug aislado: existían **tres listas
independientes** de "qué tipos de evento cuentan como interacción/ruta",
escritas en momentos distintos del proyecto y ya desincronizadas entre sí:
1. `scopeCount()` en `sidepanel.js` (los números de los chips de Auditoría)
   — ni siquiera sumaba `middleclick` bajo "Interacción".
2. Los *builders* de contexto del Asistente en `context-bridge.js` —
   excluían `navigation` de "Rutas".
3. `computeKpis()` en `bundle-schema.js` (el panel de KPIs de Auditoría, y
   lo que viaja en cada reporte exportado) — su propia lista, también sin
   `navigation` en el cálculo de interacciones.

Una cuarta lista (`INTERACTIVE_TYPES` en `report-engine.js`, usada para
correlacionar la latencia real de INP con cada interacción puntual) se
revisó y se dejó **intencionalmente distinta** — sirve un propósito más
angosto y específico (no tiene sentido medir INP de un `dragdrop`), no es
un caso de la misma inconsistencia.

*Fix:* se movieron las agrupaciones canónicas (`INTERACTION_TYPES`,
`ROUTE_TYPES`) al único módulo puro que de verdad comparten el service
worker y el panel lateral (`bundle-schema.js`) — no `context-bridge.js`,
que es exclusivo del panel y no puede ser importado desde el SW. Los tres
consumidores (`scopeCount`, los *builders* de contexto, `computeKpis`) leen
ahora de esa misma fuente; un cambio futuro se hace una sola vez y se
refleja en Auditoría, Asistente y KPIs por igual, sea la sesión temporal o
importada.

**Hallazgo secundario — `scroll`/`resize`, *workers* y cabeceras auditadas sin representación en ningún ámbito.**
Al auditar los 23 tipos de evento capturables contra los 12 ámbitos de
contexto, se encontraron tres tipos que no aparecían en absoluto en ningún
ámbito del Asistente pese a capturarse íntegramente: `scroll` y `resize`
(ahora incluidos en "Interacción", con la misma lista canónica) y `worker`/
`response-headers` (se añadió un resumen compacto — últimos *workers*
detectados, cantidad de respuestas auditadas y cuántas traían CSP — al
ámbito "Resumen"). Los *hallazgos* de seguridad derivados de las cabeceras
ya llegaban completos al Asistente desde antes (se reflejan como eventos
`security` propios), solo faltaba el registro técnico crudo por petición.

**Validado:** prueba contra el código puro real (`computeKpis` +
`ContextBridge.buildContext`, sin mocks de UI) reproduciendo el escenario
exacto reportado (4 eventos `navigation`, 0 `route`) — confirmado que el
Asistente ahora recibe las 4 navegaciones completas. Prueba en navegador
real comparando Auditoría y Asistente sobre el mismo reporte: `4 nav` ↔
`Rutas 4`, `13 click + 1 dblclick` ↔ `Interacción 14`, coincidencia exacta,
sin errores de página. Verificación cruzada de IDs sin huérfanos; sintaxis
de los 16 JS como módulo ES; arranque limpio del service worker con la
nueva cadena de imports (`bundle-schema.js` → `context-bridge.js` →
`sidepanel.js`).

**Rendimiento (punto 6 del pedido):** el fix no añade trabajo nuevo por
evento — son sumas sobre arreglos que ya se construían antes (`state.counts`,
`report.timeline`), simplemente leyendo una lista compartida en vez de una
hardcodeada en cada sitio. No hay impacto de memoria ni de latencia
adicional; el cambio es puramente de correctitud.

---

### v2.5.9 — Fix crítico de memoria/rendimiento durante la grabación, y URL fija del asistente

Esta versión parte de un reporte de diagnóstico dedicado (sin cambios de
código, solo análisis) sobre un problema concreto: grabar una plataforma web
con mapa en vivo hizo que el consumo de RAM del equipo pasara de 3GB a 11GB
en aproximadamente 2 minutos. El análisis identificó la causa raíz exacta —
no una fuga clásica, sino un patrón algorítmico incorrecto expuesto por una
fuente de eventos de alta frecuencia — y esta versión implementa, en el
orden de prioridad de ese reporte, las cuatro correcciones identificadas,
más un bug adicional confirmado por pruebas directas del usuario.

**Hallazgo crítico — cada evento capturado leía y reescribía el timeline completo.**
*Causa raíz:* `appendEntry()` llamaba a `getTimeline()` (una lectura completa
de `chrome.storage.local`) y luego reescribía el arreglo entero de vuelta,
**por cada evento individual capturado** — sin ningún tipo de agrupación.
Con `MAX_EVENTS` en 5000, el costo por evento crecía con el tamaño del
timeline ya acumulado: O(n) por evento, O(n²) sobre toda la sesión. En una
plataforma con actualización continua de un mapa en vivo (marcadores
moviéndose, tiles cargando, polling de posiciones), la tasa de eventos por
segundo es inusualmente alta — exactamente el peor caso de este patrón, y
explica por qué el crecimiento de RAM se acelera con el tiempo en vez de
subir de forma constante.
*Fix:* nuevo buffer de escritura diferida (`pendingEvents`). Los eventos se
acumulan en memoria y se persisten en **lote** — cada 400ms o cada 40
eventos, lo que ocurra primero — reduciendo drásticamente el número de
operaciones reales de storage. La asignación de `cid`/`seq`/`tRel` sigue
siendo inmediata y por evento (ya usaba una caché en memoria, `recCtx`, que
no necesitaba tocar el timeline). `getTimeline()` fusiona el buffer con lo
ya persistido, así que **ninguna lectura** (KPIs, exportación, conteos en
vivo, webhook) pierde visibilidad de eventos aún no volcados. Se agregó
volcado forzado en los puntos de mayor riesgo de pérdida: al detener una
grabación y como red de seguridad en `chrome.runtime.onSuspend`.
*Validado contra el código real:* 200 eventos enviados en ráfaga rápida
produjeron solo 5 escrituras reales a `storage.local.set` (98% menos
operaciones), con **cero eventos perdidos** en el timeline final. Por
separado, se confirmó que una lectura de estado 50ms después de enviar 3
eventos (muy por debajo del intervalo de 400ms) ya los refleja
correctamente, aunque el volcado a storage todavía no haya ocurrido.

**Hallazgo alto — el buffer nativo de Resource Timing del navegador nunca se gestionaba.**
*Causa raíz:* el proyecto nunca llamaba a `performance.clearResourceTimings()`
ni a `performance.setResourceTimingBufferSize()`. El navegador acumulaba una
entrada por cada recurso cargado durante **toda la sesión de grabación**, sin
límite propio — memoria de proceso, no solo del heap de JS de la extensión
(visible en el Administrador de Tareas, coherente con que el reporte
describiera RAM del equipo, no de una pestaña de extensión).
*Fix:* se amplía el buffer nativo al iniciar (`setResourceTimingBufferSize`)
y se limpia cada 30 segundos, con margen amplio sobre la ventana de 60ms que
usa `emitNetworkFull()`/`waterfallFor()` para correlacionar un fetch/XHR con
su entrada de Resource Timing — nunca se limpia algo que todavía se necesita
leer.

**Hallazgo medio — el `Set` de deduplicación de recursos crecía sin límite.**
*Causa raíz:* `_resSeen` (dedup de `resource-timing` por URL+inicio) vivía
durante toda la sesión sin límite de tamaño ni expiración — en un mapa de
tiles con URLs únicas por cada paneo/zoom, y polling con parámetros de
caché en la URL, este `Set` podía crecer a miles de entradas en minutos,
alimentando al problema principal a un ritmo alto.
*Fix:* se vacía junto con el buffer nativo (cada 30s) y, además, tiene un
límite de tamaño (2000 entradas) como red de seguridad ante ráfagas
extremas que llenaran el buffer antes de que llegara el ciclo periódico.

**Hallazgo bajo — `getSettings()` releía storage en cada evento.**
*Fix:* se cachea en memoria del service worker; solo se invalida cuando el
usuario cambia realmente la configuración (`setSettings`), que además
actualiza la caché de inmediato en vez de forzar una relectura.

**Bug adicional confirmado por el usuario — el asistente usaba la URL guardada, no la fija del proveedor.**
*Causa raíz:* en `openwebui-client.js`, `this.baseUrl = cfg.baseUrl ||
pDef.baseUrl || ""` priorizaba el valor guardado en la configuración sobre
la URL fija del proveedor (`PROVIDERS[provider].baseUrl`). El campo Base URL
se oculta en la UI para proveedores con URL fija (OpenAI/Gemini/Claude),
pero el *valor* que hubiera quedado ahí de un proveedor anterior (p. ej.
OpenWebUI) seguía persistido y ganaba la comparación — el propio docstring
del archivo ya documentaba el comportamiento *correcto* que el código no
cumplía. Se corrigió el orden de precedencia en el constructor del cliente
y, por consistencia, en los dos puntos de la UI que construían la misma
comparación (guardar configuración, probar conexión).
*Validado:* se reprodujo el escenario exacto — configurar OpenWebUI con una
URL propia, cambiar el proveedor a Gemini sin tocar el campo oculto — y se
confirmó que el cliente ahora resuelve `https://generativelanguage.
googleapis.com` (la fija) en vez de la URL vieja. Se confirmó por separado
que OpenWebUI sigue usando la URL que el usuario escribe, sin regresión.
También se validó la integración de Gemini end-to-end con credenciales
reales: una petición HTTP directa al endpoint OpenAI-compatible de Gemini
(mismo `baseUrl`, mismo `chatPath`, mismo header `Authorization: Bearer`
que arma nuestro cliente) respondió HTTP 200 con el modelo
`gemini-flash-lite-latest`.

**Validado (general):** sintaxis de los 16 JS como módulo ES; verificación
cruzada de IDs sin huérfanos en panel lateral y popup; prueba de humo
integrada contra el service worker real (arrancar, iniciar grabación,
capturar eventos, verificar conteo, detener) sin errores.

---

### v2.5.8c — Corrección de rumbo: fuera la importación de Playwright, foco total en la fidelidad del motor propio

Esta versión revierte una decisión de producto tomada en v2.5.8b, a partir de
una conversación explícita sobre el objetivo real: **99% de fidelidad al
reproducir una sesión grabada**. La conclusión, documentada aquí para que
quede como criterio de diseño y no se repita el mismo camino: interpretar un
script Playwright externo tiene un techo bajo — Playwright depende de
Node.js y de un protocolo de control externo al navegador (CDP), ninguno de
los cuales existe dentro de una extensión. Pulir un intérprete de texto
nunca iba a acercarse al 99% frente a scripts reales (que usan variables,
locators semánticos `getByRole`/`getByText`, lógica de control) — a lo sumo
mejora el reconocimiento de sintaxis, no la fidelidad de ejecución. El
camino real hacia una alta fidelidad — sea sobre Playwright importado o
sobre nuestras propias grabaciones — es el mismo: que el motor de replay
que vive dentro de la extensión reproduzca eventos cada vez más parecidos a
los que produce un usuario real. Ahí es donde se decidió invertir el
esfuerzo.

**Eliminado — importación de Playwright.** Se retiró por completo:
`src/qa/playwright-import.js`, las acciones del service worker
`loadPlaywright`/`getPlaywrightScript`/`clearPlaywright`/
`startPlaywrightReplay`, la clave de storage `qa:playwright`, la sección
"Playwright importado" de la pestaña Reporte, y el botón "▶ Playwright"
tanto del panel lateral como del popup. Se conserva intacta la función de
**exportar** a Playwright (`toPlaywright()`/`exportPlaywright`) — descargar
un `.spec.js` ejecutable con `npx playwright test` sigue siendo una
capacidad válida y distinta; lo que se elimina es únicamente la
*importación* e interpretación de un script ajeno. El checkbox "Recargar
el sitio al reproducir" (de v2.5.8b) se mantiene — aplica igual de bien a
la repetición normal de un reporte propio, nunca fue exclusivo de
Playwright.

**Pulido — fidelidad del motor de replay propio.** Tres mejoras concretas
y acotadas al motor existente (`content.js`), sin tocar su arquitectura:

- *Eventos de puntero además de mouse.* `fireMouse()` disparaba únicamente
  `MouseEvent` (`mousedown`/`mouseup`/`click`). Muchos componentes de UI
  modernos (Radix, MUI, la mayoría de librerías de drag&drop) escuchan
  específicamente `PointerEvent` y no reaccionan a un `MouseEvent`
  sintético aislado. Ahora cada acción de mouse dispara primero el
  `PointerEvent` correspondiente (`pointerdown`/`pointerup`) y luego el
  `MouseEvent`, en el mismo orden que produce un navegador real.
- *Doble click y click central con secuencia completa.* Antes se disparaba
  un único evento `dblclick`/`auxclick` aislado. Ahora el doble click
  reproduce los dos ciclos completos `down`/`up`/`click` antes del evento
  `dblclick` final — muchas apps detectan el doble click contando clicks
  individuales, no escuchando el evento sintético.
- *`beforeinput` en la escritura de formularios.* Tanto `commitValue()`
  (llenado directo) como `typeInto()` (tecleo carácter por carácter) ahora
  disparan `beforeinput` antes de aplicar el cambio de valor, replicando
  el orden real del navegador (`beforeinput` → cambio de valor → `input` →
  `change`). Algunos validadores y máscaras de formulario estrictos
  escuchan específicamente ese evento.

**Validado:** la secuencia de eventos generada por el código real (copiada
tal cual a una página HTML servida y ejecutada en Chromium vía Playwright,
**no simulada**) se instrumentó para registrar cada evento recibido. Un
click produjo exactamente `pointerdown → mousedown → pointerup → mouseup →
click`; el llenado de un input produjo exactamente `beforeinput → input →
change`, con el valor final correcto. Verificación cruzada de IDs sin
huérfanos tras la eliminación; sintaxis de los 15 JS como módulo ES;
búsqueda exhaustiva confirma cero referencias residuales a la
funcionalidad retirada.

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
