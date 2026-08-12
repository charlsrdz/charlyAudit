"""
ui/forms.py — Formularios de configuración (punto 5.1 del pedido).

Cada función mutila `cfg` in-place y devuelve `True` si el usuario terminó
el formulario (vs. lo canceló) — así el llamador decide si vale la pena
persistir con `save_config`.
"""

from __future__ import annotations

from pathlib import Path

import questionary

from ..config import AppConfig, TestCase
from .theme import QUESTIONARY_STYLE, console, print_info, print_warning
from rich.markup import escape

_KNOWN_PROVIDERS = {
    "gemini": "Google Gemini",
    "openai": "OpenAI / ChatGPT",
    "claude": "Anthropic Claude",
    "openwebui": "Open WebUI (propio)",
    "custom": "Proveedor personalizado",
}


def _ask_spec_path(default: str | None) -> str | None:
    while True:
        raw = questionary.path(
            "Ruta del script Playwright (.spec.ts / .spec.js):",
            default=default or "",
            style=QUESTIONARY_STYLE,
        ).ask()
        if raw is None:
            return None  # cancelado (Ctrl+C / Esc)
        path = Path(raw).expanduser()
        if not path.is_file():
            print_warning(f"No se encontró un archivo en: {path}")
            continue
        if path.suffix not in (".ts", ".js"):
            proceed = questionary.confirm(
                f"La extensión '{path.suffix}' no es .ts/.js — ¿continuar de todas formas?",
                default=False,
                style=QUESTIONARY_STYLE,
            ).ask()
            if not proceed:
                continue
        return str(path.resolve())


def _ask_url(default: str | None) -> str | None:
    def _validate(value: str) -> bool | str:
        v = value.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            return "La URL debe comenzar con http:// o https://"
        return True

    return questionary.text(
        "URL de la prueba:",
        default=default or "",
        validate=_validate,
        style=QUESTIONARY_STYLE,
    ).ask()


def _edit_headers(headers: dict[str, str]) -> dict[str, str]:
    headers = dict(headers)  # copia: no mutar hasta confirmar
    while True:
        if headers:
            console.print("\n[dim]Cabeceras actuales:[/]")
            for k, v in headers.items():
                console.print(f"  [bold]{escape(k)}[/]: {escape(v)}")
        else:
            console.print("\n[dim]Sin cabeceras personalizadas.[/]")

        choices = ["Agregar cabecera"]
        if headers:
            choices += ["Quitar una cabecera", "Continuar"]
        else:
            choices += ["Continuar"]

        action = questionary.select(
            "Cabeceras personalizadas:", choices=choices, style=QUESTIONARY_STYLE
        ).ask()
        if action is None or action == "Continuar":
            return headers

        if action == "Agregar cabecera":
            name = questionary.text("Nombre de la cabecera:", style=QUESTIONARY_STYLE).ask()
            if not name:
                continue
            value = questionary.text(f"Valor de '{name}':", style=QUESTIONARY_STYLE).ask()
            if value is None:
                continue
            headers[name.strip()] = value
        elif action == "Quitar una cabecera":
            target = questionary.select(
                "¿Cuál quitar?", choices=list(headers.keys()) + ["Cancelar"], style=QUESTIONARY_STYLE
            ).ask()
            if target and target != "Cancelar":
                headers.pop(target, None)


def configure_test(cfg: AppConfig) -> bool:
    """Formulario de la prueba: script, URL, cabeceras (5.1.1 a 5.1.3)."""
    spec = _ask_spec_path(cfg.test.spec_path)
    if spec is None:
        return False
    cfg.test.spec_path = spec

    url = _ask_url(cfg.test.url)
    if url is None:
        return False
    cfg.test.url = url.strip()

    want_headers = questionary.confirm(
        "¿Configurar cabeceras HTTP personalizadas para esta prueba?",
        default=bool(cfg.test.headers),
        style=QUESTIONARY_STYLE,
    ).ask()
    if want_headers:
        cfg.test.headers = _edit_headers(cfg.test.headers)

    return True


def configure_assistant(cfg: AppConfig, *, force: bool = False) -> bool:
    """Formulario del Asistente IA (5.1.4): se pide una sola vez — si ya
    estaba configurado y no se pide `force`, solo se ofrece actualizar."""
    if cfg.assistant.configured and not force:
        print_info(
            f"Asistente ya configurado: {_KNOWN_PROVIDERS.get(cfg.assistant.provider, cfg.assistant.provider)} "
            f"· modelo {cfg.assistant.model}"
        )
        update = questionary.confirm(
            "¿Quieres actualizar esta configuración ahora?", default=False, style=QUESTIONARY_STYLE
        ).ask()
        if not update:
            return True

    provider = questionary.select(
        "Proveedor de IA:",
        choices=[questionary.Choice(title=label, value=key) for key, label in _KNOWN_PROVIDERS.items()],
        default=cfg.assistant.provider,
        style=QUESTIONARY_STYLE,
    ).ask()
    if provider is None:
        return False
    cfg.assistant.provider = provider

    model = questionary.text(
        "Modelo:", default=cfg.assistant.model, style=QUESTIONARY_STYLE
    ).ask()
    if model is None:
        return False
    cfg.assistant.model = model.strip()

    api_key = questionary.password(
        "API key (se guarda localmente, no se envía a ningún lado más que al proveedor elegido):",
        style=QUESTIONARY_STYLE,
    ).ask()
    if api_key:  # deja la anterior si el usuario no escribe una nueva
        cfg.assistant.api_key = api_key.strip()

    cfg.assistant.configured = True
    return True


def create_test_case_form(default_name: str = "") -> TestCase | None:
    """Formulario para crear un nuevo caso de prueba."""
    # Nota: Esta función usa cuestionary (terminal) y no es compatible directamente con Tkinter.
    # Se debe implementar un formulario de Tkinter para la GUI.
    name = questionary.text("Nombre de la prueba:", default=default_name, style=QUESTIONARY_STYLE).ask()
    if not name:
        return None
    
    spec = _ask_spec_path(None)
    if spec is None:
        return None
    
    url = _ask_url(None)
    if url is None:
        return None
    
    headers = {}
    want_headers = questionary.confirm("¿Agregar cabeceras?", default=False, style=QUESTIONARY_STYLE).ask()
    if want_headers:
        headers = _edit_headers({})
        
    return TestCase(name=name, spec_path=spec, url=url, headers=headers)
