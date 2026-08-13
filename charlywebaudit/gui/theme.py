"""
gui/theme.py — Branding de la GUI (punto 2 del pedido v0.0.5, endurecido en
v0.0.6 tras una auditoría visual real con capturas de pantalla).

Toma como base la paleta real de CharlyAudit — los mismos tokens de color
que ya usa el panel lateral de la extensión (`--c-brand`, `--c-bg`,
`--c-surface`, etc., ver sidepanel.css en vendor/charlyaudit/) y el mismo
naranja de marca (`#b87619`, DEFAULT_PALETTE en constants.py) que ya se
siembra en la configuración del Asistente — así la CLI, la extensión y
ahora la GUI comparten una sola identidad visual, no tres inventadas por
separado.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from ..constants import APP_NAME, DEFAULT_PALETTE

# Paleta de colores autorizada en library-style.css (Modo Claro)
# Prohibido usar azul, morado u oscuros.
BRAND = DEFAULT_PALETTE["--c-brand"]  # "#ec9c2f" (--orange de library-style.css)
BRAND_LIGHT = "#f8ae4b"              # --ondel-orange2 (#f8ae4b)
BRAND_DIM = "#d9851c"                # Naranja activo
BG = "#ffffff"                       # --white (#ffffff)
SURFACE = "#f4f4f4"                  # --ondel-gray1 (#f4f4f4)
SURFACE_2 = "#edf1f5"                # --ondel-gray5 (#edf1f5)
BORDER = "#dddddd"                   # --gray4 (#dddddd)
TEXT = "#333333"                     # --txt-black / --ondel-text2 (#333333)
MUTED = "#6c757d"                    # --ondel-text / --gray (#6c757d)
SUCCESS = "#5cb85c"                  # --green (#5cb85c)
DANGER = "#d9534f"                   # --red (#d9534f)
WARNING = "#f3ba25"                  # --yellow (#f3ba25)

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
    """Configura ttk.Style con la paleta de marca en modo claro (library-style.css).
    Devuelve el Style para que las vistas puedan referenciar los nombres de estilo."""
    root.configure(bg=BG)
    style = ttk.Style(root)

    try:
        style.theme_use("clam")
    except tk.TclError:
        pass  # si "clam" no está disponible, se usa el tema por defecto del SO

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
        foreground="#ffffff",
        font=(FONT_FAMILY, 10, "bold"),
        borderwidth=0,
        padding=(14, 8),
    )
    style.map(
        "Brand.TButton",
        background=[("disabled", BORDER), ("active", BRAND_LIGHT)],
        foreground=[("disabled", MUTED)],
    )

    style.configure(
        "Ghost.TButton",
        background=SURFACE,
        foreground=TEXT,
        font=FONT_BODY,
        borderwidth=1,
        bordercolor=BORDER,
        padding=(12, 7),
    )
    style.map(
        "Ghost.TButton",
        background=[("disabled", BG), ("active", SURFACE_2)],
        foreground=[("disabled", MUTED)],
    )

    style.configure("TEntry", fieldbackground="#ffffff", foreground=TEXT, borderwidth=1, bordercolor=BORDER, padding=6)
    style.map(
        "TEntry",
        fieldbackground=[("disabled", SURFACE), ("readonly", SURFACE_2)],
        foreground=[("disabled", MUTED)],
    )

    style.configure("TCombobox", fieldbackground="#ffffff", foreground=TEXT, background=SURFACE_2, arrowcolor=TEXT)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE_2), ("disabled", SURFACE)],
        foreground=[("readonly", TEXT), ("disabled", MUTED)],
        background=[("readonly", SURFACE_2), ("active", SURFACE)],
        arrowcolor=[("disabled", MUTED)],
    )

    root.option_add("*TCombobox*Listbox.background", "#ffffff")
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", BRAND)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", FONT_BODY)

    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=SURFACE, foreground=MUTED, padding=(16, 8), font=FONT_BODY)
    style.map(
        "TNotebook.Tab",
        background=[("selected", BG)],
        foreground=[("selected", BRAND)],
    )
    style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", foreground=TEXT, borderwidth=1, bordercolor=BORDER)
    style.configure("Treeview.Heading", background=SURFACE_2, foreground=TEXT, font=(FONT_FAMILY, 9, "bold"))
    style.map("Treeview", background=[("selected", BRAND_LIGHT)], foreground=[("selected", "#ffffff")])

    style.configure("TSeparator", background=BORDER)

    style.configure("TCheckbutton", background=BG, foreground=TEXT, font=FONT_BODY, indicatorbackground="#ffffff")
    style.map(
        "TCheckbutton",
        background=[("active", BG)],
        foreground=[("disabled", MUTED), ("!disabled", TEXT)],
        indicatorbackground=[("selected", BRAND), ("!selected", "#ffffff")],
    )

    style.configure("TProgressbar", background=BRAND, troughcolor=SURFACE_2, borderwidth=0)

    return style


def window_title(suffix: str | None = None) -> str:
    return f"{APP_NAME} — {suffix}" if suffix else APP_NAME


def load_icon_images() -> list[tk.PhotoImage]:
    """Carga los íconos reales de la extensión (varios tamaños) para usar
    como ícono de la ventana/barra de tareas — antes de v0.0.6 la ventana
    no tenía ningún ícono propio (mostraba la pluma genérica de Tk)."""
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
    """Aplica el ícono real de CharlyAudit a la ventana (barra de título,
    barra de tareas, alt-tab) — se guardan las referencias en el propio
    root para que el recolector de basura de Python no las libere (un
    PhotoImage sin una referencia viva en, se borra de la ventana)."""
    images = load_icon_images()
    if images:
        root.iconphoto(True, *images)
        root._charly_icon_refs = images  # referencia viva — ver docstring

