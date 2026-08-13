"""
gui/__init__.py — Punto de entrada seguro para la GUI.

Bug real reportado en producción: el entry point `charlywebaudit-gui`
apuntaba directo a `gui.app:main`, y `gui/app.py` hace `import tkinter`
a nivel de módulo, sin ninguna protección — en un sistema sin Tkinter
instalado (algo común: varias distribuciones Linux, Debian/Ubuntu
incluidas, separan Tkinter del intérprete base de Python), esto producía
un traceback crudo en vez de un mensaje entendible.

Esta función es la ÚNICA forma correcta de arrancar la GUI: no importa
nada de `gui.app` (que a su vez importa tkinter/tkinterweb/pystray a nivel
de módulo) hasta confirmar que está disponible. Tanto el entry point
`charlywebaudit-gui` (pyproject.toml) como `charlywebaudit --gui`
(`__main__.py`) llaman a esta misma función — una sola fuente de verdad,
en vez de que cada punto de entrada reimplemente su propio manejo de
errores (que es exactamente como se coló el bug original: `__main__.py`
sí tenía un try/except, pero `charlywebaudit-gui` no).

También corrige un segundo bug real relacionado: el mensaje de error
anterior decía siempre "pip install \".[gui]\"" sin importar cuál fuera el
problema real — pero Tkinter es un paquete del SISTEMA OPERATIVO, `pip`
nunca puede instalarlo, así que esa instrucción era directamente
incorrecta para el caso más común (falta Tkinter). Ahora se distingue
explícitamente entre "falta Tkinter" (instrucción por sistema operativo)
y "falta una dependencia de pip" (tkinterweb/pystray/pillow — ahí sí
aplica `pip install ".[gui]"`).
"""

from __future__ import annotations

import sys


def _print_tkinter_missing() -> None:
    # Se usa print() sin formato (no rich.console) a propósito: este es el
    # camino de error más temprano posible, no debe depender de que
    # ninguna otra parte de la app funcione correctamente.
    print(
        "La interfaz gráfica necesita Tkinter, que no está instalado.\n\n"
        "Tkinter es un paquete del SISTEMA OPERATIVO, no de pip — "
        "'pip install' nunca lo va a resolver, sin importar qué extras uses.\n\n"
        "Instálalo con el gestor de paquetes de tu sistema operativo:\n"
        "  Debian/Ubuntu:  sudo apt install python3-tk\n"
        "  Fedora/RHEL:    sudo dnf install python3-tkinter\n"
        "  Arch Linux:     sudo pacman -S tk\n"
        "  Windows/macOS:  normalmente ya viene con Python — si falta, reinstala "
        "Python desde python.org asegurándote de incluir Tcl/Tk.",
        file=sys.stderr,
    )


def _print_gui_extra_missing(exc: ImportError) -> None:
    print(
        f"Falta una dependencia de la interfaz gráfica: {exc}\n\n"
        'Instálala con: pip install ".[gui]"',
        file=sys.stderr,
    )


def main() -> None:
    """Punto de entrada seguro — usado por `charlywebaudit-gui` y por
    `charlywebaudit --gui`. Nunca deja escapar un ImportError como
    traceback crudo."""
    try:
        import tkinter  # noqa: F401 — solo para verificar disponibilidad antes de importar el resto
    except ImportError:
        _print_tkinter_missing()
        sys.exit(1)

    try:
        from .app import main as gui_main
    except ImportError as exc:
        _print_gui_extra_missing(exc)
        sys.exit(1)

    gui_main()
