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
    )


def build_summary_report(reports: list[CombinedReport]) -> str:
    """Genera un reporte resumen HTML a partir de una lista de reportes."""
    html = "<h1>Informe General de Pruebas</h1><ul>"
    for r in reports:
        status = "✅ Pasó" if r.playwright.all_passed else "❌ Falló"
        html += f"<li>{r.spec_path}: {status}</li>"
    html += "</ul>"
    return html
