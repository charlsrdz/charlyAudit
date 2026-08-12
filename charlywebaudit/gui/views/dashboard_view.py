"""
gui/views/dashboard_view.py — Dashboard comparativo (punto 5 del pedido
v0.1.0a): "una vista dashboard [donde] se pueda ver una comparativa entre
cada prueba evaluando el contraste de cada resultado sobre el tiempo".

Lee `history.py` (un registro por corrida, ver __main__.run_audit) y
muestra, por cada prueba con más de un registro: un gráfico de barras de
duración a lo largo del tiempo (verde/rojo según pasó o falló), y una
tabla con el detalle de cada corrida, con acceso directo al reporte
completo de cualquiera de ellas.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
import webbrowser
from collections import defaultdict
from pathlib import Path
from tkinter import ttk

from ...history import RunRecord, load_history
from ..theme import BG, BORDER, DANGER, MUTED, SUCCESS, SURFACE, SURFACE_2, TEXT, WARNING
from ..widgets import Card, EmptyState, Header, ScrollableFrame


class DashboardView(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=20)
        self._records: list[RunRecord] = []
        self._selected_test: str | None = None
        self._build()

    def _build(self) -> None:
        def _refresh_button(parent):
            return ttk.Button(parent, text="↻ Actualizar", style="Ghost.TButton", command=self.refresh)

        Header(
            self,
            "Dashboard",
            "Comparativa de resultados por prueba a lo largo del tiempo.",
            actions=[_refresh_button],
        ).pack(fill="x", pady=(0, 12))

        selector_row = ttk.Frame(self)
        selector_row.pack(fill="x", pady=(0, 12))
        ttk.Label(selector_row, text="Prueba:", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        self.test_var = tk.StringVar()
        self.test_combo = ttk.Combobox(selector_row, textvariable=self.test_var, state="readonly", width=40)
        self.test_combo.pack(side="left")
        self.test_combo.bind("<<ComboboxSelected>>", lambda e: self._on_select_test())

        self._scrollable = ScrollableFrame(self)
        self._scrollable.pack(fill="both", expand=True)
        self._content = ttk.Frame(self._scrollable.body)
        self._content.pack(fill="both", expand=True)

        self.refresh()

    def refresh(self) -> None:
        self._records = load_history()
        by_test: dict[str, list[RunRecord]] = defaultdict(list)
        for r in self._records:
            by_test[r.test_name].append(r)
        self._by_test = dict(by_test)

        names = sorted(self._by_test.keys())
        self.test_combo["values"] = names
        if not names:
            self.test_var.set("")
            self._render_empty()
            return
        if self._selected_test not in names:
            self._selected_test = names[0]
        self.test_var.set(self._selected_test)
        self._render_test(self._selected_test)

    def _on_select_test(self) -> None:
        self._selected_test = self.test_var.get()
        self._render_test(self._selected_test)

    def _render_empty(self) -> None:
        for child in self._content.winfo_children():
            child.destroy()
        EmptyState(
            self._content,
            "Todavía no hay corridas registradas.\nCorré una prueba (desde Ejecutar o el Catálogo) para empezar a ver comparativas aquí.",
        ).pack(anchor="w", fill="x")

    def _render_test(self, test_name: str) -> None:
        for child in self._content.winfo_children():
            child.destroy()

        records = sorted(self._by_test.get(test_name, []), key=lambda r: r.started_at)
        if not records:
            self._render_empty()
            return

        # --- Resumen rápido ---------------------------------------------------
        summary_card = Card(self._content, title="RESUMEN")
        summary_card.pack(fill="x", pady=(0, 12))
        total = len(records)
        passed_runs = sum(1 for r in records if r.all_passed)
        avg_duration = sum(r.duration_ms for r in records) / total
        last = records[-1]
        stats_row = tk.Frame(summary_card.body, bg=SURFACE)
        stats_row.pack(fill="x")
        for label, value, color in [
            ("Corridas totales", str(total), TEXT),
            ("Pasaron completas", f"{passed_runs}/{total}", SUCCESS if passed_runs == total else WARNING),
            ("Duración promedio", f"{avg_duration/1000:.1f}s", TEXT),
            ("Última corrida", "✓ pasó" if last.all_passed else "✕ falló", SUCCESS if last.all_passed else DANGER),
        ]:
            col = tk.Frame(stats_row, bg=SURFACE)
            col.pack(side="left", padx=(0, 28))
            tk.Label(col, text=value, bg=SURFACE, fg=color, font=("Segoe UI", 15, "bold")).pack(anchor="w")
            tk.Label(col, text=label, bg=SURFACE, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

        # --- Gráfico: duración por corrida, verde/rojo segun resultado --------
        chart_card = Card(self._content, title="DURACIÓN POR CORRIDA (más recientes a la derecha)")
        chart_card.pack(fill="x", pady=(0, 12))
        self._draw_duration_chart(chart_card.body, records)

        # --- Tabla de corridas --------------------------------------------------
        table_card = Card(self._content, title="HISTORIAL DE CORRIDAS")
        table_card.pack(fill="both", expand=True)
        self._render_table(table_card.body, list(reversed(records)))

    def _draw_duration_chart(self, parent: tk.Widget, records: list[RunRecord]) -> None:
        width, height, pad = 760, 180, 30
        canvas = tk.Canvas(parent, width=width, height=height, bg=SURFACE, highlightthickness=0)
        canvas.pack(fill="x")

        durations = [r.duration_ms for r in records]
        max_dur = max(durations) or 1
        n = len(records)
        plot_w = width - 2 * pad
        plot_h = height - 2 * pad
        bar_w = max(6, min(40, plot_w / max(n, 1) * 0.6))
        gap = (plot_w / max(n, 1)) if n > 1 else plot_w

        # Eje base
        canvas.create_line(pad, height - pad, width - pad, height - pad, fill=BORDER)

        for i, r in enumerate(records):
            x_center = pad + gap * i + gap / 2 if n > 1 else width / 2
            bar_h = (r.duration_ms / max_dur) * plot_h
            color = SUCCESS if r.all_passed else DANGER
            if not r.assistant_analysis_complete:
                color = WARNING if r.all_passed else DANGER  # incompleto pero paso -> ambar, distinto de un exito pleno
            x0 = x_center - bar_w / 2
            x1 = x_center + bar_w / 2
            y1 = height - pad
            y0 = y1 - bar_h
            canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            canvas.create_text(x_center, y0 - 10, text=f"{r.duration_ms/1000:.1f}s", fill=MUTED, font=("Segoe UI", 7))

        legend = tk.Frame(parent, bg=SURFACE)
        legend.pack(anchor="w", pady=(6, 0))
        for color, text in [(SUCCESS, "pasó, análisis completo"), (WARNING, "pasó, análisis incompleto"), (DANGER, "falló")]:
            item = tk.Frame(legend, bg=SURFACE)
            item.pack(side="left", padx=(0, 16))
            tk.Frame(item, bg=color, width=10, height=10).pack(side="left", padx=(0, 4))
            tk.Label(item, text=text, bg=SURFACE, fg=MUTED, font=("Segoe UI", 8)).pack(side="left")

    def _render_table(self, parent: tk.Widget, records: list[RunRecord]) -> None:
        header = tk.Frame(parent, bg=SURFACE_2)
        header.pack(fill="x")
        for text, w in [("Fecha", 20), ("Resultado", 14), ("Duración", 10), ("Análisis", 14), ("", 10)]:
            tk.Label(header, text=text, bg=SURFACE_2, fg=MUTED, font=("Segoe UI", 8, "bold"), width=w, anchor="w").pack(
                side="left", padx=4, pady=4
            )

        for r in records:
            row = tk.Frame(parent, bg=SURFACE)
            row.pack(fill="x")
            date_display = r.started_at.split("T")[0] + " " + r.started_at.split("T")[1][:8] if "T" in r.started_at else r.started_at
            tk.Label(row, text=date_display, bg=SURFACE, fg=TEXT, font=("Segoe UI", 9), width=20, anchor="w").pack(side="left", padx=4, pady=3)
            result_text = f"✓ {r.passed} pasaron" if r.all_passed else f"✕ {r.failed} fallaron"
            tk.Label(row, text=result_text, bg=SURFACE, fg=(SUCCESS if r.all_passed else DANGER), font=("Segoe UI", 9), width=14, anchor="w").pack(side="left", padx=4, pady=3)
            tk.Label(row, text=f"{r.duration_ms/1000:.1f}s", bg=SURFACE, fg=TEXT, font=("Segoe UI", 9), width=10, anchor="w").pack(side="left", padx=4, pady=3)
            analysis_text = "completo" if r.assistant_analysis_complete else "incompleto"
            tk.Label(row, text=analysis_text, bg=SURFACE, fg=(SUCCESS if r.assistant_analysis_complete else WARNING), font=("Segoe UI", 9), width=14, anchor="w").pack(side="left", padx=4, pady=3)
            if r.report_path and Path(r.report_path).is_file():
                ttk.Button(row, text="Ver reporte", style="Ghost.TButton", command=lambda p=r.report_path: self._open_report(p)).pack(side="left", padx=4, pady=2)

    def _open_report(self, path: str) -> None:
        webbrowser.open(Path(path).as_uri())
