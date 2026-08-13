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
    _CONFIGURED_LABEL = "Prueba configurada (pestaña 'Configurar prueba')"

    def __init__(self, parent: tk.Widget, cfg: AppConfig, bridge: AsyncBridge, on_report_ready=None) -> None:
        super().__init__(parent, padding=20)
        self.cfg = cfg
        self.bridge = bridge
        self.on_report_ready = on_report_ready
        self._queue: "queue.Queue[ProgressMessage]" = queue.Queue()
        self._running = False
        self._sequence_queue: list = []
        self._sequence_total = 0
        self._sequence_run_index = 0
        self._build()

    def _build(self) -> None:
        def _run_button(parent):
            self.run_button = ttk.Button(parent, text="▶ Correr prueba", style="Brand.TButton", command=self._on_run_button)
            return self.run_button

        Header(
            self,
            "Ejecutar corrida",
            "Lanza el navegador, corre el spec de Playwright tal cual está escrito, "
            "y genera un reporte con el resultado — el mismo flujo que la CLI.",
            actions=[_run_button],
        ).pack(fill="x", pady=(0, 12))

        source_row = ttk.Frame(self)
        source_row.pack(fill="x", pady=(0, 10))
        ttk.Label(source_row, text="Prueba a correr:", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        self.source_var = tk.StringVar(value=self._CONFIGURED_LABEL)
        self.source_combo = ttk.Combobox(source_row, textvariable=self.source_var, state="readonly", width=40)
        self.source_combo.pack(side="left")
        self._refresh_source_options()

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

    def _refresh_source_options(self) -> None:
        """Punto 3 del pedido original: repuebla las opciones del selector
        con la prueba configurada + cada prueba guardada del Catálogo —
        se llama al construir la vista y cada vez que el Catálogo cambia
        (ver app._on_config_changed)."""
        names = [self._CONFIGURED_LABEL] + [tc.name for tc in self.cfg.test_catalog]
        self.source_combo["values"] = names
        if self.source_var.get() not in names:
            self.source_var.set(self._CONFIGURED_LABEL)

    def _resolve_selected_source(self):
        """Devuelve `None` (prueba configurada) o el `TestCase` del Catálogo
        que coincide con la selección actual del combo."""
        selected = self.source_var.get()
        if selected == self._CONFIGURED_LABEL:
            return None
        return next((tc for tc in self.cfg.test_catalog if tc.name == selected), None)

    def _on_run_button(self) -> None:
        self._start_run(self._resolve_selected_source())

    def refresh(self) -> None:
        self._refresh_source_options()

    def _append_log(self, text: str, tag: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _start_run(self, test_case=None) -> None:
        """`test_case`: si viene de "Correr" en el Catálogo, es un
        `config.TestCase` — se usan su spec/url/cabeceras para ESTA
        corrida (sin pisar permanentemente la config de "Configurar
        prueba"), y su nombre queda registrado en el historial/Dashboard."""
        if self._running:
            return

        if test_case is not None:
            run_spec_path, run_url, run_headers = test_case.spec_path, test_case.url, test_case.headers
            run_test_name = test_case.name
        else:
            run_spec_path, run_url, run_headers = self.cfg.test.spec_path, self.cfg.test.url, self.cfg.test.headers
            run_test_name = None

        if not run_spec_path or not run_url:
            self._append_log("⚠ Configura primero el script y la URL en 'Configurar prueba'.", "warning")
            return

        self._running = True
        self.run_button.configure(state="disabled")
        self.status_label.configure(text="● Corriendo…", style="Warning.TLabel")
        self.progress.pack(fill="x", pady=(0, 8), before=self.log_text.master)
        self.progress.start(12)
        if self._sequence_total == 0:
            self._sequence_run_index = 0  # corrida suelta (no secuencia): nunca cuenta como "continuacion"
        self._sequence_run_index += 1
        if self._sequence_run_index <= 1:
            # Bug real corregido: borrar el log aca incondicionalmente
            # tambien borraba el historial de corridas ANTERIORES de una
            # secuencia ("Ejecutar todas" del Catalogo) cada vez que
            # arrancaba la siguiente — confirmado con una prueba real
            # (solo quedaba visible la ultima prueba de la secuencia). Solo
            # se limpia en la primera corrida (de una secuencia, o una
            # corrida suelta) — las siguientes de la misma secuencia
            # conservan el historial completo.
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        if self._sequence_total > 0:
            display_name = run_test_name or "(sin nombre)"
            self._append_log(
                f"\n▶ Secuencia: prueba {self._sequence_run_index}/{self._sequence_total} — {display_name}", "section"
            )

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

        # Corrida desde el catalogo: se usan spec/url/cabeceras del TestCase
        # SIN tocar self.cfg.test (la config de "Configurar prueba" no debe
        # cambiar solo porque se corrio algo distinto desde el catalogo).
        run_cfg = self.cfg
        if test_case is not None:
            import copy

            run_cfg = copy.deepcopy(self.cfg)
            run_cfg.test.spec_path = run_spec_path
            run_cfg.test.url = run_url
            run_cfg.test.headers = run_headers

        self.bridge.submit(
            run_audit(
                run_cfg,
                reporter=reporter,
                ask_save_path=_ask_save_path,
                confirm_install=confirm_install,
                test_name=run_test_name,
            ),
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
            if isinstance(exc, CharlyWebAuditError):
                text = exc.message
                if exc.hint:
                    text += f"\n{exc.hint}"
            else:
                text = str(exc)
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
            self._advance_sequence()  # un fallo en una prueba de la secuencia no detiene al resto
            return
        self.status_label.configure(text="● Corrida completa", style="Success.TLabel")
        self._append_log("\n✓ Corrida completa.", "success")
        if result is not None and self.on_report_ready:
            self.on_report_ready(result)
        self._advance_sequence()

    def run_sequence(self, test_cases: list) -> None:
        """Punto 2 del pedido v0.1.5: "Ejecutar todas" del Catálogo — corre
        cada prueba una por una, en orden, esperando a que cada una termine
        (con o sin error) antes de empezar la siguiente. Reusa el mismo
        camino de una corrida individual (`_start_run`), sin duplicar
        nada."""
        if self._running or not test_cases:
            return
        self._sequence_queue = list(test_cases)
        self._sequence_total = len(test_cases)
        self._advance_sequence()

    def _advance_sequence(self) -> None:
        if not self._sequence_queue:
            self._sequence_total = 0  # secuencia terminada — una corrida suelta despues debe limpiar el log normalmente
            return
        next_case = self._sequence_queue.pop(0)
        self._start_run(next_case)
