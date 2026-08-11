"""
gui/report_render.py — Adapta el reporte HTML para el visor embebido
(tkinterweb) dentro de la GUI.

Confirmado con una prueba real (captura de pantalla comparando ambos
casos): tkinterweb no soporta variables CSS (`var(--x)`) — el texto queda
invisible (mismo color que el fondo, porque ninguno de los dos resuelve la
variable). Con colores literales, renderiza correctamente. En vez de
mantener una plantilla HTML duplicada solo para la GUI, se post-procesa el
mismo HTML que ya genera `report/html.py` (una sola fuente de verdad para
el CSS), sustituyendo cada `var(--token)` por su valor literal.
"""

from __future__ import annotations

import re

from .theme import BG, BORDER, BRAND, DANGER, MUTED, SUCCESS, SURFACE, SURFACE_2, TEXT

# Mismo mapeo de tokens que usa report/templates/report.html.jinja — si esa
# plantilla agrega una variable nueva, se agrega aca tambien.
# BRAND ahora es #5b6cff (azul índigo oficial de sidepanel.css v2.5.0).
_TOKEN_MAP = {
    "--brand": BRAND,
    "--bg": BG,
    "--surface": SURFACE,
    "--surface2": SURFACE_2,
    "--border": BORDER,
    "--text": TEXT,
    "--muted": MUTED,
    "--ok": SUCCESS,
    "--err": DANGER,
}

_VAR_RE = re.compile(r"var\((--[a-zA-Z0-9-]+)\)")


def make_tkinterweb_compatible(html: str) -> str:
    """Sustituye cada `var(--token)` por su valor literal. Cualquier token
    no reconocido se deja tal cual (mejor un color equivocado visible que
    una excepción, y así se nota en pruebas si falta agregar un token
    nuevo a `_TOKEN_MAP`)."""

    def _replace(match: re.Match) -> str:
        token = match.group(1)
        return _TOKEN_MAP.get(token, match.group(0))

    return _VAR_RE.sub(_replace, html)
