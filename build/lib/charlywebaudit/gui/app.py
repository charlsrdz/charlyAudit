"""
gui/app.py — Ventana principal de charlyWebAudit.

Ata todo lo demás:
  - `theme.py` + `widgets.py` (branding, punto 2 del pedido; endurecido en v0.0.6)
  - `async_bridge.py` (el motor de orquestación corriendo en segundo plano)
  - `tray.py` (modo segundo plano, punto 3 del pedido)
  - `views/*` (formularios, ejecución, reporte y ayuda, punto 4 del pedido)
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..config import load_config
from ..constants import APP_VERSION
from .async_bridge import AsyncBridge
from .theme import MUTED, apply_theme, apply_window_icon, window_title
from .tray import TrayController
from .views.assistant_config_view import AssistantConfigView
from .views.help_view import HelpView
from .views.home_view import HomeView
from .views.report_view import ReportView
from .views.run_view import RunView
from .views.test_config_view import TestConfigView

_TAB_ORDER = ["home", "test", "assistant", "run", "report", "help"]
_TAB_LABELS = {
    "home": "Inicio",
    "test": "Configurar prueba",
    "assistant": "Asistente IA",
    "run": "Ejecutar",
    "report": "Reporte",
    "help": "Ayuda",
}


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(window_title())
        self.root.geometry("980x680")
        self.root.minsize(820, 600)
        apply_theme(self.root)
        apply_window_icon(self.root)

        self.cfg = load_config()
        self.bridge = AsyncBridge()
        self.bridge.start()

        self.tray = TrayController(self.root, on_run_now=self._trigger_run_from_tray, on_quit=self.quit)
        self.tray.enable_minimize_to_tray()

        self._build_layout()

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True)

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True)

        self.views: dict[str, ttk.Frame] = {}

        self.views["home"] = HomeView(self.notebook, self.cfg, on_navigate=self.show_view)
        self.views["test"] = TestConfigView(self.notebook, self.cfg, on_change=self._on_config_changed)
        self.views["assistant"] = AssistantConfigView(self.notebook, self.cfg, on_change=self._on_config_changed)
        self.views["run"] = RunView(self.notebook, self.cfg, self.bridge, on_report_ready=self._on_report_ready)
        self.views["report"] = ReportView(self.notebook)
        self.views["help"] = HelpView(self.notebook)

        for key in _TAB_ORDER:
            self.notebook.add(self.views[key], text=_TAB_LABELS[key])

        # Barra de estado inferior — version a la izquierda (identificacion
        # rapida de que binario/instalacion esta corriendo, util al reportar
        # un problema), minimizar a bandeja a la derecha (sin depender solo
        # del boton de cerrar de la ventana, que algunos gestores de
        # ventanas hacen menos descubrible para este proposito).
        status_bar = ttk.Frame(self.root, padding=(12, 6))
        status_bar.pack(fill="x", side="bottom")
        tk.Label(status_bar, text=f"v{APP_VERSION}", bg=self.root["bg"], fg=MUTED, font=("Segoe UI", 8)).pack(
            side="left"
        )
        ttk.Button(
            status_bar, text="Minimizar a la bandeja", style="Ghost.TButton", command=self.tray.minimize_to_tray
        ).pack(side="right")

    def show_view(self, key: str) -> None:
        if key in self.views:
            self.notebook.select(self.views[key])

    def _on_config_changed(self) -> None:
        self.views["home"].refresh()
        self.views["assistant"].refresh()
        self.views["test"].refresh()

    def _on_report_ready(self, report) -> None:
        self.views["report"].show_report(report)
        self.show_view("report")

    def _trigger_run_from_tray(self) -> None:
        self.show_view("run")
        self.views["run"]._start_run()  # mismo camino que el boton "Correr prueba" de la vista

    def quit(self) -> None:
        self.tray.shutdown()
        self.bridge.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = App()
    try:
        app.run()
    except KeyboardInterrupt:
        app.quit()


if __name__ == "__main__":
    main()
