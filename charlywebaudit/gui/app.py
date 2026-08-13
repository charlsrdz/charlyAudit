"""
gui/app.py — Ventana principal de charlyWebAudit.

Ata todo lo demás:
  - `theme.py` + `widgets.py` (branding, punto 2 del pedido; endurecido en v0.0.6)
  - `async_bridge.py` (el motor de orquestación corriendo en segundo plano)
  - `tray.py` (modo segundo plano, punto 3 del pedido)
  - `views/*` (formularios, ejecución, reporte, ayuda, catálogo y dashboard)
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
from .views.catalog_view import CatalogView
from .views.dashboard_view import DashboardView
from .views.help_view import HelpView
from .views.home_view import HomeView
from .views.report_view import ReportView
from .views.run_view import RunView
from .views.test_config_view import TestConfigView

_TAB_ORDER = ["home", "test", "assistant", "catalog", "run", "dashboard", "report", "help"]
_TAB_LABELS = {
    "home": "Inicio",
    "test": "Configurar prueba",
    "assistant": "Asistente IA",
    "catalog": "Catálogo",
    "run": "Ejecutar",
    "dashboard": "Dashboard",
    "report": "Reporte",
    "help": "Ayuda",
}


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(window_title())
        self.root.geometry("1080x720")
        self.root.minsize(860, 620)
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
        self.views["catalog"] = CatalogView(
            self.notebook, self.cfg, self.bridge, on_run_test=self._run_from_catalog, on_run_all=self._run_all_from_catalog
        )
        self.views["run"] = RunView(self.notebook, self.cfg, self.bridge, on_report_ready=self._on_report_ready)
        self.views["dashboard"] = DashboardView(self.notebook)
        self.views["report"] = ReportView(self.notebook)
        self.views["help"] = HelpView(self.notebook)

        for key in _TAB_ORDER:
            self.notebook.add(self.views[key], text=_TAB_LABELS[key])

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Barra de estado inferior — version a la izquierda (identificacion
        # rapida de que binario/instalacion esta corriendo, util al reportar
        # un problema), minimizar a bandeja a la derecha (sin depender solo
        # del boton de cerrar de la ventana, que algunos gestores de
        # ventanas hacen menos descubrible para este proposito).
        #
        # Bug real corregido en v0.1.0a2: el menú contextual del ícono de
        # bandeja depende del backend de `pystray` de cada sistema
        # operativo (GTK/AppIndicator en Linux, Cocoa en macOS, Win32 en
        # Windows) — en algunos entornos de escritorio ese menú no
        # funciona de forma confiable (solo queda disponible la acción por
        # defecto: doble clic para restaurar la ventana), dejando a quien
        # usa la app sin ninguna forma de salir salvo matar el proceso
        # desde la terminal. La corrección robusta: un botón "Salir"
        # SIEMPRE visible en la ventana principal — nunca depende de que
        # el menú de la bandeja funcione en el sistema del usuario.
        status_bar = ttk.Frame(self.root, padding=(12, 6))
        status_bar.pack(fill="x", side="bottom")
        tk.Label(status_bar, text=f"v{APP_VERSION}", bg=self.root["bg"], fg=MUTED, font=("Segoe UI", 8)).pack(
            side="left"
        )
        actions = ttk.Frame(status_bar)
        actions.pack(side="right")
        ttk.Button(actions, text="Salir", style="Ghost.TButton", command=self.quit).pack(side="right", padx=(8, 0))
        ttk.Button(
            actions, text="Minimizar a la bandeja", style="Ghost.TButton", command=self.tray.minimize_to_tray
        ).pack(side="right")

    def show_view(self, key: str) -> None:
        if key in self.views:
            self.notebook.select(self.views[key])

    def _on_tab_changed(self, event) -> None:
        """Refresca el selector de "Ejecutar" cada vez que esa pestaña queda
        visible — sea por navegación programática (show_view) o porque el
        usuario clickeó la pestaña directamente. Así el selector nunca
        muestra pruebas del Catálogo obsoletas (borradas, renombradas)."""
        current = self.notebook.select()
        if current and str(self.views.get("run")) == current:
            self.views["run"].refresh()

    def _on_config_changed(self) -> None:
        self.views["home"].refresh()
        self.views["test"].refresh()
        self.views["assistant"].refresh()

    def _on_report_ready(self, report) -> None:
        self.views["report"].show_report(report)
        self.views["dashboard"].refresh()  # la corrida que acaba de terminar ya debe verse en el Dashboard
        self.show_view("report")

    def _run_from_catalog(self, test_case) -> None:
        self.show_view("run")
        self.views["run"]._start_run(test_case)

    def _run_all_from_catalog(self, test_cases: list) -> None:
        self.show_view("run")
        self.views["run"].run_sequence(test_cases)

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
