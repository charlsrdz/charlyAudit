# charlyWebAudit

CLI + GUI en Python 3.11+ que orquesta [CharlyAudit](../CharlyPlugin) junto
con un script real de `@playwright/test` (TypeScript, tal cual lo
escribiría cualquier equipo de QA) para producir un único reporte HTML: el
resultado de Playwright + un análisis de IA ámbito por ámbito (los 15
ámbitos de contexto del Asistente de CharlyAudit) sobre la misma sesión
grabada.

**Versión actual: 0.1.4**

---

## Por qué existe

CharlyAudit es una extensión de Chrome — pensada para que una persona la
use manualmente. charlyWebAudit existe para automatizar ese mismo flujo:
correr un spec real de Playwright con CharlyAudit grabando la sesión, y
que el propio Asistente de la extensión analice lo que grabó, sin que
nadie tenga que hacerlo a mano cada vez.

## Instalación

### Prerrequisitos

charlyWebAudit orquesta dos motores externos que **no instala por ti**
(salvo Chromium, ver más abajo) — conviene confirmarlos antes de instalar
nada de este proyecto:

| Requisito | Por qué hace falta | Verificar |
|---|---|---|
| **Python 3.11+** | Es el lenguaje del propio proyecto. | `python3 --version` |
| **Node.js + npm** (LTS recomendado) | Los specs que corre charlyWebAudit son de `@playwright/test`, el framework de pruebas *real* de Node — no la librería `playwright` de Python. Ver "Decisiones de arquitectura" más abajo para el porqué. | `node --version` y `npm --version` |
| **Chromium** (gestionado por Playwright) | El navegador donde se carga la extensión y corre el spec. | No hace falta instalarlo a mano — charlyWebAudit lo detecta al arrancar y **ofrece instalarlo** si falta (con reintento si la instalación falla). |
| **Linux únicamente: un entorno gráfico o `xvfb-run`** | La extensión necesita `headless: false` — en un servidor/CI Linux sin sesión gráfica hace falta un display virtual. | charlyWebAudit detecta esto solo y envuelve el comando con `xvfb-run` automáticamente si está disponible; si no, te avisa con la instrucción exacta (`apt install xvfb` en Debian/Ubuntu). |

Si no tienes Node.js instalado: descárgalo de
[nodejs.org](https://nodejs.org) (versión LTS) — es un instalador estándar
en Windows/macOS, o el gestor de paquetes de tu distribución en Linux
(`apt install nodejs npm`, etc.). charlyWebAudit **no** intenta instalar
Node automáticamente (a diferencia de Chromium): se instala de formas muy
distintas según el sistema operativo, y automatizarlo sería más frágil que
útil — si falta, charlyWebAudit se detiene con un mensaje claro de dónde
conseguirlo.

### Cómo instalar

**Opción A — binario standalone (recomendado si no quieres instalar Python):**

Descarga el ejecutable de tu sistema operativo (ver "Construir el
instalador binario" en la sección Desarrollo) y corrélo directamente — no
necesita `pip install` ni Python instalado en la máquina que lo ejecuta.
Sigue necesitando Node.js/npm (ver tabla arriba) — eso no se empaqueta
dentro del binario.

```bash
./charlywebaudit            # Linux/macOS
charlywebaudit.exe           # Windows
```

**Opción B — desde código fuente, solo CLI:**

```bash
python3 -m venv .venv && source .venv/bin/activate   # opcional pero recomendado; en Windows: .venv\Scripts\activate
pip install .
playwright install chromium   # opcional: charlyWebAudit lo ofrece solo si falta, pero instalarlo ahora evita el paso interactivo la primera vez
```

**Opción C — desde código fuente, con interfaz gráfica:**

```bash
pip install ".[gui]"
```

El extra `[gui]` agrega `tkinterweb` (visor del reporte embebido),
`pystray` (bandeja del sistema) y `pillow` — Tkinter en sí ya viene con
Python en la instalación estándar de Windows/macOS. **Excepción real en
Linux**: varias distribuciones (Debian/Ubuntu incluidas) separan Tkinter
del intérprete base — si `pip install ".[gui]"` corre bien pero
`charlywebaudit --gui` falla con `ModuleNotFoundError: No module named
'tkinter'`, instala el paquete de sistema correspondiente antes de
reintentar (`sudo apt install python3-tk` en Debian/Ubuntu, `sudo dnf
install python3-tkinter` en Fedora) — no hay forma de resolver esto solo
con `pip`, es un paquete del sistema operativo, no de PyPI.

### Primer arranque

```bash
charlywebaudit          # menú de terminal
charlywebaudit --gui    # interfaz gráfica (si instalaste el extra [gui])
```

La primera vez, te va a pedir dos cosas una sola vez cada una (después
quedan recordadas en tu configuración local — ver `config.py`):

1. **Chromium**, si no lo instalaste ya con `playwright install chromium`
   — confirma con "sí" cuando te lo ofrezca.
2. **El Asistente IA** (proveedor, modelo, API key) — se guarda localmente
   y de ahí en adelante solo se ofrece *actualizar*, nunca se vuelve a
   pedir desde cero.

La extensión CharlyAudit ya viaja empaquetada dentro de charlyWebAudit
(`charlywebaudit/vendor/charlyaudit/`) — no hace falta descargarla ni
configurar su ruta a mano; solo se te pregunta si esa copia interna no
aparece (por ejemplo, si estás corriendo un checkout de código fuente
incompleto).

## Uso

```bash
charlywebaudit          # menu de terminal (CLI)
charlywebaudit --gui    # interfaz grafica
charlywebaudit-gui      # equivalente directo a --gui
```

Abre un menú interactivo (o, con `--gui`, la ventana correspondiente):

- **Correr prueba** — la corrida completa (ver flujo abajo).
- **Configurar prueba** — ruta del script `.spec.ts`, URL, cabeceras HTTP
  personalizadas. Se recuerda entre corridas.
- **Configurar Asistente IA** — proveedor, modelo, API key. Se pide una
  sola vez; después solo se ofrece actualizar.

La GUI es una interfaz alternativa sobre el **mismo motor** — no hay dos
implementaciones del flujo de corrida, `run_audit()` es idéntico para
ambas (ver "Interfaz gráfica" más abajo, sección "Cómo se comparte el
motor con la CLI").

## El flujo de una corrida

1. Verifica Chromium (lo instala si falta, con reintento) y Node/npm (si
   falta, bloquea con instrucciones — no se instala automáticamente).
2. Genera un `playwright.config.ts` que carga CharlyAudit vía
   `launchOptions.args` y expone un puerto de depuración remota — sin
   tocar ni una línea del `.spec.ts` del usuario.
3. Lanza `npx playwright test` como subproceso de Node — el mismo camino
   que el usuario correría a mano.
4. Se conecta por CDP en paralelo y arma un mecanismo de sincronización:
   la pestaña que el spec está a punto de usar queda **congelada** en el
   instante de su creación (no navega, no ejecuta nada) hasta que
   charlyWebAudit libera la pausa explícitamente.
5. Abre el panel lateral de CharlyAudit como una pestaña propia (los
   popups nativos de extensión no son automatizables por CDP — se navega
   a su URL como cualquier página), aplica la configuración de captura
   (variables globales vigiladas), libera la pausa, identifica la pestaña
   del spec y activa la grabación apuntando explícitamente a ella.
6. Espera a que el spec termine, detiene la grabación.
7. Activa cada uno de los 15 ámbitos del Asistente **uno a la vez**, pide
   un análisis de cada uno, y desactiva ese ámbito antes de pasar al
   siguiente — aislado, no todos juntos.
8. Une el resultado de Playwright + las 15 respuestas en un solo reporte
   HTML y ofrece guardarlo.

## Decisiones de arquitectura (y por qué)

### El script del usuario es un spec real de `@playwright/test`

No una función que reciba una `page` ya abierta — un archivo TypeScript
completo con `import { test, expect } from '@playwright/test'`, corrido
por el CLI de Node, exactamente como cualquier equipo de QA ya lo hace.
Esto es lo que hace falta Node/npm como prerrequisito además de Chromium.

### La extensión tiene un ID fijo

`manifest.json` de CharlyAudit incluye una `key` RSA (par de claves en
`../CharlyPlugin` — la clave privada no se distribuye, solo determina el
ID). Sin esto, el ID de una extensión cargada sin publicar cambia en cada
carga, y no habría forma estable de construir la URL del panel lateral
(`chrome-extension://<id>/...`) de antemano.

### El perfil de Chromium es efímero — no persistente

Un spec con el `import` estándar usa las fixtures por defecto de
Playwright Test, que crean un perfil nuevo en cada corrida. No hay forma
de forzar un perfil persistente sin que el spec importe un fixture propio
— y eso violaría la regla de no tocar el script del usuario. Por eso la
"memoria" de la configuración de la extensión no vive en el perfil del
navegador: vive en el `config.json` de charlyWebAudit, y se re-aplica
sobre la extensión al inicio de cada corrida (`runner/seed.py`).

### El mecanismo de sincronización (pausa CDP)

`Target.setAutoAttach` con `waitForDebuggerOnStart: true` congela
cualquier pestaña nueva en el instante de su creación. Esto es lo que
garantiza que la grabación esté activa antes de que el spec navegue —
sin esto, hay una ventana real donde el primer `page.goto()` del spec
podría ocurrir sin que nadie lo esté grabando todavía.

**Restricción real descubierta en validación**: mientras la pestaña sigue
congelada, `chrome.tabs.query()` no la ve — Chrome no la registra como
pestaña "consultable" hasta que empieza a ejecutar algo. Además,
Playwright Test tiene un mecanismo interno de paciencia limitada: si el
navegador queda sin responder demasiado tiempo, lo da por colgado y lo
cierra. Por eso el diseño final libera la pausa **primero**, y de
inmediato (sin esperas artificiales) identifica la pestaña y activa la
grabación — la ventana de riesgo se redujo de "indefinida" a
"milisegundos de ida y vuelta CDP", muy por debajo del tiempo real que
toma cargar cualquier página (DNS/TCP/TLS), así que en la práctica no se
pierde contenido real. No es una garantía matemática perfecta como el
diseño original con el que arrancamos, pero es el punto más cercano a eso
que el comportamiento real de Playwright Test permite.

Por la misma razón, la configuración se aplica en dos fases: solo lo que
de verdad afecta qué se captura (variables vigiladas) se aplica **antes**
de liberar la pausa — rápido, a propósito. El Asistente IA y la paleta de
colores (que no afectan la grabación, solo el análisis posterior y la
apariencia) se aplican **después** de confirmar que la grabación está
activa, sin ninguna presión de tiempo.

### Comunicación MAIN↔ISOLATED del panel lateral por JS inyectado, no CDP de alto nivel

Toda la interacción con el panel lateral (clics, llenado de campos, lectura
de resultados) se hace vía `Runtime.evaluate` sobre la **misma** conexión
CDP cruda que arma el mecanismo de pausa — no se abre una segunda conexión
con Playwright de alto nivel, que competiría por el control de
`Target.setAutoAttach` sobre el mismo navegador.

## Desarrollo

### Preparar el entorno

```bash
git clone <este-repo>
cd charlyWebAudit
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[gui,build]"                         # editable + GUI + herramientas de empaquetado
playwright install chromium
```

`pip install -e .` (modo editable) es lo correcto **para desarrollar** —
los cambios en `charlywebaudit/*.py` se reflejan de inmediato sin
reinstalar. **Importante**: ese modo editable es *solo* para desarrollo —
`build/build_installer.py` exige explícitamente una instalación normal
(`pip install .`, sin `-e`) antes de construir el binario, porque una
instalación editable confunde el análisis estático de PyInstaller y
produce un ejecutable que compila pero falla al arrancar (ver "Bugs reales
corregidos" en v0.0.2 más abajo — es un problema real que se encontró
construyendo esta misma herramienta, no una precaución teórica).

### Estructura del proyecto

```
charlywebaudit/
  __main__.py          Punto de entrada CLI; run_audit() es el motor
                        completo de orquestación (compartido con la GUI)
  config.py             Configuración persistente (platformdirs)
  constants.py           Branding, valores por defecto, ID fijo de la extensión
  errors.py               Excepciones propias con mensaje + consejo accionable
  reporter.py              Interfaz Reporter — desacopla el progreso de CÓMO se muestra
  browser/                Todo lo relacionado con lanzar y controlar el navegador
    launcher.py             Orquesta: genera config, lanza Node, conecta CDP
    cdp_sync.py               Mecanismo de pausa de sincronización (cliente CDP crudo)
    extension_page.py          Interactúa con páginas de la extensión (panel lateral)
    config_gen.py               Genera playwright.config.ts dinámicamente
    chromium.py                  Detecta/instala Chromium
    node_check.py                 Detecta Node/npm/@playwright-test
    platform_utils.py              Helpers multiplataforma (Windows/macOS/Linux)
  runner/                  Lo que pasa DURANTE una corrida
    seed.py                  Aplica la configuración a la extensión
    recorder.py                Inicia/detiene la grabación
    assistant.py                 Pide análisis de los 15 ámbitos, uno a la vez
    test_exec.py                  Parsea el reporte JSON de Playwright
  report/                   Construye y renderiza el reporte final
  ui/                        La CLI (questionary + rich)
  gui/                        La interfaz gráfica (Tkinter) — ver v0.0.5/v0.0.6/v0.1.0a abajo
    theme.py                    Branding: paleta, fuentes, estilos ttk
    widgets.py                   Componentes reutilizables (Header/Card/etc — v0.0.6)
    dialogs.py                    Diálogos seguros entre hilos (v0.0.8)
    assets/                       Íconos reales de la extensión (branding compartido)
    views/                         Las ocho pantallas (formularios, ejecución, catálogo,
                                    dashboard, reporte, ayuda — v0.1.0a agregó catálogo/dashboard)
  history.py                Historial de corridas + reportes auto-guardados (v0.1.0a)
  vendor/charlyaudit/       Copia empaquetada de la extensión CharlyAudit
build/
  build_installer.py       Script de build del binario (mismo en los 3 SO)
  entrypoint.py               Wrapper que PyInstaller necesita (ver nota abajo)
```

### Correr desde código fuente durante el desarrollo

```bash
python3 -m charlywebaudit          # CLI
python3 -m charlywebaudit --gui    # GUI
```

No hay una suite de pruebas automatizadas en este proyecto — la validación
se hizo, en cada versión, corriendo la pieza real (navegador real,
extensión real, llamadas reales a la API del Asistente) y confirmando el
resultado con evidencia verificable (capturas de pantalla para la GUI,
lectura directa de `chrome.storage.local`/`localStorage` para confirmar
que una configuración se aplicó, inspección de archivos extraídos para
confirmar que un binario empaqueta lo que debe). Si vas a modificar algo,
el estándar de este proyecto es: **antes de dar un cambio por bueno,
ejecutá el camino real que ejercita, no solo confirmés que compila** — las
secciones "Qué está validado con evidencia real" y los bloques de cada
versión más abajo muestran ejemplos concretos de qué tipo de prueba
cuenta como suficiente aquí.

En Linux sin entorno gráfico (este mismo repo se desarrolló así), un
patrón útil para probar la GUI manualmente:

```bash
Xvfb :99 -screen 0 980x680x24 &
export DISPLAY=:99
python3 -m charlywebaudit --gui
```

### Construir el instalador binario

```bash
pip install ".[build]"
pip install .              # SIN -e — ver nota en "Preparar el entorno"
python build/build_installer.py
# o los wrappers de conveniencia:
#   ./build/build_unix.sh       (Linux/macOS)
#   build\build_windows.bat     (Windows)
```

Produce `dist/charlywebaudit` (Linux/macOS) o `dist/charlywebaudit.exe`
(Windows). Es el **mismo script**, sin ninguna diferencia, el que produce
el binario correcto en cada sistema operativo — PyInstaller no compila
cruzado: hay que correr este script *en* cada SO objetivo para obtener su
binario nativo (ver el detalle completo, con lo ya validado y lo
pendiente, en "Instalador binario multiplataforma" dentro de v0.0.2 más
abajo).

### Convención de versiones

`APP_VERSION` vive en dos lugares que deben coincidir siempre:
`pyproject.toml` (`[project].version`) y
`charlywebaudit/constants.py` (`APP_VERSION`) — no hay un mecanismo
automático que los sincronice, así que al subir versión hay que tocar
ambos. `charlywebaudit --version` es la forma más rápida de confirmar cuál
quedó activa en un entorno instalado o en un binario ya construido.

## v0.0.2 — Bugs, oportunidades, compatibilidad multiplataforma e instalador binario

### Bugs reales corregidos (encontrados releyendo el código, no reportados por nadie)

- **`finally` enmascarando el error real con un `NameError`.** En
  `run_audit()`, si `orchestrator.launch()` fallaba, el bloque `finally`
  intentaba limpiar una variable `run` que nunca llegó a asignarse —
  ocultando el error original detrás de un `NameError` confuso, y dejando
  el directorio temporal de la corrida sin limpiar. *Validado*: se simuló
  el fallo y se confirmó que ahora se propaga la excepción real, con
  `work_dir` limpio de todas formas.
- **Fuga de la conexión CDP en fallos parciales de `launch()`.** Si el
  navegador conectaba pero un paso posterior fallaba (p. ej. no se
  encontraba la pestaña del spec), la conexión websocket nunca se cerraba
  explícitamente. Corregido con limpieza garantizada en cualquier camino
  de fallo dentro de `launch()`.
- **`is_chromium_installed()` verificaba el binario equivocado.** Lanzaba
  Chromium con `headless=True` para comprobar la instalación, pero la app
  siempre usa `headless=False` en la práctica (las extensiones no cargan
  de forma fiable en modo headless puro) — en versiones recientes de
  Playwright, `headless=True` puede resolver a un binario *distinto*
  (`chrome-headless-shell`). La comprobación podía decir "instalado" con
  el binario equivocado, o "no instalado" teniendo el correcto. *Fix*:
  comprobar la existencia del binario exacto (`executable_path`) en disco,
  sin lanzar ningún proceso — de paso, ~780ms en vez de varios segundos.
- **`"npx"` sin resolver en `launcher.py` — bug real de Windows.** Pasar el
  nombre desnudo a `subprocess.Popen` sin `shell=True` puede fallar en
  Windows: los ejecutables de npm son en realidad scripts `.cmd`, y
  `CreateProcess` no resuelve la extensión igual que `cmd.exe`. *Fix*: un
  helper compartido (`resolve_executable`) que siempre usa la ruta
  completa que devuelve `shutil.which()`.
- **`os.chmod` podía abortar todo el guardado de configuración.** Si el
  ajuste de permisos fallaba (posible en Windows, donde el modelo de
  permisos es distinto), el `except OSError` envolvente lo trataba como si
  el archivo entero no se hubiera guardado — aunque sí se hubiera escrito
  correctamente. Separado en un intento best-effort independiente.
- **Bug real de empaquetado (PyInstaller), encontrado construyendo el
  instalador**: apuntar PyInstaller directo a `charlywebaudit/__main__.py`
  lo ejecuta como script suelto, sin paquete padre — sus imports relativos
  internos fallan. Resuelto con `build/entrypoint.py`, un wrapper fuera del
  paquete. Un segundo bug relacionado: una instalación editable
  (`pip install -e .`) confunde el análisis estático de PyInstaller y
  produce un binario que *compila* pero falla al *arrancar* con
  `ModuleNotFoundError` — sin ningún aviso durante el build en sí. Ahora
  `build_installer.py` verifica esto explícitamente antes de invocar
  PyInstaller, con un mensaje claro de qué corregir.
- Limpieza de código muerto (`profile_dir`, `seeded`,
  `default_profile_dir()`) — residuos del diseño original de perfil
  persistente, abandonado en v0.0.1 al descubrir la restricción real de
  perfiles efímeros.

### Oportunidades implementadas

- **La extensión CharlyAudit viaja empaquetada dentro de charlyWebAudit**
  (`charlywebaudit/vendor/charlyaudit/`) — en v0.0.1 había que localizarla
  y configurar su ruta manualmente en cada instalación nueva; ahora se usa
  automáticamente si no hay una ruta personalizada configurada. *Validado*:
  wheel real instalado en un entorno virtual limpio, extensión completa
  (con su `key` fija) disponible sin ninguna configuración manual.
- **`--version`/`--help`** — CLI mínima estándar (vía `argparse`, sin
  dependencias nuevas), útil para scripts y para confirmar qué versión
  corresponde a un binario instalado.
- **Auto-detección y uso de `xvfb-run` en Linux sin entorno gráfico.**
  CharlyAudit necesita `headless: false` — en un servidor/CI Linux sin
  `$DISPLAY`, eso fallaría en cada corrida. Ahora se detecta y se envuelve
  el comando automáticamente si `xvfb-run` está disponible (o se avisa
  claramente si no lo está). *Validado*: corrida completa sin `$DISPLAY`
  ni envolver nada manualmente — antes había que anteponer `xvfb-run -a`
  a mano en cada prueba.

### Compatibilidad Windows/macOS (análisis, sin poder ejecutar en esos SO)

Este proyecto se desarrolló y validó en Linux — todo lo de esta sección es
**análisis de código, no ejecución real** en Windows/macOS. Se documenta
así de manera explícita en vez de dar por sentado que "debería funcionar".

- **Rutas**: todo el proyecto usa `pathlib.Path` y `platformdirs` — ambos
  ya manejan las diferencias de SO internamente, no hay lógica de rutas
  hecha a mano en ningún lado.
- **`subprocess` con ejecutables de npm**: corregido (ver bugs arriba) —
  ahora siempre se usa la ruta resuelta por `shutil.which()`, nunca el
  nombre desnudo, en las tres plataformas.
- **Permisos del archivo de configuración**: `chmod 600` funciona tal cual
  en Linux/macOS. En Windows, `os.chmod` con banderas POSIX no ofrece la
  misma protección — el modelo de permisos de Windows es por ACLs, no bits
  rwx; `os.chmod` ahí solo puede aproximar alternando el atributo de
  solo-lectura. Documentado como limitación conocida, no se intentó
  implementar el equivalente en ACLs de Windows (fuera de alcance de esta
  versión).
- **Consola con colores (`rich`/`questionary`)**: ambas librerías manejan
  la compatibilidad con terminales de Windows internamente (`rich` recurre
  a `colorama` cuando hace falta) — no se necesitó código propio para esto.
- **Firma de código**: los binarios que produce PyInstaller no están
  firmados. En Windows, un `.exe` sin firmar dispara la advertencia de
  SmartScreen ("Windows protegió su PC"); en macOS, un binario transferido
  sin notarización queda marcado por Gatekeeper y no se abre con doble clic
  sin un paso adicional (clic derecho → Abrir, o `xattr -d
  com.apple.quarantine`). Firmar correctamente requiere un certificado de
  desarrollador de cada plataforma — fuera de alcance de esta versión;
  documentado aquí para que no sea una sorpresa al distribuir el binario.

### Instalador binario multiplataforma (punto 4 del pedido)

`build/build_installer.py` construye un ejecutable standalone (PyInstaller,
`--onefile`) que no necesita Python instalado para correr. Es el **mismo
script**, sin ninguna diferencia, el que produce el binario correcto en
cada sistema operativo — PyInstaller no compila cruzado: hay que correr
este script *en* cada SO objetivo para obtener su binario nativo. Eso es
lo que lo hace "multiplataforma": el sistema de build es idéntico en los
tres SO, no que un solo comando produzca los tres binarios a la vez.

```bash
pip install ".[build]"
pip install .              # NO -e (ver nota de compatibilidad abajo)
python build/build_installer.py
# o los wrappers de conveniencia:
#   ./build/build_unix.sh       (Linux/macOS)
#   build\build_windows.bat     (Windows)
```

Produce `dist/charlywebaudit` (Linux/macOS) o `dist/charlywebaudit.exe`
(Windows).

**Validado de verdad, no solo construido**: el binario Linux se construyó y
se probó de forma aislada — copiado a un directorio limpio, con el paquete
`charlywebaudit` **desinstalado** de pip, confirmando que el binario corre
sin ninguna instalación de Python activa. `pyi-archive_viewer` confirmó los
26 archivos de la extensión embebidos completos (`manifest.json` con su
`key`, cada script de `src/`). En tiempo de ejecución, se confirmó que
PyInstaller extrae la extensión a la ruta exacta que `bundled_extension_path()`
espera. `--version`/`--help` responden correctamente; la corrida completa
hacia el menú interactivo renderiza el banner real y la configuración
persistida, fallando únicamente donde es *esperado* que falle (sin una
terminal real conectada — `questionary` necesita un TTY genuino, no es una
limitación del binario).

**No construido ni probado en esta sesión** (Linux, sin acceso a Windows ni
macOS): los binarios de esas dos plataformas. El script es idéntico y no
tiene ninguna rama de código específica de Linux que pudiera fallar en
otro SO, pero "el código no debería fallar" no es lo mismo que "se probó y
funcionó" — esa validación queda pendiente de correr en cada plataforma
real.

## v0.0.5 — Interfaz gráfica (Tkinter), branding, modo segundo plano y visualización completa

### 1 — Interfaz gráfica multiplataforma (Tkinter)

`charlywebaudit/gui/` — una GUI de escritorio construida sobre Tkinter
(incluida en la librería estándar de Python en Windows/macOS/Linux, sin
dependencias de sistema adicionales más allá de lo que ya trae Python en
la mayoría de instalaciones — en este entorno de desarrollo Linux hizo
falta instalar el paquete `python3-tk` a nivel de sistema, algo a tener en
cuenta al documentar requisitos para usuarios Linux).

**El problema de arquitectura central**: todo el motor de orquestación
(`browser/`, `runner/`, `__main__.run_audit`) está construido sobre
`asyncio` — corre corrutinas, espera conexiones CDP, procesos de Node,
etc. El bucle principal de Tkinter es síncrono y de un solo hilo: no puede
bloquearse esperando una corrida que tarda minutos sin congelar toda la
interfaz. `gui/async_bridge.py` resuelve esto con un event loop de asyncio
corriendo en un hilo dedicado, sometiendo corrutinas con
`asyncio.run_coroutine_threadsafe()` y devolviendo el progreso a Tkinter
por una `queue.Queue` (la única forma segura de cruzar la frontera
hilo-a-interfaz — tocar un widget de Tkinter directamente desde otro hilo
no es seguro).

**Cómo se comparte el motor con la CLI**: se extrajo `reporter.py`, una
interfaz mínima (`Reporter`) con métodos como `.info()`/`.success()`/
`.error()` — antes `run_audit()` llamaba directamente a los `print_*` de
`rich` (acoplado a terminal). Ahora `run_audit()` solo conoce esta
interfaz; la CLI implementa `CliReporter` (envuelve los mismos `print_*`
de siempre, comportamiento idéntico) y la GUI implementa `QueueReporter`
(empuja a la cola thread-safe). **Un solo motor, dos superficies** — no
hay una segunda implementación de la lógica de corrida para la GUI.

*Validado con evidencia real*: con el `mainloop()` real de Tkinter
corriendo (no simulado), se confirmó que la interfaz procesó 35
actualizaciones de progreso mientras una corrutina de 0.5 segundos corría
en paralelo en el hilo de fondo — nunca se congeló. Se confirmó también
que el refactor de `run_audit()` para aceptar un `Reporter` no cambió en
absoluto el comportamiento existente de la CLI (mismo error se propaga
igual que antes del refactor).

### 2 — Branding (mismo diseño que la extensión)

`gui/theme.py` toma como base la paleta real de CharlyAudit — el mismo
naranja de marca (`#b87619`, la misma constante `DEFAULT_PALETTE` que ya
se siembra en la configuración de la extensión desde v0.0.1) y los mismos
tokens oscuros que usa `sidepanel.css` de la extensión
(`--c-bg`/`--c-surface`/`--c-border`/etc.) — la CLI, la extensión y ahora
la GUI comparten una sola identidad visual, ninguna inventada por
separado. Los íconos de la GUI y de la bandeja del sistema son los mismos
PNG reales de `vendor/charlyaudit/icons/`.

*Validado*: capturas de pantalla reales (bajo Xvfb) de la ventana
renderizada, confirmando fondo oscuro, naranja de marca en títulos y
botones primarios, y la paleta completa aplicada de forma consistente en
las cinco pantallas de la app.

### 3 — Modo segundo plano (bandeja del sistema / barra de estado)

`gui/tray.py` — minimizar la ventana a un ícono de bandeja (Windows/Linux)
o barra de menú (macOS) en vez de cerrar la app, vía `pystray`. Se eligió
`pystray` porque ya declara sus propias dependencias nativas condicionales
por sistema operativo en su propio paquete (`pyobjc-framework-Quartz` en
macOS, `python-xlib` en Linux, API Win32 nativa en Windows) — no hace
falta lógica propia de detección de plataforma para instalar lo correcto
en cada una.

**Nota de UX honesta sobre macOS**: a diferencia de Windows/Linux, lo
convencional en macOS es que una app minimizada siga viviendo en el Dock,
no solo en la barra de menú — pystray coloca el ícono en la barra de menú
(NSStatusBar) igual que en los otros dos sistemas, funcionalmente
equivalente a "estado de visibilidad mínima" mientras que se aparta un
poco de la convención más común de macOS (integración nativa de Dock,
fuera de alcance de esta versión).

El botón de cerrar de la ventana (y un botón explícito "Minimizar a la
bandeja", por si el gesto de cerrar-para-minimizar no es descubrible en
algunos gestores de ventanas) ocultan la ventana (`withdraw()`) y arrancan
el ícono; el menú del ícono ofrece "Abrir", "Correr prueba ahora" (dispara
una corrida sin necesidad de restaurar la ventana primero) y "Salir" (cierre
real, no minimizado).

*Validado*: ciclo de vida completo probado con la ventana Tk real —
visible → minimizado (`withdraw` confirmado) → ícono de bandeja activo →
restaurado (`deiconify` confirmado) → ícono detenido. 5/5 aserciones
correctas, repetido en dos corridas independientes.

### 4 — Visualización completa: reporte, configuraciones, formularios

Las cinco pantallas de la GUI (`gui/views/`):

- **Inicio** — resumen del estado de configuración (script/URL/Asistente)
  y accesos directos.
- **Configurar prueba** — formulario completo: selector de archivo para el
  script (`.spec.ts`), campo de URL, editor de cabeceras HTTP
  personalizadas (agregar/quitar filas dinámicamente).
- **Asistente IA** — proveedor (selector), modelo, API key (con
  mostrar/ocultar).
- **Ejecutar** — la corrida en vivo: progreso en tiempo real (la misma
  información que vería la CLI, coloreada por tipo — sección/info/éxito/
  advertencia/error), conectado al motor real vía `async_bridge.py`.
- **Reporte** — el hallazgo técnico más importante de esta versión:
  **`tkinterweb` (el renderizador HTML embebido) no soporta variables CSS**
  (`var(--token)`) — confirmado comparando capturas de pantalla antes/después:
  con variables, el texto queda invisible (mismo color que el fondo,
  porque ninguno de los dos resuelve la variable); con colores literales,
  renderiza correctamente. En vez de mantener una plantilla HTML duplicada
  solo para la GUI, `gui/report_render.py` post-procesa el mismo HTML que
  ya genera `report/html.py` (una sola fuente de verdad para el CSS),
  sustituyendo cada `var(--token)` por su valor literal antes de
  entregárselo a `tkinterweb`. El botón "Abrir en el navegador" sirve el
  HTML **sin adaptar** (con las variables CSS intactas) para la fidelidad
  visual completa que un navegador real sí soporta.

*Validado*: reporte real (con casos de Playwright pasando/fallando y
respuestas reales de ámbitos del Asistente) renderizado dentro de la
ventana — capturas de pantalla confirmando colores correctos (verde real
para "passed", rojo real para "failed", naranja de marca en títulos). Flujo
de punta a punta confirmado: correr una prueba navega automáticamente a la
pestaña Reporte con el resultado ya cargado. Formulario de configuración
probado de forma interactiva real (no solo visual): llenar campos, hacer
clic en Guardar, y confirmar que la configuración se persistió en disco y
se reflejó en la pantalla de Inicio.

### El instalador binario ahora incluye la GUI

`build/build_installer.py` se actualizó para empaquetar también los
recursos de la GUI: los íconos propios, los datos internos de
`tkinterweb` (trae su propia hoja de estilos en `.tcl` que PyInstaller no
detecta por análisis estático de código Python), y los hidden-imports de
`pystray` correspondientes a la plataforma donde se construye (pystray
elige su backend en tiempo de ejecución; PyInstaller no siempre sigue esa
selección dinámica por análisis estático).

*Validado con el mismo rigor que en v0.0.2*: el binario completo (~115MB,
con Tkinter/tkinterweb/pystray/Pillow empaquetados) se probó de forma
aislada — sin ninguna instalación de Python activa — y `charlywebaudit
--gui` mostró la ventana completa, idéntica a la versión de código
fuente. Nota honesta sobre el proceso de validación: en un primer intento,
el binario pareció no mostrar ninguna ventana — tras diagnosticar
metódicamente (aislando pieza por pieza: Tkinter solo, Tkinter+
async_bridge, la app completa con impresión de cada paso), se confirmó que
no era un bug — un binario de ese tamaño (con NumPy/PIL/cryptography
empaquetados como dependencias transitivas) tarda varios segundos en
autoextraerse antes de que el código Python arranque, y las primeras
comprobaciones no esperaron lo suficiente. Se documenta el diagnóstico
aquí porque forma parte de la validación real, no para ocultar que hubo
una falsa alarma en el camino.

## v0.0.6 — Auditoría de usabilidad, branding real, estructura para mantenibilidad, Ayuda

Esta versión arrancó con una auditoría real, no con suposiciones: antes de
tocar código, se capturaron las seis pantallas de la GUI tal como estaban
(bajo Xvfb, con datos de muestra reales) y se revisaron con el mismo
criterio con el que se validó todo lo demás en este proyecto — mirar el
resultado real, no asumir que "debería verse bien". Esa auditoría encontró
9 problemas concretos, dos de ellos serios.

### Bugs de usabilidad reales, encontrados con evidencia visual

- **El combobox "Proveedor" era prácticamente ilegible** — texto lavado
  sobre fondo claro, rompiendo por completo el tema oscuro. Causa real:
  con el tema `ttk` "clam", `style.configure()` no alcanza para el estado
  `readonly` (el que usa este combobox) — hace falta un `style.map()`
  explícito por estado, o el widget cae a colores del sistema. El listado
  desplegable (al hacer clic en la flecha) es además un widget de Tk
  aparte que `ttk.Style` **no cubre en absoluto** — necesita
  `root.option_add()` por separado.
- **El checkbox "mostrar" (junto a la API key) no mostraba ningún texto.**
  Misma causa raíz: `TCheckbutton` con "clam" necesita `style.map()` para
  el color del texto en todos los estados, no basta con `configure()`.
- **Sin ícono de ventana/barra de tareas** — nunca se llamó
  `root.iconphoto()`. La ventana mostraba la pluma genérica de Tk en vez
  de la marca de CharlyAudit.
- **Barra de progreso confusa en reposo** — un `Progressbar` en modo
  indeterminado sin iniciar muestra un pequeño segmento fijo por diseño de
  `ttk` — visualmente parecía una corrida a medio completar aunque no
  hubiera ninguna corrida activa. Ahora solo aparece mientras corre algo.
- **Contraste insuficiente en botones deshabilitados** — el color usado
  para el estado `disabled` estaba muy cerca del color normal del botón.
- **Espacio vacío desproporcionado** en Inicio, Configurar prueba y
  Asistente IA — más de la mitad de la ventana sin ningún contenido en
  Inicio, sin ningún logo ni guía para alguien que abre la app por primera
  vez.
- **Regresión introducida durante el propio refactor de esta versión**: al
  consolidar el patrón de encabezado (título + subtítulo) en un solo
  componente (`Header`), un subtítulo largo se cortaba sin ajustar línea —
  la versión original de cada vista tenía un salto de línea manual
  (`\n`) que se perdió al unificar el texto en una sola cadena. Se
  encontró en la misma pasada de validación visual que confirmó el resto
  de los arreglos, antes de darlo por terminado.
- **Contenido inaccesible al tamaño mínimo real de la ventana** (820×600):
  la tarjeta "Requisitos" de la nueva sección Ayuda quedaba completamente
  fuera de vista, sin ninguna forma de llegar a ella — encontrado
  probando la ventana en su tamaño mínimo documentado, no solo en el
  tamaño por defecto (980×680) donde el problema no era visible.
- **`AssistantConfigView` no tenía `refresh()`** — a diferencia de
  `HomeView` (que sí lo tenía desde v0.0.5), si la configuración cambiaba
  desde otra vista el estado "Configurado/Sin configurar" podía quedar
  desactualizado sin ninguna forma de refrescarlo salvo reabrir la app.

*Todos y cada uno de estos, corregidos y confirmados con captura de
pantalla real antes/después — no solo revisados en el código.*

### Branding real (punto 2 del pedido)

- `apply_window_icon()` (`gui/theme.py`) aplica el ícono real de
  CharlyAudit (16/48/128px) a la ventana — título, barra de tareas,
  alt-tab.
- `BrandHeader` (`gui/widgets.py`) muestra el logo real **dentro** de la
  propia ventana (Inicio, Ayuda) — antes de esta versión, ninguna pantalla
  mostraba el ícono real más que en la barra de título.
- El combobox y el checkbox corregidos (arriba) también son, en el fondo,
  un problema de branding: un tema oscuro con un widget que se cae a
  colores claros del sistema rompe la identidad visual tanto como un color
  equivocado a propósito.

### Estructura del proyecto para mantenibilidad

`gui/widgets.py` — nuevo módulo con los componentes que las seis vistas
repetían cada una a su manera, con pequeñas inconsistencias entre sí:

- `Header` — título + subtítulo + acciones alineadas a la derecha.
- `Card` — contenedor con borde y título, mismo lenguaje visual que las
  tarjetas del reporte HTML (para que la GUI y el reporte se sientan parte
  de la misma familia visual).
- `StatusRow` — fila "Etiqueta: valor" con color según si el valor
  representa un estado resuelto o pendiente.
- `EmptyState` — estado vacío consistente, con acción opcional.
- `ScrollableFrame` — contenedor con scroll vertical (ver el fix de
  "Requisitos" arriba); reutilizable en cualquier vista futura cuyo
  contenido pueda crecer más que la ventana.
- `BrandHeader`, `logo_image()` — el logo real, listo para usar en
  cualquier pantalla.

Las seis vistas se reescribieron sobre estos componentes — un cambio de
diseño (por ejemplo, el espaciado de un encabezado) se hace una vez en
`widgets.py`, no en seis archivos por separado. Esto es lo que hizo
posible, de paso, encontrar y corregir la regresión del subtítulo cortado
antes de que llegara a producción: el bug estaba en un solo lugar
(`Header`), no replicado con variaciones sutiles en cada vista.

### Sección Ayuda, con información del autor

Sexta pestaña (`gui/views/help_view.py`): qué es la herramienta, el flujo
de una corrida paso a paso, los requisitos, y — el punto explícito del
pedido — autor (`RedGPS`, vía las constantes centralizadas
`AUTHOR_NAME`/`AUTHOR_DESCRIPTION` en `constants.py`), versión, y enlaces.
Con scroll (`ScrollableFrame`) para que el contenido nunca quede
inaccesible sin importar el tamaño de la ventana.

## v0.0.7 — Bug real reportado en producción, y el escaneo que destapó

Un usuario real corrió `charlywebaudit-gui` en su máquina (Linux,
entorno virtual con `pip install .` normal) y se encontró con un
traceback crudo de Python en vez de la interfaz gráfica. Reportó también
el mensaje de `charlywebaudit --gui`, que decía `pip install "."` — sin
el extra `[gui]` que sí estaba en el código fuente.

### El bug reportado, diagnosticado con el error exacto que compartió

```
ModuleNotFoundError: No module named 'tkinter'
```

`charlywebaudit-gui` (el entry point directo, declarado en
`pyproject.toml`) apuntaba a `charlywebaudit.gui.app:main` — y
`gui/app.py` hace `import tkinter` a nivel de módulo, sin ninguna
protección. `charlywebaudit --gui` (el flag de la CLI) sí tenía un
try/except, pero era un camino de código *separado y distinto* —
exactamente el tipo de duplicación que deja un hueco cuando solo se
protege uno de los dos caminos.

*Fix*: `gui/__init__.py` — un punto de entrada seguro, única fuente de
verdad, que usan tanto `charlywebaudit-gui` (pyproject.toml) como
`charlywebaudit --gui` (`__main__.py`). No importa nada de `gui.app`
hasta confirmar que Tkinter está disponible. *Validado*: se simuló el
error exacto que reportó el usuario (monkey-patching el import de
`tkinter` para que fallara) y se confirmó `sys.exit(1)` limpio, con un
mensaje que distingue explícitamente dos causas que antes se confundían
en un solo mensaje genérico:

- **Falta Tkinter** → instrucción por sistema operativo (`sudo apt
  install python3-tk` en Debian/Ubuntu — el caso real del usuario —,
  equivalentes para Fedora/Arch/Windows/macOS). Tkinter es un paquete del
  **sistema operativo**, `pip` nunca puede instalarlo — decirle a alguien
  "pip install X" para este error específico no solo no ayuda, es
  directamente incorrecto.
- **Falta una dependencia de pip** (`tkinterweb`/`pystray`/`pillow`) →
  ahí sí, `pip install ".[gui]"`.

### El segundo bug, encontrado diagnosticando el primero

El mensaje que compartió el usuario —
`Instala las dependencias con: pip install "."` — le faltaba el `[gui]`
que sí estaba escrito en el código fuente. Causa real: ese mensaje se
mostraba con `rich.console.Console.print()` en modo marcado (`markup`)
activo, y **Rich interpreta `[gui]` como una etiqueta de estilo** — al no
existir un estilo llamado "gui", lo elimina en silencio del texto
visible. Confirmado reproduciendo el mensaje exacto y viendo `[gui]`
desaparecer.

### El escaneo de bugs que pidió el usuario — encontró algo más serio

El mismo patrón (interpolar texto dinámico dentro de una cadena con
marcado de Rich activo, sin escapar) apareció en más lugares — y en uno
de ellos, la consecuencia es **pérdida real de información**, no solo
cosmética:

- **`Reporter.raw()`** — muestra la salida *cruda* de `npx playwright
  test`. npm y Playwright usan corchetes en su propio formato de log
  (`[WARN]`, nombres de test como `login [flaky]`). Con el marcado de
  Rich activo, cualquier fragmento entre corchetes se interpretaba como
  una etiqueta de estilo y **desaparecía en silencio** del texto
  mostrado. *Validado*: se probó con salida realista de
  npm/Playwright y se confirmó que `[flaky]` y `[otra-cosa]`
  desaparecían por completo — información de diagnóstico real perdida,
  no un detalle visual. *Fix*: `console.print(text, markup=False)` para
  contenido no confiable/externo.
- `ui/menu.py` (resumen de configuración: URL, ruta del script,
  cabeceras) y `ui/forms.py` (nombre/valor de cada cabecera que el
  usuario escribe) interpolaban texto controlado por el usuario dentro de
  marcado de Rich sin escapar — el mismo riesgo, con datos que sí puede
  llegar a escribir alguien (una URL con `?a[]=1`, una cabecera con
  corchetes en el nombre). *Fix*: `rich.markup.escape()` sobre el valor
  dinámico antes de interpolarlo.
- **Un segundo bug de la misma familia que el original, más difícil de
  encontrar**: `tkinterweb` y `pystray` se importan de forma perezosa —
  recién cuando el usuario abre la pestaña Reporte o minimiza a la
  bandeja, no al arrancar la app. Esto significa que una instalación
  parcial (`pip install .` sin el extra `[gui]`, en un sistema que ya
  trae Tkinter por su cuenta — el caso típico de Windows/macOS) deja
  **arrancar la app con total normalidad**, y recién crashea con un
  traceback crudo al primer intento de usar esas dos funciones
  específicas — incluido desde el propio botón de cerrar la ventana
  (que intenta minimizar a la bandeja). *Fix*: ambos puntos ahora
  degradan con gracia — la vista de Reporte muestra un mensaje claro sin
  perder la función de "Abrir en el navegador"; minimizar a la bandeja
  avisa y mantiene la ventana abierta en vez de crashear. *Validado* con
  captura de pantalla real del mensaje degradado dentro de la propia app.

## v0.0.8 — Bug arquitectónico real: la GUI heredaba prompts de terminal

Un usuario corrió `charlywebaudit --gui` sobre v0.0.7 y compartió este log:

```
⚠ No se encontró un Chromium funcional gestionado por Playwright.
/usr/lib/python3.12/tkinter/__init__.py:861: RuntimeWarning: coroutine 'Application.run_async' was never awaited
  func(*args)
RuntimeWarning: Enable tracemalloc to get the object allocation traceback
⚠ No se encontró un Chromium funcional gestionado por Playwright.
⚠ No se encontró un Chromium funcional gestionado por Playwright.

── Prerrequisitos ──
✕ La corrida terminó con un error: asyncio.run() cannot be called from a running event loop
```

### Diagnóstico

`ensure_chromium()` (`browser/chromium.py`) llamaba directamente a
`questionary.confirm().ask()` para preguntar si instalar Chromium — un
prompt de **terminal**, pensado para la CLI. `run_audit()` (el motor
compartido entre CLI y GUI) llama a `ensure_chromium()` sin pasar por la
abstracción `Reporter` que sí se usa para el resto de los mensajes — un
hueco real que quedó del refactor de v0.0.5.

Cuando la GUI corre una prueba, `run_audit()` se ejecuta dentro del hilo
en segundo plano de `AsyncBridge`, que **ya tiene su propio event loop de
asyncio corriendo** (`loop.run_forever()`). `questionary` (construido
sobre `prompt_toolkit`) detecta ese loop activo e intenta usar su propio
camino asíncrono (`Application.run_async()`) — pero `ensure_chromium()`
lo llama de forma síncrona (`.ask()`, sin `await`), así que esa corrutina
interna de `prompt_toolkit` nunca se espera (de ahí el
`RuntimeWarning`), y más adentro, el intento de `prompt_toolkit` de
resolverlo de todas formas termina llamando `asyncio.run()` — que revienta
porque ya hay un loop corriendo en ese hilo. *Validado*: se reprodujo el
error **exacto** del usuario (mismo `RuntimeWarning`, mismo `RuntimeError`)
armando a mano un hilo con su propio event loop y llamando
`ensure_chromium()` dentro, antes de tocar una sola línea de código.

### Fix

`ensure_chromium()` ya no sabe nada de `questionary` ni de `rich`
directamente — recibe un `Reporter` (para su salida) y una función
`confirm(pregunta) -> bool` (para la decisión interactiva), ambos
intercambiables. La CLI pasa la versión de `questionary` de siempre
(comportamiento idéntico, cero cambios visibles). La GUI pasa una nueva
función construida en `gui/dialogs.py`: agenda un diálogo nativo de
Tkinter en el hilo principal con `root.after(0, ...)` y bloquea —
mediante un `threading.Event`— únicamente el hilo en segundo plano que
preguntó, nunca el de Tkinter, hasta que el usuario responde. Es el mismo
patrón de cruce de hilos que `tray.py` ya usaba (validado en v0.0.5) para
las acciones del menú de bandeja, aplicado aquí a una pregunta que
necesita una respuesta de vuelta, no solo notificar algo.

*Validado*: con `ensure_chromium()` real, un hilo real con su propio event
loop, y un `mainloop()` de Tkinter real corriendo — se confirmó que la
pregunta se resuelve correctamente en el hilo principal y que el resultado
es la excepción esperada (`ChromiumNotInstalledError`, simulando que el
usuario responde que no), **no** el crash original. También se confirmó
que la CLI, sin pasar ningún override, sigue funcionando exactamente
igual que antes de este cambio.

## v0.0.9 — La instalación de Chromium fallaba sin ningún diagnóstico real

Un usuario compartió este log, ya sin el crash de v0.0.8 (esa parte quedó
resuelta — el diálogo de confirmación funcionó correctamente):

```
BEWARE: your OS is not officially supported by Playwright; downloading fallback build for ubuntu24.04-x64.
(repetido 9 veces)

── Prerrequisitos ──
⚠ No se encontró un Chromium funcional gestionado por Playwright.
› Instalando Chromium (puede tardar varios minutos)…
✕ La instalación de Chromium falló.
› Instalando Chromium (puede tardar varios minutos)…
✕ La instalación de Chromium falló.
› Instalando Chromium (puede tardar varios minutos)…
✕ La instalación de Chromium falló.
✕ La corrida terminó con un error: Chromium sigue sin instalarse; no se puede continuar.
```

### Diagnóstico

`_run_playwright_install()` corría `python -m playwright install chromium`
con `subprocess.run(..., check=False)` **sin capturar su salida** — el
proceso hijo heredaba directamente los descriptores de la terminal. Para
alguien usando la CLI desde una terminal, esto significa que sí *veía* la
salida real (de ahí las líneas "BEWARE" en el log compartido) — pero esa
salida nunca pasaba por nuestro propio sistema de reporte, así que:

- El mensaje de error final (`"La instalación de Chromium falló."`) nunca
  incluía ningún detalle real — la variable `err` quedaba **siempre
  vacía** en el caso de fallo genuino (proceso terminó con código
  distinto de cero, no una excepción al lanzarlo), y el único consejo
  mostrado era un genérico "revisa tu conexión a internet y espacio en
  disco" que no reflejaba la causa real.
- Alguien usando la GUI **sin una terminal abierta** (el caso más común —
  lanzar la app desde un ícono, no desde una consola) no vería *ninguna*
  información de la salida real de Playwright, ni siquiera las líneas
  "BEWARE" — el panel de la vista Ejecutar se quedaba con el mismo
  mensaje genérico y nada más, sin ninguna pista de qué estaba fallando
  de verdad.

Tres reintentos sin ningún cambio de información entre uno y otro es
exactamente el patrón que describió el usuario — no hay forma de
diagnosticar ni de saber si vale la pena seguir reintentando cuando cada
intento es una caja negra idéntica.

### Fix

`_run_playwright_install()` ahora captura la salida real línea por línea
(`subprocess.Popen` con las tuberías conectadas) y la transmite en vivo a
través de `reporter.raw()` — la misma interfaz compartida que ya usa el
resto del proyecto, así que la salida real aparece tanto en la terminal
de la CLI como en el panel de la vista Ejecutar de la GUI, en tiempo real,
no solo al final. Las últimas líneas de esa salida (donde casi siempre
vive el motivo real del fallo) se incluyen textualmente en el mensaje de
error, reemplazando el "Detalle:" vacío de antes.

Además, se agregó una sugerencia específica: si la salida real contiene
"not officially supported" (el caso exacto reportado — Ubuntu 24.04 es
demasiado reciente para la lista de sistemas operativos que Playwright
prueba oficialmente, así que usa un "build de reserva"), el consejo
mostrado deja de ser el genérico de conexión/disco y pasa a sugerir
`npx playwright install-deps chromium` — el comando que el propio
Playwright provee para instalar las bibliotecas de sistema que Chromium
necesita en tiempo de ejecución, la causa más común de que un build de
reserva descargue bien pero no logre ejecutarse.

*Validado*: se simuló el patrón exacto del log compartido (una
instalación que imprime la advertencia de SO no soportado y termina con
código de error) y se confirmó que la salida real se captura y transmite
línea por línea a través de `reporter.raw()` — funciona igual para
`QueueReporter` (GUI) que para `CliReporter` — y que el mensaje de error
final incluye tanto el detalle real como la sugerencia específica de
`install-deps`, en vez del mensaje genérico sin información de antes.

## v0.1.0a — Bugs arquitectónicos reales, catálogo de pruebas y dashboard

Esta versión parte de un aporte real de un usuario (RedGPS): tomó el
proyecto, llegó al punto de que el navegador abría correctamente, y
reportó dos problemas junto con su propio intento de solución. Se
analizó su versión a fondo (diff completo contra la línea base), se
extrajo lo aprovechable, y se investigó el resto hasta la causa raíz real
— con Chrome real instalado en el entorno de desarrollo, no solo
revisando código.

### 1 — "El navegador se cierra de forma inesperada" (`no close frame received or sent`)

Se encontraron y corrigieron **dos causas raíz genuinamente distintas**:

**Causa 1 — el mecanismo de sincronización nunca fue confiable.** Desde
v0.0.1, este proyecto "pausaba" la pestaña del spec vía CDP
(`waitForDebuggerOnStart`) para hacer su propio trabajo antes de dejarla
navegar. Se descubrió que Playwright Test abre su **propia** conexión
CDP, independiente de la nuestra (`--remote-debugging-pipe` vs. nuestro
`--remote-debugging-port`) — y la sesión de Playwright libera **su
propia** pausa como parte de su arranque normal, sin importar la
nuestra. La carrera la ganaba Playwright, no nosotros: el spec podía
terminar de correr *antes* de que nuestro código llamara `release()`, y
al intentar seguir usando la conexión, el navegador ya se había cerrado.
Confirmado con logging en vivo mostrando "1 passed" apareciendo antes de
nuestro propio `release()`.

*Fix*: se reemplazó el pausado por depurador por **intercepción a nivel
de red** (dominio `Fetch` de CDP) sobre la primera navegación real — esto
sí bloquea la navegación sin importar qué sesión CDP la dispare.
Validado con un ciclo completo: interceptar, hacer el trabajo real
(abrir extensión, sembrar config), liberar, y confirmar que el test
termina exitosamente después. El pausado por depurador se conserva solo
como ventana de tiempo para armar la intercepción antes de que cualquier
navegación pueda dispararse.

**Causa 2 — el archivo de configuración no se podía resolver.**
`playwright.config.ts` se escribía en un directorio temporal separado
(`work_dir`), fuera de la carpeta del proyecto del usuario — Node
resuelve `require('@playwright/test')` desde el directorio del propio
archivo hacia arriba, nunca desde el `cwd` del proceso, así que un
config fuera de esa jerarquía fallaba con `MODULE_NOT_FOUND`. **Esta
corrección ya estaba en la versión de RedGPS** (escribir el config junto
al spec del usuario) — se extrajo y se aplicó, con limpieza explícita al
terminar (antes vivía en `work_dir`, que se borra solo; ahora vive en el
proyecto del usuario, así que se borra aparte).

Con ambos arreglos, se corrió `run_audit()` real de principio a fin
—lanzamiento, intercepción, spec de Playwright, resultado— sin ningún
crash. Además, se agregó **degradación elegante** en dos puntos donde el
navegador puede cerrarse más rápido de lo que tarda nuestro propio
trabajo (specs muy cortos, o el cierre normal de Playwright Test al
terminar): en vez de un traceback crudo, el usuario recibe un reporte
con los resultados reales de Playwright y un aviso claro de qué faltó.

### 2 — "Debería ser 1 navegador con 2 pestañas, no 2 navegadores"

Investigado a fondo, con una prueba real: se intentó forzar la página de
la extensión al mismo contexto de navegador que usa Playwright Test para
la pestaña del spec — y la extensión **dejó de cargar** ahí
(`chrome-error://chromewebdata/`, confirmado leyendo el estado real de
la página). Chrome solo habilita extensiones cargadas por línea de
comandos (`--load-extension`) en el contexto de navegador *por defecto*
— un contexto adicional creado por Playwright Test no la hereda. Es una
restricción real de seguridad de Chrome, no algo resolvible sin tocar el
spec del usuario (se consideraron y descartaron varias alternativas:
`launchPersistentContext`, reporters personalizados, interceptar el
cierre del navegador — ninguna es compatible con "nunca modificar el
.spec.ts del usuario", el principio de diseño de este proyecto desde
v0.0.1). Se revirtió el intento y se documentó la razón real en el
código para que nadie lo reintente sin saber por qué falló.

### 3 — Mejoras de UI

La ventana creció a 1080×720 (antes 980×680) para dar espacio a las dos
pestañas nuevas sin apretar el contenido existente. El resto de la UI
(branding, tema, formularios) no mostró elementos "desaparecidos" al
revisar contra la línea base — si seguís viendo algo faltante en tu
propio uso, es información valiosa: decinos exactamente qué pantalla y
qué elemento para investigarlo con el mismo rigor que el resto de esta
sección.

### 4 — Reportes completos

`CombinedReport` ahora incluye `assistant_analysis_complete: bool` — el
reporte HTML muestra un aviso visible al inicio cuando el análisis del
Asistente no se pudo completar (ver degradación elegante, punto 1 de
arriba), en vez de que el usuario tenga que notar por su cuenta que cada
uno de los 15 ámbitos dice "Sin respuesta". *Validado*: confirmado que
el aviso aparece cuando corresponde y no aparece cuando el análisis está
completo.

### 5 — Catálogo de pruebas + Dashboard comparativo

Dos pestañas nuevas:

- **Catálogo** (`gui/views/catalog_view.py`): pruebas guardadas con
  nombre (`config.TestCase` — nombre, script, URL, cabeceras) — crear,
  editar, eliminar, y correr cualquiera con un clic, reusando el mismo
  motor (`run_audit`) sin duplicar nada.
- **`history.py`** (nuevo): cada corrida — venga del catálogo o sea una
  prueba suelta — agrega un `RunRecord` (nombre, fecha, duración,
  pasaron/fallaron, si el análisis del Asistente se completó, y la ruta
  del reporte completo) a un historial persistente en la carpeta de
  datos del usuario. El reporte de cada corrida se guarda automáticamente
  ahí también — el dashboard siempre tiene algo que mostrar, sin
  depender de que el usuario recuerde guardar manualmente.
- **Dashboard** (`gui/views/dashboard_view.py`): por cada prueba con
  historial, un resumen (corridas totales, cuántas pasaron completas,
  duración promedio), un gráfico de barras de duración por corrida a lo
  largo del tiempo (verde = pasó con análisis completo, ámbar = pasó
  pero el análisis quedó incompleto, rojo = falló — dibujado con
  `tk.Canvas`, sin dependencias nuevas), y una tabla con acceso directo
  al reporte completo de cualquier corrida pasada.

*Validado*: guardado y recarga real del catálogo y del historial
confirmados con datos reales (no solo revisado); capturas de pantalla de
ambas pestañas con datos poblados.

## v0.1.0a2 — Chrome exclusivamente, y arreglo real de la bandeja del sistema

A pedido explícito del usuario, tras confirmar que `npx playwright
install-deps chromium` no resolvía el problema real de instalación en su
sistema: **se abandona por completo el Chromium gestionado por
Playwright.** charlyWebAudit usa exclusivamente Google Chrome (canal
estable) del sistema — se detecta si ya está instalado; si no, se
muestran instrucciones claras y se detiene ahí. **Nunca se ofrece
instalarlo automáticamente** — decisión de producto explícita, no solo
técnica.

### Un hallazgo real, no resuelto — documentado con total honestidad

Antes de entregar esto, se probó correr la extensión CharlyAudit con
Chrome real (no solo revisado en código, con el orquestador real de este
proyecto) y se encontró una incompatibilidad real que **sigue sin
resolverse**: la carga de la extensión con Chrome fue inconsistente en
las pruebas —

- En algunos intentos, la extensión no aparece en absoluto entre los
  targets/procesos activos de Chrome (ni service worker, ni background
  page).
- En otros, el service worker de la extensión sí aparece con su ID fijo
  correcto, pero al intentar navegar directamente a su panel lateral
  (`sidepanel.html`), Chrome responde `ERR_BLOCKED_BY_CLIENT` — la
  extensión declara `side_panel` en su manifest (la API nativa de Chrome
  para paneles laterales), que en versiones recientes de Chrome puede
  exigir abrirse vía `chrome.sidePanel.open()` en vez de navegación
  directa a su URL. Esta pista no llegó a confirmarse como la causa
  completa, dado que en otras corridas ni el service worker llegó a
  existir.

Se contrastó limpiamente contra el Chromium gestionado por Playwright, en
la misma prueba exacta: ahí la extensión sí carga correctamente todas las
veces. La diferencia es real y reproducible, no una casualidad de una
sola corrida.

**Qué significa esto en la práctica**: con esta versión, charlyWebAudit
va a lanzar Chrome, correr el spec de Playwright, y producir un reporte
con los resultados de Playwright — pero la grabación de CharlyAudit y el
análisis del Asistente pueden no funcionar de forma confiable todavía,
dependiendo de si la extensión logra cargar en tu Chrome específico. Si
te encontrás con esto, es información valiosa: contame exactamente qué
ves (¿el panel lateral abre pero vacío? ¿nunca abre? ¿algún error en la
pestaña de Reporte?) para seguir esta investigación con datos de tu
entorno real, que puede diferir del entorno de desarrollo donde se
probó esto.

### Bandeja del sistema — no se podía cerrar la app sin matar el proceso

Reportado en producción: al minimizar a la bandeja, el menú contextual
(clic derecho, con la opción "Salir") no aparecía de forma confiable en
todos los entornos de escritorio — solo quedaba disponible la acción por
defecto (doble clic para restaurar la ventana), sin ninguna forma de
cerrar la app salvo terminar el proceso desde la terminal.

*Diagnóstico*: el menú contextual del ícono de bandeja depende del
backend de `pystray` de cada sistema operativo (GTK/AppIndicator en
Linux, Cocoa en macOS, Win32 en Windows) — en algunos entornos de
escritorio ese menú no es confiable, un problema de la plataforma más
que de la app en sí.

*Fix*: en vez de intentar arreglar el menú de bandeja para cada backend
posible (frágil, imposible de probar exhaustivamente sin acceso a todos
los entornos de escritorio existentes), se agregó un botón **"Salir"**
siempre visible en la ventana principal, junto a "Minimizar a la
bandeja" — nunca depende de que el menú de la bandeja funcione en el
sistema del usuario. *Validado*: confirmado que el botón llama al cierre
real de la aplicación (`root.destroy()`), no solo minimiza.

### Bug adicional encontrado en el camino

`default_playwright_browsers_path()` no respetaba `PLAYWRIGHT_BROWSERS_PATH`
— la variable de entorno oficial que el propio Playwright usa para
personalizar dónde instala los navegadores — lo que podía dar un falso
negativo de "Chromium no instalado" cuando sí lo estaba. Corregido junto
con el resto (aunque la función en sí quedó sin uso tras abandonar el
Chromium gestionado — se documenta el fix igual, por si se reintroduce
en el futuro).

## v0.1.1 — Rediseño radical: sin extensión en el flujo principal, telemetría del navegador

A partir de un nuevo error real reportado en producción (`No se pudo
conectar al navegador orquestado por CDP`, con Node v24.19.0) y un pedido
explícito de cambio de dirección, esta versión reescribe el corazón del
motor de orquestación.

### Diagnóstico del error reportado

Hasta v0.1.0a2, `RunOrchestrator.launch()` establecía **su propia
conexión CDP inmediatamente** al lanzar el navegador, para pausar la
primera pestaña y sincronizar con la extensión CharlyAudit antes de
dejarla navegar. Con una versión de Node considerablemente más nueva
(v24.19.0) que la usada en desarrollo hasta ese momento, esa conexión CDP
inmediata competía con el propio arranque del navegador de una forma que
versiones anteriores de Node no exponían — de ahí el error. Se analizó un
proyecto de referencia compartido por un usuario para contrastar
mecanismos de lanzamiento, aunque la causa raíz resultó estar en el
propio diseño de sincronización temprana, no en una diferencia de
configuración puntual.

### Punto 1 — sin extensión en el flujo principal

`run_audit()` (`__main__.py`) fue reescrito desde cero: ya no orquesta la
extensión CharlyAudit en absoluto — nada de pausa de pestaña, panel
lateral, siembra de configuración, grabación, ni análisis del Asistente.
Lanza el navegador, corre el spec de Playwright tal cual el usuario lo
escribió, y arma el reporte con el resultado. `browser/launcher.py`
también se reescribió: `RunOrchestrator.launch()` ya no abre ninguna
conexión CDP propia — solo genera el config (sin ningún argumento de
extensión) y lanza el subproceso de Node. *Validado*: corrida real de
principio a fin, sin ningún error de CDP, con Chrome real.

Los módulos que orquestaban la extensión (`browser/extension_page.py`,
`runner/seed.py`, `runner/recorder.py`, `runner/assistant.py`,
`gui/views/assistant_config_view.py`) se conservan intactos y validados
en versiones anteriores, pero quedan **sin uso** en el código activo —
marcados explícitamente como tales en sus propios docstrings, por si se
reintroduce soporte de extensión en el futuro. La pestaña "Asistente IA"
se quitó de la navegación de la GUI por la misma razón.

### Punto 2 — reporte compatible con o sin extensión

`CombinedReport` (`report/builder.py`) tiene un campo nuevo,
`extension_used: bool`. El flujo principal siempre lo deja en `False` —
el reporte HTML omite la sección "Análisis del Asistente IA" por
completo en ese caso (no la muestra vacía ni con una advertencia, que
implicaría que algo salió mal cuando en realidad es el comportamiento
esperado). *Validado*: ambos casos (con y sin extensión) confirmados con
datos reales — el reporte sin extensión se ve limpio, sin secciones que
no aplican.

### Punto 3 — telemetría del navegador

Nuevo módulo, `browser/telemetry.py`: una conexión CDP puramente pasiva
(nunca envía ningún comando que module el comportamiento del navegador,
a diferencia del mecanismo de sincronización retirado) que responde tres
preguntas con evidencia real: ¿el navegador llegó a arrancar?, ¿la
conexión se mantuvo viva durante toda la corrida?, y si se perdió,
¿coincide con un cierre normal (al terminar la prueba) o inesperado
(antes de que la prueba terminara — se cerró solo, o alguien lo cerró)?

Se descartó un primer diseño que comparaba, con timestamps propios, "en
qué momento detecté yo la desconexión" contra "en qué momento el proceso
de Node me dijo que terminó" — confirmado con una prueba real (matar
Chrome a mano a mitad de una corrida) que estos son dos relojes
independientes con retrasos de detección impredecibles entre sí: Node
notició la muerte del navegador casi al instante en una prueba, mucho más
lento en otra, sin ningún patrón confiable. El diseño final usa lo que
Playwright Test mismo reportó (código de salida, mensajes de error como
"Target page, context or browser has been closed" en su salida) como la
señal principal, con la conexión CDP propia como corroboración — no al
revés. *Validado* con dos escenarios reales: una corrida normal (sin
falsos positivos) y una corrida con Chrome matado a mano a mitad de
camino (detectado correctamente como cierre inesperado).

En el camino se encontró y corrigió un bug real en `CDPClient.send()`: el
timeout interno fijo de 15 segundos (pensado para operaciones CDP
normales) hacía que detectar una conexión realmente muerta tardara hasta
15 segundos — demasiado lento para telemetría útil. Ahora `send()` acepta
un `timeout` configurable, y la telemetría usa uno corto (2 segundos)
para sus sondeos de salud.

## v0.1.2 — Auditoría de seguridad, telemetría con diagnóstico ampliado

A partir de un reporte real ("abre el navegador, pasa un minuto y se
cierra solo, la prueba se interrumpe sin ningún registro previo que
explique por qué") y un pedido explícito de revisar seguridad y
rendimiento.

### Diagnóstico del cierre reportado — sin causa raíz confirmada, pero con las herramientas para encontrarla

Se revisó todo el código propio buscando cualquier timeout cercano a un
minuto — no se encontró ninguno. Esto apunta a una causa **externa** al
propio código (el sistema operativo cerrando el proceso por falta de
memoria, una política del entorno del usuario, un crash del navegador) —
sin poder reproducir el problema exacto en este entorno de desarrollo, no
se puede confirmar la causa con certeza. Lo que sí se puede garantizar:
la próxima vez que pase, va a quedar un rastro real para diagnosticarlo.

### Punto 1 — telemetría con pulso periódico y diagnóstico ampliado

`browser/telemetry.py` ahora recibe el `Reporter` de la corrida y:

- Registra un **pulso cada 15 segundos** ("Navegador sigue conectado (Xs
  transcurridos)") mientras la conexión sigue viva — así, si el navegador
  se cierra solo, el registro de la corrida muestra hasta qué segundo
  exacto seguía respondiendo, en vez de un silencio total hasta el error
  final.
- Al detectar que el navegador dejó de responder, captura un
  **diagnóstico ampliado en el momento exacto**: memoria disponible del
  sistema (vía `/proc/meminfo` en Linux) y cualquier señal reciente de
  que el OOM-killer del kernel actuó (buscado en `dmesg`, best-effort —
  nunca interrumpe la corrida si no está disponible). Si la causa es
  falta de memoria, esto lo va a mostrar directamente ("Memoria
  disponible: 50MB de 4000MB" es una pista mucho más clara que ningún
  registro en absoluto).

*Validado con un cierre real*: se mató Chrome a mano a mitad de una
corrida y se confirmó que el pulso periódico se registró correctamente
hasta el momento del cierre, y que el diagnóstico ampliado capturó la
memoria real del sistema en ese instante exacto.

### Punto 2 — seguridad y rendimiento

**Dos vulnerabilidades reales encontradas y corregidas, no solo
revisadas en el código:**

- **XSS real en el reporte HTML** (`report/html.py`): el reporte se
  generaba con `select_autoescape(["html"])`, que decide si escapar el
  contenido mirando la extensión *final* del nombre del archivo de la
  plantilla — para `report.html.jinja`, esa extensión es `.jinja`, no
  `.html`, así que **el autoescape nunca se activaba**. Confirmado con un
  payload real: una URL con `<script>alert(document.cookie)</script>` se
  incrustaba sin escapar en el reporte generado — cualquier URL o ruta de
  script con contenido malicioso se hubiera ejecutado al abrir el
  reporte. *Fix*: `autoescape=True` incondicional (esta plantilla siempre
  produce HTML, no hace falta adivinar por extensión). *Validado*: el
  mismo payload ahora queda escapado correctamente, y el contenido
  legítimo del Asistente (marcado explícitamente con `| safe`) sigue
  renderizando sin cambios.
- **Path traversal real en el nombre de archivo del reporte auto-guardado**
  (`__main__.py`): el nombre de una prueba del Catálogo (que el usuario
  escribe libremente) se usaba tal cual en el nombre del archivo del
  reporte — un nombre como `../../../tmp/x` escapaba por completo el
  directorio de reportes, confirmado con una prueba real antes del fix
  (el archivo terminaba en `/tmp/`, no en la carpeta de reportes). *Fix*:
  `_sanitize_filename_component()` — se queda solo con caracteres seguros
  para un nombre de archivo, sin importar el sistema operativo.
  *Validado* con varios intentos de escape (incluidas rutas anidadas
  tipo `....//....//etc`), todos quedan contenidos correctamente.

**Revisado y confirmado seguro** (sin cambios necesarios):

- Ningún uso de `shell=True` en todo el proyecto — sin riesgo de
  inyección de comandos vía `subprocess`.
- El puerto de depuración CDP (`--remote-debugging-port`) solo escucha en
  localhost — confirmado con `curl` real que no responde ni en `0.0.0.0`
  ni en la IP de red de la máquina, aunque `--remote-allow-origins=*` esté
  activo (necesario para que Chrome moderno acepte la conexión — ver
  v0.1.1).
- El archivo de configuración (que puede contener una API key) tiene
  permisos `600` aplicados desde versiones anteriores.

**Rendimiento**: el sondeo de telemetría (cada 250ms durante toda la
corrida) representa una carga real mínima — un mensaje JSON pequeño por
WebSocket local, ~1200 sondeos en una corrida de 5 minutos, sin impacto
medible. `history.py` reescribe el archivo completo en cada corrida
(en vez de solo agregar al final) — con el límite de 500 registros ya
existente, esto es un archivo de a lo sumo un par de cientos de KB,
reescrito una vez por corrida (no en un bucle) — no representa un
problema real a esta escala.

## v0.1.3 — Análisis profundo de un log real, dos bugs corregidos, validación de dependencias con instalación en vivo

Un usuario compartió un log real de una corrida fallida en macOS, con tres
síntomas distintos en el mismo intento. Se analizó cada uno por separado.

### Bug 1 (real, corregido): `Cannot find module '@playwright/test'`

La causa real, confirmada reproduciendo el escenario exacto: `@playwright/test`
estaba instalado **globalmente** (`npm install -g`), no localmente en la
carpeta del spec. `ensure_playwright_test()` (la verificación anterior)
solo confirmaba que `npx playwright --version` corriera — y eso funciona
igual con una instalación global o local, porque `npx` sabe resolver el
CLI desde cualquiera de las dos. Pero el `.charlywebaudit.config.ts` que
generamos, que vive junto al spec del usuario, hace su propio
`require('@playwright/test')` — y Node **no** busca en el `node_modules`
global al resolver un `require()` normal, solo en el local. Confirmado
reproduciendo exactamente esto antes del fix: el CLI corría bien, pero
`require.resolve()` fallaba con `MODULE_NOT_FOUND` desde la carpeta del
spec.

### Bug 2 (real, corregido): `argument should be a str... not 'coroutine'`

Dos causas independientes, ambas con el mismo síntoma final:

- `ask_save_path(default_name)` se llamaba **sin `await`** — pero la GUI
  pasa una función `async def`. Llamar una función async sin `await` no
  la ejecuta, devuelve un objeto corrutina — y un objeto corrutina es
  "truthy" en Python, así que `if dest_raw:` pasaba igual, y
  `Path(dest_raw)` fallaba con exactamente el error reportado.
- La versión CLI de `ask_save_path` llamaba `questionary.path().ask()`
  de forma síncrona desde dentro de `run_audit()` (que corre bajo
  `asyncio.run()`) — el mismo tipo de conflicto de event loop que ya se
  había corregido una vez para `ensure_chromium()` en v0.0.8, pero nunca
  se aplicó acá. Confirmado que, según la versión de `prompt_toolkit`
  instalada, esto puede lanzar un error limpio o —el caso real
  reportado— devolver en silencio una corrutina sin ejecutar.

*Fix*: `ask_save_path` ahora es consistentemente `await`-eado en
`run_audit()`, y la versión CLI (`_default_ask_save_path`) corre
`questionary` en un hilo aparte (vía `run_in_executor`), sin ningún event
loop propio con el que pueda chocar.

### Punto explícito del pedido: validación de dependencias con instalación en vivo

Nuevo módulo, `dependencies.py`: valida Python, Node.js/npm, Google
Chrome, y —la pieza nueva y central— si `@playwright/test` resuelve
**localmente** desde la carpeta del spec (la comprobación que faltaba,
ver Bug 1 arriba). Cuando algo falta, se ofrece resolverlo según qué tan
seguro sea hacerlo automáticamente:

- **`@playwright/test`: sí se instala en vivo**, con confirmación
  explícita — es un paquete que vive dentro del proyecto del usuario
  (su propio `node_modules`), reversible con solo borrar esa carpeta.
  Después de instalar, se vuelve a correr la MISMA verificación de
  resolución — no se confía en que un `npm install` sin error signifique
  que quedó realmente utilizable ("garantizando que después de la
  instalación podrá funcionar", como se pidió explícitamente).
- **Node.js y Google Chrome: se detectan, pero no se instalan
  automáticamente** — decisión de producto ya tomada explícitamente antes
  para Chrome, y consistente para Node por el mismo motivo: ambos se
  instalan de formas muy distintas según el sistema operativo, y
  automatizarlo sería más frágil que útil. Se dan instrucciones claras de
  dónde conseguirlos.

*Validado de punta a punta*: se reprodujo el escenario exacto del bug 1
(`@playwright/test` global, sin instalación local) a través del motor
real completo — confirmado que detecta el problema, ofrece instalar,
instala correctamente, verifica la resolución real después, y el spec de
Playwright **corre y pasa** a continuación.

### Pendiente conocido

En la misma corrida de validación, la telemetría del navegador no logró
conectar (`No se pudo conectar la telemetría del navegador`) a pesar de
que el navegador sí funcionó correctamente y el test pasó — una
desconexión entre lo que la telemetría reporta y lo que realmente pasó.
No se investigó a fondo en esta versión por restricción de tiempo; queda
documentado para revisar en una próxima versión.

## v0.1.4 — Falsa alarma de telemetría corregida, buena noticia sobre el sistema de dependencias

Un usuario compartió un log real donde el sistema de validación de
dependencias (v0.1.3) funcionó perfecto — detectó `@playwright/test` sin
instalación local, lo instaló en vivo, lo verificó, y el spec corrió y
produjo un resultado real y legítimo (una aserción de texto que no
coincidía, sin ninguna relación con charlyWebAudit). Pero había una
anomalía real en el medio: la telemetría avisó que el navegador "dejó de
responder" a los 17.9s, **en plena mitad de la corrida** — y el test
siguió corriendo con total normalidad, terminando 8.5s después con un
resultado válido.

### Diagnóstico

El sondeo de salud de la telemetría (`Target.getTargets`, con un timeout
de 2 segundos) declaraba la conexión perdida ante **un solo intento sin
respuesta**. Durante un test real, Chrome está ocupado procesando el
tráfico CDP genuino de Playwright Test (navegación, consultas al DOM,
red) — nuestro propio sondeo, liviano pero en la misma cola de mensajes,
puede quedar detrás de ese tráfico real y tardar más de 2 segundos sin
que la conexión esté rota en absoluto. Un solo sondeo lento no es lo
mismo que un navegador cerrado.

*Por qué el resultado final fue correcto de todas formas*: el diseño ya
tenía una segunda capa de protección (`evaluate_browser_outcome()`, ver
v0.1.1) que usa la salida real de Playwright como señal principal — como
el test terminó con una falla de aserción normal (no con texto de error
de infraestructura tipo "browser has disconnected"), el veredicto final
correctamente dijo "cierre normal". Pero el aviso intermedio, mostrado
DURANTE la corrida, sí era una falsa alarma real y confusa.

### Fix

Ahora se exigen **3 sondeos fallidos consecutivos** antes de declarar la
conexión perdida (el mismo patrón que usa, por ejemplo, un *liveness
probe* de Kubernetes: `failureThreshold`) — un sondeo lento aislado ya no
dispara nada, y si el siguiente sondeo responde bien, el contador se
reinicia (recuperación de un bache transitorio, no un cierre real). Peor
caso para confirmar una desconexión genuina: ~6 segundos (3 intentos × 2s
cada uno) — sigue siendo rápido para detectar un cierre real, mucho más
resistente a la congestión normal de un test real.

*Validado*: prueba unitaria confirmando que 2 fallos consecutivos
(bache transitorio) no disparan ninguna alarma, que 3 fallos consecutivos
genuinos sí se detectan correctamente, y una corrida real con
interacciones de DOM repetidas (tráfico CDP intenso, buscando reproducir
el mismo tipo de congestión) sin ninguna falsa alarma intermedia.

### Bug adicional encontrado validando esto

`websockets.exceptions.ConnectionClosed`, referenciado dinámicamente
dentro de un bloque `except`, falló intermitentemente con
`AttributeError: module 'websockets' has no attribute 'exceptions'` —
un problema de la carga perezosa de submódulos de la librería
`websockets`, reproducido durante las pruebas de esta misma corrección.
*Fix*: se importa la clase explícitamente una sola vez al cargar el
módulo (`from websockets.exceptions import ConnectionClosed`), en vez de
depender de una resolución diferida repetida en cada excepción — aplicado
tanto en `telemetry.py` como, preventivamente, en `cdp_sync.py`.

## Qué está validado con evidencia real (no solo revisado)

- El mecanismo de pausa CDP: confirmado que una pestaña congelada no
  navega, y que al liberarla el propio test runner de Playwright completa

  la prueba con éxito.
- La extensión carga correctamente con el ID fijo esperado (confirmado
  leyendo el target real del service worker por CDP).
- Un clic real sobre el botón "Grabar" del panel cambia el estado real
  (`aria-pressed`).
- El `tabId` explícito graba la pestaña correcta, no la que envía el
  mensaje (ver v2.6.2 en el README de CharlyAudit).
- `seed_pre_release`/`seed_post_release` aplican correctamente la
  configuración, verificado leyendo directamente `chrome.storage.local` y
  `localStorage` de la extensión (las fuentes de verdad reales).
- El flujo completo de preguntarle al Asistente y recibir una respuesta
  real: validado con una llamada real a Gemini, sobre una sesión con un
  evento real grabado — la respuesta fue correcta y coherente con lo
  grabado.
- El parseo del reporte JSON de Playwright: validado contra un reporte
  real generado por `@playwright/test`, con un caso que pasa y uno que
  falla.
- La generación del reporte HTML final: validada con datos reales de las
  pruebas anteriores — incluye los 15 ámbitos, el caso fallido completo, y
  la respuesta real del Asistente.
- El spec de ejemplo adjuntado por el usuario corrió contra el sitio real
  de producción y encontró una discrepancia genuina en el propio spec
  (diferencia de mayúscula en una aserción de texto) — prueba de que la
  ejecución orquestada es auténtica, no simulada.

## Lo que NO está validado con la misma certeza

**El flujo completo de principio a fin, en una sola corrida continua sin
interrupciones, no se logró confirmar de forma consistente en el entorno
de desarrollo usado para esta validación.** Cada pieza individual se
validó por separado con éxito repetido — pero al encadenar todo (lanzar
+ pausar + abrir panel + sembrar + liberar + identificar + grabar + correr
Playwright + analizar 15 ámbitos + generar reporte) en una sola ejecución,
se observaron desconexiones intermitentes de la conexión CDP en distintos
puntos de la secuencia, sin un patrón consistente que apunte a una causa
específica en el código — los recursos del sistema (memoria, descriptores
de archivo) estaban sanos en cada caso. La hipótesis más plausible es
contención de recursos/programación en el entorno compartido donde se hizo
esta validación (una única sesión con decenas de lanzamientos de Chromium
consecutivos), no un defecto de diseño — pero no se pudo confirmar con
certeza, y por eso se documenta aquí en vez de darlo por resuelto.

Se agregó reintento con backoff en el cliente CDP (`browser/cdp_sync.py`)
para tolerar mejor este tipo de latencia transitoria, pero no se llegó a
confirmar que esto sea suficiente en todos los casos.

**Antes del primer uso real, se recomienda correr el flujo completo en una
máquina con recursos dedicados (no un entorno compartido/contenedor bajo
uso intensivo) para confirmar la estabilidad de principio a fin.**

## Pendientes conocidos

- No se validó el flujo con cabeceras HTTP personalizadas activas ni con
  cabeceras erróneas/vacías.
- No se probaron specs con múltiples `test()` en el mismo archivo, ni
  specs que fallan por completo (error de sintaxis, timeout global).
- **GUI en macOS/Windows**: al igual que el instalador binario de v0.0.2,
  todo lo de la GUI se construyó y probó en Linux (bajo Xvfb) — no se
  ejecutó en macOS ni Windows reales. Tkinter/pystray/tkinterweb son
  multiplataforma por diseño y no tienen ninguna rama de código específica
  de Linux en este proyecto, pero eso es análisis, no ejecución confirmada.
- La bandeja del sistema no se probó con una sesión de escritorio completa
  (notificaciones, D-Bus) — solo con los servicios mínimos que hacen
  falta para que el ícono en sí funcione. El comportamiento de
  notificaciones emergentes del propio `pystray` no se ejercitó.
- El botón "Correr prueba ahora" del menú de bandeja dispara la corrida
  real (mismo camino que el botón de la vista Ejecutar) pero no se validó
  específicamente ese camino de principio a fin — solo el ciclo de
  minimizar/restaurar del ícono en sí.
- La vista de Reporte, al abrir un archivo `.html` externo (no generado en
  esta sesión), no vuelve a aplicar `make_tkinterweb_compatible()` de
  forma diferenciada si ese archivo ya tiene sus propias variables CSS con
  nombres distintos a los de `gui/theme.py` — funciona para reportes
  generados por esta misma herramienta, no se probó con HTML arbitrario
  de otro origen.
- El manejo de errores de red durante la fase de Asistente (proveedor
  caído, API key inválida a mitad de la corrida) no se ejercitó
  específicamente — existe manejo básico (`AssistantError` por ámbito, sin
  abortar el resto), pero no se confirmó con una API key inválida real.
- Los binarios de Windows y macOS no se construyeron ni probaron en esta
  sesión (sin acceso a esos sistemas operativos) — ver "Instalador binario
  multiplataforma" arriba.
- Los binarios no están firmados/notarizados — SmartScreen (Windows) y
  Gatekeeper (macOS) van a advertir al abrirlos. No se abordó en esta
  versión (requiere certificados de desarrollador de cada plataforma).
- El endurecimiento de permisos del archivo de configuración (`chmod 600`)
  no tiene equivalente real en Windows — se documentó como limitación
  conocida en vez de implementar el equivalente en ACLs.
