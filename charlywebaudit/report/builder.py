"""
report/builder.py — Punto 5.1.6/5.1.7 del pedido: une el resultado de
Playwright con las 15 respuestas del Asistente (una por ámbito) en una sola
estructura, sin omitir nada — cada ámbito conserva su respuesta completa tal
cual la devolvió la IA, y cada caso de Playwright conserva su error completo
si lo tuvo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..constants import APP_VERSION, ASSISTANT_SCOPES, SCOPE_LABELS
from ..runner.test_exec import PlaywrightRunResult


@dataclass
class CombinedReport:
    generated_at: str
    url: str
    spec_path: str
    playwright: PlaywrightRunResult
    assistant_scopes: list[dict] = field(default_factory=list)  # [{id, label, html}]
    assistant_analysis_complete: bool = True
    """Punto 4 del pedido v0.1.0a ("garantiza los reportes completos"):
    en vez de dejar que el usuario descubra por su cuenta que faltan
    ámbitos (cada uno con su propio texto "Sin respuesta..."), el reporte
    HTML muestra un aviso visible al inicio cuando esto es False — nunca
    se pretende que un reporte parcial es uno completo."""


def build_combined_report(
    *,
    url: str,
    spec_path: str,
    playwright_result: PlaywrightRunResult,
    assistant_responses: dict[str, str],
) -> CombinedReport:
    scopes = [
        {
            "id": scope_id,
            "label": SCOPE_LABELS[scope_id],
            "html": assistant_responses.get(scope_id, "<p><em>Sin respuesta para este ámbito.</em></p>"),
        }
        for scope_id in ASSISTANT_SCOPES
    ]
    return CombinedReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        url=url,
        spec_path=spec_path,
        playwright=playwright_result,
        assistant_scopes=scopes,
        assistant_analysis_complete=bool(assistant_responses),
    )
