"""
ui/theme.py — Diseño básico estándar (punto 3 del pedido).

Un único lugar para: la paleta de colores de la CLI, el banner de marca, y
los helpers de mensaje (error/éxito/aviso) que todo el resto del proyecto
reutiliza — así ningún módulo inventa su propio formato de salida.
"""

from __future__ import annotations

import questionary
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from ..constants import APP_NAME, APP_TAGLINE, APP_VERSION
from ..errors import CharlyWebAuditError

console = Console()

# Mismo naranja de marca que usa la GUI (gui/theme.py, BRAND_COLOR en
# constants.py) — coherencia visual entre la CLI y la GUI.
BRAND = "#b87619"

QUESTIONARY_STYLE = questionary.Style(
    [
        ("qmark", f"fg:{BRAND} bold"),
        ("question", "bold"),
        ("answer", f"fg:{BRAND} bold"),
        ("pointer", f"fg:{BRAND} bold"),
        ("highlighted", f"fg:{BRAND} bold"),
        ("selected", f"fg:{BRAND}"),
        ("separator", "fg:#6c6c6c"),
        ("instruction", "fg:#6c6c6c italic"),
        ("text", ""),
    ]
)


def print_banner() -> None:
    title = Text(f"{APP_NAME}", style=f"bold {BRAND}")
    sub = Text(f"v{APP_VERSION} · {APP_TAGLINE}", style="dim")
    console.print(Panel.fit(Text.assemble(title, "\n", sub), border_style=BRAND))


def print_section(title: str) -> None:
    console.rule(f"[bold {BRAND}]{escape(title)}[/]")


def print_success(message: str) -> None:
    console.print(f"[bold green]✓[/] {escape(message)}")


def print_info(message: str) -> None:
    console.print(f"[bold {BRAND}]›[/] {escape(message)}")


def print_warning(message: str) -> None:
    console.print(f"[bold yellow]⚠[/] {escape(message)}")


def print_error(exc: Exception) -> None:
    """Formato consistente para cualquier error mostrado al usuario: nunca
    un traceback crudo, siempre mensaje + (si existe) un consejo accionable."""
    if isinstance(exc, CharlyWebAuditError):
        body = Text(exc.message, style="bold red")
        if exc.hint:
            body.append("\n\n")
            body.append(exc.hint, style="dim")
        console.print(Panel(body, title="[bold red]Error[/]", border_style="red"))
    else:
        console.print(
            Panel(
                Text(str(exc), style="bold red"),
                title=f"[bold red]Error inesperado ({type(exc).__name__})[/]",
                border_style="red",
            )
        )


class CliReporter:
    """Implementación de `Reporter` (ver reporter.py) para la CLI — envuelve
    los `print_*` de este mismo módulo, sin ningún cambio de comportamiento
    respecto a como funcionaba antes de que existiera la abstracción."""

    def section(self, title: str) -> None:
        print_section(title)

    def info(self, message: str) -> None:
        print_info(message)

    def success(self, message: str) -> None:
        print_success(message)

    def warning(self, message: str) -> None:
        print_warning(message)

    def error(self, exc: Exception) -> None:
        print_error(exc)

    def raw(self, text: str) -> None:
        # Bug real corregido: esto muestra la salida CRUDA de `npx playwright
        # test` — npm y Playwright usan corchetes en su propio formato de log
        # ("[WARN]", nombres de test como "login [flaky]", etc.). Con el
        # marcado de Rich activo, cualquier fragmento entre corchetes se
        # interpreta como una etiqueta de estilo y se borra en silencio del
        # texto mostrado — confirmado con un caso real antes de este fix.
        # markup=False trata el contenido como texto literal, correcto para
        # la salida de un proceso externo que no controlamos.
        console.print(text, markup=False)
