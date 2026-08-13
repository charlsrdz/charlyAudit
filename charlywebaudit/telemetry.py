"""
telemetry.py — Módulo de telemetría de uso y errores a Telegram.

Envía notificaciones de forma asíncrona / no bloqueante en segundo plano
para que nunca afecte ni retrase la ejecución principal de la aplicación.
"""

from __future__ import annotations

import json
import logging
import platform
import sys
import threading
import urllib.parse
import urllib.request
from typing import Any

from .constants import APP_NAME, APP_VERSION

TELEGRAM_BOT_TOKEN = "8737782242:AAFfwzolgJushk6dSTmtIRFbJM85yGMQ1gc"
TELEGRAM_CHAT_ID = "8915914042"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

logger = logging.getLogger(__name__)


def _send_request(text: str) -> None:
    """Función auxiliar privada ejecutada en un hilo en segundo plano."""
    try:
        data = urllib.parse.urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }).encode("utf-8")
        req = urllib.request.Request(TELEGRAM_API_URL, data=data, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            pass
    except Exception as exc:
        logger.debug("Falló el envío de telemetría a Telegram: %s", exc)


def send_telemetry(event_type: str, message: str, details: Any = None) -> None:
    """Envía un evento de telemetría a Telegram sin bloquear la aplicación."""
    try:
        header_emoji = "🚨" if "ERROR" in event_type.upper() or "FAIL" in event_type.upper() else "📊"
        lines = [
            f"<b>{header_emoji} Telemetría — {APP_NAME} v{APP_VERSION}</b>",
            f"<b>Evento:</b> <code>{event_type}</code>",
            f"<b>Sistema:</b> {platform.system()} {platform.release()}",
            f"<b>Detalle:</b> {message}",
        ]
        if details:
            if isinstance(details, (dict, list)):
                details_str = json.dumps(details, ensure_ascii=False, indent=2)
            else:
                details_str = str(details)
            if len(details_str) > 1500:
                details_str = details_str[:1500] + "\n... [truncado]"
            safe_details = details_str.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"<pre>{safe_details}</pre>")

        full_text = "\n".join(lines)
        thread = threading.Thread(target=_send_request, args=(full_text,), daemon=True)
        thread.start()
    except Exception:
        pass


def setup_telemetry_hooks() -> None:
    """Instala hooks globales para capturar excepciones no controladas."""
    orig_excepthook = sys.excepthook

    def custom_excepthook(exc_type, exc_value, exc_traceback):
        import traceback

        tb_lines = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        send_telemetry("UNHANDLED_EXCEPTION", f"{exc_type.__name__}: {exc_value}", tb_lines)
        if orig_excepthook:
            orig_excepthook(exc_type, exc_value, exc_traceback)

    sys.excepthook = custom_excepthook
