"""
browser/chromium.py — Punto 5.2.1 del pedido.

v0.1.0a2 — a pedido explícito, tras confirmar en producción (dos veces)
que el Chromium gestionado por Playwright falla al instalarse en sistemas
operativos fuera de su lista de soporte oficial (Ubuntu 24.04, sin que
`npx playwright install-deps chromium` resuelva el problema): se
abandona por completo el Chromium gestionado por Playwright. charlyWebAudit
usa exclusivamente Google Chrome (canal estable) del sistema — se detecta
si ya está instalado; si no, se muestra cómo instalarlo y se detiene ahí.
**Nunca se ofrece instalarlo automáticamente** — es una decisión de
producto explícita, no solo técnica: instalar un navegador en el sistema
del usuario sin que lo pida activamente es más intrusivo que pedirle que
lo instale él mismo por el canal oficial de su sistema operativo.

Nota de investigación honesta, para quien retome esto: se probó ejecutar
la extensión CharlyAudit con Chrome real (no solo revisado en código) y
se encontró una incompatibilidad real, no resuelta todavía — la carga de
la extensión con Chrome resultó inconsistente en las pruebas (a veces la
extensión no aparece en absoluto entre los procesos/targets activos de
Chrome; en otro intento apareció pero bloqueada al navegar directamente a
su panel lateral, con `ERR_BLOCKED_BY_CLIENT` — la extensión declara
`side_panel` en su manifest, la API nativa de Chrome para paneles
laterales, que puede exigir abrirse vía `chrome.sidePanel.open()` en vez
de navegación directa a su URL en versiones recientes de Chrome; esto NO
se confirmó como la causa completa, dado que en otras corridas ni
siquiera el service worker de la extensión llegó a aparecer). Se decidió
priorizar el pedido explícito del usuario (dejar de usar Chromium) sobre
seguir bloqueando la entrega mientras se investiga esto a fondo — pero
es un problema real y abierto, no una limitación ya resuelta. Si
`charlywebaudit` corre pero CharlyAudit no graba nada, este es el primer
lugar para revisar.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..errors import ChromiumNotInstalledError
from ..reporter import Reporter
from ..ui.theme import CliReporter
from .platform_utils import find_xvfb_run, is_linux, is_macos, is_windows, needs_virtual_display


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


def ensure_browser(*, reporter: Reporter | None = None, confirm=None) -> str | None:
    """Punto de entrada: garantiza que Google Chrome (canal estable) esté
    disponible antes de continuar, o lanza `ChromiumNotInstalledError`.

    No instala nada automáticamente — solo detecta y, si falta, muestra
    cómo instalarlo (decisión de producto explícita, ver docstring del
    módulo). `confirm` se acepta por compatibilidad de firma con el resto
    del pipeline (`run_audit`) pero no se usa aquí: no hay ninguna
    decisión de "instalar sí/no" que ofrecer, solo un hecho que reportar
    y, si hace falta, instrucciones para que el usuario lo resuelva él
    mismo.

    Devuelve `'chrome'` — el valor que `config_gen.py` usa para el
    parámetro `channel` de Playwright."""
    reporter = reporter or CliReporter()
    _warn_if_missing_display(reporter)

    path = find_chrome_executable()
    if path:
        reporter.success(f"Google Chrome detectado: {path}")
        return "chrome"

    reporter.warning("No se encontró Google Chrome (versión estable) en este sistema.")
    raise ChromiumNotInstalledError(
        "charlyWebAudit necesita Google Chrome (canal estable) instalado.",
        hint=_chrome_install_instructions(),
    )
