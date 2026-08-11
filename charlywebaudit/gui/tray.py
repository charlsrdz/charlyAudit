"""
gui/tray.py — Modo segundo plano (punto 3 del pedido v0.0.5): minimizar la
ventana a un ícono de bandeja del sistema / barra de estado, en vez de
cerrar la app.

Backend: `pystray` — es la librería estándar de facto para esto en Python,
y ya declara sus propias dependencias nativas condicionales por sistema
operativo (`pyobjc-framework-Quartz` en macOS, `python-xlib` en Linux,
Win32 API nativa en Windows vía `ctypes`, sin dependencia adicional) — no
hace falta lógica propia de detección de plataforma para instalar lo
correcto en cada una.

Nota de UX honesta sobre macOS: a diferencia de Windows/Linux, en macOS lo
convencional es que una app minimizada siga viviendo en el Dock, no
(solo) en la barra de menú — pystray coloca el ícono en la barra de menú
(NSStatusBar) igual que en los otros dos sistemas, que es funcionalmente
equivalente a lo pedido ("estado de visibilidad mínima") pero se aparta un
poco de la convención más común de macOS. Se documenta aquí en vez de
prometer una integración 100% nativa de Dock que está fuera de alcance de
esta versión.

pystray corre su propio bucle bloqueante (`icon.run()`) — igual que el
motor asyncio (ver async_bridge.py), necesita su propio hilo dedicado; los
callbacks del menú de bandeja se disparan DESDE ese hilo, nunca desde el de
Tkinter, así que cualquier acción que toque la ventana se reprograma de
vuelta al hilo principal con `root.after(0, ...)` — mismo patrón ya
validado en el resto de la GUI.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path

from PIL import Image

from ..constants import APP_NAME

_ICON_PATH = Path(__file__).parent / "assets" / "icon48.png"


class TrayController:
    """Encapsula el ícono de bandeja y la lógica de minimizar/restaurar.
    `root` es la ventana Tk principal — nunca se destruye al "cerrar" en
    modo bandeja, solo se oculta (`withdraw()`) y se vuelve a mostrar
    (`deiconify()`)."""

    def __init__(self, root: tk.Tk, *, on_run_now=None, on_quit=None) -> None:
        self.root = root
        self.on_run_now = on_run_now
        self.on_quit = on_quit
        self._icon = None
        self._thread: threading.Thread | None = None
        self._minimized = False

    def enable_minimize_to_tray(self) -> None:
        """Intercepta el botón de cerrar de la ventana: en vez de terminar
        la app, la manda a la bandeja. Se llama una vez al iniciar la GUI."""
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)

    def minimize_to_tray(self) -> None:
        if self._minimized:
            return
        self._minimized = True
        self.root.withdraw()
        self._start_icon()

    def restore(self) -> None:
        if not self._minimized:
            return
        self._minimized = False
        self.root.after(0, self._restore_on_main_thread)

    def _restore_on_main_thread(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._stop_icon()

    def _start_icon(self) -> None:
        if self._thread is not None:
            return
        try:
            import pystray
        except ImportError:
            # Bug real corregido: sin esto, faltar pystray (misma causa que
            # con tkinterweb — instalacion parcial sin el extra "[gui]")
            # crasheaba con un traceback crudo al intentar minimizar a la
            # bandeja, incluido desde el propio boton X de cerrar la
            # ventana. Se avisa y se mantiene la ventana abierta en vez de
            # fallar silenciosa o ruidosamente.
            self._minimized = False
            from tkinter import messagebox

            messagebox.showwarning(
                APP_NAME,
                "No se pudo minimizar a la bandeja: falta el paquete 'pystray'.\n\n"
                'Instálalo con: pip install ".[gui]"\n\n'
                "La ventana se mantiene abierta.",
                parent=self.root,
            )
            self.root.deiconify()
            return

        try:
            image = Image.open(_ICON_PATH)
        except OSError:
            # Fallback minimo: un cuadrado solido con el color de marca, por
            # si el icono empaquetado no se pudo leer (nunca debe impedir
            # que el modo bandeja funcione).
            from .theme import BRAND

            image = Image.new("RGB", (48, 48), color=BRAND)

        menu = pystray.Menu(
            pystray.MenuItem("Abrir " + APP_NAME, lambda: self.restore(), default=True),
            pystray.MenuItem("Correr prueba ahora", lambda: self._trigger_run()),
            pystray.MenuItem("Salir", lambda: self._trigger_quit()),
        )
        self._icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)
        self._thread = threading.Thread(target=self._icon.run, daemon=True, name="redgpswebaudit-tray")
        self._thread.start()

    def _stop_icon(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self._icon = None
        self._thread = None

    def _trigger_run(self) -> None:
        self.restore()
        if self.on_run_now:
            self.root.after(150, self.on_run_now)  # deja que la ventana termine de restaurarse primero

    def _trigger_quit(self) -> None:
        self._stop_icon()
        self.root.after(0, self._quit_on_main_thread)

    def _quit_on_main_thread(self) -> None:
        if self.on_quit:
            self.on_quit()
        else:
            self.root.destroy()

    def shutdown(self) -> None:
        """Se llama al cerrar la app de verdad (no minimizar) — limpia el
        ícono de bandeja si estaba activo."""
        self._stop_icon()
