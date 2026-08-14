"""
gui/views/assistant_config_view.py — Formulario de configuración del
Asistente IA: proveedor, modelo, API key.

Esta configuración alimenta el análisis por IA directo de los resultados
de Playwright (`ai_playwright.py`) — el reporte de cada corrida incluye
qué funcionó, qué no, y recomendaciones para mejorar el script, siempre
que haya credenciales configuradas.

v0.0.6: envuelto en Card (consistencia visual con el resto de la GUI) y se
agregó refresh() — antes de esto, si la configuración cambiaba desde otra
vista o desde un archivo config.json externo, el estado "Configurado/Sin
configurar" que se ve aquí podía quedar desactualizado sin ninguna forma
de refrescarlo salvo reabrir la app (HomeView sí tenía este mecanismo
desde v0.0.5, esta vista se quedó sin él por descuido).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...config import AppConfig, save_config
from ..widgets import Card, Header

_PROVIDERS = {
    "gemini": "Google Gemini",
    "openai": "OpenAI / ChatGPT",
    "claude": "Anthropic Claude",
    "openwebui": "Open WebUI (propio)",
    "custom": "Proveedor personalizado",
}


class AssistantConfigView(ttk.Frame):
    def __init__(self, parent: tk.Widget, cfg: AppConfig, on_change=None) -> None:
        super().__init__(parent, padding=20)
        self.cfg = cfg
        self.on_change = on_change
        self._build()

    def _build(self) -> None:
        Header(self, "Asistente IA", "Se configura una sola vez — queda guardado para las próximas corridas.").pack(
            fill="x", pady=(0, 16)
        )

        card = Card(self, title="CONFIGURACIÓN DEL PROVEEDOR")
        card.pack(fill="x")
        body = card.body

        ttk.Label(body, text="PROVEEDOR", style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        self.provider_var = tk.StringVar(value=self.cfg.assistant.provider)
        provider_combo = ttk.Combobox(
            body,
            textvariable=self.provider_var,
            values=list(_PROVIDERS.keys()),
            state="readonly",
            width=30,
        )
        provider_combo.pack(anchor="w")
        self._provider_label = tk.Label(
            body, text=_PROVIDERS.get(self.cfg.assistant.provider, ""), bg=body["bg"], fg="#8a90b5", font=("Segoe UI", 9)
        )
        self._provider_label.pack(anchor="w", pady=(2, 0))
        provider_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self._provider_label.configure(text=_PROVIDERS.get(self.provider_var.get(), "")),
        )

        ttk.Label(body, text="MODELO", style="Section.TLabel").pack(anchor="w", pady=(16, 4))
        self.model_var = tk.StringVar(value=self.cfg.assistant.model)
        ttk.Entry(body, textvariable=self.model_var, width=40).pack(anchor="w")

        ttk.Label(body, text="API KEY", style="Section.TLabel").pack(anchor="w", pady=(16, 4))
        key_row = ttk.Frame(body)
        key_row.pack(anchor="w")
        self.key_var = tk.StringVar(value=self.cfg.assistant.api_key)
        self.key_entry = ttk.Entry(key_row, textvariable=self.key_var, width=45, show="•")
        self.key_entry.pack(side="left")
        self._show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            key_row, text="mostrar", variable=self._show_key, command=self._toggle_key_visibility
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            body,
            text="Se guarda solo en este equipo (config.json local), nunca se envía a ningún lado\n"
            "más que al proveedor elegido.",
            bg=body["bg"],
            fg="#8a90b5",
            font=("Segoe UI", 9),
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        status_row = ttk.Frame(body)
        status_row.pack(anchor="w", pady=(20, 0))
        self.state_label = tk.Label(status_row, bg=body["bg"], font=("Segoe UI", 10))
        self.state_label.pack(side="left")
        self._render_state()

        ttk.Button(body, text="Guardar", style="Brand.TButton", command=self._save).pack(anchor="w", pady=(12, 0))
        self.status_label = ttk.Label(self, text="", style="Success.TLabel")
        self.status_label.pack(anchor="w", pady=(8, 0))

    def _render_state(self) -> None:
        configured = self.cfg.assistant.configured
        self.state_label.configure(
            text=("✓ Configurado" if configured else "Sin configurar todavía"),
            fg=("#34d399" if configured else "#f5b544"),
        )

    def _toggle_key_visibility(self) -> None:
        self.key_entry.configure(show="" if self._show_key.get() else "•")

    def _save(self) -> None:
        self.cfg.assistant.provider = self.provider_var.get()
        self.cfg.assistant.model = self.model_var.get().strip()
        if self.key_var.get().strip():
            self.cfg.assistant.api_key = self.key_var.get().strip()
        self.cfg.assistant.configured = True
        save_config(self.cfg)
        self._render_state()
        self.status_label.configure(text="✓ Configuración guardada.")
        self.after(2500, lambda: self.status_label.configure(text=""))
        if self.on_change:
            self.on_change()

    def refresh(self) -> None:
        """Refleja cambios hechos desde otra vista/fuente sin reabrir la
        app — bug de consistencia corregido en v0.0.6 (ver docstring del
        módulo)."""
        self.provider_var.set(self.cfg.assistant.provider)
        self._provider_label.configure(text=_PROVIDERS.get(self.cfg.assistant.provider, ""))
        self.model_var.set(self.cfg.assistant.model)
        self.key_var.set(self.cfg.assistant.api_key)
        self._render_state()
