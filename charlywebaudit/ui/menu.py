"""
ui/menu.py — Punto 2 del pedido: menú interactivo principal.
"""

from __future__ import annotations

import questionary

from ..config import AppConfig, load_config, save_config
from .forms import configure_assistant, configure_test
from .catalog import manage_catalog
from .theme import QUESTIONARY_STYLE, console, print_banner, print_info
from rich.markup import escape


def show_current_config(cfg: AppConfig) -> None:
    console.print()
    console.print("[bold]Configuración actual[/]")
    
    if cfg.test.test_cases:
        console.print(f"  Catálogo: [dim]{len(cfg.test.test_cases)} prueba(s) configurada(s)[/]")
    else:
        console.print(f"  Script:    [dim]{escape(cfg.test.spec_path or '(sin configurar)')}[/]")
        console.print(f"  URL:       [dim]{escape(cfg.test.url or '(sin configurar)')}[/]")
        console.print(f"  Cabeceras: [dim]{escape(str(cfg.test.headers or '(ninguna)'))}[/]")
        
    console.print(
        f"  Asistente: [dim]{escape('configurado (' + cfg.assistant.provider + ')' if cfg.assistant.configured else 'sin configurar')}[/]"
    )
    console.print()


def main_menu_loop(on_run) -> None:
    """`on_run(cfg)` ahora recibe opcionalmente una lista de pruebas."""
    print_banner()
    cfg = load_config()

    while True:
        show_current_config(cfg)
        choices = [
            questionary.Choice("▶  Correr prueba (simple)", value="run_simple"),
            questionary.Choice("📚 Gestionar catálogo de pruebas", value="manage_catalog"),
            questionary.Choice("⚙  Configurar prueba (simple)", value="cfg_test"),
            questionary.Choice("🤖 Configurar Asistente IA", value="cfg_ai"),
            questionary.Choice("✕  Salir", value="exit"),
        ]
        
        if cfg.test.test_cases:
            choices.insert(1, questionary.Choice("▶▶ Correr catálogo completo (secuencia)", value="run_catalog"))
            choices.insert(2, questionary.Choice("▶  Correr prueba específica del catálogo", value="run_specific"))

        choice = questionary.select(
            "¿Qué quieres hacer?",
            choices=choices,
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

        if choice == "manage_catalog":
            manage_catalog(cfg)
            continue
            
        if choice == "run_simple":
            if not cfg.test.spec_path or not cfg.test.url:
                console.print("[yellow]Configura primero el script y la URL de la prueba.[/]")
                continue
            if not cfg.assistant.configured:
                console.print("[yellow]Configura primero el Asistente IA (se pide una sola vez).[/]")
                if not configure_assistant(cfg):
                    continue
                save_config(cfg)
            on_run(cfg, test_case=None)

        if choice == "run_catalog":
            if not cfg.assistant.configured:
                console.print("[yellow]Configura primero el Asistente IA (se pide una sola vez).[/]")
                if not configure_assistant(cfg):
                    continue
                save_config(cfg)
            on_run(cfg, test_case="all")

        if choice == "run_specific":
            if not cfg.test.test_cases:
                console.print("[yellow]El catálogo está vacío.[/]")
                continue
            
            selected_case = questionary.select(
                "Selecciona la prueba a ejecutar:",
                choices=[questionary.Choice(tc.name, value=tc) for tc in cfg.test.test_cases],
                style=QUESTIONARY_STYLE,
            ).ask()
            
            if selected_case:
                on_run(cfg, test_case=selected_case)
