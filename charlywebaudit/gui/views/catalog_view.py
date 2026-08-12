"""
gui/views/catalog_view.py — Catálogo de pruebas guardadas (punto 5 del
pedido v0.1.0a): un lugar para definir pruebas con nombre, correrlas
repetidamente, y que cada corrida quede en el historial para comparar en
el Dashboard.
"""

from __future__ import annotations

import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk

from ...config import AppConfig, TestCase, save_config
from ..theme import BORDER, DANGER, MUTED, SUCCESS, SURFACE, SURFACE_2, TEXT
from ..widgets import Card, EmptyState, Header, ScrollableFrame


class CatalogView(ttk.Frame):
    def __init__(self, parent: tk.Widget, cfg: AppConfig, bridge, on_run_test=None) -> None:
        super().__init__(parent, padding=20)
        self.cfg = cfg
        self.bridge = bridge
        self.on_run_test = on_run_test
        self._editing_id: str | None = None
        self._build()

    def _build(self) -> None:
        def _new_button(parent):
            return ttk.Button(parent, text="+ Nueva prueba", style="Brand.TButton", command=self._open_form_new)

        Header(
            self,
            "Catálogo de pruebas",
            "Pruebas guardadas con nombre — corrélas cuando quieras y compará resultados en el Dashboard.",
            actions=[_new_button],
        ).pack(fill="x", pady=(0, 16))

        self._scrollable = ScrollableFrame(self)
        self._scrollable.pack(fill="both", expand=True)
        self._list_container = ttk.Frame(self._scrollable.body)
        self._list_container.pack(fill="both", expand=True)

        self._form_container = tk.Frame(self, bg=SURFACE)  # se muestra/oculta segun haga falta

        self.refresh()

    def refresh(self) -> None:
        for child in self._list_container.winfo_children():
            child.destroy()

        if not self.cfg.test_catalog:
            EmptyState(
                self._list_container,
                "Todavía no hay pruebas guardadas en el catálogo.\nUsá \"+ Nueva prueba\" para agregar la primera.",
            ).pack(anchor="w", fill="x")
            return

        for tc in self.cfg.test_catalog:
            self._render_test_case_row(tc)

    def _render_test_case_row(self, tc: TestCase) -> None:
        card = Card(self._list_container)
        card.pack(fill="x", pady=(0, 10))
        body = card.body

        top = tk.Frame(body, bg=SURFACE)
        top.pack(fill="x")
        tk.Label(top, text=tc.name, bg=SURFACE, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(side="left")

        btns = tk.Frame(top, bg=SURFACE)
        btns.pack(side="right")
        ttk.Button(btns, text="▶ Correr", style="Brand.TButton", command=lambda: self._run(tc)).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Editar", style="Ghost.TButton", command=lambda: self._open_form_edit(tc)).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Eliminar", style="Ghost.TButton", command=lambda: self._delete(tc)).pack(side="left")

        tk.Label(body, text=f"URL: {tc.url}", bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
        tk.Label(body, text=f"Script: {tc.spec_path}", bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        if tc.headers:
            tk.Label(
                body, text=f"Cabeceras: {', '.join(tc.headers.keys())}", bg=SURFACE, fg=MUTED, font=("Segoe UI", 9)
            ).pack(anchor="w")

    def _run(self, tc: TestCase) -> None:
        if self.on_run_test:
            self.on_run_test(tc)

    def _delete(self, tc: TestCase) -> None:
        if not messagebox.askyesno("Eliminar prueba", f"¿Eliminar \"{tc.name}\" del catálogo?\n(el historial de corridas pasadas se conserva)"):
            return
        self.cfg.test_catalog = [t for t in self.cfg.test_catalog if t.id != tc.id]
        save_config(self.cfg)
        self.refresh()

    def _open_form_new(self) -> None:
        self._open_form(None)

    def _open_form_edit(self, tc: TestCase) -> None:
        self._open_form(tc)

    def _open_form(self, existing: TestCase | None) -> None:
        for child in self._form_container.winfo_children():
            child.destroy()
        self._form_container.pack(fill="x", pady=(0, 16), before=self._scrollable)

        title = "Editar prueba" if existing else "Nueva prueba"
        body = tk.Frame(self._form_container, bg=SURFACE, padx=16, pady=14)
        body.pack(fill="x")
        tk.Label(body, text=title, bg=SURFACE, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 10))

        name_var = tk.StringVar(value=existing.name if existing else "")
        spec_var = tk.StringVar(value=existing.spec_path if existing else "")
        url_var = tk.StringVar(value=existing.url if existing else "")

        tk.Label(body, text="NOMBRE", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        ttk.Entry(body, textvariable=name_var, width=50).pack(anchor="w", pady=(2, 8))

        tk.Label(body, text="SCRIPT PLAYWRIGHT (.spec.ts)", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        spec_row = tk.Frame(body, bg=SURFACE)
        spec_row.pack(fill="x", pady=(2, 8))
        ttk.Entry(spec_row, textvariable=spec_var, width=44).pack(side="left")
        ttk.Button(
            spec_row, text="Examinar…", style="Ghost.TButton",
            command=lambda: spec_var.set(
                filedialog.askopenfilename(filetypes=[("Playwright spec", "*.spec.ts *.spec.js")]) or spec_var.get()
            ),
        ).pack(side="left", padx=(8, 0))

        tk.Label(body, text="URL", bg=SURFACE, fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        ttk.Entry(body, textvariable=url_var, width=50).pack(anchor="w", pady=(2, 12))

        def _save() -> None:
            if not name_var.get().strip() or not spec_var.get().strip() or not url_var.get().strip():
                messagebox.showwarning("Faltan datos", "Nombre, script y URL son obligatorios.")
                return
            if existing:
                existing.name = name_var.get().strip()
                existing.spec_path = spec_var.get().strip()
                existing.url = url_var.get().strip()
            else:
                self.cfg.test_catalog.append(
                    TestCase(id=uuid.uuid4().hex[:12], name=name_var.get().strip(), spec_path=spec_var.get().strip(), url=url_var.get().strip())
                )
            save_config(self.cfg)
            self._form_container.pack_forget()
            self.refresh()

        actions = tk.Frame(body, bg=SURFACE)
        actions.pack(anchor="w")
        ttk.Button(actions, text="Guardar", style="Brand.TButton", command=_save).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Cancelar", style="Ghost.TButton", command=self._form_container.pack_forget).pack(side="left")
