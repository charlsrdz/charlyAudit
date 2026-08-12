import tkinter as tk
from tkinter import ttk
from ...config import AppConfig, save_config, TestCase
from ..widgets import BrandHeader, Card

class CatalogView(ttk.Frame):
    def __init__(self, parent: tk.Widget, cfg: AppConfig, on_change=None) -> None:
        super().__init__(parent, padding=20)
        self.cfg = cfg
        self.on_change = on_change
        self._build()

    def _build(self) -> None:
        BrandHeader(self, subtitle="CATÁLOGO DE PRUEBAS").pack(anchor="w", pady=(0, 20))
        
        self.list_frame = ttk.Frame(self)
        self.list_frame.pack(fill="both", expand=True)
        
        self.refresh()
        
        ttk.Button(self, text="➕ Agregar Prueba", command=self._add_test).pack(pady=10)

    def refresh(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        
        for tc in self.cfg.test.test_cases:
            frame = ttk.Frame(self.list_frame)
            frame.pack(fill="x", pady=5)
            ttk.Label(frame, text=tc.name).pack(side="left")
            ttk.Button(frame, text="🗑 Eliminar", command=lambda t=tc: self._remove_test(t)).pack(side="right")

    def _add_test(self) -> None:
        from .test_case_form import TestCaseForm
        
        def on_save(tc):
            if tc:
                self.cfg.test.test_cases.append(tc)
                from ...config import save_config
                save_config(self.cfg)
                self.refresh()
                if self.on_change:
                    self.on_change()
                    
        TestCaseForm(self, on_save)


    def _remove_test(self, tc: TestCase) -> None:
        self.cfg.test.test_cases.remove(tc)
        from ...config import save_config
        save_config(self.cfg)
        self.refresh()
        if self.on_change:
            self.on_change()
