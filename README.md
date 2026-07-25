# CharlyPlugin

Plantilla base **profesional** para extensiones de Google Chrome sobre
**Manifest V3**. Trae una clase utilitaria (`CharlyAPI`) que centraliza el
acceso al navegador, al sistema/PC y a la pestaña activa, más una maqueta
mínima (popup "Hola, mundo") para confirmar que todo funciona.

Diseñada con criterio: permisos sensibles como opcionales, configuración
segura por defecto y código documentado. Sin atajos improvisados.

---

## Estructura

```
CharlyPlugin/
├── manifest.json                 # Configuración MV3
├── icons/                        # 16 / 48 / 128 px
│   ├── icon16.png
│   ├── icon48.png
│   └── icon128.png
└── src/
    ├── lib/
    │   └── CharlyAPI.js          # Clase central (toda la API)
    ├── background/
    │   └── service-worker.js     # Ciclo de vida + router de mensajería
    ├── content/
    │   └── content-script.js     # Puente DOM ↔ extensión (huella mínima)
    └── popup/
        ├── popup.html            # Maqueta "Hola, mundo"
        ├── popup.css             # Estilos (claro/oscuro)
        └── popup.js              # Demo en vivo de CharlyAPI
```

## Instalación (modo desarrollador)

1. Abre `chrome://extensions`.
2. Activa **Modo de desarrollador** (arriba a la derecha).
3. Pulsa **Cargar descomprimida** y selecciona la carpeta `CharlyPlugin/`.
4. Fija el icono y abre el popup: deberías ver **"Hola, mundo"** y los botones
   de demostración (Pestaña activa / Sistema).

## La clase `CharlyAPI`

Vive en `src/lib/CharlyAPI.js` y se importa como módulo ES. Funciona en el
**service worker** y en **páginas de extensión** (popup / options). Todos los
métodos devuelven `Promise` y propagan errores como `Error`.

```js
import CharlyAPI from "../lib/CharlyAPI.js";
const api = new CharlyAPI();

const tab     = await api.getActiveTab();
const resumen = await api.getSystemSummary();
const info    = await api.getPageInfo();
```

### Capacidades por área

| Área | Métodos principales |
|------|---------------------|
| Runtime | `getExtensionInfo`, `openOptionsPage`, `getResourceUrl` |
| Permisos | `hasPermissions`, `requestPermissions`, `removePermissions` |
| Almacenamiento | `storageSet/Get/Remove/Clear` |
| Pestañas | `getActiveTab`, `getTabs`, `createTab`, `updateTab`, `reloadTab`, `duplicateTab`, `closeTabs` |
| Ventanas | `getCurrentWindow`, `getAllWindows`, `createWindow`, `focusWindow`, `closeWindow` |
| Página actual | `executeInActiveTab`, `getPageInfo`, `getSelectedText`, `querySelectorAll`, `clickElement`, `injectCSS`, `captureScreenshot` |
| Marcadores | `getBookmarkTree`, `searchBookmarks`, `createBookmark` |
| Historial | `searchHistory` |
| Cookies | `getCookies` |
| Descargas | `download`, `searchDownloads` |
| Sistema / PC | `getCpuInfo`, `getMemoryInfo`, `getStorageInfo`, `getDisplayInfo`, `getSystemSummary` |
| Dispositivo | `getPlatformInfo`, `getUserAgent`, `getLanguages`, `getNetworkInfo`, `getBatteryInfo`, `getGeolocation` |
| Portapapeles | `clipboardWrite`, `clipboardRead` |
| Notificaciones | `notify` |
| Inactividad | `getIdleState` |
| Mensajería | `sendRuntimeMessage`, `sendTabMessage` |

### Matriz de contextos

| Capacidad | Service worker | Popup / Options | Content script |
|-----------|:---:|:---:|:---:|
| `chrome.tabs` / `windows` | ✅ | ✅ | ❌ (vía mensaje) |
| `chrome.system.*` | ✅ | ✅ | ❌ |
| `scripting` en pestaña | ✅ | ✅ | n/a |
| `navigator.getBattery` | ❌ | ✅ | ✅ |
| `navigator.clipboard` | ❌ | ✅ | limitado |
| `geolocation` | ❌ | ✅ | ✅ |
| Lectura del DOM | ❌ | vía `scripting` | ✅ directo |

> El content script accede al DOM pero tiene `chrome.*` limitado, por eso
> delega en el service worker mediante el router de mensajería.

## Configuración de seguridad

Decisiones por defecto, pensadas para ser seguras y revisables:

- **Permisos mínimos requeridos**: `storage`, `tabs`, `activeTab`,
  `scripting`, `notifications`, `idle` y `system.*`.
- **Permisos sensibles como opcionales** (`optional_permissions`):
  `bookmarks`, `history`, `cookies`, `downloads`, `clipboardRead`,
  `clipboardWrite`, `geolocation`. Se solicitan **en tiempo de ejecución**
  con `api.requestPermissions([...])`, siempre desde un gesto del usuario.
- **Sin host permissions por defecto**: el acceso a la pestaña usa `activeTab`
  (se concede al pulsar el icono). El acceso amplio (`<all_urls>`) está en
  `optional_host_permissions` y debe solicitarse explícitamente.
- **CSP estricta** para páginas de extensión: `script-src 'self'`. No hay
  scripts inline; el popup carga su lógica como módulo externo.
- **Telemetría desactivada** por defecto en la configuración inicial.

### Solicitar un permiso opcional

```js
// Desde un handler de click en el popup:
const ok = await api.requestPermissions(["bookmarks"]);
if (ok) {
  const arbol = await api.getBookmarkTree();
}
```

## Nota sobre el content script

El `content_script` se declara para `<all_urls>`, lo que en la Chrome Web Store
muestra el aviso *"Leer y cambiar tus datos en todos los sitios"*. Para
producción, **restringe `matches`** a los dominios que realmente necesites en
`manifest.json`, o elimina el bloque `content_scripts` e inyecta bajo demanda
con `chrome.scripting`.

## Extender la plantilla

- **Nuevas capacidades** → añade un método a `CharlyAPI` (mismo patrón:
  `async`, JSDoc, verificación de permiso si aplica).
- **Exponer al content script** → regístralo en la lista blanca `ACTIONS`
  del service worker.
- **Página de opciones** → crea `src/options/` y declara
  `"options_page"` en el manifest.

## Suite de QA: session replay y diagnostico (`src/qa/`)

Auditoria de calidad y session replay: graba interacciones, red, consola, estado
y errores en una linea de tiempo unica, y la exporta como reporte JSON o como
script de Cypress / Playwright.

| Archivo | Mundo | Rol |
|---------|-------|-----|
| `src/qa/injected.js` | MAIN (pagina) | Consola, `window.onerror`/`unhandledrejection`, interceptor `fetch`+`XHR`, lectura de globals, ruteo SPA y patching de funciones. |
| `src/qa/content.js` | aislado | Clicks + inputs con selector robusto, throttling/debouncing, enmascarado y persistencia en `beforeunload`. Puente al SW. |
| `src/qa/background.js` | service worker | Estado, buffer del timeline en `chrome.storage.local`, difusion de config y motor de reportes. |
| `src/qa/report-engine.js` | modulo puro | Ensambla `{ metadata, timeline }` con el `delay` entre eventos. |
| `src/qa/exporters.js` | modulo puro | Traduce el reporte a una PRUEBA ejecutable Cypress (`.cy.js`) o Playwright (`.spec.js`): no solo repite clicks, sino que AFIRMA el resultado (asercion de URL en cada navegacion, del valor real tras escribir, y guard que falla si la app lanza errores JS). Sirve como regresion en CI, headless y sin la extension. |

### Las 7 herramientas

1. **Interacciones y DOM** — clicks, **doble click, click central (`auxclick`),
   drag & drop** e inputs (`change`/`blur`), mas **teclado** (`keydown` con
   secuencias de comandos, enmascarado en campos sensibles). El selector prioriza
   `data-qa`/`data-testid`/`data-cy`/`id` no dinamico/`name` y descarta clases de
   framework (emotion `css-`, styled-components `sc-`, CSS modules, hashes),
   validando unicidad con `querySelectorAll`. Tambien **auditoria estructural por
   foco** (`focusin`): atributos HTML, subconjunto de CSS computado y `rect`.
2. **Eventos globales y consola** — `window.onerror`, `unhandledrejection`,
   errores de recurso y monkey patching de `console.log/warn/error`. Cada error y
   cada fallo de red se **correlaciona** con la ultima accion del usuario
   (`trigger: "user-action" | "automatic"` + `lastAction`).
3. **Red** — intercepta `fetch` y `XMLHttpRequest`: metodo, URL, status y tiempo.
   Nunca captura cabeceras ni cuerpos; redacta tokens en query (`?token=***`).
4. **Estado y globals** — lee rutas como `store`, `user.profile` en cada click y
   cambio de ruta.
5. **Pasos: navegacion y persistencia** — `pushState`/`replaceState`/`popstate`/
   `hashchange` para SPA; `beforeunload` respalda el buffer en `sessionStorage` y
   se reanuda al cargar (deduplicado por `id` en el SW).
6. **Optimizacion y privacidad** — `throttle` en scroll, `debounce` en resize;
   enmascara inputs `password`, `.private` y `[data-private]` como `***`.
7. **Callbacks y funciones** — monkey patching de rutas como `miApp.enviarPago`:
   graba argumentos, duracion y retorno o error (soporta Promesas).

### Flujo

```
pagina ─fetch/XHR/console/onerror/SPA/fn─▶ injected.js (MAIN)
injected.js ─window.postMessage──────────▶ content.js (aislado)
content.js  ─sendMessage({channel:"qa"})─▶ background.js ─▶ chrome.storage.local
popup       ─sendMessage({channel:"qa-control"})─▶ background.js (motor de reportes)
background  ─sendMessage({channel:"qa-config"})──▶ content.js (difusion de estado/config)
```

### Cableado en el manifest (ya aplicado)

```jsonc
"content_scripts": [
  // injected.js entra en el MUNDO PRINCIPAL en document_start: asi sus
  // interceptores de fetch/XHR y onerror quedan listos ANTES que la pagina.
  { "matches": ["<all_urls>"], "js": ["src/qa/injected.js"], "world": "MAIN", "run_at": "document_start" },
  // content.js corre en el mundo aislado (acceso a chrome.*).
  { "matches": ["<all_urls>"], "js": ["src/qa/content.js"], "run_at": "document_start" }
]
```

Con `"world": "MAIN"` no hace falta `web_accessible_resources` ni inyectar el
script a mano: Chrome (>=111) lo coloca en el contexto de la pagina. La
coordinacion injected↔content es por `window.postMessage` (handshake
`injected-ready` + config). `background.js` **no** es un segundo service worker
(MV3 solo permite uno): se **importa** desde el SW principal:

```js
// src/background/service-worker.js
import "../qa/background.js";
```

El router principal ignora los mensajes con `channel` para no interferir.

### Control desde el popup

```js
const sw = (action, extra) => chrome.runtime.sendMessage({ channel: "qa-control", action, ...extra });
await sw("start");                 // o "stop" / "toggle"
await sw("getState");              // { isRecording, count, counts, config, meta }
await sw("getReport");             // { report: { metadata, timeline } }
await sw("exportCypress");         // { script }  (.cy.js)
await sw("exportPlaywright");      // { script }  (.spec.js)
await sw("setConfig", { config }); // globals a vigilar, funciones a parchear, mascaras
await sw("clear");
```

Mientras graba, el icono muestra el badge **REC**. El popup (`src/popup/`) es una
consola premium: cinta cronologica de eventos, chips por tipo, log en vivo,
configuracion editable y descarga directa de JSON/Cypress/Playwright.

### Replay: importar y ejecutar

El popup importa un reporte JSON exportado y lo **reproduce** sobre la pestana
activa (clicks, doble/medio click, drag & drop, teclas, scroll), respetando los
delays (con control de velocidad) y mostrando un banner con boton **Detener**:

```js
// popup -> content script de la pestana activa
await chrome.tabs.sendMessage(tabId, {
  channel: "qa-replay", action: "play", report, options: { speed: 1 },
});
await chrome.tabs.sendMessage(tabId, { channel: "qa-replay", action: "stop" });
```

Durante el replay, la captura se pausa para no contaminar la grabacion con los
eventos sinteticos. **Sin atajos de teclado:** el bloque `commands` del manifest
se elimino a proposito para no interferir con la captura/replay de teclas; el
plugin se abre solo manualmente (icono o menu contextual).

**Fidelidad del replay:**
- *Escritura real* en inputs: usa el setter nativo del prototipo + `InputEvent`
  por caracter, asi frameworks como React/Vue detectan el cambio y el valor
  queda aplicado de verdad (no solo eventos de teclado vacios).
- *Espera fiel a la carga*: respeta el tiempo que el usuario espero entre
  interacciones, espera `readyState=complete` y la quietud de mutaciones, y busca
  el elemento con timeout adaptativo (hasta ~15s si la grabacion lo amerita), en
  vez de saltar pasos cuando el DOM aun no termino de renderizar.
- *Telemetria de consecuencias*: por cada paso observa las mutaciones del DOM y el
  cambio de URL, y los contrasta con el efecto ESPERADO segun la grabacion
  (navegacion, red, errores). Las inconsistencias (elemento no encontrado,
  navegacion que no ocurrio, sin cambios en el DOM) se acumulan en el job del SW
  (`getReplayTrace`) y se exponen al asistente como el ambito **Repeticion** para
  diagnosticar por que el replay no fue fiel.

## Capacidades avanzadas (v1.1)

`CharlyAPI` se amplio con control total de pestanas (grupos, sesiones, zoom,
mute, mover, descartar), acceso profundo a la pagina activa (HTML, enlaces,
imagenes, ejecucion en mundo MAIN, content scripts dinamicos), navegador
(topSites, management, browsingData, menus, alarmas, energia, panel lateral) y
sistema (perfil de dispositivo, almacenamiento, captura de escritorio). Las
capacidades que Chrome no admite como opcionales (`debugger`,
`declarativeNetRequest`, `proxy`, `tts`) tienen metodos listos que avisan que
debe anadirse el permiso al array `permissions` antes de usarlos.

## Asistente IA (panel lateral, `src/sidepanel/`)

Panel lateral nativo para **analizar** sesiones de QA conversando con un modelo
de Open WebUI. Es de solo lectura: no graba ni exporta (eso vive en el popup).
Se abre desde el boton "Abrir asistente IA" del popup o desde el menu contextual
("CharlyPlugin: abrir asistente IA").

| Archivo | Rol |
|---------|-----|
| `lib/openwebui-client.js` | Cliente OpenAI-compatible (`/api/chat/completions`), timeout + abort. |
| `lib/markdown.js` | Render Markdown seguro (escape-first, anti-XSS). Estiliza el bloque `<details>🧭 Proceso</details>` como flujo de pasos. Nunca ejecuta la salida. |
| `lib/context-bridge.js` | Extrae datos de QA del SW (solo lectura), redacta claves sensibles y arma un resumen compacto. Ambitos: Resumen, Errores, Red, Consola, Rutas, Funciones, Interaccion y **Estructura** (auditoria HTML/CSS por foco). |
| `lib/chat-cache.js` | Cache de la conversacion en `localStorage` con mantener/guardar/liberar. |
| `sidepanel.js` | Controlador: flujo bloqueante, contexto, ajustes. |

### Integracion Open WebUI

Usa el endpoint OpenAI-compatible (`POST /api/chat/completions`, `Authorization:
Bearer`). La configuracion (`baseUrl`, `model`, `apiKey`, `proxyUrl`) se guarda en
`chrome.storage.local` y es editable desde el icono de ajustes. El host debe estar
en `host_permissions` (ya incluido `https://assistant.service24gps.com/*`) para que
el `fetch` del panel evite restricciones CORS. El modelo es de un solo turno: cada
peticion reenvia instrucciones + contexto adjunto + historial reciente.

### Integracion del contexto (modelo de un solo turno)

Como el asistente no guarda memoria, cada mensaje reenvia el contexto. Para que
sea util y barato con la captura rica (arboles de origen, frames, codigo, CSS),
`context-bridge.js` aplica: **presupuesto** (`CONTEXT_BUDGET`, ~12k chars) con
**recorte por prioridad** (errores+codigo > red > consola > rutas > funciones >
interaccion > estructura); **compactacion** (selectores reducidos a su cola
identificativa con `shortSel()`, sin duplicar arbol+selector; CSS reducido;
colecciones acotadas); un **digest** en lenguaje natural que encabeza el contexto
(p. ej. "468 eventos · 42s · 5 errores (3 por accion del usuario) · 6 peticiones
fallidas"); y **cache** del contexto por (ambitos + nº de eventos). El panel
muestra el tamano estimado (`~X KB`) y avisa cuando se recorta.

El contexto viaja como material de REFERENCIA interno entre delimitadores
`<<<CONTEXTO_INTERNO>>>`, con instruccion explicita de **no reproducirlo** en la
respuesta (evita que el modelo "eco" repita el JSON en el chat).

### Acciones de captura en el panel

Ademas de analizar, el panel ofrece accesos directos a las funciones de captura
(misma mensajeria `qa-control` que el popup): **Grabar/Detener**,
**Importar/Reproducir/Detener** replay (el panel persiste, asi que el dialogo de
archivo no lo cierra) y **Configuracion de captura** (selectores a enmascarar,
variables globales a vigilar y funciones a interceptar). El boton de grabar
refleja el estado real de la grabacion.

### Robustez de los globos de chat

El render de Markdown (`markdown.js`) protege los spans de codigo antes de aplicar
enfasis (asi `snake_case` o `a*b` dentro de codigo no se vuelven cursiva), acota
la cursiva a limites de palabra, cierra bloques de codigo sin terminar y ajusta
las lineas largas (selectores) para que no rompan el globo.

### Seguridad y flujo

- **Solo lectura del contexto**: el panel usa unicamente `getState`/`getReport`/
  `getTimeline` del canal `qa-control`. Nunca `start`/`stop`/`clear`/export.
- **Sin ejecucion de la IA**: a diferencia del script de referencia, NO se porta
  el protocolo de `<script>`/acciones ejecutables. La respuesta se muestra como
  Markdown saneado y nada mas.
- **Anti-inyeccion**: el contexto se sanea (claves sensibles -> `***`, strings
  truncados) y la salida se escapa antes de renderizar.
- **Flujo fijo solicitud->respuesta**: un solo request en vuelo; la entrada se
  bloquea hasta la respuesta (sin solapamiento), con intervalo minimo entre
  envios (anti-saturacion), aborto por el usuario y descarte de respuestas
  obsoletas por numero de secuencia.

### Apertura del panel

```js
// Popup o menu contextual (gesto de usuario):
await chrome.sidePanel.open({ tabId });
```

> Nota de produccion: incrustar la API key en el cliente la hace visible a quien
> inspeccione la extension. Para entornos reales usa `proxyUrl` (un proxy que
> adjunte la credencial del lado servidor); el panel ya lo soporta.

## Compatibilidad

`minimum_chrome_version` está fijado en **116**. Las APIs usadas son estándar
de MV3; las basadas en callback (`chrome.system.*`, `chrome.idle`) se
promisifican internamente para una interfaz uniforme.

## Deuda técnica (mantenida)

Lista viva de deuda técnica conocida. Las partidas se retiran cuando se
implementan y se documentan.

### Resueltas en v2.3.0

- **Selectores frágiles en replay** → `waitForEl()` con sondeo, reintento y
  verificación de visibilidad antes de actuar.
- **Volumen de `keydown`/`focusin`** → coalescing del texto tecleado en "runs"
  por campo + dedupe de focos por selector.
- **`getComputedStyle` por foco** → una sola auditoría por selector + tope por
  sesión (`AUDIT_MAX`), acotando los reflows.
- **Sin streaming de tokens** → `chatStream()` (SSE) con render progresivo de
  Markdown y fallback a no-stream.
- **`getReport` reconstruido por consulta** → caché en el service worker
  invalidada al añadir eventos o vaciar.
- **Source maps sin resolver** → consumidor VLQ propio; los bloques de código se
  devuelven desde el origen legible cuando hay `.map`.
- **`sourceCache` sin límite** → cachés LRU con tope (~12 archivos) para texto
  fuente y source maps.
- **`code-block` solo para errores** → también eager para `console.error/warn`
  (deduplicado por `ref`).
- **Volumen del árbol de origen** → `path` se almacena como cadena compacta
  `a > b > c` en lugar de arreglo.

### Pendiente — Suite de QA / recorder

- Drag & drop sintético aproximado (sin `DataTransfer` real ni coordenadas de
  puntero); algunas librerías de DnD no reaccionan.
- Replay: se espera `readyState` y la quietud de mutaciones antes de cada paso,
  pero no el fin de animaciones/transiciones CSS especificas (pueden interceptar
  un click sobre un overlay en transicion).
- `window.scrollTo` absoluto: la posición puede no corresponder en layout
  responsive distinto.
- Ventana de correlación de error fija (1.5s): heurística; acciones async largas
  pueden marcarse como "automatic".
- `keydown` global captura teclas en iframes de terceros con selector incompleto.
- Parser del flujo de proceso (`<details>`) acoplado a un formato concreto.

### Pendiente — Asistente IA (general)

- API key embebida en el cliente (usar `proxyUrl` en producción).
- Historial de un solo turno limitado (sin memoria del lado del modelo).
- `new Error().stack` por cada `console.warn/error` tiene costo en páginas muy
  verbosas (falta opción para desactivar la captura de origen en logs).

### Pendiente — Replay + origen/código

- Reanudación del replay y el paso que navega: si el progreso no se entrega antes
  del `unload`, puede repetirse el click que navega → posible bucle.
- Pestañas abiertas antes de instalar/actualizar requieren recarga; falta avisar
  en el popup.

### Pendiente — Integración de contexto plugin↔asistente

- **[Crítica]** Conteo de tokens aproximado (`chars/4`); un payload límite aún
  puede desbordar/truncar del lado servidor.
- **[Crítica]** Sin verificación del límite real de contexto del modelo
  (`charly-pt`); el presupuesto está fijo en código.
- **[Alto]** Recorte "ciego" por prioridad, no por relevancia a la pregunta
  (falta contexto dirigido por la consulta / mini-RAG sobre el timeline).
- **[Alto]** Sin compresión semántica del timeline (secuencias repetitivas no se
  colapsan).
- **[Medio]** Historial truncado por caracteres, no por turnos semánticos.
- **[Medio]** Caché de contexto invalidada solo por nº de eventos (contenido
  podría cambiar sin cambiar el conteo; falta hash o `ts` del último evento).
- **[Medio]** Petición monolítica (todo en un único mensaje `user`).
- **[Medio]** Reconstrucción frecuente del snapshot del contexto mientras se graba.
- **[Bajo]** Sin telemetría de costo/latencia por consulta.

## CharlyAudit como evidencia de incidencias (QA + Perf + Security)

Cada grabacion es un artefacto reproducible, regresionable y auditable, sin escribir codigo:

- **QA — baseline + diff:** cada interaccion guarda una firma estructural ligera
  del DOM (titulo, URL, conteo de nodos). En el replay, la telemetria contrasta el
  efecto ESPERADO (segun la grabacion) con lo observado y aade un **diff de
  baseline** (variacion de nodos > 15% o cambio de titulo => "estructura divergente"),
  convirtiendo "fallo" en "fallo aqui, asi". Los exportadores Cypress/Playwright ya
  generan aserciones reales (URL, valor, sin errores JS).
- **Performance (Web Vitals):** en `injected.js`, `PerformanceObserver` captura
  **LCP, CLS, INP, TBT/long tasks** y recursos pesados (transferSize/duracion, sin
  cuerpos). Se emite un snapshot `web-vitals` periodico y al ocultar la pestana. El
  export a Playwright incluye un **presupuesto de performance ejecutable**: mide LCP/CLS
  en vivo y **falla si empeora** respecto a lo grabado (umbral x1.2). Ambito **Performance**.
- **Security (pasivo, ofensivo-controlado):** reusa la captura de red/origen para
  detectar **fugas** (JWT, tokens/api-key/secret, email, patrones de tarjeta) en URLs
  y parametros (reportando la *clave*, nunca el valor), **mixed content**, **cookies
  sensibles legibles por JS** (sin HttpOnly), **CSP meta ausente** y **sin HTTPS**. Cada
  hallazgo lleva severidad; se agregan al reporte y al ambito **Seguridad**, y se listan
  en los tests exportados. Complementa la auditoria externa (DNS/TLS/headers) y el BCP ISO 27001.

> Nota de privacidad: no se capturan cabeceras ni cuerpos de red; el scanner de fugas
> opera sobre URLs/parametros y metadatos, y siempre redacta la evidencia.

## Auditoria de la repeticion (ver inconsistencias) + replay premium

- **Fuente "Repeticion"** en la pestana Auditoria: muestra la telemetria del ultimo
  replay (paso a paso) con las inconsistencias resaltadas — estado, efecto esperado
  vs observado, diff de DOM y el detalle de cada problema. Al terminar un replay, el
  banner indica la **fidelidad %** y su grado (premium / buena / revisar) y remite a
  esta vista.
- **Selector resiliente en replay:** si el selector exacto no resuelve (re-render que
  cambia indices `:nth-of-type`), se prueban variantes progresivamente mas laxas
  (sin nth -> ultimos 2 segmentos -> ultimo segmento), reduciendo los "elemento no
  encontrado" sin perder identidad.

### Estandares de calidad para un replay premium
- **Fidelidad >= 98% y 0 inconsistencias** = premium. Selectores estables
  (data-testid/id) elevan la fidelidad; los `:nth-of-type` la degradan.
- **Timing:** respetar el think-time grabado + esperar `readyState` y quietud de
  mutaciones antes de actuar (ya implementado). Evita clicks sobre DOM a medio render.
- **Escritura:** valor real via setter nativo + InputEvent por caracter (frameworks).
- **Observabilidad:** cada paso deja traza (esperado vs observado + diff), de modo que
  toda divergencia sea diagnosticable ("fallo aqui, asi").

## Artefacto unico de auditoria (export = webhook = contexto IA)

Se elimino la duplicidad de import/export. Ahora hay **un solo Importar** y **un solo
Exportar**. El export produce un **bundle canonico** (`charlyaudit/audit-bundle@1`,
en `background.js:buildBundle`) que es EXACTAMENTE lo mismo que se envia por webhook y
la base del contexto del asistente. Incluye, sin excluir metadatos:
extension (nombre/version), settings + config de captura del exportador, report
completo (metadata + timeline), telemetria de replay (trace + resumen), errores
destacados y conteos. Basta para cargar un **contexto simulado** reproducible.

**Al importar** un bundle: se carga su `report` como fuente **Importado** (diferenciada
del temporal) y queda reproducible; su `settings` es **solo metadata** del exportador y
**NO reemplaza** la configuracion persistente local. Cypress/Playwright siguen siendo
generadores de prueba (artefacto distinto: codigo de test), etiquetados aparte.

## Estado de mejoras (actualizado)

Resuelto en esta iteracion:
- **Persistencia de `recState` (MV3):** el estado de grabacion en memoria se REHIDRATA
  desde el storage al (re)arrancar el service worker (`hydrateRecState`), y los
  listeners asincronos esperan `recStateReady`. Asi webRequest/onUpdated siguen
  gateando correctamente una grabacion en curso tras un reinicio del SW.

Pendientes (por prioridad de ROI):
1. **Base vectorial:** fingerprint semantico por evento (error/endpoint/paso
   normalizados) sobre `cid`/`tRel`; redaccion canonica pre-embedding; chunking del
   bundle en unidades indexables; `partitionKey` por dominio/tenant (el `contentHash`
   ya lo genera `stampIntegrity`).
2. **Performance:** INP real por interaccion (hoy aproximado), TBT por navegacion y
   waterfall de red completo (inicio/fin/tamano por `requestId`, no solo pesados).
3. **Replay:** screenshot diff (pixel) via `captureVisibleTab` para complementar el
   diff estructural; assertions de negocio inferidas del baseline en los exports.
4. **Navegacion fina:** SPA vs full load, `referrer`, redirects y correlacion
   explicita del documento cargado en el replay desde la URL de inicio.
5. **Seguridad:** re-lectura de cookies tras `Set-Cookie` por request y severidad
   agregada por sesion.
6. **UX:** ajustes mas completos (perfil/webhook/dominios), indicador visible de
   "grabando en pestana X", y mostrar assertions/known-issues del baseline en la vista.

## Estado de mejoras (actualizado 2)

Resuelto en esta iteracion:
- **Fingerprint semantico por evento (`fp`):** cada evento del timeline lleva una
  firma estable y normalizada (`fingerprintEvent` en report-engine) que ignora
  partes volatiles (numeros, ids, UUID, URLs, query) y usa el ANCLA semantica en
  interacciones (rol/nombre), no la posicion. Errores/endpoints/clicks equivalentes
  comparten `fp` entre sesiones y usuarios: es la clave de agrupacion para la base
  vectorial (junto al `cid` canonico, `tRel` monotonico y `contentHash` ya
  existentes). Se expone en el reporte, el bundle y el webhook.

Pendientes (por prioridad de ROI):
1. **Base vectorial (resto):** redaccion canonica pre-embedding; chunking del bundle
   en unidades indexables; `partitionKey` por dominio/tenant en el contrato de ingesta.
2. **Performance:** INP real por interaccion (hoy aproximado), TBT por navegacion y
   waterfall de red completo (inicio/fin/tamano por `requestId`, no solo pesados).
3. **Replay:** screenshot diff (pixel) via `captureVisibleTab`; assertions de negocio
   inferidas del baseline en los exports.
4. **Navegacion fina:** SPA vs full load, `referrer`, redirects y correlacion explicita
   del documento cargado en el replay desde la URL de inicio.
5. **Seguridad:** re-lectura de cookies tras `Set-Cookie` por request y severidad
   agregada por sesion.
6. **UX:** ajustes mas completos (perfil/webhook/dominios), indicador visible de
   "grabando en pestana X", y mostrar assertions/known-issues del baseline en la vista.

## Estado de mejoras (actualizado 3)

Resuelto en esta iteracion:
- **Redaccion canonica pre-embedding:** el bundle que se exporta y se envia por
  webhook pasa por `redactBundle` (en bundle-schema), que elimina PII/tokens del
  texto libre (email, JWT, Bearer, api/secret keys, tarjetas, hashes) ANTES de que
  salga hacia la base vectorial. No muta el reporte en vivo (el asistente conserva
  fidelidad); el `contentHash` se calcula sobre el contenido ya redactado (dedup
  correcto). Marca `redactado: true` en el envelope.

Pendientes (por prioridad de ROI):
1. **Base vectorial (resto):** chunking del bundle en unidades indexables (error,
   request lento, hallazgo, paso divergente) y `partitionKey` por dominio/tenant en
   el contrato de ingesta. (Ya listos: `cid`, `tRel`, `fp` semantico, `contentHash`,
   redaccion canonica.)
2. **Performance:** INP real por interaccion, TBT por navegacion y waterfall de red
   completo (inicio/fin/tamano por `requestId`, no solo pesados).
3. **Replay:** screenshot diff (pixel) via `captureVisibleTab`; assertions de negocio
   inferidas del baseline en los exports.
4. **Navegacion fina:** SPA vs full load, `referrer`, redirects y correlacion explicita
   del documento cargado en el replay desde la URL de inicio.
5. **Seguridad:** re-lectura de cookies tras `Set-Cookie` por request y severidad
   agregada por sesion.
6. **UX:** ajustes mas completos (perfil/webhook/dominios), indicador visible de
   "grabando en pestana X", y mostrar assertions/known-issues del baseline en la vista.

## Estado de mejoras (actualizado 4)

Resuelto en esta iteracion:
- **Webhook con reintentos + cola de reenvio:** si el POST falla (red/HTTP no-2xx),
  se reintenta con backoff exponencial (2,4,8,16,30 min, hasta 5 intentos) via
  alarma `qa-webhook-retry`. Como el bundle es un snapshot completo, cada reintento
  re-envia el estado actual (no se pierde evidencia en silencio). El estado se
  persiste en `qa:webhookPending` (intentos/proximo/at) para poder mostrarlo en UI.

Pendientes (por prioridad de ROI):
1. **Base vectorial (resto):** chunking del bundle en unidades indexables y
   `partitionKey` por dominio/tenant. (Listos: `cid`, `tRel`, `fp`, `contentHash`,
   redaccion canonica, y ahora reintentos de ingesta.)
2. **Performance:** INP real por interaccion, TBT por navegacion y waterfall de red
   completo por `requestId`.
3. **Replay:** screenshot diff (pixel); assertions de negocio inferidas del baseline.
4. **Navegacion fina:** SPA vs full load, `referrer`, redirects y correlacion del
   documento cargado en el replay.
5. **Seguridad:** re-lectura de cookies tras `Set-Cookie` por request y severidad
   agregada por sesion.
6. **UX:** ajustes mas completos, indicador visible de "grabando en pestana X",
   known-issues del baseline en la vista, e indicador de `qa:webhookPending`.

Backlog nuevo (ideas sumadas, sin priorizar):
- gzip + HMAC del bundle; muestreo de eventos ruidosos; retencion/auto-purga local;
  aviso de consentimiento al grabar; shadow DOM/iframes en captura y replay;
  plugin-health (self-diagnostico); a11y como auditoria; installId anonimo para
  correlacion cross-sesion; suite de pruebas de modulos puros en CI.

## Estado de mejoras (actualizado 5)

Resuelto en esta iteracion:
- **Chunking + partitionKey (contrato de ingesta a la vector DB):** `chunkBundle`
  fragmenta el bundle en UNIDADES INDEXABLES (error, request fallido/lento, hallazgo
  de seguridad, navegacion, vitals, worker, interaccion), excluyendo el ruido
  (scroll/resize/2xx rapidos). Cada chunk es autocontenido: `partitionKey`
  (domain:<host>, separa tenants), identidad canonica (`cid`/`fp`/`seq`/`tRel`),
  `texto` REDACTADO para embedding, `contexto` (url + entorno) y su propio
  `contentHash`. Expuesto via accion `exportChunks`. Con esto la capa de base
  vectorial queda COMPLETA (cid + tRel + fp + contentHash + redaccion + chunking).

Pendientes (por prioridad de ROI):
1. **Performance:** INP real por interaccion, TBT por navegacion y waterfall de red
   completo por `requestId` (no solo pesados/fallidos).
2. **Replay:** screenshot diff (pixel) via `captureVisibleTab`; assertions de negocio
   inferidas del baseline en los exports.
3. **Navegacion fina:** SPA vs full load, `referrer`, redirects y correlacion del
   documento cargado en el replay.
4. **Seguridad:** re-lectura de cookies tras `Set-Cookie` por request y severidad
   agregada por sesion.
5. **Ingesta (endurecer):** gzip + HMAC del bundle; enviar chunks por webhook con
   idempotencia por `contentHash`.
6. **UX:** ajustes mas completos, indicador de "grabando en pestana X",
   known-issues del baseline en la vista, indicador de `qa:webhookPending`.

Backlog (ideas sumadas): muestreo de eventos ruidosos; retencion/auto-purga local;
consentimiento al grabar; shadow DOM/iframes; plugin-health; a11y como auditoria;
installId anonimo; suite de pruebas de modulos puros en CI.

## Estado de mejoras (actualizado 6)

Resuelto en esta iteracion:
- **Firma HMAC + cabeceras de integridad en la ingesta:** el envio por webhook
  incluye `X-CharlyAudit-Signature: sha256=<hmac>` (HMAC-SHA256 del cuerpo con el
  token del webhook como secreto compartido) y `X-CharlyAudit-Content-Hash`. El
  servidor puede verificar autenticidad e integridad (evidencia no manipulada) y
  deduplicar por contentHash. Validado: la firma coincide con HMAC nativo y detecta
  cualquier manipulacion del cuerpo.

Pendientes (por prioridad de ROI):
1. **Performance:** INP real por interaccion, TBT por navegacion y waterfall de red
   completo por `requestId`.
2. **Replay:** screenshot diff (pixel) via `captureVisibleTab`; assertions de negocio
   inferidas del baseline en los exports.
3. **Navegacion fina:** SPA vs full load, `referrer`, redirects y correlacion del
   documento cargado en el replay.
4. **Seguridad:** re-lectura de cookies tras `Set-Cookie` por request y severidad
   agregada por sesion.
5. **Ingesta (resto):** gzip del cuerpo (CompressionStream) y modo webhook que envie
   chunks con idempotencia por `contentHash`. (Listo: HMAC + content-hash.)
6. **UX:** ajustes mas completos, indicador de "grabando en pestana X",
   known-issues del baseline en la vista, indicador de `qa:webhookPending`.

Backlog (ideas sumadas): muestreo de eventos ruidosos; retencion/auto-purga local;
consentimiento al grabar; shadow DOM/iframes; plugin-health; a11y como auditoria;
installId anonimo; suite de pruebas de modulos puros en CI.

## Estado de mejoras (actualizado 7)

Resuelto en esta iteracion:
- **Navegacion fina:** la carga de documento ahora distingue `tipo` (full / reload /
  back_forward via Navigation Timing), captura `referrer`, `redirects` y timings
  (`ttfbMs`, `domListoMs`); y los cambios de ruta SPA (pushState/replaceState/popstate)
  se etiquetan `tipo: "spa"` con su `referrer`. Se muestra en el timeline y fluye al
  bundle/chunks. Mejora la fidelidad de la evidencia de navegacion y la correlacion
  del documento cargado en el replay.

Pendientes (por prioridad de ROI):
1. **Performance:** INP real por interaccion, TBT por navegacion y waterfall de red
   completo por `requestId`.
2. **Replay:** screenshot diff (pixel) via `captureVisibleTab`; assertions de negocio
   inferidas del baseline en los exports.
3. **Seguridad:** re-lectura de cookies tras `Set-Cookie` por request y severidad
   agregada por sesion.
4. **Ingesta (resto):** gzip del cuerpo (CompressionStream) y modo webhook que envie
   chunks con idempotencia por `contentHash`. (Listo: HMAC + content-hash + chunking.)
5. **UX:** ajustes mas completos, indicador de "grabando en pestana X",
   known-issues del baseline en la vista, indicador de `qa:webhookPending`.

Backlog (ideas sumadas): muestreo de eventos ruidosos; retencion/auto-purga local;
consentimiento al grabar; shadow DOM/iframes; plugin-health; a11y como auditoria;
installId anonimo; suite de pruebas de modulos puros en CI.

## Estado de mejoras (actualizado 8)

Resuelto en esta iteracion:
- **KPIs agregados por sesion (`bundle.kpis`):** `computeKpis` calcula metricas
  consultables sin recorrer el timeline: eventos, duracion, errores, Web Vitals
  (LCP/CLS/INP/TBT/longtasks), red (total/fallidas/mas lenta), seguridad (total +
  por severidad critica/alta/media/baja) e interacciones, y la fidelidad del replay
  (% pasos sin inconsistencia). Se incluyen en el bundle/webhook para dashboards,
  deteccion de regresiones y agrupacion/filtrado por contexto en la base vectorial.

Pendientes (por prioridad de ROI):
1. **Performance:** INP real por interaccion, TBT por navegacion y waterfall de red
   completo por `requestId`.
2. **Replay:** screenshot diff (pixel) via `captureVisibleTab`; assertions de negocio
   inferidas del baseline en los exports.
3. **Seguridad:** re-lectura de cookies tras `Set-Cookie` por request.
4. **Ingesta (resto):** gzip del cuerpo (CompressionStream) y modo webhook que envie
   chunks con idempotencia. (Listo: HMAC + content-hash + chunking + KPIs.)
5. **UX:** ajustes mas completos, indicador de "grabando en pestana X", known-issues
   del baseline en la vista, indicador de `qa:webhookPending`, panel de KPIs.

Backlog (ideas sumadas): muestreo de eventos ruidosos; retencion/auto-purga local;
consentimiento al grabar; shadow DOM/iframes; plugin-health; a11y como auditoria;
installId anonimo; suite de pruebas de modulos puros en CI.

## Fix: boton Reproducir nunca se habilitaba en el panel lateral

**Causa raiz:** al importar un reporte/bundle desde la pestana Auditoria del panel,
el handler nunca llamaba a `refreshReplayState()` (la funcion que habilita el boton
Reproducir de la barra de acciones) — solo actualizaba la vista de la linea de
tiempo. Ademas, el sincronizador `storage.onChanged` del panel no escuchaba cambios
de `qa:replay`/`qa:replayJob`, asi que tampoco se corregia solo.

**Fix:** el import ahora llama a `refreshReplayState()` de inmediato tras cargar el
replay, y el listener de sincronizacion escucha tambien `qa:replay`/`qa:replayJob`
(asi importar desde el popup tambien habilita el boton del panel, y viceversa).
Validado con un harness que simula la seleccion de archivo real: el boton pasa de
deshabilitado a habilitado sin depender del sondeo de 4s.

## Estado de mejoras (actualizado 9)

Resuelto en esta iteracion:
- **Indicador visible de "grabando en pestana X":** el badge del icono de la
  extension ahora queda ANCLADO a la pestana grabada (`chrome.action.setBadgeText`
  con `tabId`), con el dominio en el tooltip (`CharlyAudit · grabando app.ejemplo.com`);
  otras pestanas no muestran el badge. Ademas, se inyecta un banner discreto
  directamente en la pagina grabada (Shadow DOM cerrado, no contamina el DOM del
  host) mientras dura la grabacion, y se retira al detener. Da transparencia real
  a quien usa el sitio de que esa pestana especifica esta siendo grabada.
  Validado en navegador: el banner aparece, no contamina el DOM y se retira; el
  service worker carga sin errores con la nueva firma de `updateBadge`.

Pendientes (por prioridad de ROI):
1. **Performance:** INP real por interaccion, TBT por navegacion y waterfall de red
   completo por `requestId`.
2. **Replay:** screenshot diff (pixel) via `captureVisibleTab`; assertions de negocio
   inferidas del baseline en los exports.
3. **Seguridad:** re-lectura de cookies tras `Set-Cookie` por request.
4. **Ingesta (resto):** gzip del cuerpo (CompressionStream) y modo webhook que envie
   chunks con idempotencia. (Listo: HMAC + content-hash + chunking + KPIs.)
5. **UX (resto):** ajustes mas completos (perfil/webhook/dominios); known-issues del
   baseline en la vista; indicador de `qa:webhookPending`; panel de KPIs.

Backlog (ideas sumadas): muestreo de eventos ruidosos; retencion/auto-purga local;
consentimiento explicito al grabar (mas alla del indicador visible); shadow DOM/
iframes en captura+replay; plugin-health (self-diagnostico); a11y como auditoria;
installId anonimo cross-sesion; suite de pruebas de modulos puros en CI.

## UX (resto) + analisis de accesibilidad de los elementos de accion

### Implementado

- **Panel de KPIs** (pestana Auditoria, boton "KPIs ▾"): resumen de la sesion
  (eventos, errores, LCP/CLS/INP/TBT, red total/fallida, seguridad por severidad,
  interacciones, fidelidad de replay) sin recorrer el timeline. Nueva accion de SW
  `getKpis` (reusa `computeKpis`).
- **Indicador de `qa:webhookPending`**: pill visible en el panel (junto a la fuente
  del reporte) y en el popup (pie), mostrando intentos/proximo reintento o "envio
  agotado". Nueva accion de SW `getWebhookStatus`; sincronizado en tiempo real via
  `storage.onChanged` en ambas UIs.
- **Known-issues del baseline en la vista**: cualquier click/input/tecla capturado
  sobre un elemento SIN nombre accesible (sin `name`/`aria-label`/texto/placeholder)
  se marca visualmente (⚠, fila resaltada) y explica el problema en el detalle —
  reutiliza el ancla semantica ya capturada, cero costo adicional de captura.
- **Ajustes mas completos**: validacion clara del webhook (exige HTTPS/localhost)
  ANTES de guardar en vez de fallar en silencio despues; mensaje de confirmacion
  con resumen (n.º de dominios, estado del webhook) tras guardar.

### Bug critico encontrado y corregido durante esta iteracion

`init()` en el panel lateral llamaba a una funcion `wireQA()` que **nunca existio**
(la logica de la pestana Auditoria vive en una IIFE autoejecutable `setupQaTab`).
Esto lanzaba una excepcion no capturada que **impedia que se ejecutara todo lo que
venia despues** en `init()`: la conexion inicial (`refreshConnection`), el registro
del listener `storage.onChanged` (la sincronizacion popup<->panel), la carga inicial
del indicador de webhook y el sondeo periodico de 4s. Es decir, la sincronizacion
entre pestanas implementada en una iteracion anterior nunca llegaba a activarse.
Se elimino la llamada invalida; validado en navegador real (Playwright, con
listener de `pageerror`) que la pagina carga sin excepciones y que conexion,
sincronizacion y sondeo ahora se ejecutan correctamente.

### Analisis de accesibilidad de los elementos de accion (plus final)

**Objetivo:** que el flujo de la extension —tanto visual como por teclado/lector de
pantalla— sea inequivoco: que se pueda saber en todo momento que accion se va a
tomar, cual es el estado actual (grabando/no, expandido/no, cargado/no) y que no
haya callejones sin salida en la navegacion.

**Hallazgos y correcciones aplicadas:**
1. *Botones toggle sin estado anunciado* — `act-record` (Grabar/Detener) no
   comunicaba su estado a tecnologia asistiva. Se agrego `aria-pressed`, sincronizado
   con el estado real en cada refresco.
2. *Desplegables sin relacion semantica* — `act-cfg` ya tenia `aria-expanded`; se
   replico el patron en el nuevo `tl-kpis-toggle` (`aria-expanded` + `aria-controls`
   apuntando al panel que despliega).
3. *Pestanas de fuente sin rol de pestana* — el selector Temporal/Importado/
   Repeticion tenia `role="tablist"` en el contenedor pero los botones no eran
   `role="tab"`/`aria-selected`; se corrigio para que un lector de pantalla anuncie
   correctamente cual esta activa.
4. *Etiquetas ambiguas para lector de pantalla* — los botones "Cy"/"PW" (generar
   prueba) tienen texto visual críptico; se agrego `aria-label` con el nombre
   completo ("Generar prueba Cypress/Playwright") mientras se conserva el texto
   corto visualmente (no penaliza el espacio en un panel angosto).
5. *Foco de teclado invisible* — no habia estilo de foco diferenciado; con
   `:focus-visible` en todos los elementos de accion, formularios y tabs se hace
   visible EXACTAMENTE cuando se navega por teclado (sin "iluminar" en cada click de
   mouse, que resulta molesto visualmente).
6. *Mensajes de estado sin anuncio* — los mensajes transitorios (`cfg-settings-msg`)
   no se anunciaban a lectores de pantalla al cambiar; se agrego `role="status"
   aria-live="polite"` (los `toast` de ambas UIs ya lo tenian correctamente).
7. *Boton Reproducir sin relacion con su estado textual* — se vinculo `act-play`
   con `aria-describedby="act-replay-info"` para que un lector de pantalla anuncie
   por que esta deshabilitado ("Sin replay cargado") al enfocarlo.
8. *Consistencia visual de alertas* — se reutilizo la misma clase `.tl-row.bad`
   (ya usada para inconsistencias de replay) para marcar known-issues del baseline
   en la vista normal: el mismo patron visual siempre significa "requiere atencion",
   reduciendo la carga cognitiva de aprender iconografia nueva por contexto.

**Validado:** cero errores de pagina, `aria-pressed`/`aria-expanded` reflejan el
estado real, deteccion de known-issue verificada con precision (marca solo el
elemento realmente sin nombre accesible), navegacion por Tab alcanza los controles.

**Pendiente de accesibilidad (no critico, para una pasada dedicada):** auditoria de
contraste de color formal (WCAG AA) sobre la paleta personalizable por el usuario
(la paleta es configurable, por lo que el contraste depende de la eleccion); orden
de tabulacion completo documentado (skip-links); soporte de `prefers-reduced-motion`
para la animacion del banner de "grabando" en la pagina auditada.

## Estado de mejoras (actualizado 10)

Resuelto en esta iteracion: UX (resto) completo — panel de KPIs, indicador de
webhookPending, known-issues del baseline en la vista, ajustes con validacion clara
— mas la correccion del bug critico de sincronizacion (`wireQA`) y la pasada de
accesibilidad de los elementos de accion.

Pendientes (por prioridad de ROI):
1. **Performance:** INP real por interaccion, TBT por navegacion y waterfall de red
   completo por `requestId`.
2. **Replay:** screenshot diff (pixel) via `captureVisibleTab`; assertions de negocio
   inferidas del baseline en los exports.
3. **Seguridad:** re-lectura de cookies tras `Set-Cookie` por request.
4. **Ingesta (resto):** gzip del cuerpo (CompressionStream) y modo webhook que envie
   chunks con idempotencia. (Listo: HMAC + content-hash + chunking + KPIs.)
5. **Accesibilidad (resto):** auditoria formal de contraste WCAG AA sobre la paleta
   personalizable; skip-links; `prefers-reduced-motion` en el banner de grabacion.

Backlog (ideas sumadas): muestreo de eventos ruidosos; retencion/auto-purga local;
consentimiento explicito al grabar; shadow DOM/iframes en captura+replay;
plugin-health (self-diagnostico); a11y del SITIO AUDITADO como categoria propia de
auditoria (mas alla del known-issue puntual ya agregado); installId anonimo
cross-sesion; suite de pruebas de modulos puros en CI.

## Version

**2.4.0** — sube desde 2.3.0. Incluye: fix critico de sincronizacion (`wireQA`
inexistente bloqueaba `refreshConnection`/`storage.onChanged`/sondeo periodico en
el panel), panel de KPIs, indicador de `qa:webhookPending`, known-issues del
baseline en la vista, validacion de ajustes, y pasada de accesibilidad de los
elementos de accion (aria-pressed/expanded/selected/label/describedby, foco de
teclado visible, mensajes de estado anunciados).
