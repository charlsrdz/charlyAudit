"""
dependencies.py — Validación automática de TODAS las dependencias externas
(Python, Node.js/npm, Google Chrome, @playwright/test) antes de cada
corrida, con instalación "en vivo" ofrecida donde es SEGURA y CONTENIDA —
nunca de forma silenciosa, siempre con confirmación explícita del usuario.

Qué se ofrece instalar en vivo, y qué no, y por qué:

- **@playwright/test: SÍ se ofrece instalar** (`npm install --save-dev
  @playwright/test`, corrido dentro de la carpeta del propio spec del
  usuario) — es un paquete que vive DENTRO del proyecto del usuario (su
  propio `node_modules`), reversible con solo borrar esa carpeta, y es
  exactamente lo que soluciona un bug real reportado en producción:
  `MODULE_NOT_FOUND` cuando `@playwright/test` está instalado
  *globalmente* (vía `npm install -g`) pero no *localmente* en el
  proyecto — `npx playwright --version` funciona igual en ambos casos
  (encuentra el CLI global), pero el `require('@playwright/test')` que
  hace nuestro propio `.charlywebaudit.config.ts` generado solo resuelve
  si está instalado LOCALMENTE, ya que Node no busca en el `node_modules`
  global al resolver un `require()` normal. Confirmado reproduciendo el
  escenario exacto antes de este fix.
- **Node.js/npm: NO se instala automáticamente** — se instala de formas
  muy distintas según el sistema operativo (instalador oficial, nvm,
  gestor de paquetes del SO...) y automatizarlo sería más frágil que útil.
  Se detecta y se dan instrucciones claras de dónde conseguirlo.
- **Google Chrome: NO se instala automáticamente** — decisión de producto
  explícita, ya tomada antes: instalar un navegador en el sistema del
  usuario sin que lo pida activamente es más intrusivo que pedirle que lo
  instale él mismo por el canal oficial de su sistema operativo. Se
  detecta y se dan instrucciones claras.

Todas las verificaciones (incluida la de Python, meramente informativa —
si este código está corriendo, Python ya está disponible) quedan
registradas en el log de la corrida, para que quede un rastro claro de
"qué se verificó y qué se encontró" en cada ejecución.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .browser.chromium import find_chrome_executable
from .browser.platform_utils import resolve_executable, resolve_npx
from .errors import NodeNotFoundError, PlaywrightTestNotAvailableError
from .reporter import Reporter


@dataclass
class DependencyStatus:
    name: str
    ok: bool
    detail: str
    """Lo que se encontró (ruta, versión) o por qué falta — siempre se
    registra, haya ido bien o mal, para que el log de la corrida sea un
    rastro completo de qué se verificó."""


def check_python() -> DependencyStatus:
    """Meramente informativo: si esta función está corriendo, Python ya
    está disponible — pero registrar la versión exacta ayuda a diagnosticar
    problemas específicos de una versión en el futuro."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    ok = sys.version_info >= (3, 11)
    return DependencyStatus("Python", ok, f"v{version}" + ("" if ok else " (se requiere 3.11+)"))


def check_node() -> DependencyStatus:
    try:
        node_path = resolve_executable("node")
        proc = subprocess.run([node_path, "--version"], capture_output=True, text=True, timeout=10, check=False)
        if proc.returncode == 0:
            return DependencyStatus("Node.js", True, f"{proc.stdout.strip()} ({node_path})")
    except (OSError, subprocess.TimeoutExpired, NodeNotFoundError):
        pass
    return DependencyStatus("Node.js", False, "no encontrado en el PATH")


def check_chrome() -> DependencyStatus:
    path = find_chrome_executable()
    if path:
        return DependencyStatus("Google Chrome", True, path)
    return DependencyStatus("Google Chrome", False, "no encontrado")


def _local_playwright_test_resolves(cwd: str) -> bool:
    """Verifica que `require('@playwright/test')` resuelva DESDE el
    directorio del spec — distinto (y más estricto) que `npx playwright
    --version`, que solo confirma que el CLI corre sin importar si viene
    de una instalación global o local. Confirmado con una prueba real que
    esta distinción es exactamente la causa de un bug real: el CLI corría
    bien (instalación global), pero el config generado por charlyWebAudit
    (que vive junto al spec y hace su propio `require`) no podía
    resolverlo, con `@playwright/test` instalado solo globalmente."""
    try:
        node_path = resolve_executable("node")
    except NodeNotFoundError:
        return False
    script = "try { require.resolve('@playwright/test'); process.exit(0); } catch (e) { process.exit(1); }"
    try:
        proc = subprocess.run([node_path, "-e", script], cwd=cwd, capture_output=True, text=True, timeout=10, check=False)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_playwright_test_local(cwd: str) -> DependencyStatus:
    if _local_playwright_test_resolves(cwd):
        return DependencyStatus("@playwright/test (local)", True, f"resuelve correctamente desde {cwd}")
    return DependencyStatus("@playwright/test (local)", False, f"no resuelve desde {cwd} (¿solo instalado globalmente?)")


def install_playwright_test_local(cwd: str, reporter: Reporter) -> bool:
    """Instala `@playwright/test` LOCALMENTE, dentro del proyecto del
    usuario — `npm install --save-dev @playwright/test`, con la salida
    real transmitida en vivo (mismo patrón ya usado para la instalación de
    Chromium en versiones anteriores). Verifica el resultado real después
    (no solo el código de salida de npm) — "garantizando que después de
    la instalación podrá funcionar", como se pidió explícitamente: se
    vuelve a correr la MISMA verificación de resolución local, no se
    asume que un `npm install` sin error significa que quedó realmente
    resoluble."""
    npm_path = resolve_executable("npm")
    reporter.info(f"Instalando @playwright/test en {cwd} (puede tardar un minuto)…")
    try:
        proc = subprocess.Popen(
            [npm_path, "install", "--save-dev", "@playwright/test"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if proc.stdout:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line:
                    reporter.raw(line)
        proc.wait(timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        reporter.error(
            PlaywrightTestNotAvailableError(f"La instalación de @playwright/test falló: {exc}")
        )
        return False

    # La garantia real: no confiar en el codigo de salida de npm solo --
    # se vuelve a verificar la resolucion real, la misma comprobacion que
    # detecto el problema en primer lugar.
    if _local_playwright_test_resolves(cwd):
        reporter.success("@playwright/test instalado y verificado — ya resuelve correctamente.")
        return True
    reporter.error(
        PlaywrightTestNotAvailableError(
            "@playwright/test se instaló pero todavía no resuelve correctamente.",
            hint="Puede haber un problema con la configuración de npm en este proyecto. "
            "Corre 'npm install --save-dev @playwright/test' manualmente en la carpeta del spec para más detalle.",
        )
    )
    return False


ConfirmFn = Callable[[str], bool]


def ensure_dependencies(
    *, spec_dir: str, reporter: Reporter, confirm: ConfirmFn | None = None
) -> list[DependencyStatus]:
    """Punto de entrada: corre TODAS las verificaciones de dependencias, en
    orden, registrando cada una en el log de la corrida — y ofrece
    instalar en vivo lo que es seguro ofrecer (ver docstring del módulo).
    Lanza la excepción correspondiente si algo bloqueante sigue faltando
    después de dar la oportunidad de resolverlo. Devuelve el resumen
    completo (para mostrar en un panel de diagnóstico si hace falta)."""
    statuses: list[DependencyStatus] = []

    py_status = check_python()
    statuses.append(py_status)
    reporter.info(f"Python: {py_status.detail}")

    node_status = check_node()
    statuses.append(node_status)
    if not node_status.ok:
        reporter.warning(f"Node.js: {node_status.detail}")
        raise NodeNotFoundError(
            "No se encontró Node.js/npm en el PATH del sistema.",
            hint="charlyWebAudit ejecuta los specs de @playwright/test con el CLI de Node — "
            "instala Node.js desde https://nodejs.org (versión LTS recomendada) y vuelve a intentarlo.",
        )
    reporter.success(f"Node.js: {node_status.detail}")

    chrome_status = check_chrome()
    statuses.append(chrome_status)
    # No se lanza excepcion aca -- ensure_browser() (chromium.py) ya maneja
    # este caso con su propio mensaje detallado; esta verificacion es para
    # que quede en el registro consolidado de dependencias.

    pw_status = check_playwright_test_local(spec_dir)
    if not pw_status.ok:
        reporter.warning(f"@playwright/test: {pw_status.detail}")
        should_install = (confirm or (lambda q: False))(
            "@playwright/test no está instalado en la carpeta del proyecto (aunque el comando "
            "'npx playwright' funcione, eso puede venir de una instalación global que no alcanza "
            "para correr el spec). ¿Instalarlo ahora en esa carpeta?"
        )
        if should_install:
            installed = install_playwright_test_local(spec_dir, reporter)
            pw_status = check_playwright_test_local(spec_dir)
            if not installed or not pw_status.ok:
                raise PlaywrightTestNotAvailableError(
                    "No se pudo dejar @playwright/test resoluble en la carpeta del proyecto.",
                    hint=f"Corre 'npm install --save-dev @playwright/test' manualmente en {spec_dir}.",
                )
        else:
            raise PlaywrightTestNotAvailableError(
                "@playwright/test no está disponible localmente en la carpeta del proyecto.",
                hint=f"Corre 'npm install --save-dev @playwright/test' en {spec_dir}, o volvé a intentarlo "
                "y aceptá la instalación automática cuando se ofrezca.",
            )
    statuses.append(pw_status)
    reporter.success(f"@playwright/test: {pw_status.detail}")

    return statuses
