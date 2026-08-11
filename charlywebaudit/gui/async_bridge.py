"""
gui/async_bridge.py — Conecta el motor de orquestación (asyncio, ver
`__main__.run_audit` y todo `browser/`+`runner/`) con el bucle principal de
Tkinter (síncrono, de un solo hilo, no puede bloquearse esperando una
corrida que tarda minutos).

Mecanismo:
  1. Un hilo en segundo plano corre su propio event loop de asyncio
     (`loop.run_forever()`), separado del hilo principal de Tkinter.
  2. La GUI somete corrutinas a ese loop con
     `asyncio.run_coroutine_threadsafe()` — la forma correcta y soportada
     de cruzar la frontera hilo-a-loop sin condiciones de carrera.
  3. El progreso (via `Reporter`, ver reporter.py) y el resultado final se
     comunican de vuelta a Tkinter por una `queue.Queue` — es thread-safe
     por diseño (a diferencia de tocar un widget Tkinter directamente desde
     otro hilo, que NO es seguro y puede corromper el estado de Tk). La GUI
     sondea la cola con `root.after(...)`, nunca al revés.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable

from ..reporter import Reporter


@dataclass
class ProgressMessage:
    kind: str  # "section" | "info" | "success" | "warning" | "error" | "raw"
    payload: Any


class QueueReporter:
    """Implementación de `Reporter` para la GUI — en vez de imprimir en una
    terminal, empuja mensajes a una cola thread-safe que el hilo de
    Tkinter sondea periódicamente."""

    def __init__(self, q: "queue.Queue[ProgressMessage]") -> None:
        self._q = q

    def section(self, title: str) -> None:
        self._q.put(ProgressMessage("section", title))

    def info(self, message: str) -> None:
        self._q.put(ProgressMessage("info", message))

    def success(self, message: str) -> None:
        self._q.put(ProgressMessage("success", message))

    def warning(self, message: str) -> None:
        self._q.put(ProgressMessage("warning", message))

    def error(self, exc: Exception) -> None:
        self._q.put(ProgressMessage("error", exc))

    def raw(self, text: str) -> None:
        self._q.put(ProgressMessage("raw", text))


class AsyncBridge:
    """Un event loop de asyncio corriendo en un hilo dedicado, vivo durante
    toda la vida de la aplicación GUI — se crea una sola vez al arrancar,
    se apaga limpiamente al cerrar la ventana."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return  # ya esta corriendo
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="redgpswebaudit-asyncio")
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def submit(self, coro, *, on_done: Callable[[Any, Exception | None], None] | None = None) -> None:
        """Somete una corrutina al loop en segundo plano. `on_done(result,
        exc)` se llama desde ESE hilo en segundo plano (no el de Tkinter) —
        quien lo use debe encolar de vuelta a Tkinter (ver ProgressMessage)
        en vez de tocar widgets directamente ahí."""
        if self._loop is None:
            raise RuntimeError("AsyncBridge no está iniciado — llamar start() primero.")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def _on_future_done(fut: "asyncio.Future") -> None:
            exc = fut.exception()
            result = fut.result() if exc is None else None
            if on_done:
                on_done(result, exc)

        future.add_done_callback(_on_future_done)

    def stop(self) -> None:
        if self._loop is not None:
            # En lugar de solo detener, intentamos cerrar tareas de forma segura
            # ignorando errores de conexión (TargetClosedError) que son inevitables.
            async def _cleanup():
                tasks = [t for t in asyncio.all_tasks(self._loop) if t is not asyncio.current_task()]
                for t in tasks:
                    t.cancel()
                # Esperar a que las cancelaciones se propaguen sin lanzar excepciones
                await asyncio.gather(*tasks, return_exceptions=True)
                self._loop.stop()

            # Asegurar que el loop se detenga en el hilo principal del bridge
            self._loop.call_soon_threadsafe(lambda: asyncio.create_task(_cleanup()))
        
        if self._thread is not None:
            self._thread.join(timeout=3)
