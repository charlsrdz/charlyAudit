"""
gui/views/dashboard_view.py — Dashboard comparativo (punto 5 del pedido
v0.1.0a): "una vista dashboard [donde] se pueda ver una comparativa entre
cada prueba evaluando el contraste de cada resultado sobre el tiempo".

Lee `history.py` (un registro por corrida, ver __main__.run_audit) y
muestra dos niveles de detalle:

  1. RESUMEN GENERAL (v0.1.6, siempre visible, cruza TODAS las pruebas):
     tasa de éxito global, la prueba más rápida en promedio, la que más
     falla, la más confiable, y la actividad más reciente — para que
     alguien con muchas pruebas guardadas tenga una foto completa sin
     tener que revisar cada una por separado.
  2. Por cada prueba (selector, como antes): un gráfico de barras de
     duración a lo largo del tiempo, y una tabla con el detalle de cada
     corrida, con acceso directo al reporte completo de cualquiera.
"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from collections import defaultdict
from pathlib import Path
from tkinter import ttk

from ...config import AppConfig, save_config
from ...history import RunRecord, load_history
from ...timezone_utils import COMMON_TIMEZONES, detect_local_timezone, format_local
from ..theme import BORDER, BRAND, DANGER, MUTED, SUCCESS, SURFACE, SURFACE_2, TEXT, WARNING
from ..widgets import Card, EmptyState, Header, ScrollableFrame

_BROWSER_OUTCOME_LABELS = {
    "closed_normally": ("cierre normal", SUCCESS),
    "closed_unexpectedly": ("cierre inesperado", DANGER),
    "never_connected": ("sin conectar", WARNING),
}


class DashboardView(ttk.Frame):
    def __init__(self, parent: tk.Widget, cfg: AppConfig) -> None:
        super().__init__(parent, padding=20)
        self.cfg = cfg
        self._detected_tz = detect_local_timezone()
        self._records: list[RunRecord] = []
        self._by_test: dict[str, list[RunRecord]] = {}
        self._selected_test: str | None = None
        self._build()

    def _tz_label(self) -> str:
        """Etiqueta legible para el combo — la zona guardada en cfg, o la
        detectada automáticamente si no hay ninguna explícita todavía."""
        return self.cfg.timezone or self._detected_tz

    def _build(self) -> None:
        def _refresh_button(parent):
            return ttk.Button(parent, text="↻ Actualizar", style="Ghost.TButton", command=self.refresh)

        Header(
            self,
            "Dashboard",
            "Panorama general de todas tus pruebas, y comparativa detallada por prueba a lo largo del tiempo.",
            actions=[_refresh_button],
        ).pack(fill="x", pady=(0, 12))

        tz_row = ttk.Frame(self)
        tz_row.pack(fill="x", pady=(0, 12))
        ttk.Label(tz_row, text="Zona horaria:", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        self.tz_var = tk.StringVar(value=self._tz_label())
        tz_values = [self._detected_tz] + [z for z in COMMON_TIMEZONES if z != self._detected_tz]
        self.tz_combo = ttk.Combobox(tz_row, textvariable=self.tz_var, values=tz_values, width=32)
        self.tz_combo.pack(side="left")
        self.tz_combo.bind("<<ComboboxSelected>>", lambda e: self._on_tz_changed())
        self.tz_combo.bind("<Return>", lambda e: self._on_tz_changed())
        ttk.Label(
            tz_row, text=f"(detectada automáticamente: {self._detected_tz})", style="Muted.TLabel"
        ).pack(side="left", padx=(10, 0))

        self._scrollable = ScrollableFrame(self)
        self._scrollable.pack(fill="both", expand=True)

        self._overview_container = ttk.Frame(self._scrollable.body)
        self._overview_container.pack(fill="x")

        selector_row = ttk.Frame(self._scrollable.body)
        selector_row.pack(fill="x", pady=(18, 12))
        ttk.Label(selector_row, text="Ver el detalle de:", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        self.test_var = tk.StringVar()
        self.test_combo = ttk.Combobox(selector_row, textvariable=self.test_var, state="readonly", width=40)
        self.test_combo.pack(side="left")
        self.test_combo.bind("<<ComboboxSelected>>", lambda e: self._on_select_test())

        self._content = ttk.Frame(self._scrollable.body)
        self._content.pack(fill="both", expand=True)

        self.refresh()

    def _on_tz_changed(self) -> None:
        """Guarda la zona elegida en la config persistida — valida contra
        el universo real de zonas conocidas antes de aceptarla (si el
        usuario escribió algo inválido a mano, no se guarda silenciosamente
        una zona que después haría fallar la conversión)."""
        from zoneinfo import available_timezones

        candidate = self.tz_var.get().strip()
        is_offset_format = candidate.startswith("UTC+") or candidate.startswith("UTC-")
        if candidate and candidate not in available_timezones() and not is_offset_format:
            self.tz_var.set(self._tz_label())  # invalida — revierte al valor anterior
            return
        self.cfg.timezone = "" if candidate == self._detected_tz else candidate
        save_config(self.cfg)
        self.refresh()

    def refresh(self) -> None:
        self._records = load_history()
        by_test: dict[str, list[RunRecord]] = defaultdict(list)
        for r in self._records:
            by_test[r.test_name].append(r)
        self._by_test = dict(by_test)

        self._render_overview()

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

    # --- Resumen general (siempre visible, cruza todas las pruebas) --------

    def _render_overview(self) -> None:
        """Punto 1.2 del pedido: "resumen general (mejores tiempos, pruebas
        con más fallos/aciertos, etc.)" — se calcula sobre TODO el
        historial, no sobre la prueba seleccionada abajo."""
        for child in self._overview_container.winfo_children():
            child.destroy()

        if not self._records:
            return

        card = Card(self._overview_container, title="RESUMEN GENERAL — TODAS LAS PRUEBAS")
        card.pack(fill="x")
        body = card.body

        total_runs = len(self._records)
        total_tests = len(self._by_test)
        total_passed_runs = sum(1 for r in self._records if r.all_passed)
        pass_rate = total_passed_runs / total_runs * 100

        # Promedios por prueba, para elegir "la mas rapida" y "la que mas falla"
        # de forma justa (no solo la ultima corrida de cada una).
        per_test_avg_duration: dict[str, float] = {}
        per_test_pass_rate: dict[str, float] = {}
        for name, records in self._by_test.items():
            per_test_avg_duration[name] = sum(r.duration_ms for r in records) / len(records)
            per_test_pass_rate[name] = sum(1 for r in records if r.all_passed) / len(records) * 100

        fastest_name = min(per_test_avg_duration, key=per_test_avg_duration.get)
        fastest_time = per_test_avg_duration[fastest_name] / 1000

        most_reliable_name = max(per_test_pass_rate, key=lambda n: (per_test_pass_rate[n], len(self._by_test[n])))
        most_reliable_rate = per_test_pass_rate[most_reliable_name]

        worst_name = min(per_test_pass_rate, key=lambda n: (per_test_pass_rate[n], -len(self._by_test[n])))
        worst_rate = per_test_pass_rate[worst_name]
        worst_fail_count = sum(1 for r in self._by_test[worst_name] if not r.all_passed)

        most_recent = max(self._records, key=lambda r: r.started_at)

        stats_row = tk.Frame(body, bg=SURFACE)
        stats_row.pack(fill="x")
        for label, value, color in [
            ("Corridas totales", str(total_runs), TEXT),
            ("Pruebas distintas", str(total_tests), TEXT),
            ("Tasa de éxito global", f"{pass_rate:.0f}%", SUCCESS if pass_rate >= 90 else (WARNING if pass_rate >= 60 else DANGER)),
        ]:
            col = tk.Frame(stats_row, bg=SURFACE)
            col.pack(side="left", padx=(0, 32))
            tk.Label(col, text=value, bg=SURFACE, fg=color, font=("Segoe UI", 20, "bold")).pack(anchor="w")
            tk.Label(col, text=label, bg=SURFACE, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=12)

        highlights = tk.Frame(body, bg=SURFACE)
        highlights.pack(fill="x")
        highlights.columnconfigure((0, 1), weight=1, uniform="highlights")
        self._highlight_tile(
            highlights, row=0, col=0, title="⚡ MÁS RÁPIDA (promedio)", name=fastest_name,
            detail=f"{fastest_time:.1f}s de promedio · {len(self._by_test[fastest_name])} corrida(s)", color=BRAND,
        )
        self._highlight_tile(
            highlights, row=0, col=1, title="🛡 MÁS CONFIABLE", name=most_reliable_name,
            detail=f"{most_reliable_rate:.0f}% de éxito · {len(self._by_test[most_reliable_name])} corrida(s)", color=SUCCESS,
        )
        self._highlight_tile(
            highlights, row=1, col=0, title="⚠ MÁS PROBLEMÁTICA", name=worst_name,
            detail=f"{worst_fail_count} fallo(s) de {len(self._by_test[worst_name])} corrida(s) ({worst_rate:.0f}% éxito)",
            color=DANGER if worst_rate < 100 else SUCCESS, pady_top=10,
        )
        recent_date = format_local(most_recent.started_at, self._tz_label(), with_seconds=False)
        self._highlight_tile(
            highlights, row=1, col=1, title="🕓 ACTIVIDAD MÁS RECIENTE", name=most_recent.test_name,
            detail=f"{recent_date} · {'✓ pasó' if most_recent.all_passed else '✕ falló'}",
            color=SUCCESS if most_recent.all_passed else DANGER, pady_top=10,
        )

    def _highlight_tile(self, parent, *, row, col, title, name, detail, color, pady_top=0):
        tile = tk.Frame(parent, bg=SURFACE_2, padx=12, pady=10)
        tile.grid(row=row, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0 if col == 1 else 8), pady=(pady_top, 0))
        tk.Label(tile, text=title, bg=SURFACE_2, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(tile, text=name, bg=SURFACE_2, fg=color, font=("Segoe UI", 12, "bold"), wraplength=280, justify="left").pack(
            anchor="w", pady=(2, 0)
        )
        tk.Label(tile, text=detail, bg=SURFACE_2, fg=TEXT, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

    # --- Detalle por prueba --------------------------------------------------

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

        # --- Resumen de la prueba seleccionada ---------------------------------
        summary_card = Card(self._content, title=f"RESUMEN — {test_name}")
        summary_card.pack(fill="x", pady=(0, 12))
        total = len(records)
        passed_runs = sum(1 for r in records if r.all_passed)
        avg_duration = sum(r.duration_ms for r in records) / total
        best_duration = min(r.duration_ms for r in records) / 1000
        last = records[-1]
        unexpected_closures = sum(1 for r in records if r.browser_outcome == "closed_unexpectedly")

        stats_row = tk.Frame(summary_card.body, bg=SURFACE)
        stats_row.pack(fill="x")
        for label, value, color in [
            ("Corridas totales", str(total), TEXT),
            ("Pasaron completas", f"{passed_runs}/{total}", SUCCESS if passed_runs == total else WARNING),
            ("Duración promedio", f"{avg_duration/1000:.1f}s", TEXT),
            ("Mejor tiempo", f"{best_duration:.1f}s", BRAND),
            ("Última corrida", "✓ pasó" if last.all_passed else "✕ falló", SUCCESS if last.all_passed else DANGER),
        ]:
            col = tk.Frame(stats_row, bg=SURFACE)
            col.pack(side="left", padx=(0, 24))
            tk.Label(col, text=value, bg=SURFACE, fg=color, font=("Segoe UI", 15, "bold")).pack(anchor="w")
            tk.Label(col, text=label, bg=SURFACE, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")

        stats_row2 = tk.Frame(summary_card.body, bg=SURFACE)
        stats_row2.pack(fill="x", pady=(10, 0))
        for label, value, color in [
            ("Cierres inesperados del navegador", str(unexpected_closures), DANGER if unexpected_closures else SUCCESS),
        ]:
            col = tk.Frame(stats_row2, bg=SURFACE)
            col.pack(side="left", padx=(0, 28))
            tk.Label(col, text=value, bg=SURFACE, fg=color, font=("Segoe UI", 13, "bold")).pack(anchor="w")
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
            x0 = x_center - bar_w / 2
            x1 = x_center + bar_w / 2
            y1 = height - pad
            y0 = y1 - bar_h
            canvas.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            canvas.create_text(x_center, y0 - 10, text=f"{r.duration_ms/1000:.1f}s", fill=MUTED, font=("Segoe UI", 7))

        legend = tk.Frame(parent, bg=SURFACE)
        legend.pack(anchor="w", pady=(6, 0))
        for color, text in [(SUCCESS, "pasó"), (DANGER, "falló")]:
            item = tk.Frame(legend, bg=SURFACE)
            item.pack(side="left", padx=(0, 16))
            tk.Frame(item, bg=color, width=10, height=10).pack(side="left", padx=(0, 4))
            tk.Label(item, text=text, bg=SURFACE, fg=MUTED, font=("Segoe UI", 8)).pack(side="left")

    def _render_table(self, parent: tk.Widget, records: list[RunRecord]) -> None:
        header = tk.Frame(parent, bg=SURFACE_2)
        header.pack(fill="x")
        for text, w in [
            ("Fecha", 18),
            ("Resultado", 12),
            ("Duración", 9),
            ("Navegador", 16),
            ("", 10),
        ]:
            tk.Label(header, text=text, bg=SURFACE_2, fg=MUTED, font=("Segoe UI", 8, "bold"), width=w, anchor="w").pack(
                side="left", padx=4, pady=4
            )

        for i, r in enumerate(records):
            # Zebra striping: alterna entre SURFACE y SURFACE_2, sutil, para
            # que una tabla con muchas filas se escanee visualmente mas facil
            # (mejora real de legibilidad, sin agregar ruido de color nuevo).
            row_bg = SURFACE if i % 2 == 0 else SURFACE_2
            row = tk.Frame(parent, bg=row_bg)
            row.pack(fill="x")
            date_display = format_local(r.started_at, self._tz_label())
            tk.Label(row, text=date_display, bg=row_bg, fg=TEXT, font=("Segoe UI", 9), width=18, anchor="w").pack(side="left", padx=4, pady=4)
            result_text = f"✓ {r.passed} pasaron" if r.all_passed else f"✕ {r.failed} fallaron"
            tk.Label(row, text=result_text, bg=row_bg, fg=(SUCCESS if r.all_passed else DANGER), font=("Segoe UI", 9), width=12, anchor="w").pack(side="left", padx=4, pady=4)
            tk.Label(row, text=f"{r.duration_ms/1000:.1f}s", bg=row_bg, fg=TEXT, font=("Segoe UI", 9), width=9, anchor="w").pack(side="left", padx=4, pady=4)
            outcome_text, outcome_color = _BROWSER_OUTCOME_LABELS.get(r.browser_outcome or "", ("—", MUTED))
            tk.Label(row, text=outcome_text, bg=row_bg, fg=outcome_color, font=("Segoe UI", 9), width=16, anchor="w").pack(side="left", padx=4, pady=4)
            if r.report_path and Path(r.report_path).is_file():
                ttk.Button(row, text="Ver reporte", style="Ghost.TButton", command=lambda p=r.report_path: self._open_report(p)).pack(side="left", padx=4, pady=2)
            else:
                tk.Label(row, text="(sin reporte)", bg=row_bg, fg=MUTED, font=("Segoe UI", 8, "italic")).pack(side="left", padx=4, pady=4)

    def _open_report(self, path: str) -> None:
        webbrowser.open(Path(path).as_uri())
