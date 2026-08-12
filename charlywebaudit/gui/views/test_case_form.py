"""
gui/views/test_case_form.py — Formulario nativo Tkinter para crear/editar
casos de prueba en la GUI, evitando depender de 'questionary' (terminal).
"""
import tkinter as tk
from tkinter import ttk, filedialog
from ...config import TestCase

class TestCaseForm(tk.Toplevel):
    def __init__(self, parent: tk.Widget, callback) -> None:
        super().__init__(parent)
        self.title("Nueva Prueba")
        self.geometry("400x300")
        self.callback = callback
        self.result = None
        
        # Estilos y layout
        self.columnconfigure(1, weight=1)
        
        ttk.Label(self, text="Nombre:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.name_entry = ttk.Entry(self)
        self.name_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        ttk.Label(self, text="Script (.spec.ts):").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.spec_entry = ttk.Entry(self)
        self.spec_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        ttk.Button(self, text="Buscar", command=self._browse_file).grid(row=1, column=2, padx=5)
        
        ttk.Label(self, text="URL:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.url_entry = ttk.Entry(self)
        self.url_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        
        # Validación básica para asegurar campos requeridos
        self.save_btn = ttk.Button(self, text="Guardar", command=self._save)
        self.save_btn.grid(row=3, column=0, columnspan=3, pady=20)

    def _browse_file(self) -> None:
        filename = filedialog.askopenfilename(filetypes=[("TypeScript/JS files", "*.ts *.js")])
        if filename:
            self.spec_entry.delete(0, tk.END)
            self.spec_entry.insert(0, filename)

    def _save(self) -> None:
        name = self.name_entry.get().strip()
        spec = self.spec_entry.get().strip()
        url = self.url_entry.get().strip()
        
        if not name or not spec or not url:
            tk.messagebox.showerror("Error", "Todos los campos son obligatorios")
            return
            
        self.result = TestCase(name=name, spec_path=spec, url=url)
        self.callback(self.result)
        self.destroy()
