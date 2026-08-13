"""
report/builder.py — Punto 5.1.6/5.1.7 del pedido: une el resultado de
Playwright con las 15 respuestas del Asistente (una por ámbito) en una sola
estructura, sin omitir nada — cada ámbito conserva su respuesta completa tal
cual la devolvió la IA, y cada caso de Playwright conserva su error completo
si lo tuvo.

v0.1.1 — punto 2 del pedido: el reporte generado debe ser compatible tenga
o no datos de la extensión CharlyAudit. Desde esta versión, el flujo
principal NO usa la extensión en absoluto (punto 1 del pedido) — el campo
`extension_used` distingue esto de `assistant_analysis_complete` (que
significaba "se usó la extensión pero no se pudo completar el análisis"):
`extension_used=False` es el caso NORMAL y esperado ahora, no un fallo a
señalar con una advertencia — el reporte HTML simplemente omite la sección
del Asistente por completo en ese caso, en vez de mostrarla vacía o con un
aviso de "incompleto" que no aplica.
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
    """Solo relevante cuando `extension_used=True`: si la extensión se usó
    pero no se pudo completar el análisis de los 15 ámbitos (el navegador
    se cerró antes de tiempo), el reporte HTML muestra un aviso visible —
    nunca se pretende que un reporte parcial es uno completo."""
    extension_used: bool = False
    """Punto 2 del pedido v0.1.1: si la corrida usó la extensión CharlyAudit
    o no. Desde v0.1.1, el flujo principal siempre deja esto en False — el
    reporte HTML omite la sección del Asistente por completo en ese caso
    (no la muestra vacía ni con una advertencia, que implicaría que algo
    salió mal cuando en realidad es el comportamiento esperado)."""
    browser_outcome: str | None = None
    """Punto 3 del pedido v0.1.1: qué observó la telemetría del navegador
    durante la corrida (ver browser/telemetry.py, BrowserOutcome) — 'closed_normally',
    'closed_unexpectedly', 'never_connected', o None si no hubo telemetría."""
    browser_outcome_description: str | None = None
    """La descripción legible correspondiente a `browser_outcome`."""


def build_combined_report(
    *,
    url: str,
    spec_path: str,
    playwright_result: PlaywrightRunResult,
    assistant_responses: dict[str, str],
    extension_used: bool = False,
    browser_outcome: str | None = None,
    browser_outcome_description: str | None = None,
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
        extension_used=extension_used,
        browser_outcome=browser_outcome,
        browser_outcome_description=browser_outcome_description,
    )
