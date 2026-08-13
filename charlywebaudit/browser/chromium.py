"""
browser/chromium.py — Punto 5.2.1 del pedido.

v0.1.0a2 — Google Chrome (canal estable) es el navegador por defecto para
el flujo simple (solo Playwright, sin extensión) — se detecta si ya está
instalado; si no, se muestra cómo instalarlo y se detiene ahí. **Nunca se
ofrece instalarlo automáticamente** — es una decisión de producto
explícita: instalar un navegador en el sistema del usuario sin que lo
pida activamente es más intrusivo que pedirle que lo instale él mismo.

v0.1.5 — la extensión CharlyAudit se reintegra como OPCIÓN (`use_extension`
en `config.TestConfig`/`TestCase`) — y, a diferencia del flujo simple,
SÍ necesita el Chromium gestionado por Playwright: confirmado varias
veces con el orquestador real que Chrome NO carga la extensión (el
service worker nunca llega a existir, o la navegación a su panel lateral
queda bloqueada con `ERR_BLOCKED_BY_CLIENT` — la extensión declara
`side_panel` en su manifest, la API nativa de paneles laterales de
Chrome, que exige abrirse vía `chrome.sidePanel.open()` en vez de
navegación directa en versiones recientes). Cuando una corrida pide usar
la extensión, se ofrece instalar Chromium en vivo (con confirmación) —
distinto de Chrome: Chromium vive en la caché propia de Playwright, no a
nivel de sistema operativo, así que instalarlo con confirmación explícita
es razonable. Ver `ensure_browser(need_extension=...)`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from ..errors import ChromiumInstallFailedError, ChromiumNotInstalledError
from ..reporter import Reporter
from ..ui.theme import CliReporter
from .platform_utils import (
    default_playwright_browsers_path,
    find_xvfb_run,
    is_linux,
    is_macos,
    is_windows,
    needs_virtual_display,
    resolve_npx,
)


def _warn_if_missing_display(reporter: Reporter) -> None:
    """CharlyAudit necesita `headless: false` (las extensiones de Chrome no
    cargan de forma fiable en modo headless puro) — en Linux, eso requiere
    un entorno gráfico real o uno virtual (Xvfb). `browser/launcher.py` ya
    envuelve el comando con `xvfb-run` automáticamente cuando hace falta
    — esto es solo el aviso informativo."""
    if not needs_virtual_display():
        return
    if find_xvfb_run():
        reporter.info(
            "No se detectó un entorno gráfico ($DISPLAY vacío), pero 'xvfb-run' está disponible — "
            "charlyWebAudit lo usará automáticamente."
        )
    else:
        reporter.warning(
            "No se detectó un entorno gráfico ($DISPLAY vacío) ni 'xvfb-run' instalado. "
            "CharlyAudit necesita un navegador con interfaz (las extensiones no cargan en modo headless puro) — "
            "instala un paquete de Xvfb (p. ej. 'apt install xvfb') o corre charlyWebAudit desde un entorno con pantalla."
        )


_LINUX_CHROME_CANDIDATES = ["google-chrome-stable", "google-chrome", "chrome"]
_MACOS_CHROME_APP = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
_WINDOWS_CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


def find_chrome_executable() -> str | None:
    """Devuelve la ruta del ejecutable de Chrome estable si lo encuentra, o
    `None`. No lanza nada — solo mira el sistema de archivos/PATH, así que
    es instantáneo y no depende de tener ya un navegador funcionando."""
    if is_linux():
        for name in _LINUX_CHROME_CANDIDATES:
            path = shutil.which(name)
            if path:
                return path
        return None
    if is_macos():
        if _MACOS_CHROME_APP.is_file():
            return str(_MACOS_CHROME_APP)
        return shutil.which("google-chrome") or shutil.which("chrome")
    if is_windows():
        for path in _WINDOWS_CHROME_CANDIDATES:
            if path.is_file():
                return str(path)
        import os

        local = os.environ.get("LOCALAPPDATA")
        if local:
            user_path = Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if user_path.is_file():
                return str(user_path)
        return None
    return shutil.which("google-chrome") or shutil.which("chrome")


def _chrome_install_instructions() -> str:
    if is_linux():
        return (
            "Instálalo desde el paquete oficial de Google (requiere sudo):\n"
            "  wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb\n"
            "  sudo apt install ./google-chrome-stable_current_amd64.deb\n\n"
            "(en Fedora/RHEL: descarga el .rpm equivalente desde google.com/chrome)\n\n"
            "Después de instalarlo, volvé a intentarlo desde este menú."
        )
    if is_macos():
        return (
            "Descárgalo desde https://www.google.com/chrome/ e instálalo como cualquier app de macOS.\n\n"
            "Después de instalarlo, volvé a intentarlo desde este menú."
        )
    return (
        "Descárgalo e instálalo desde https://www.google.com/chrome/\n\n"
        "Después de instalarlo, volvé a intentarlo desde este menú."
    )


# --- Chromium gestionado por Playwright (necesario para la extensión) -------
#
# v0.1.5: la extensión CharlyAudit se reintegra como opción — pero solo el
# Chromium gestionado por Playwright la carga de forma confiable (Chrome
# real no la carga en absoluto, confirmado varias veces con el orquestador
# real: el service worker de la extensión nunca llega a existir). Por eso,
# cuando una corrida pide usar la extensión, se necesita Chromium
# específicamente — con instalación en vivo ofrecida (mismo patrón que
# @playwright/test en dependencies.py), pero con una advertencia clara: en
# sistemas operativos fuera de la lista de soporte oficial de Playwright
# (Ubuntu 24.04, confirmado en producción), esta instalación puede fallar.


def is_chromium_installed() -> bool:
    """Verifica que exista en disco la instalación de Chromium gestionada
    por Playwright — revisando el directorio de navegadores directamente,
    sin usar la API de Python de Playwright (que puede chocar con el
    propio event loop de asyncio ya corriendo — ver historial de este
    módulo)."""
    browsers_dir = default_playwright_browsers_path()
    if not browsers_dir or not browsers_dir.is_dir():
        return False
    try:
        for entry in browsers_dir.iterdir():
            if not entry.is_dir() or not entry.name.startswith("chromium"):
                continue
            if (entry / "INSTALLATION_COMPLETE").exists():
                return True
            if any(sub.is_dir() and "chrome" in sub.name.lower() for sub in entry.iterdir()):
                return True
    except OSError:
        return False
    return False


def install_chromium_live(reporter: Reporter) -> bool:
    """Instala el Chromium gestionado por Playwright, con la salida real
    transmitida en vivo, y verifica el resultado real después (no solo el
    código de salida) — mismo patrón que `dependencies.install_playwright_test_local`."""
    reporter.info("Instalando Chromium (puede tardar varios minutos)…")
    is_frozen = getattr(sys, "frozen", False)
    cmd = (
        [resolve_npx(), "--yes", "playwright", "install", "chromium"]
        if is_frozen
        else [sys.executable, "-m", "playwright", "install", "chromium"]
    )
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        lines: list[str] = []
        if proc.stdout:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line:
                    reporter.raw(line)
                    lines.append(line)
        proc.wait(timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        reporter.error(ChromiumInstallFailedError(f"La instalación de Chromium falló: {exc}"))
        return False

    if is_chromium_installed():
        reporter.success("Chromium instalado y verificado — ya está disponible.")
        return True

    tail = "\n".join(lines[-8:])
    hint = "Revisa tu conexión a internet y espacio en disco."
    if "not officially supported" in tail.lower():
        hint = (
            "Tu sistema operativo no está en la lista de SO probados por Playwright — esto puede fallar "
            "sin más diagnóstico disponible (confirmado en producción, Ubuntu 24.04). Si esto sigue "
            "fallando, la extensión CharlyAudit no va a poder usarse en este equipo; el resto de "
            "charlyWebAudit (Playwright con Chrome) funciona igual sin ella."
        )
    reporter.error(
        ChromiumInstallFailedError(
            "Chromium se intentó instalar pero no quedó disponible." + (f"\n\nDetalle:\n{tail}" if tail else ""),
            hint=hint,
        )
    )
    return False


def ensure_browser(*, reporter: Reporter | None = None, confirm=None, need_extension: bool = False) -> str | None:
    """Punto de entrada: garantiza que haya un navegador utilizable.

    `need_extension=False` (default): Google Chrome (canal estable) —
    detecta, nunca instala automáticamente (decisión de producto
    explícita). Devuelve `'chrome'`.

    `need_extension=True`: Chromium gestionado por Playwright — la única
    opción confirmada que carga la extensión CharlyAudit. Si falta, SÍ se
    ofrece instalar en vivo (con confirmación) — a diferencia de Chrome,
    Chromium vive en una caché propia de Playwright, no se instala a nivel
    de sistema operativo. Si la instalación falla o se declina, la
    corrida puede seguir sin la extensión (quien llama decide) — se lanza
    la excepción para que `run_audit()` decida cómo degradar.

    `confirm`: función `(pregunta) -> bool` — igual que en el resto del
    pipeline. Devuelve el `channel` a usar (`'chrome'` o `None` para
    Chromium)."""
    reporter = reporter or CliReporter()
    _warn_if_missing_display(reporter)

    if not need_extension:
        path = find_chrome_executable()
        if path:
            reporter.success(f"Google Chrome detectado: {path}")
            return "chrome"
        reporter.warning("No se encontró Google Chrome (versión estable) en este sistema.")
        raise ChromiumNotInstalledError(
            "charlyWebAudit necesita Google Chrome (canal estable) instalado.",
            hint=_chrome_install_instructions(),
        )

    if is_chromium_installed():
        reporter.success("Chromium (gestionado por Playwright) detectado — necesario para la extensión CharlyAudit.")
        return None

    reporter.warning(
        "La extensión CharlyAudit necesita el Chromium gestionado por Playwright (Chrome real no la carga) "
        "y no está instalado."
    )
    should_install = (confirm or (lambda q: False))(
        "¿Instalar Chromium ahora? (necesario para usar la extensión CharlyAudit en esta corrida)"
    )
    if should_install and install_chromium_live(reporter):
        return None

    raise ChromiumNotInstalledError(
        "Chromium no está disponible — no se puede usar la extensión CharlyAudit sin él.",
        hint="Corré 'npx playwright install chromium' manualmente, o desmarcá 'usar CharlyAudit' "
        "para esta prueba y corré solo Playwright con Chrome.",
    )
