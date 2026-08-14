"""
gui/widgets.py — Componentes reutilizables de la GUI (v0.0.6).

Antes de este archivo, cada vista (`views/*.py`) repetía a mano el mismo
patrón de "título grande + subtítulo apagado" y no existía ninguna tarjeta
visual consistente para agrupar contenido — de ahí el espacio vacío y la
sensación de pantallas a medio terminar que encontró la auditoría visual
de v0.0.6. Consolidar estos patrones aquí significa que un cambio de
diseño (por ejemplo, el espaciado de un encabezado) se hace una vez, no en
cada archivo de vista por separado.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import BG, BORDER, BRAND, FONT_SECTION, FONT_SUBTITLE, FONT_TITLE, MUTED, SURFACE, TEXT, load_icon_images


class Header(ttk.Frame):
    """Título + subtítulo + acciones opcionales alineadas a la derecha —
    el mismo patrón que las cinco vistas repetían con pequeñas
    variaciones. `actions` es una lista de widgets ya construidos (botones)
    que se empacan a la derecha, en el orden dado.

    Bug real corregido en v0.0.6: el subtítulo se cortaba sin ajustar línea
    cuando el texto era largo (una regresión al consolidar este patrón —
    la versión original de cada vista tenía un salto de línea manual con
    `\\n` que se perdía al unificar el texto en una sola cadena). Ahora
    envuelve automáticamente con un ancho razonable para la ventana
    mínima (820px) menos el espacio de los botones de acción."""

    def __init__(
        self, parent: tk.Widget, title: str, subtitle: str | None = None, actions=None, subtitle_wraplength: int = 620
    ) -> None:
        super().__init__(parent)
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text=title, style="Title.TLabel").pack(side="left")
        if actions:
            action_row = ttk.Frame(top)
            action_row.pack(side="right")
            for widget_factory in actions:
                widget_factory(action_row).pack(side="left", padx=(8, 0))
        if subtitle:
            ttk.Label(
                self, text=subtitle, style="Subtitle.TLabel", wraplength=subtitle_wraplength, justify="left"
            ).pack(anchor="w", pady=(4, 0))


class Card(tk.Frame):
    """Contenedor con borde y título opcional, mismo lenguaje visual que
    las tarjetas del reporte HTML (`report/templates/report.html.jinja`) —
    para que la GUI y el reporte se sientan parte de la misma familia
    visual, no dos diseños distintos."""

    def __init__(self, parent: tk.Widget, title: str | None = None, **kwargs) -> None:
        super().__init__(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, **kwargs)
        self.body = tk.Frame(self, bg=SURFACE, padx=16, pady=14)
        if title:
            title_label = tk.Label(self.body, text=title, bg=SURFACE, fg=BRAND, font=FONT_SECTION, anchor="w")
            title_label.pack(fill="x", pady=(0, 8))
        self.body.pack(fill="both", expand=True)


class StatusRow(ttk.Frame):
    """Una fila "Etiqueta: valor" con color según si el valor representa un
    estado resuelto (verde) o pendiente (ámbar) — usado en Inicio para el
    resumen de configuración, y reutilizable dondequiera que haga falta un
    patrón similar a futuro (evita reinventar el mismo layout)."""

    def __init__(self, parent: tk.Widget, label: str, value: str, *, ok: bool, width: int = 14) -> None:
        super().__init__(parent)
        ttk.Label(self, text=f"{label}:", style="Muted.TLabel", width=width).pack(side="left")
        ttk.Label(self, text=value, style=("Success.TLabel" if ok else "Warning.TLabel")).pack(side="left")


class EmptyState(ttk.Frame):
    """Estado vacío consistente: ícono/texto centrado con un mensaje claro
    de qué hacer — reemplaza los bloques de texto sueltos que cada vista
    escribía a mano para su caso "todavía no hay nada que mostrar"."""

    def __init__(self, parent: tk.Widget, text: str, action_widget_factory=None) -> None:
        super().__init__(parent, padding=(0, 32))
        ttk.Label(self, text=text, style="Subtitle.TLabel", justify="left").pack(anchor="w")
        if action_widget_factory:
            action_widget_factory(self).pack(anchor="w", pady=(12, 0))


class ScrollableFrame(ttk.Frame):
    """Contenedor con scroll vertical — bug real de usabilidad confirmado
    en la auditoría de v0.0.6: a la altura mínima real de la ventana
    (820x600), el contenido de Ayuda se cortaba por debajo del borde
    inferior sin ninguna forma de llegar a él. Cualquier vista con
    contenido que pueda crecer más que la ventana (Ayuda, o Configurar
    prueba si se agregan muchas cabeceras) debería usar esto en vez de un
    `ttk.Frame` simple.

    Uso: `sf = ScrollableFrame(parent); sf.pack(fill="both", expand=True)`,
    y todo el contenido va dentro de `sf.body` (no de `sf` directamente).
    """

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.body = ttk.Frame(canvas)

        self.body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        # El ancho interno debe seguir al del canvas (no solo su alto) para
        # que el contenido no quede angosto ni recorte texto horizontalmente
        # al redimensionar la ventana.
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Rueda del mouse — Linux usa Button-4/5, Windows/macOS usan
        # <MouseWheel> con el signo de `delta` invertido entre ellos. Se
        # activa/desactiva solo mientras el cursor esta sobre ESTE
        # contenedor (bind_all afectaria el scroll de TODA la aplicacion,
        # incluso con otra pestana activa — un bug real que se detecto y
        # corrigio antes de integrar este widget).
        def _on_wheel(event):
            if event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")
            else:
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        def _bind_wheel(_event=None):
            canvas.bind_all("<Button-4>", _on_wheel)
            canvas.bind_all("<Button-5>", _on_wheel)
            canvas.bind_all("<MouseWheel>", _on_wheel)

        def _unbind_wheel(_event=None):
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)


def logo_image(size: int = 48) -> tk.PhotoImage | None:
    """Devuelve el ícono de la app en el tamaño más cercano disponible
    (16/48/128) — usado para mostrar la marca dentro de la propia ventana
    (Inicio, Ayuda), no solo en la barra de título."""
    images = load_icon_images()
    if not images:
        return None
    # Elige la imagen mas cercana al tamaño pedido en vez de siempre la primera.
    return min(images, key=lambda img: abs(img.width() - size))


class BrandHeader(tk.Frame):
    """Logo + nombre de la app — usado en Inicio y Ayuda para reforzar la
    identidad visual con algo más que texto (antes v0.0.6, ninguna
    pantalla mostraba el ícono real dentro de la propia ventana, solo en
    la barra de título)."""

    def __init__(self, parent: tk.Widget, subtitle: str | None = None) -> None:
        super().__init__(parent, bg=BG)
        from ..constants import APP_NAME

        img = logo_image(48)
        if img:
            self._logo_ref = img  # referencia viva, ver theme.apply_window_icon
            tk.Label(self, image=img, bg=BG).pack(side="left", padx=(0, 12))
        text_col = tk.Frame(self, bg=BG)
        text_col.pack(side="left")
        tk.Label(text_col, text=APP_NAME, bg=BG, fg=TEXT, font=FONT_TITLE).pack(anchor="w")
        if subtitle:
            tk.Label(text_col, text=subtitle, bg=BG, fg=MUTED, font=FONT_SUBTITLE).pack(anchor="w")
