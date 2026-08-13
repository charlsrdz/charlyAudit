"""
gui/views/test_config_view.py — Formulario de configuración de la prueba:
script Playwright, URL, cabeceras HTTP personalizadas.

v0.0.6: envuelto en Card (consistencia visual) y se agregaron textos de
ejemplo bajo cada campo — la auditoría visual notó que un campo vacío sin
ninguna pista de formato esperado (¿ruta absoluta? ¿con o sin http://?)
es más difícil de completar correctamente a la primera.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from ...config import AppConfig, save_config
from ..theme import BG, MUTED
from ..widgets import Card, Header


class TestConfigView(ttk.Frame):
    def __init__(self, parent: tk.Widget, cfg: AppConfig, on_change=None) -> None:
        super().__init__(parent, padding=20)
        self.cfg = cfg
        self.on_change = on_change
        self._headers_rows: list[tuple[tk.StringVar, tk.StringVar, ttk.Frame]] = []
        self._build()

    def _build(self) -> None:
        Header(self, "Configurar prueba", "Script de Playwright, URL objetivo y cabeceras HTTP personalizadas.").pack(
            fill="x", pady=(0, 16)
        )

        card = Card(self, title="DATOS DE LA PRUEBA")
        card.pack(fill="x")
        body = card.body

        # --- Script ---------------------------------------------------------
        ttk.Label(body, text="SCRIPT PLAYWRIGHT (.spec.ts)", style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        spec_row = ttk.Frame(body)
        spec_row.pack(fill="x")
        self.spec_var = tk.StringVar(value=self.cfg.test.spec_path or "")
        spec_entry = ttk.Entry(spec_row, textvariable=self.spec_var, width=60)
        spec_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(spec_row, text="Examinar…", style="Ghost.TButton", command=self._browse_spec).pack(
            side="left", padx=(8, 0)
        )
        self._hint(body, "Ejemplo: /home/usuario/proyectos/mi-suite/login.spec.ts")

        # --- URL --------------------------------------------------------------
        ttk.Label(body, text="URL DE LA PRUEBA", style="Section.TLabel").pack(anchor="w", pady=(16, 4))
        self.url_var = tk.StringVar(value=self.cfg.test.url or "")
        ttk.Entry(body, textvariable=self.url_var, width=60).pack(anchor="w", fill="x")
        self._hint(body, "Ejemplo: https://plataforma.redgps.com/mapa/google")

        # --- Cabeceras ----------------------------------------------------
        ttk.Label(body, text="CABECERAS HTTP PERSONALIZADAS", style="Section.TLabel").pack(anchor="w", pady=(16, 4))
        self.headers_container = tk.Frame(body, bg=body["bg"])
        self.headers_container.pack(anchor="w", fill="x")
        for name, value in (self.cfg.test.headers or {}).items():
            self._add_header_row(name, value)

        ttk.Button(body, text="+ Agregar cabecera", style="Ghost.TButton", command=lambda: self._add_header_row()).pack(
            anchor="w", pady=(8, 0)
        )

        # --- Extensión CharlyAudit --------------------------------------------
        ttk.Label(body, text="EXTENSIÓN CHARLYAUDIT", style="Section.TLabel").pack(anchor="w", pady=(16, 4))
        self.use_extension_var = tk.BooleanVar(value=self.cfg.test.use_extension)
        ttk.Checkbutton(
            body, text="Usar la extensión CharlyAudit en esta prueba (grabación, KPIs, 15 ámbitos)",
            variable=self.use_extension_var,
        ).pack(anchor="w")
        self._hint(
            body,
            "Necesita el Chromium gestionado por Playwright (se ofrece instalar). Nota: en algunos "
            "sistemas, la grabación puede no activarse por una restricción real de Chrome — si eso "
            "pasa, la prueba de Playwright sigue corriendo con normalidad, solo sin esos datos extra.",
        )

        # --- Guardar --------------------------------------------------------
        ttk.Button(body, text="Guardar configuración", style="Brand.TButton", command=self._save).pack(
            anchor="w", pady=(20, 0)
        )
        self.status_label = ttk.Label(self, text="", style="Success.TLabel")
        self.status_label.pack(anchor="w", pady=(8, 0))

    def _hint(self, parent: tk.Widget, text: str) -> None:
        tk.Label(parent, text=text, bg=parent["bg"], fg=MUTED, font=("Segoe UI", 8), wraplength=560, justify="left").pack(
            anchor="w", pady=(3, 0)
        )

    def _browse_spec(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecciona el script de Playwright",
            filetypes=[("Playwright spec", "*.spec.ts *.spec.js"), ("Todos los archivos", "*.*")],
        )
        if path:
            self.spec_var.set(path)

    def _add_header_row(self, name: str = "", value: str = "") -> None:
        row = tk.Frame(self.headers_container, bg=self.headers_container["bg"])
        row.pack(fill="x", pady=2)
        name_var = tk.StringVar(value=name)
        value_var = tk.StringVar(value=value)
        ttk.Entry(row, textvariable=name_var, width=22).pack(side="left")
        ttk.Label(row, text=":", style="Muted.TLabel").pack(side="left", padx=4)
        ttk.Entry(row, textvariable=value_var, width=32).pack(side="left")
        remove_btn = ttk.Button(row, text="✕", style="Ghost.TButton", width=3)
        remove_btn.pack(side="left", padx=(8, 0))
        entry_tuple = (name_var, value_var, row)
        self._headers_rows.append(entry_tuple)
        remove_btn.configure(command=lambda: self._remove_header_row(entry_tuple))

    def _remove_header_row(self, entry_tuple) -> None:
        _, _, row = entry_tuple
        row.destroy()
        self._headers_rows.remove(entry_tuple)

    def _collect_headers(self) -> dict[str, str]:
        headers = {}
        for name_var, value_var, _ in self._headers_rows:
            name = name_var.get().strip()
            if name:
                headers[name] = value_var.get()
        return headers

    def _save(self) -> None:
        self.cfg.test.spec_path = self.spec_var.get().strip() or None
        self.cfg.test.url = self.url_var.get().strip() or None
        self.cfg.test.headers = self._collect_headers()
        self.cfg.test.use_extension = self.use_extension_var.get()
        save_config(self.cfg)
        self.status_label.configure(text="✓ Configuración guardada.")
        self.after(2500, lambda: self.status_label.configure(text=""))
        if self.on_change:
            self.on_change()

    def refresh(self) -> None:
        """Refleja cambios hechos desde otra vista/fuente — consistente con
        el resto de las vistas de configuración (v0.0.6)."""
        self.spec_var.set(self.cfg.test.spec_path or "")
        self.url_var.set(self.cfg.test.url or "")
        self.use_extension_var.set(self.cfg.test.use_extension)
