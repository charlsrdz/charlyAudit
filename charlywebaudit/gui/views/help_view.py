"""
gui/views/help_view.py — Sección Ayuda (v0.0.6): información del autor,
qué hace la herramienta, y dónde encontrar más detalle.
"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk

from ...constants import (
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    AUTHOR_DESCRIPTION,
    AUTHOR_NAME,
)
from ..widgets import BrandHeader, Card, Header, ScrollableFrame


class HelpView(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent, padding=20)
        self._build()

    def _build(self) -> None:
        Header(self, "Ayuda").pack(fill="x", pady=(0, 16))

        # Contenedor con scroll: bug real de usabilidad corregido en
        # v0.0.6 — al tamaño mínimo real de la ventana (820x600), la
        # tarjeta "Requisitos" quedaba completamente fuera de vista, sin
        # ninguna forma de llegar a ella.
        scrollable = ScrollableFrame(self)
        scrollable.pack(fill="both", expand=True)
        body_root = scrollable.body

        body = ttk.Frame(body_root)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        # --- Columna izquierda: qué es, cómo funciona ---------------------
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        about_card = Card(left, title="QUÉ ES CHARLYWEBAUDIT")
        about_card.pack(fill="x")
        ttk.Label(
            about_card.body,
            text=f"{APP_TAGLINE}.\n\n"
            "El script corre tal cual está escrito, sin ninguna modificación. Si configurás el "
            "Asistente IA, el reporte incluye un análisis de qué funcionó, qué no, y cómo mejorar "
            "el script.",
            style="Surface.TLabel",
            justify="left",
            wraplength=380,
        ).pack(anchor="w")

        steps_card = Card(left, title="FLUJO DE UNA CORRIDA")
        steps_card.pack(fill="x", pady=(12, 0))
        for i, step in enumerate(
            [
                "Configurar prueba: script .spec.ts, URL, cabeceras opcionales.",
                "Asistente IA (opcional, pero recomendado): proveedor, modelo\n   y API key, para el análisis de resultados.",
                "Ejecutar: elegí si correr la prueba configurada o una guardada\n   en el Catálogo, y lanzá la corrida.",
                "Reporte: resultado de Playwright + análisis por IA + estado\n   del navegador durante la corrida.",
            ],
            start=1,
        ):
            ttk.Label(
                steps_card.body, text=f"{i}. {step}", style="Surface.TLabel", justify="left", wraplength=380
            ).pack(anchor="w", pady=(0, 6))

        req_card = Card(left, title="REQUISITOS")
        req_card.pack(fill="x", pady=(12, 0))
        for req in [
            "Node.js + npm (para correr specs de @playwright/test)",
            "Google Chrome (canal estable) — hay que instalarlo vos, la app no lo hace por su cuenta",
            "Asistente IA (opcional): API key de Google Gemini u OpenAI, para el análisis de resultados",
        ]:
            ttk.Label(req_card.body, text=f"• {req}", style="Surface.TLabel", justify="left", wraplength=380).pack(
                anchor="w", pady=(0, 4)
            )

        # --- Columna derecha: autor, version, enlaces -----------------------
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")

        author_card = Card(right, title="AUTOR")
        author_card.pack(fill="x")
        BrandHeader(author_card.body).pack(anchor="w", pady=(0, 10))
        ttk.Label(author_card.body, text=AUTHOR_NAME, style="Surface.TLabel", font=("Segoe UI", 11, "bold")).pack(
            anchor="w"
        )
        ttk.Label(
            author_card.body, text=AUTHOR_DESCRIPTION, style="SurfaceMuted.TLabel", justify="left", wraplength=380
        ).pack(anchor="w", pady=(6, 0))

        version_card = Card(right, title="VERSIÓN")
        version_card.pack(fill="x", pady=(12, 0))
        ttk.Label(version_card.body, text=f"{APP_NAME} v{APP_VERSION}", style="Surface.TLabel").pack(anchor="w")

        links_card = Card(right, title="MÁS INFORMACIÓN")
        links_card.pack(fill="x", pady=(12, 0))
        self._link(links_card.body, "Documentación (README del proyecto)", "readme")
        self._link(links_card.body, "nodejs.org — instalar Node.js", "https://nodejs.org")

        contact_card = Card(right, title="CONTACTO")
        contact_card.pack(fill="x", pady=(12, 0))
        self._link(contact_card.body, "zona-tech.com", "https://zona-tech.com")
        self._link(contact_card.body, "hola@zona-tech.com", "mailto:hola@zona-tech.com")
        self._link(contact_card.body, "WhatsApp: +52 22 13 63 61 92", "https://wa.me/message/6K7O6JAJ6W6HC1")

    def _link(self, parent: tk.Widget, text: str, target: str) -> None:
        label = ttk.Label(parent, text=text, style="Link.TLabel", cursor="hand2")
        label.pack(anchor="w", pady=(0, 6))
        if target.startswith("http") or target.startswith("mailto:"):
            label.bind("<Button-1>", lambda e: webbrowser.open(target))
        # target == "readme": el README vive junto al codigo fuente, no como
        # una URL — se deja como texto informativo, sin acción de clic (no
        # tiene sentido fingir un enlace que no lleva a ningún lado en un
        # binario empaquetado, donde el README no viaja incluido).
