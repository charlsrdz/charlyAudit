"""
gui/theme.py — Branding de la GUI (punto 2 del pedido v0.0.5, endurecido en
v0.0.6 tras una auditoría visual real con capturas de pantalla).
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ..constants import APP_NAME, BRAND_COLOR

# Paleta completa — colores oscuros consistentes en toda la GUI.
BRAND = BRAND_COLOR  # "#b87619"
BRAND_LIGHT = "#cc8a24"
BRAND_DIM = "#8a5813"
BG = "#0e1020"
SURFACE = "#171a2e"
SURFACE_2 = "#1f2440"
BORDER = "#2a2f4c"
TEXT = "#e7e9f5"
MUTED = "#8a90b5"
SUCCESS = "#34d399"
DANGER = "#ff6b5e"
WARNING = "#f5b544"

FONT_FAMILY = "Segoe UI" if tk.TkVersion else "Helvetica"  # ttk resuelve la mejor disponible por SO
FONT_MONO = "Consolas"

FONT_TITLE = (FONT_FAMILY, 16, "bold")
FONT_SUBTITLE = (FONT_FAMILY, 10)
FONT_SECTION = (FONT_FAMILY, 11, "bold")
FONT_BODY = (FONT_FAMILY, 10)
FONT_SMALL = (FONT_FAMILY, 9)
FONT_MONO_BODY = (FONT_MONO, 9)

ASSETS_DIR = Path(__file__).parent / "assets"


def apply_theme(root: tk.Tk) -> ttk.Style:
    """Configura ttk.Style con la paleta de marca — se llama una vez al
    crear la ventana principal. Devuelve el Style para que las vistas
    puedan referenciar los nombres de estilo definidos aquí."""
    root.configure(bg=BG)
    style = ttk.Style(root)

    # "clam" es el tema base de ttk que mejor responde a personalización de
    # colores en los tres sistemas operativos — los temas nativos (aqua en
    # macOS, vista/winnative en Windows) ignoran gran parte de lo que se
    # configura aquí porque delegan el dibujo al SO.
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass  # si "clam" no esta disponible, se usa el tema por defecto del SO

    style.configure(".", background=BG, foreground=TEXT, font=FONT_BODY)
    style.configure("TFrame", background=BG)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("Card.TFrame", background=SURFACE, relief="flat", borderwidth=1)

    style.configure("TLabel", background=BG, foreground=TEXT, font=FONT_BODY)
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=FONT_TITLE)
    style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=FONT_SUBTITLE)
    style.configure("Section.TLabel", background=BG, foreground=BRAND, font=FONT_SECTION)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_SMALL)
    style.configure("Surface.TLabel", background=SURFACE, foreground=TEXT, font=FONT_BODY)
    style.configure("SurfaceMuted.TLabel", background=SURFACE, foreground=MUTED, font=FONT_SMALL)
    style.configure("Success.TLabel", background=BG, foreground=SUCCESS, font=FONT_BODY)
    style.configure("Danger.TLabel", background=BG, foreground=DANGER, font=FONT_BODY)
    style.configure("Warning.TLabel", background=BG, foreground=WARNING, font=FONT_BODY)
    style.configure("Link.TLabel", background=BG, foreground=BRAND, font=(FONT_FAMILY, 10, "underline"))

    style.configure(
        "Brand.TButton",
        background=BRAND,
        foreground="#1a1200",
        font=(FONT_FAMILY, 10, "bold"),
        borderwidth=0,
        padding=(14, 8),
    )
    style.map(
        "Brand.TButton",
        background=[("disabled", SURFACE_2), ("active", BRAND_LIGHT)],
        # Bug real corregido en v0.0.6 (auditoria visual): el estado
        # deshabilitado usaba BORDER, muy cercano visualmente al color
        # normal del boton — apenas se distinguia un boton activo de uno
        # inhabilitado. MUTED sobre SURFACE_2 da contraste real.
        foreground=[("disabled", MUTED)],
    )

    style.configure(
        "Ghost.TButton",
        background=SURFACE,
        foreground=TEXT,
        font=FONT_BODY,
        borderwidth=1,
        padding=(12, 7),
    )
    style.map(
        "Ghost.TButton",
        background=[("disabled", BG), ("active", SURFACE_2)],
        foreground=[("disabled", MUTED)],
    )

    style.configure("TEntry", fieldbackground=SURFACE_2, foreground=TEXT, borderwidth=1, padding=6)
    style.map(
        "TEntry",
        fieldbackground=[("disabled", BG), ("readonly", SURFACE_2)],
        foreground=[("disabled", MUTED)],
    )

    # --- Combobox: bug real corregido en v0.0.6 -----------------------------
    # Auditoria visual encontro el combobox de "Proveedor" practicamente
    # ilegible (fondo claro, texto lavado) — con el tema "clam", configure()
    # solo no alcanza para el estado "readonly" (el que usa el selector de
    # proveedor): ttk exige un map() explicito por estado o cae a colores
    # por defecto del sistema, que no coinciden con el tema oscuro.
    style.configure("TCombobox", fieldbackground=SURFACE_2, foreground=TEXT, background=SURFACE_2, arrowcolor=TEXT)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE_2), ("disabled", BG)],
        foreground=[("readonly", TEXT), ("disabled", MUTED)],
        background=[("readonly", SURFACE_2), ("active", SURFACE)],
        arrowcolor=[("disabled", MUTED)],
    )
    # El listado desplegable de un Combobox es un widget Tk aparte (un
    # Listbox), que ttk.Style NO cubre — sin esto, aunque el campo cerrado
    # se vea bien, al desplegarlo las opciones aparecen con los colores por
    # defecto del sistema (fondo blanco), rompiendo el tema oscuro.
    root.option_add("*TCombobox*Listbox.background", SURFACE_2)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", BRAND_DIM)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", FONT_BODY)

    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=SURFACE, foreground=MUTED, padding=(16, 8), font=FONT_BODY)
    style.map(
        "TNotebook.Tab",
        background=[("selected", BG)],
        foreground=[("selected", BRAND)],
    )
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, borderwidth=0)
    style.configure("Treeview.Heading", background=SURFACE_2, foreground=TEXT, font=(FONT_FAMILY, 9, "bold"))
    style.map("Treeview", background=[("selected", BRAND_DIM)])

    style.configure("TSeparator", background=BORDER)

    # --- Checkbutton: bug real corregido en v0.0.6 --------------------------
    # Auditoria visual encontro el checkbox "mostrar" (junto al campo de
    # API key) sin NINGUN texto visible. Con "clam", configure() por si solo
    # no fija el color del texto en todos los estados — hace falta un map()
    # explicito, igual que con el Combobox.
    style.configure("TCheckbutton", background=BG, foreground=TEXT, font=FONT_BODY, indicatorbackground=SURFACE_2)
    style.map(
        "TCheckbutton",
        background=[("active", BG)],
        foreground=[("disabled", MUTED), ("!disabled", TEXT)],
        indicatorbackground=[("selected", BRAND), ("!selected", SURFACE_2)],
    )

    style.configure("TProgressbar", background=BRAND, troughcolor=SURFACE_2, borderwidth=0)

    return style


def window_title(suffix: str | None = None) -> str:
    return f"{APP_NAME} — {suffix}" if suffix else APP_NAME


def load_icon_images() -> list[tk.PhotoImage]:
    """Carga los íconos de la app (varios tamaños) para usar como ícono de
    la ventana/barra de tareas — antes de v0.0.6 la ventana no tenía
    ningún ícono propio (mostraba la pluma genérica de Tk)."""
    images = []
    for name in ("icon16.png", "icon48.png", "icon128.png"):
        path = ASSETS_DIR / name
        if path.is_file():
            try:
                images.append(tk.PhotoImage(file=str(path)))
            except tk.TclError:
                continue
    return images


def apply_window_icon(root: tk.Tk) -> None:
    """Aplica el ícono de la app a la ventana (barra de título, barra de
    tareas, alt-tab) — se guardan las referencias en el propio root para
    que el recolector de basura de Python no las libere (un PhotoImage
    sin una referencia viva se borra de la ventana)."""
    images = load_icon_images()
    if images:
        root.iconphoto(True, *images)
        root._charly_icon_refs = images  # referencia viva — ver docstring

