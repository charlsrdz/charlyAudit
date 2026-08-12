"""
history.py — Historial persistente de corridas (punto 5 del pedido v0.1.0a).

"Cuando [se corre una] prueba debe guardar un reporte con los detalles de
su ejecución para posteriormente en una vista dashboard se pueda ver una
comparativa entre cada prueba evaluando el contraste de cada resultado
sobre el tiempo" — este módulo es esa memoria: cada corrida (venga del
catálogo o sea una prueba suelta) agrega un `RunRecord` a un archivo JSON
en la carpeta de datos del usuario (vía `platformdirs`, la misma
convención que `config.py` ya usa para su propio archivo) — nunca se
sobreescribe, solo se agrega, así el dashboard puede graficar la
tendencia real a lo largo del tiempo, no solo la última corrida.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from platformdirs import user_data_dir

from .constants import APP_SLUG

HISTORY_DIR = Path(user_data_dir(APP_SLUG, appauthor=False))
HISTORY_FILE = HISTORY_DIR / "run_history.json"
REPORTS_DIR = HISTORY_DIR / "reports"


def reports_dir() -> Path:
    """Directorio donde se guarda automáticamente una copia de CADA reporte
    generado (ver __main__.run_audit, punto 4 del pedido v0.1.0a) — así el
    dashboard siempre puede abrir el detalle completo de cualquier corrida
    pasada, incluso si el usuario nunca pidió guardar una copia aparte."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR

# Un archivo de historial ilimitado creceria para siempre en un uso muy
# prolongado — se retiene un numero grande pero acotado de corridas mas
# recientes, suficiente para cualquier comparacion razonable en el
# dashboard, sin arriesgar un archivo que tarde en cargar.
MAX_RECORDS = 500


@dataclass
class RunRecord:
    """Un resumen comparable de UNA corrida — no el reporte completo (eso
    vive en su propio .html, ver `report_path`), solo lo necesario para
    graficar tendencias: nombre de la prueba, cuándo corrió, cuánto tardó,
    y su resultado."""

    id: str
    test_name: str
    """Nombre de la prueba del catálogo, o el nombre del archivo .spec.ts
    si fue una corrida suelta (sin catálogo) — siempre hay un nombre legible,
    nunca queda vacío."""
    spec_path: str
    url: str
    started_at: str  # ISO 8601, UTC
    duration_ms: int
    passed: int
    failed: int
    skipped: int
    all_passed: bool
    assistant_analysis_complete: bool
    """Falso si la corrida se degradó (ver __main__.py, v0.1.0a) — el
    navegador se cerró antes de completar el análisis del Asistente. El
    dashboard lo marca visualmente distinto: un resultado sin analisis
    completo no es tan comparable como uno que sí lo tiene."""
    report_path: str | None = None
    """Ruta del reporte .html completo de esta corrida, si se guardó uno —
    permite abrir el detalle completo desde el dashboard."""


def _ensure_dir() -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def load_history() -> list[RunRecord]:
    """Carga el historial completo — nunca falla si el archivo no existe
    todavía (primera corrida) o está corrupto (se trata como vacío en vez
    de bloquear al usuario; el historial es informativo, no crítico)."""
    if not HISTORY_FILE.exists():
        return []
    try:
        raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    records = []
    for item in raw:
        known = {k: v for k, v in item.items() if k in RunRecord.__dataclass_fields__}
        try:
            records.append(RunRecord(**known))
        except TypeError:
            continue  # registro corrupto/incompleto — se omite, no bloquea el resto
    return records


def append_run_record(record: RunRecord) -> None:
    """Agrega un registro al historial — nunca REEMPLAZA nada existente.
    Best-effort: si falla (disco lleno, permisos), no debe interrumpir el
    resultado real de la corrida que ya terminó — solo se pierde la
    entrada del dashboard, no el reporte en sí."""
    try:
        _ensure_dir()
        records = load_history()
        records.append(record)
        if len(records) > MAX_RECORDS:
            records = records[-MAX_RECORDS:]
        HISTORY_FILE.write_text(
            json.dumps([asdict(r) for r in records], indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def make_run_id() -> str:
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
