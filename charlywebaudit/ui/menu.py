"""
ui/menu.py — Punto 2 del pedido: menú interactivo principal.
"""

from __future__ import annotations

import questionary

from ..config import AppConfig, load_config, save_config
from .forms import configure_assistant, configure_test
from .theme import QUESTIONARY_STYLE, console, print_banner, print_info
from rich.markup import escape


def show_current_config(cfg: AppConfig) -> None:
    console.print()
    console.print("[bold]Configuración actual[/]")
    console.print(f"  Script:    [dim]{escape(cfg.test.spec_path or '(sin configurar)')}[/]")
    console.print(f"  URL:       [dim]{escape(cfg.test.url or '(sin configurar)')}[/]")
    console.print(f"  Cabeceras: [dim]{escape(str(cfg.test.headers or '(ninguna)'))}[/]")
    console.print(
        f"  Asistente: [dim]{escape('configurado (' + cfg.assistant.provider + ')' if cfg.assistant.configured else 'sin configurar')}[/]"
    )
    console.print()


def main_menu_loop(on_run) -> None:
    """`on_run(cfg)` es el callback que dispara la corrida completa (async,
    ver __main__.py) — se mantiene fuera de este módulo para que ui/ no
    dependa de browser/runner directamente."""
    print_banner()
    cfg = load_config()

    while True:
        show_current_config(cfg)
        choice = questionary.select(
            "¿Qué quieres hacer?",
            choices=[
                questionary.Choice("▶  Correr prueba", value="run"),
                questionary.Choice("⚙  Configurar prueba (script, URL, cabeceras)", value="cfg_test"),
                questionary.Choice("🤖 Configurar Asistente IA", value="cfg_ai"),
                questionary.Choice("✕  Salir", value="exit"),
            ],
            style=QUESTIONARY_STYLE,
        ).ask()

        if choice is None or choice == "exit":
            print_info("Hasta luego.")
            return

        if choice == "cfg_test":
            if configure_test(cfg):
                save_config(cfg)
            continue

        if choice == "cfg_ai":
            if configure_assistant(cfg, force=True):
                save_config(cfg)
            continue

        if choice == "run":
            if not cfg.test.spec_path or not cfg.test.url:
                console.print("[yellow]Configura primero el script y la URL de la prueba.[/]")
                continue
            if not cfg.assistant.configured:
                console.print("[yellow]Configura primero el Asistente IA (se pide una sola vez).[/]")
                if not configure_assistant(cfg):
                    continue
                save_config(cfg)
            on_run(cfg)
