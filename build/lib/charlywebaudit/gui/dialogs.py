"""
gui/dialogs.py — Diálogos seguros entre hilos (v0.0.8).

El motor de orquestación (`run_audit`, `ensure_chromium`, etc.) corre en el
hilo en segundo plano de `AsyncBridge` — un hilo con su propio event loop
de asyncio, completamente separado del hilo principal de Tkinter. Mostrar
un diálogo (`messagebox`) DEBE ocurrir en el hilo principal (es una regla
general de las UI de escritorio, no solo de Tkinter) — pero el código que
NECESITA la respuesta (por ejemplo, `ensure_chromium()` decidiendo si
instalar Chromium) vive en el otro hilo.

Este módulo resuelve ese cruce: agenda el diálogo en el hilo principal con
`root.after(0, ...)`, y bloquea el hilo que preguntó (con un
`threading.Event`) hasta que la respuesta esté lista — el mismo patrón que
ya usa `tray.py` para las acciones del menú de bandeja, aplicado aquí a
preguntas que necesitan una respuesta de vuelta, no solo notificar algo.

Bug real corregido en v0.0.8: antes de este módulo, `ensure_chromium()`
usaba `questionary` (prompts de terminal) directamente — funciona en la
CLI, pero llamado desde el hilo de `AsyncBridge` producía
`RuntimeWarning: coroutine 'Application.run_async' was never awaited`
seguido de un crash real (`asyncio.run() cannot be called from a running
event loop`) — reproducido y confirmado antes de este fix.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable

from ..constants import APP_NAME


def make_thread_safe_confirm(root: tk.Tk) -> Callable[[str], bool]:
    """Devuelve una función `confirm(pregunta) -> bool` segura de llamar
    desde el hilo en segundo plano de `AsyncBridge`. Bloquea ESE hilo
    (nunca el de Tkinter) hasta que el usuario responde el diálogo, que se
    muestra correctamente en el hilo principal."""

    def confirm(question: str) -> bool:
        result: dict[str, bool] = {}
        answered = threading.Event()

        def _show_dialog() -> None:
            try:
                result["value"] = messagebox.askyesno(APP_NAME, question, parent=root)
            finally:
                answered.set()

        root.after(0, _show_dialog)
        answered.wait()
        return result.get("value", False)

    return confirm
