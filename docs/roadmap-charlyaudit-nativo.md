# Plan de fases: reimplementar CharlyAudit de forma nativa

> Este documento existe porque v0.1.7 retiró por completo el soporte de
> la extensión de Chrome CharlyAudit del proyecto charlyWebAudit — ver la
> entrada de v0.1.7 en el [README](../README.md) para el diagnóstico
> completo de por qué. Acá se plantea CÓMO recuperar ese valor (grabación
> de sesión, KPIs, análisis de contexto) sin depender de una extensión,
> en fases concretas y verificables una por una.

## La idea central que hace esto posible

CharlyAudit fallaba por una restricción real de Chrome: la pestaña del
spec de Playwright y el panel lateral de la extensión viven en
`browserContextId` distintos, y las APIs de la extensión (`chrome.tabs.*`,
`chrome.scripting.*`) están limitadas al mismo contexto de navegador
donde la extensión corre — no pueden ver ni tocar una pestaña en otro
contexto.

**Esa restricción es específica de las APIs de extensión — no de CDP.**
Confirmado durante la investigación de v0.1.5: nuestra propia conexión
CDP (la que ya usa `browser/telemetry.py`, sin ninguna extensión de por
medio) sí ve ambos targets, con sus `browserContextId` distintos, en la
misma respuesta de `Target.getTargets()`. Un cliente CDP propio puede
adjuntarse (`Target.attachToTarget`) directamente al target del spec por
su `targetId`, sin pasar por ninguna API de extensión — y desde ahí,
escuchar eventos de red (`Network.*`), consola (`Runtime.consoleAPICalled`),
y excepciones no controladas (`Runtime.exceptionThrown`) del propio spec,
en tiempo real, mientras corre.

Esa es la base de todo este plan: **todo lo que CharlyAudit grababa se
puede capturar con un cliente CDP propio, adjuntado directamente al
target del spec — nunca con una extensión de Chrome.** Esto evita la
restricción de raíz, en vez de intentar trabajar alrededor de ella otra
vez.

## Cómo leer este plan

Cada fase:
- Tiene un **entregable concreto** (algo que termina en el reporte, no
  solo código interno).
- Es **independiente** — se puede parar después de cualquier fase y lo
  entregado hasta ahí sigue siendo útil por sí solo (nada queda a medio
  construir esperando una fase futura para funcionar).
- Tiene un **criterio de validación real** — siguiendo el mismo estándar
  que el resto del proyecto: nunca dar una fase por terminada solo porque
  el código compila, sino porque se ejerció con un caso real y se
  confirmó el resultado.

---

## Fase 0 — Prueba de concepto: adjuntar CDP directo al target del spec

**Por qué va primero:** todo el resto del plan asume que esto funciona.
Antes de construir nada encima, hay que confirmarlo con evidencia real,
no solo con la lectura de la documentación de CDP.

**Qué implica:**
- Extender `browser/telemetry.py` (o un módulo nuevo,
  `browser/session_capture.py`) para, además de sondear que el navegador
  responda, identificar el target de tipo `page` que corresponde al spec
  (filtrando por URL — no `chrome-extension://`, no `about:blank` — el
  mismo criterio que ya se usó para `_identify_tab_id`, pero vía
  `Target.getTargets()` de CDP en vez de `chrome.tabs.query()`).
- Adjuntarse a ese target con `Target.attachToTarget` (`flatten: true`) y
  confirmar que se puede:
  - Recibir al menos un evento de `Network.requestWillBeSent` mientras el
    spec navega.
  - Recibir al menos un mensaje de `Runtime.consoleAPICalled` si el spec
    hace un `console.log`.
  - Sobrevivir a que el spec navegue varias veces (`page.goto()` repetido)
    sin perder la suscripción a eventos.

**Riesgo conocido a validar:** un target puede navegar a una URL nueva
(`Page.frameNavigated`) sin que el `targetId` cambie — hay que confirmar
que los listeners de red/consola sobreviven a una navegación completa
dentro del mismo target, no solo a la carga inicial.

**Criterio de validación:** un spec real de dos o tres pasos (navegar,
interactuar, navegar de nuevo) corrido de punta a punta, con capturas de
consola reales impresas por Python mientras la corrida está en curso —
no una simulación con datos falsos.

---

## Fase 1 — Captura de red, consola y errores JS (la base de todo lo demás)

**Entregable:** una nueva sección en el reporte, "Actividad durante la
sesión", con tres listas reales: solicitudes de red (URL, método, código
de estado, si fue bloqueada), mensajes de consola (nivel, texto), y
excepciones de JavaScript no controladas (mensaje, stack).

**Por qué esta fase primero (y no KPIs o los "ámbitos"):** es exactamente
el tipo de dato que ya demostró tener valor real — el caso que motivó
gran parte de este proyecto (v0.1.5, el log real compartido) mostraba una
aserción de Playwright que fallaba porque el spec del usuario ya
capturaba errores de JS *a mano*, dentro del propio spec. Esta fase le da
esa misma capacidad a charlyWebAudit sin que el usuario tenga que
escribir ese código en cada spec.

**Qué implica:**
- `browser/session_capture.py`: clase que se adjunta al target (Fase 0),
  habilita `Network.enable`, `Runtime.enable`, `Log.enable`, y acumula
  los eventos en listas estructuradas (dataclasses), con timestamps
  relativos al inicio de la corrida.
- Correlacionar solicitudes de red con sus respuestas
  (`Network.responseReceived`) por `requestId` — para saber no solo que
  se pidió una URL, sino qué código de estado devolvió y si terminó en
  error.
- Integrar en `__main__.run_audit()`: arrancar la captura junto con la
  telemetría, detenerla cuando el spec termina, pasar los datos
  acumulados a `report/builder.py`.
- Nueva sección en la plantilla del reporte, con las mismas convenciones
  visuales ya establecidas (ver v0.1.6: no dejar que los errores
  acaparen toda la atención — mostrar también las solicitudes exitosas,
  no solo las fallidas).

**Consideración de privacidad, real y no hipotética:** las solicitudes de
red capturadas pueden incluir cabeceras de autorización, cookies, o
parámetros con datos sensibles. Antes de incluir esto en el reporte (que
puede compartirse, o —si el Asistente IA está configurado— alimentar el
prompt de análisis), hay que decidir una política de redacción explícita
(por ejemplo, no capturar el valor de cabeceras `Authorization`/`Cookie`,
solo su presencia) — no asumir que "no importa" sin decidirlo a
propósito.

**Criterio de validación:** una corrida real contra un sitio real (no
`example.com`) que tenga al menos una solicitud fallida (404 o similar) y
al menos un mensaje de consola — confirmar que el reporte generado
muestra ambos correctamente, con datos reales, no simulados.

---

## Fase 2 — KPIs derivados de la sesión capturada

**Entregable:** una sección de KPIs en el reporte (el mismo lugar donde
antes vivían los KPIs de CharlyAudit, ahora con datos propios) — cantidad
de solicitudes de red, cuántas fallaron, tamaño total transferido,
cantidad de errores de consola, duración de la navegación principal.

**Por qué depende de la Fase 1:** son números calculados directamente
sobre los datos que esa fase ya captura — no hace falta ninguna captura
nueva, solo agregación.

**Qué implica:**
- Un módulo de cálculo (`report/kpis.py`) que recibe los datos
  estructurados de la Fase 1 y devuelve un resumen — separado de la
  lógica de captura, para poder ajustar qué se calcula sin tocar cómo se
  captura.
- Extender la plantilla del reporte con la sección de KPIs (el hueco ya
  existe en el diseño — solo hay que volver a poblarlo, esta vez con
  datos propios en vez de HTML inyectado desde una extensión).

**Criterio de validación:** los números que aparecen en el reporte deben
coincidir, contados a mano, con los datos reales de una corrida de
prueba controlada (por ejemplo, un spec contra una página con una
cantidad conocida de recursos).

---

## Fase 3 — Análisis por IA enriquecido con el contexto de la sesión

**Entregable:** el análisis por IA que ya existe (`ai_playwright.py`,
desde v0.1.6) deja de ver solo el resultado de Playwright — también ve
un resumen de la actividad de red/consola/errores de la Fase 1, así que
puede señalar cosas como "la página hizo 3 solicitudes a un dominio
distinto al que se está probando" o "hubo un error de consola justo
antes de que fallara la aserción", sin que el spec del usuario tenga que
capturar nada de eso a mano.

**Qué implica:**
- Extender `_build_prompt()` en `ai_playwright.py` para incluir un
  resumen de la Fase 1 (no el volcado completo — resumido, para no
  disparar el tamaño/costo del prompt sin necesidad).
- Aplicar la misma política de redacción de datos sensibles definida en
  la Fase 1 también acá, ya que este resumen viaja a un proveedor
  externo de IA.

**Criterio de validación:** comparar el análisis generado por la IA
antes y después de este cambio, sobre la misma corrida real con un
problema real capturado (por ejemplo, una solicitud fallida) — confirmar
que el análisis posterior menciona ese hallazgo específico, no una
descripción genérica.

---

## Fase 4 — Dominios permitidos (captura filtrada)

**Entregable:** una opción en "Configurar prueba" (reincorporando algo
similar a lo que existía para la extensión, pero nativo) para limitar la
captura de red de la Fase 1 a un dominio o lista de dominios — útil
cuando el sitio bajo prueba carga recursos de terceros (analytics,
publicidad) que no aportan nada al reporte y solo agregan ruido.

**Qué implica:**
- Un campo nuevo en `config.TestConfig` (dominio o lista de dominios
  permitidos — vacío, por defecto, significa "todos", igual que hacía la
  extensión).
- Filtrar en `browser/session_capture.py` antes de acumular cada
  solicitud, no después — evita procesar/guardar datos que de todas
  formas no se van a mostrar.

**Criterio de validación:** una corrida real contra un sitio que carga
recursos de al menos dos dominios distintos, confirmando que solo los
del dominio permitido aparecen en el reporte final.

---

## Fase 5 — Línea de tiempo visual de la sesión

**Entregable:** una vista en el reporte que muestra la secuencia de
eventos capturados (navegaciones, solicitudes de red relevantes, errores)
en orden cronológico — el equivalente nativo a la pestaña "Timeline" que
tenía el panel lateral de CharlyAudit.

**Por qué va después de las fases anteriores:** es la fase con más
trabajo de diseño visual (HTML/CSS) proporcionalmente al valor nuevo que
agrega — los datos que muestra ya existen desde la Fase 1; acá el
trabajo es de presentación, no de captura.

**Qué implica:**
- Una nueva sección en `report/templates/report.html.jinja` — una lista
  vertical con marcas de tiempo relativas, coloreada por tipo de evento
  (navegación, red, error), reusando el mismo lenguaje visual ya
  establecido (bordes de color por resultado, como en la sección de
  casos de Playwright desde v0.1.6).

**Criterio de validación:** revisión visual real (captura de pantalla
del reporte renderizado, no solo confirmar que el HTML generado contiene
las etiquetas esperadas) con una corrida que tenga al menos tres tipos
de eventos distintos en la línea de tiempo.

---

## Fase 6 — Rendimiento y seguridad (Web Vitals, cabeceras)

**Entregable:** dos secciones adicionales del reporte — métricas de
rendimiento reales de la página (usando la API de `PerformanceObserver`
del propio navegador, leída vía `Runtime.evaluate` sobre el target del
spec — el mismo patrón de adjuntar directo por CDP que el resto del
plan) y un resumen de cabeceras de seguridad presentes/ausentes en la
respuesta principal (CSP, HSTS, X-Frame-Options, etc. — reusando el
criterio ya aplicado en `security-header-hardening` del propio
charlyWebAudit).

**Por qué va al final:** es la fase más especializada — valiosa, pero
con la audiencia más acotada (equipos que ya les importa performance o
seguridad específicamente) comparado con el valor más transversal de las
fases anteriores.

**Criterio de validación:** una corrida real contra un sitio con
cabeceras de seguridad conocidas de antemano (por ejemplo, un sitio
propio donde se pueda confirmar exactamente qué cabeceras están
configuradas) — confirmar que el reporte las lista con precisión, sin
falsos positivos ni negativos.

---

## Qué se descarta explícitamente, y por qué

- **Grabación de video/captura de pantalla de la sesión** — CharlyAudit
  no la tenía tampoco (más allá de capturas puntuales), y agregarla
  implicaría un costo de almacenamiento y complejidad (codificación de
  video) desproporcionado frente al valor que aportaría sobre lo que ya
  cubren las fases anteriores.
- **Cualquier mecanismo que vuelva a depender de una extensión de
  Chrome** — la razón de ser de este documento es específicamente
  evitar esa dependencia. Si en el futuro alguna fase pareciera necesitar
  una extensión para algo puntual, la respuesta por defecto debería ser
  "¿se puede hacer con CDP directo en su lugar?", no reabrir esa puerta.
