"""
report/html.py — Punto 5.1.7/5.1.8: genera el HTML final y lo guarda.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..constants import APP_VERSION
from .builder import CombinedReport

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def render_html(report: CombinedReport) -> str:
    # Bug de seguridad real corregido: select_autoescape(["html"]) mira la
    # extension FINAL del nombre del archivo del template — para
    # "report.html.jinja" esa extension es ".jinja", no ".html", asi que
    # el autoescape NUNCA se activaba. Confirmado con un payload XSS real
    # (una URL con <script>) que se incrustaba sin escapar en el reporte
    # generado. Esta plantilla siempre produce HTML, asi que autoescape=True
    # incondicional es lo correcto — no depende de adivinar la extension.
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("report.html.jinja")
    return template.render(report=report, app_version=APP_VERSION)


def save_report(report: CombinedReport, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_html(report), encoding="utf-8")
    return dest
