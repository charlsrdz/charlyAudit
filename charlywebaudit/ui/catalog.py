"""
ui/catalog.py — Gestión del catálogo de pruebas.
"""
from __future__ import annotations

import questionary
from ..config import AppConfig, save_config
from .forms import create_test_case_form
from .theme import QUESTIONARY_STYLE, console

def manage_catalog(cfg: AppConfig) -> None:
    while True:
        choices = [
            questionary.Choice("➕  Agregar nueva prueba", value="add"),
        ]
        if cfg.test.test_cases:
            choices += [
                questionary.Choice("➖  Eliminar prueba", value="remove"),
                questionary.Choice("🔙 Volver", value="back"),
            ]
        else:
            choices += [questionary.Choice("🔙 Volver", value="back")]
            
        choice = questionary.select(
            "Gestionar catálogo:",
            choices=choices,
            style=QUESTIONARY_STYLE,
        ).ask()
        
        if choice == "back" or choice is None:
            return
        
        if choice == "add":
            tc = create_test_case_form()
            if tc:
                cfg.test.test_cases.append(tc)
                save_config(cfg)
                console.print(f"[green]Prueba '{tc.name}' agregada.[/]")
                
        elif choice == "remove":
            tc_to_remove = questionary.select(
                "¿Qué prueba eliminar?",
                choices=[questionary.Choice(tc.name, value=tc) for tc in cfg.test.test_cases] + ["Cancelar"],
                style=QUESTIONARY_STYLE,
            ).ask()
            
            if tc_to_remove and tc_to_remove != "Cancelar":
                cfg.test.test_cases.remove(tc_to_remove)
                save_config(cfg)
                console.print(f"[yellow]Prueba '{tc_to_remove.name}' eliminada.[/]")
