"""
gui/views/report_view.py — Visualización completa del reporte generado
(punto 4 del pedido): el mismo HTML que produce `report/html.py`,
renderizado dentro de la propia app (no solo un enlace externo), más
"Abrir en el navegador" (para la fidelidad completa de CSS que tkinterweb
no soporta del todo — ver report_render.py) y "Guardar como".

v0.0.6: se corrigió el contraste de los botones deshabilitados ("Guardar
como…" antes de tener un reporte) — ver el fix en gui/theme.py, que
antes usaba un color muy cercano al estado activo.
"""

from __future__ import annotations

import tempfile
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, ttk

from ...report.builder import CombinedReport
from ...report.html import render_html, save_report
from ..report_render import make_tkinterweb_compatible
from ..theme import SURFACE
from ..widgets import EmptyState, Header


class ReportView(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=(20, 20, 20, 0))
        self.report: CombinedReport | None = None
        self._raw_html: str | None = None
        self._build()

    def _build(self) -> None:
        def _open_browser_btn(parent):
            self.open_browser_btn = ttk.Button(
                parent, text="Abrir en el navegador", style="Ghost.TButton", command=self._open_in_browser, state="disabled"
            )
            return self.open_browser_btn

        def _save_btn(parent):
            self.save_btn = ttk.Button(
                parent, text="Guardar como…", style="Brand.TButton", command=self._save_as, state="disabled"
            )
            return self.save_btn

        Header(self, "Reporte", actions=[_open_browser_btn, _save_btn]).pack(fill="x")

        def _open_saved_button(parent):
            return ttk.Button(parent, text="Abrir reporte .html guardado…", style="Ghost.TButton", command=self._open_saved_file)

        self._empty_state = EmptyState(
            self,
            "Todavía no hay un reporte para mostrar — corre una prueba desde 'Ejecutar',\n"
            "o abre un reporte .html ya guardado.",
            action_widget_factory=_open_saved_button,
        )
        self._empty_state.pack(anchor="w", fill="x")

        self._html_container = tk.Frame(self, bg=SURFACE)
        self._html_frame = None  # se crea perezosamente: importar tkinterweb es relativamente costoso

    def show_report(self, report: CombinedReport) -> None:
        self.report = report
        html = render_html(report)
        self._display_html(html)
        self.open_browser_btn.configure(state="normal")
        self.save_btn.configure(state="normal")

    def _open_saved_file(self) -> None:
        path = filedialog.askopenfilename(title="Abrir reporte HTML", filetypes=[("HTML", "*.html"), ("Todos", "*.*")])
        if not path:
            return
        html = Path(path).read_text(encoding="utf-8")
        self.report = None  # viene de un archivo externo, no de un CombinedReport en memoria
        self._raw_html = html
        self._display_html(html)
        self.open_browser_btn.configure(state="normal")
        self.save_btn.configure(state="disabled")  # "guardar como" reexporta un CombinedReport; un archivo ya abierto ya esta guardado

    def _display_html(self, html: str) -> None:
        self._raw_html = html
        self._empty_state.pack_forget()
        self._html_container.pack(fill="both", expand=True, pady=(16, 20))

        if self._html_frame is None:
            # Import diferido: tkinterweb es una dependencia opcional (extra
            # "gui") — no debe fallar el resto de la app si no esta instalada,
            # y solo se paga su costo de import cuando de verdad hace falta.
            from tkinterweb import HtmlFrame

            self._html_frame = HtmlFrame(self._html_container, messages_enabled=False)
            self._html_frame.pack(fill="both", expand=True)

        adapted = make_tkinterweb_compatible(html)
        self._html_frame.load_html(adapted)

    def _open_in_browser(self) -> None:
        if not self._raw_html:
            return
        # Un archivo temporal con el HTML SIN adaptar (var() intactas) — el
        # navegador real si soporta CSS variables, a diferencia de tkinterweb.
        tmp = Path(tempfile.gettempdir()) / "charlywebaudit-report-preview.html"
        tmp.write_text(self._raw_html, encoding="utf-8")
        webbrowser.open(tmp.as_uri())

    def _save_as(self) -> None:
        if not self.report:
            return
        default_name = f"reporte-{Path(self.report.spec_path).stem}.html"
        path = filedialog.asksaveasfilename(
            title="Guardar reporte", initialfile=default_name, defaultextension=".html", filetypes=[("HTML", "*.html")]
        )
        if not path:
            return
        save_report(self.report, Path(path))
