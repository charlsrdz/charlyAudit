"""
browser/chromium.py — Punto 5.2.1 del pedido.

v0.1.0a2 — Google Chrome (canal estable) es el navegador exclusivo de
charlyWebAudit — se detecta si ya está instalado; si no, se muestra cómo
instalarlo y se detiene ahí. **Nunca se ofrece instalarlo
automáticamente** — es una decisión de producto explícita: instalar un
navegador en el sistema del usuario sin que lo pida activamente es más
intrusivo que pedirle que lo instale él mismo.

v0.1.7 — se retira por completo el soporte del Chromium gestionado por
Playwright (había sido reintroducido en v0.1.5 exclusivamente para cargar
la extensión CharlyAudit, ya eliminada del proyecto — ver
`docs/roadmap-charlyaudit-nativo.md`). Chrome vuelve a ser la única
opción, sin ninguna rama condicional.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..errors import ChromiumNotInstalledError
from ..reporter import Reporter
from ..ui.theme import CliReporter
from .platform_utils import find_xvfb_run, is_linux, is_macos, is_windows, needs_virtual_display


def _warn_if_missing_display(reporter: Reporter) -> None:
    """Chrome con `headless: false` (más confiable para pruebas reales con
    interacción visual que el modo headless puro) — en Linux, eso requiere
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
            "Instala un paquete de Xvfb (p. ej. 'apt install xvfb') o corre charlyWebAudit desde un "
            "entorno con pantalla."
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
    del pipeline (`run_audit`, `dependencies.ensure_dependencies`) pero no
    se usa aquí: no hay ninguna decisión de "instalar sí/no" que ofrecer.

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
