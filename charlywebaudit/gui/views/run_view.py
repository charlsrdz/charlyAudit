"""
gui/views/run_view.py — Ejecuta la corrida completa (`run_audit`, el mismo
motor que usa la CLI) y muestra el progreso en vivo.

Sondea la cola del `QueueReporter` con `root.after(...)` — nunca toca sus
propios widgets desde el hilo en segundo plano del `AsyncBridge`, sigue el
mismo patrón validado en async_bridge.py.

v0.0.6: la barra de progreso quedaba visible en reposo mostrando un
pequeño segmento naranja fijo (comportamiento normal de un
`Progressbar` indeterminado sin iniciar, pero confuso — parecía una
corrida a medias sin haber arrancado nada). Ahora solo aparece mientras
hay una corrida activa.
"""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk

from ...config import AppConfig
from ...errors import CharlyWebAuditError
from ..async_bridge import AsyncBridge, ProgressMessage, QueueReporter
from ..theme import BORDER, DANGER, MUTED, SUCCESS, SURFACE, TEXT, WARNING
from ..widgets import Header

_PLACEHOLDER = "El registro de la corrida aparecerá aquí una vez que le des a \"Correr prueba\"."


class RunView(ttk.Frame):
    def __init__(self, parent: tk.Widget, cfg: AppConfig, bridge: AsyncBridge, on_report_ready=None) -> None:
        super().__init__(parent, padding=20)
        self.cfg = cfg
        self.bridge = bridge
        self.on_report_ready = on_report_ready
        self._queue: "queue.Queue[ProgressMessage]" = queue.Queue()
        self._running = False
        self._build()

    def _build(self) -> None:
        def _run_button(parent):
            self.run_button = ttk.Button(parent, text="▶ Correr prueba", style="Brand.TButton", command=self._start_run)
            return self.run_button

        Header(
            self,
            "Ejecutar corrida",
            "Lanza el navegador con CharlyAudit, corre el spec de Playwright, y pide el "
            "análisis de los 15 ámbitos del Asistente — el mismo flujo que la CLI.",
            actions=[_run_button],
        ).pack(fill="x", pady=(0, 12))

        status_row = ttk.Frame(self)
        status_row.pack(fill="x", pady=(0, 4))
        self.status_label = ttk.Label(status_row, text="● Listo para correr", style="Muted.TLabel")
        self.status_label.pack(side="left")

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        # A proposito NO se empaqueta aca — solo aparece mientras corre algo
        # (ver _start_run/_finish_run). Bug real corregido en v0.0.6: antes
        # quedaba siempre visible, mostrando un segmento fijo en reposo que
        # parecia una corrida estancada.

        log_frame = tk.Frame(self, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text = tk.Text(
            log_frame,
            bg=SURFACE,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            wrap="word",
            padx=12,
            pady=10,
            font=("Consolas", 9),
            state="disabled",
        )
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.log_text.tag_configure("section", foreground="#b87619", font=("Consolas", 9, "bold"))
        self.log_text.tag_configure("info", foreground=MUTED)
        self.log_text.tag_configure("success", foreground=SUCCESS)
        self.log_text.tag_configure("warning", foreground=WARNING)
        self.log_text.tag_configure("error", foreground=DANGER)
        self.log_text.tag_configure("raw", foreground=TEXT)
        self.log_text.tag_configure("placeholder", foreground=MUTED, font=("Consolas", 9, "italic"))

        self._show_placeholder()

    def _show_placeholder(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", _PLACEHOLDER, "placeholder")
        self.log_text.configure(state="disabled")

    def _append_log(self, text: str, tag: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _start_run(self) -> None:
        if self._running:
            return
        if not self.cfg.test.spec_path or not self.cfg.test.url:
            self._append_log("⚠ Configura primero el script y la URL en 'Configurar prueba'.", "warning")
            return
        if not self.cfg.assistant.configured:
            self._append_log("⚠ Configura primero el Asistente IA.", "warning")
            return

        self._running = True
        self.run_button.configure(state="disabled")
        self.status_label.configure(text="● Corriendo…", style="Warning.TLabel")
        self.progress.pack(fill="x", pady=(0, 8), before=self.log_text.master)
        self.progress.start(12)
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

        reporter = QueueReporter(self._queue)

        async def _ask_save_path(default_name: str):
            # La GUI no pregunta por consola — devuelve None para NO guardar
            # automaticamente; el usuario guarda desde la vista de Reporte
            # (con su propio dialogo nativo) una vez que ve el resultado.
            return None

        from ...__main__ import run_audit  # import diferido: evita ciclos (run_audit importa desde gui indirectamente en algunos flujos)
        from ..dialogs import make_thread_safe_confirm

        # Bug real corregido en v0.0.8: sin esto, ensure_chromium() usaba su
        # confirmacion por defecto (questionary, pensada para terminal) —
        # incompatible con el hilo en segundo plano de AsyncBridge, donde
        # esta corrida realmente se ejecuta. Ver gui/dialogs.py.
        confirm_install = make_thread_safe_confirm(self.winfo_toplevel())

        self.bridge.submit(
            run_audit(self.cfg, reporter=reporter, ask_save_path=_ask_save_path, confirm_install=confirm_install),
            on_done=self._on_run_done,
        )
        self._poll_queue()

    def _on_run_done(self, result, exc) -> None:
        # Se llama desde el hilo en segundo plano — solo encola, nunca toca
        # widgets directamente aqui (mismo patron que QueueReporter).
        self._queue.put(ProgressMessage("__done__", (result, exc)))

    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                if msg.kind == "__done__":
                    result, exc = msg.payload
                    self._finish_run(result, exc)
                    return
                self._handle_message(msg)
        except queue.Empty:
            pass
        if self._running:
            self.after(80, self._poll_queue)

    def _handle_message(self, msg: ProgressMessage) -> None:
        if msg.kind == "section":
            self._append_log(f"── {msg.payload} ──", "section")
        elif msg.kind == "info":
            self._append_log(f"› {msg.payload}", "info")
        elif msg.kind == "success":
            self._append_log(f"✓ {msg.payload}", "success")
        elif msg.kind == "warning":
            self._append_log(f"⚠ {msg.payload}", "warning")
        elif msg.kind == "error":
            exc = msg.payload
            text = exc.message if isinstance(exc, CharlyWebAuditError) else str(exc)
            self._append_log(f"✕ {text}", "error")
        elif msg.kind == "raw":
            self._append_log(str(msg.payload), "raw")

    def _finish_run(self, result, exc) -> None:
        self._running = False
        self.run_button.configure(state="normal")
        self.progress.stop()
        self.progress.pack_forget()
        if exc is not None:
            text = exc.message if isinstance(exc, CharlyWebAuditError) else str(exc)
            self.status_label.configure(text="● Terminó con error", style="Danger.TLabel")
            self._append_log(f"\n✕ La corrida terminó con un error: {text}", "error")
            return
        self.status_label.configure(text="● Corrida completa", style="Success.TLabel")
        self._append_log("\n✓ Corrida completa.", "success")
        if result is not None and self.on_report_ready:
            self.on_report_ready(result)
