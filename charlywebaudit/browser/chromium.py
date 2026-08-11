"""
browser/chromium.py — Punto 5.2.1 del pedido.

Valida si el Chromium que gestiona Playwright está instalado; si no, ofrece
instalarlo (nunca lo hace sin preguntar) y no permite continuar sin él —
reintentando el proceso de instalación si falla, tantas veces como el
usuario quiera.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import questionary
from playwright.sync_api import sync_playwright

from ..errors import ChromiumInstallFailedError, ChromiumNotInstalledError
from ..ui.theme import QUESTIONARY_STYLE, console, print_error, print_info, print_success, print_warning
from .platform_utils import find_xvfb_run, needs_virtual_display


def _warn_if_missing_display() -> None:
    """CharlyAudit necesita `headless: false` (las extensiones de Chrome no
    cargan de forma fiable en modo headless puro) — en Linux, eso requiere
    un entorno gráfico real o uno virtual (Xvfb). En macOS/Windows correr
    de forma interactiva siempre implica una sesión gráfica, así que esto
    nunca aplica ahí (`needs_virtual_display()` ya filtra por plataforma)."""
    if not needs_virtual_display():
        return
    if find_xvfb_run():
        print_info(
            "No se detectó un entorno gráfico ($DISPLAY vacío), pero 'xvfb-run' está disponible — "
            "charlyWebAudit lo usará automáticamente."
        )
    else:
        print_warning(
            "No se detectó un entorno gráfico ($DISPLAY vacío) ni 'xvfb-run' instalado. "
            "CharlyAudit necesita un navegador con interfaz (las extensiones no cargan en modo headless puro) — "
            "instala un paquete de Xvfb (p. ej. 'apt install xvfb') o corre charlyWebAudit desde un entorno con pantalla."
        )


def is_chromium_installed() -> bool:
    """Verifica que exista en disco el binario exacto que la app usará en la
    práctica — sin lanzar un navegador completo.

    Bug real corregido en v0.0.2: la versión anterior comprobaba lanzando
    Chromium con `headless=True` — pero la app SIEMPRE usa `headless=False`
    (las extensiones de Chrome no cargan de forma fiable en modo headless
    puro). En versiones recientes de Playwright, `headless=True` puede
    resolver a un binario DISTINTO (`chrome-headless-shell`), separado del
    que usa `headless=False` — es decir, la verificación podía decir "sí
    está instalado" cuando en realidad faltaba el binario que la corrida
    real necesita, o viceversa. Comprobar `executable_path` directamente
    verifica el binario correcto, es más rápido (no lanza ni cierra un
    proceso completo), y no depende de tener un display disponible."""
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


def _run_playwright_install() -> tuple[bool, str]:
    """Corre `python -m playwright install chromium` como subproceso,
    mostrando su salida en vivo (la instalación puede tardar minutos y el
    usuario debe ver que sigue avanzando, no una pantalla congelada)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
        )
        return proc.returncode == 0, ""
    except OSError as exc:
        return False, str(exc)


def ensure_chromium() -> None:
    """Punto de entrada: garantiza que Chromium esté instalado antes de
    continuar, o lanza ChromiumNotInstalledError si el usuario declina."""
    _warn_if_missing_display()

    if is_chromium_installed():
        print_success("Chromium detectado.")
        return

    print_warning("No se encontró un Chromium funcional gestionado por Playwright.")

    while True:
        proceed = questionary.confirm(
            "¿Instalar Chromium ahora? (requerido para continuar)",
            default=True,
            style=QUESTIONARY_STYLE,
        ).ask()
        if not proceed:
            raise ChromiumNotInstalledError(
                "Chromium es obligatorio para ejecutar cualquier prueba.",
                hint="Corre 'python -m playwright install chromium' manualmente cuando quieras, "
                "o vuelve a intentarlo desde este menú.",
            )

        print_info("Instalando Chromium (puede tardar varios minutos)…")
        ok, err = _run_playwright_install()
        if ok and is_chromium_installed():
            print_success("Chromium instalado correctamente.")
            return

        print_error(
            ChromiumInstallFailedError(
                "La instalación de Chromium falló." + (f" Detalle: {err}" if err else ""),
                hint="Revisa tu conexión a internet y espacio en disco. Puedes reintentar ahora mismo.",
            )
        )
        retry = questionary.confirm("¿Reintentar la instalación?", default=True, style=QUESTIONARY_STYLE).ask()
        if not retry:
            raise ChromiumNotInstalledError(
                "Chromium sigue sin instalarse; no se puede continuar.",
            )
