"""
browser/chromium.py — Punto 5.2.1 del pedido.

Valida si el Chromium que gestiona Playwright está instalado; si no, ofrece
instalarlo (nunca lo hace sin preguntar) y no permite continuar sin él —
reintentando el proceso de instalación si falla, tantas veces como el
usuario quiera.

v0.0.8 — bug arquitectónico real corregido, reportado en producción: esta
función llamaba directamente a `questionary.confirm().ask()` y a los
`print_*` de la CLI (`rich.console`) — funcionaba bien para la CLI, pero
`run_audit()` (el motor compartido, ver `__main__.py`) también la llama
cuando corre desde la GUI, dentro del hilo en segundo plano de
`AsyncBridge`, que YA tiene su propio event loop de asyncio corriendo.
`questionary`/`prompt_toolkit` no puede usarse de forma segura ahí — lo
confirmó exactamente el error reportado: `RuntimeWarning: coroutine
'Application.run_async' was never awaited` seguido de `asyncio.run()
cannot be called from a running event loop`. Se reprodujo el error exacto
en un hilo con su propio loop antes de corregir esto.

Ahora `ensure_chromium()` no sabe nada de `questionary` ni de `rich`
directamente — recibe un `Reporter` (la misma interfaz compartida
CLI/GUI, ver `reporter.py`) para su salida, y una función `confirm` para
la decisión interactiva de instalar/reintentar. La CLI pasa la versión
basada en `questionary` (comportamiento idéntico al de antes); la GUI
pasa una versión que muestra un diálogo nativo de Tkinter de forma
segura entre hilos (ver `gui/dialogs.py`).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

import questionary
from playwright.sync_api import sync_playwright

from ..errors import ChromiumInstallFailedError, ChromiumNotInstalledError
from ..reporter import Reporter
from ..ui.theme import CliReporter, QUESTIONARY_STYLE
from .platform_utils import find_xvfb_run, needs_virtual_display

ConfirmFn = Callable[[str], bool]


def _cli_confirm(question: str) -> bool:
    """Confirmación interactiva por terminal (comportamiento original,
    preservado tal cual para la CLI) — nunca se llama desde la GUI."""
    return bool(questionary.confirm(question, default=True, style=QUESTIONARY_STYLE).ask())


def _warn_if_missing_display(reporter: Reporter) -> None:
    """CharlyAudit necesita `headless: false` (las extensiones de Chrome no
    cargan de forma fiable en modo headless puro) — en Linux, eso requiere
    un entorno gráfico real o uno virtual (Xvfb). En macOS/Windows correr
    de forma interactiva siempre implica una sesión gráfica, así que esto
    nunca aplica ahí (`needs_virtual_display()` ya filtra por plataforma)."""
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


def _run_playwright_install(reporter: Reporter) -> tuple[bool, str]:
    """Corre `python -m playwright install chromium` como subproceso,
    transmitiendo su salida real en vivo a través de `reporter` (funciona
    igual para la CLI y para el panel de la GUI — antes, la salida solo se
    heredaba directamente a la terminal, invisible para quien corre la GUI
    sin una consola abierta, y descartada por completo del mensaje de
    error).

    Bug real corregido: antes, cuando la instalación fallaba de verdad
    (proceso termina con código distinto de cero, no una excepción al
    lanzarlo), el detalle devuelto era un string vacío — el mensaje de
    error mostrado ("La instalación de Chromium falló.") no tenía ninguna
    información real de la causa, solo un consejo genérico ("revisa tu
    conexión a internet"). Ahora se capturan las últimas líneas reales de
    la salida del propio Playwright (que suelen contener el motivo real:
    fallo de red, biblioteca de sistema faltante, etc.) y se incluyen en
    el mensaje de error."""
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines: list[str] = []
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line:
                reporter.raw(line)
                lines.append(line)
        proc.wait()
        # Las ultimas lineas suelen contener el motivo real del fallo (el
        # resto suele ser progreso de descarga, menos util como detalle).
        tail = "\n".join(lines[-8:])
        return proc.returncode == 0, tail
    except OSError as exc:
        return False, str(exc)


def ensure_chromium(*, reporter: Reporter | None = None, confirm: ConfirmFn | None = None) -> None:
    """Punto de entrada: garantiza que Chromium esté instalado antes de
    continuar, o lanza ChromiumNotInstalledError si el usuario declina.

    `reporter`: por defecto `CliReporter()` (mismo comportamiento que
    antes de existir esta abstracción). `confirm`: por defecto un prompt
    de `questionary` (mismo comportamiento CLI de siempre) — la GUI DEBE
    pasar su propia versión (ver `gui/dialogs.py`), nunca usar la
    por defecto, porque `questionary` no es seguro de llamar desde el
    hilo en segundo plano de `AsyncBridge` (ver docstring del módulo)."""
    reporter = reporter or CliReporter()
    confirm = confirm or _cli_confirm

    _warn_if_missing_display(reporter)

    if is_chromium_installed():
        reporter.success("Chromium detectado.")
        return

    reporter.warning("No se encontró un Chromium funcional gestionado por Playwright.")

    while True:
        proceed = confirm("¿Instalar Chromium ahora? (requerido para continuar)")
        if not proceed:
            raise ChromiumNotInstalledError(
                "Chromium es obligatorio para ejecutar cualquier prueba.",
                hint="Corre 'python -m playwright install chromium' manualmente cuando quieras, "
                "o vuelve a intentarlo desde este menú.",
            )

        reporter.info("Instalando Chromium (puede tardar varios minutos)…")
        ok, err = _run_playwright_install(reporter)
        if ok and is_chromium_installed():
            reporter.success("Chromium instalado correctamente.")
            return

        # Sugerencia especifica cuando la salida real de Playwright avisa que
        # el sistema operativo no esta oficialmente soportado (usa un build
        # "de reserva") — un patron real observado en produccion (Ubuntu
        # 24.04, demasiado reciente para la lista de SO probados de
        # Playwright) que muy seguido significa que faltan bibliotecas de
        # sistema que el propio binario de Chromium necesita en tiempo de
        # ejecucion, no que la descarga en si haya fallado.
        hint = "Revisa tu conexión a internet y espacio en disco. Puedes reintentar ahora mismo."
        if "not officially supported" in err.lower():
            hint = (
                "Tu sistema operativo no está en la lista de SO probados por Playwright "
                "(usa un build de reserva) — esto suele significar que faltan bibliotecas de "
                "sistema que Chromium necesita en tiempo de ejecución, no que la descarga haya "
                "fallado en sí. Corre este comando y luego reintentá:\n"
                "  npx playwright install-deps chromium"
            )

        reporter.error(
            ChromiumInstallFailedError(
                "La instalación de Chromium falló." + (f"\n\nDetalle (salida real de Playwright):\n{err}" if err else ""),
                hint=hint,
            )
        )
        retry = confirm("¿Reintentar la instalación?")
        if not retry:
            raise ChromiumNotInstalledError(
                "Chromium sigue sin instalarse; no se puede continuar.",
            )
