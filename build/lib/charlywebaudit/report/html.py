"""
report/html.py — Punto 5.1.7/5.1.8: genera el HTML final y lo guarda.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..constants import APP_VERSION
from .builder import CombinedReport

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def render_html(report: CombinedReport) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.jinja")
    return template.render(report=report, app_version=APP_VERSION)


def save_report(report: CombinedReport, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_html(report), encoding="utf-8")
    return dest
