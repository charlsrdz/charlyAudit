"""
report/builder.py — Une el resultado de Playwright con el análisis por IA
(si el Asistente está configurado) en una sola estructura para el reporte
final, sin omitir nada — cada caso de Playwright conserva su error
completo si lo tuvo.

v0.1.7 — se retiró todo lo relacionado con la extensión CharlyAudit
(`extension_used`, `kpis_html`, `assistant_scopes`) — ver
`docs/roadmap-charlyaudit-nativo.md` para el plan de reimplementar esas
capacidades de forma nativa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..runner.test_exec import PlaywrightRunResult


@dataclass
class CombinedReport:
    generated_at: str
    url: str
    spec_path: str
    playwright: PlaywrightRunResult
    browser_outcome: str | None = None
    """Qué observó la telemetría del navegador durante la corrida (ver
    browser/telemetry.py, BrowserOutcome) — 'closed_normally',
    'closed_unexpectedly', 'never_connected', o None si no hubo
    telemetría."""
    browser_outcome_description: str | None = None
    """La descripción legible correspondiente a `browser_outcome`."""
    ai_analysis_html: str | None = None
    """Análisis por IA de los resultados de Playwright (qué funcionó, qué
    no, hallazgos reales dentro de fallas de aserción, y recomendaciones
    concretas para mejorar el script) — vía llamada directa a la API del
    proveedor configurado (ver ai_playwright.py) — presente siempre que
    el Asistente esté configurado."""
    test_name: str | None = None
    """El nombre bajo el que esta corrida quedó registrada (catálogo o el
    nombre del archivo del spec)."""
    headers: dict[str, str] = field(default_factory=dict)
    """Cabeceras HTTP personalizadas usadas en esta corrida."""


def build_combined_report(
    *,
    url: str,
    spec_path: str,
    playwright_result: PlaywrightRunResult,
    browser_outcome: str | None = None,
    browser_outcome_description: str | None = None,
    ai_analysis_html: str | None = None,
    test_name: str | None = None,
    headers: dict[str, str] | None = None,
) -> CombinedReport:
    return CombinedReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        url=url,
        spec_path=spec_path,
        playwright=playwright_result,
        browser_outcome=browser_outcome,
        browser_outcome_description=browser_outcome_description,
        ai_analysis_html=ai_analysis_html,
        test_name=test_name,
        headers=headers or {},
    )
