"""
reporter.py — Desacopla el motor de orquestación (`__main__.run_audit`) de
CÓMO se muestra el progreso.

Hasta v0.0.2, `run_audit()` llamaba directamente a los `print_*` de
`ui/theme.py` (acoplado a `rich`, pensado para terminal). En v0.0.5 se
agrega una GUI que necesita mostrar ese mismo progreso en widgets Tkinter,
desde un hilo en segundo plano — no puede llamar a `rich.Console.print`
directamente sin arriesgarse a interferir con el hilo principal de Tkinter.

`Reporter` es la interfaz mínima que ambas superficies implementan.
`run_audit()` solo conoce esta interfaz, nunca `rich` ni `tkinter`
directamente — el mismo motor de orquestación sirve a las dos.
"""

from __future__ import annotations

from typing import Protocol


class Reporter(Protocol):
    def section(self, title: str) -> None: ...
    def info(self, message: str) -> None: ...
    def success(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def error(self, exc: Exception) -> None: ...
    def raw(self, text: str) -> None: ...
    """Salida cruda sin formato — usada para el stdout combinado del
    subproceso de Playwright Test, que ya trae su propio formato."""


class NullReporter:
    """No-op — útil en pruebas o cuando no hace falta mostrar progreso."""

    def section(self, title: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def success(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, exc: Exception) -> None:
        pass

    def raw(self, text: str) -> None:
        pass
