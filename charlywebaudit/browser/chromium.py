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

import asyncio
import concurrent.futures
import subprocess
import sys
from pathlib import Path
from typing import Callable

import questionary
from playwright.sync_api import sync_playwright

from ..errors import ChromiumInstallFailedError, ChromiumNotInstalledError
from ..reporter import Reporter
from ..ui.theme import CliReporter, QUESTIONARY_STYLE
from .platform_utils import find_xvfb_run, needs_virtual_display, resolve_npx

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
            "RedGpsWebAudit lo usará automáticamente."
        )
    else:
        reporter.warning(
            "No se detectó un entorno gráfico ($DISPLAY vacío) ni 'xvfb-run' instalado. "
            "CharlyAudit necesita un navegador con interfaz (las extensiones no cargan en modo headless puro) — "
            "instala un paquete de Xvfb (p. ej. 'apt install xvfb') o corre RedGpsWebAudit desde un entorno con pantalla."
        )


def _check_chromium_executable_exists() -> bool:
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


def is_chromium_installed() -> bool:
    """Verifica que exista en disco el binario exacto que la app usará en la
    práctica — sin lanzar un navegador completo.

    Bug real corregido: si `is_chromium_installed()` se llama desde un hilo
    donde ya hay un event loop de asyncio corriendo (como ocurre en la GUI a
    través de `AsyncBridge` o en flujos asíncronos), `sync_playwright()` lanza
    un `Error: Playwright Sync API cannot be used inside an asyncio event loop`.
    La excepción era capturada por `except Exception:` devolviendo `False`
    falsamente incluso cuando Chromium sí estaba instalado.
    Ejecutar la comprobación en un `ThreadPoolExecutor` secundario evita el
    conflicto con el event loop y devuelve el estado real.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_check_chromium_executable_exists).result()

    return _check_chromium_executable_exists()


def _exec_command(cmd: list[str], reporter: Reporter) -> tuple[bool, str]:
    """Ejecuta un comando en subproceso retransmitiendo salida en vivo."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        lines: list[str] = []
        if proc.stdout:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line:
                    reporter.raw(line)
                    lines.append(line)
        proc.wait()
        tail = "\n".join(lines[-12:])
        return proc.returncode == 0, tail
    except OSError as exc:
        return False, str(exc)


def _run_playwright_install(reporter: Reporter) -> tuple[bool, str]:
    """Corre la instalación de Chromium a través de Playwright.

    Bug real corregido: si la aplicación corre empaquetada como ejecutable
    PyInstaller (`sys.frozen == True`), `sys.executable` apunta al binario propio de
    `charlywebaudit`, NO al intérprete de Python. Invocarlo con `-m playwright` provocaba
    que `charlywebaudit` se re-ejecutara a sí mismo en bucle e intentara verificar
    prerrequisitos recursivamente.
    En ese escenario (o como fallback si `python -m playwright` falla), se utiliza
    `npx playwright install chromium` usando la instalación de Node.js/npm del sistema.
    """
    is_frozen = getattr(sys, "frozen", False)
    tail = ""

    if not is_frozen:
        ok, tail = _exec_command([sys.executable, "-m", "playwright", "install", "chromium"], reporter)
        if ok:
            return True, tail

    try:
        npx_path = resolve_npx()
        ok_npx, tail_npx = _exec_command([npx_path, "--yes", "playwright", "install", "chromium"], reporter)
        if ok_npx:
            return True, tail_npx
        return False, tail_npx or tail
    except Exception as exc:
        return False, tail or str(exc)


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
