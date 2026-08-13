"""
gui/views/home_view.py — Pantalla de inicio: resumen de configuración,
accesos directos, y una guía rápida para quien recién abre la app.

v0.0.6: rediseñada tras la auditoría visual — la versión anterior dejaba
más de la mitad de la ventana completamente vacía debajo de los accesos
rápidos, sin ningún logo ni contenido que orientara a alguien que abre la
app por primera vez.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...config import AppConfig
from ...constants import APP_TAGLINE, APP_VERSION
from ..widgets import BrandHeader, Card, StatusRow


class HomeView(ttk.Frame):
    def __init__(self, parent: tk.Widget, cfg: AppConfig, on_navigate=None) -> None:
        super().__init__(parent, padding=20)
        self.cfg = cfg
        self.on_navigate = on_navigate
        self._build()

    def _build(self) -> None:
        BrandHeader(self, subtitle=f"v{APP_VERSION} · {APP_TAGLINE}").pack(anchor="w", pady=(0, 20))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        self._status_card = Card(left, title="ESTADO DE LA CONFIGURACIÓN")
        self._status_card.pack(fill="x")
        self._render_status()

        quick_card = Card(left, title="ACCESOS RÁPIDOS")
        quick_card.pack(fill="x", pady=(12, 0))
        quick = ttk.Frame(quick_card.body)
        quick.pack(anchor="w")
        ttk.Button(quick, text="Configurar prueba", style="Ghost.TButton", command=lambda: self._go("test")).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(quick, text="▶ Correr prueba", style="Brand.TButton", command=lambda: self._go("run")).pack(
            side="left"
        )

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")

        tips_card = Card(right, title="CÓMO FUNCIONA")
        tips_card.pack(fill="both", expand=True)
        steps = [
            "1. Configura el script de Playwright y la URL de la prueba.",
            "2. Corré la prueba — lanza el navegador y corre tu spec real,\n    tal cual está escrito.",
            "3. Al terminar, se genera un reporte con el resultado y el\n    estado del navegador durante la corrida.",
        ]
        for step in steps:
            ttk.Label(tips_card.body, text=step, style="Surface.TLabel", justify="left", wraplength=220).pack(
                anchor="w", pady=(0, 10)
            )
        ttk.Button(
            tips_card.body, text="Ver Ayuda completa", style="Ghost.TButton", command=lambda: self._go("help")
        ).pack(anchor="w", pady=(4, 0))

    def _go(self, view_name: str) -> None:
        if self.on_navigate:
            self.on_navigate(view_name)

    def _render_status(self) -> None:
        for child in self._status_card.body.winfo_children():
            if isinstance(child, tk.Label) and child.cget("text") == "ESTADO DE LA CONFIGURACIÓN":
                continue  # conserva el titulo de la tarjeta, solo limpia las filas
            child.destroy()

        rows = [
            ("Script", self.cfg.test.spec_path or "(sin configurar)"),
            ("URL", self.cfg.test.url or "(sin configurar)"),
        ]
        for label, value in rows:
            ok = "sin configurar" not in value
            StatusRow(self._status_card.body, label, value, ok=ok).pack(anchor="w", pady=2)

    def refresh(self) -> None:
        self._render_status()
