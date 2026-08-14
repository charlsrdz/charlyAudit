"""
constants.py — Branding, versionado y valores por defecto de charlyWebAudit.
"""

from __future__ import annotations

APP_NAME = "charlyWebAudit"
APP_SLUG = "charlywebaudit"  # usado para rutas de config (platformdirs)
APP_VERSION = "0.1.7"
APP_TAGLINE = "Corre pruebas reales de Playwright y genera un reporte con el resultado y el estado del navegador"

# --- Autor / créditos (sección Ayuda de la GUI, v0.0.6) ---------------------
AUTHOR_NAME = "RedGPS"
AUTHOR_DESCRIPTION = "charlyWebAudit es una herramienta interna de RedGPS para automatizar la auditoría de calidad de sus plataformas de rastreo."

# Puerto de depuración remota fijo para el navegador orquestado. Fijo (no
# aleatorio) a propósito: simplifica que tanto el subproceso de Node/Playwright
# Test como nuestro propio proceso Python se conecten al MISMO navegador sin
# tener que pasarse el puerto por un canal adicional.
CDP_PORT = 9333

DEFAULT_ASSISTANT_CONFIG = {
    "provider": "gemini",
    "model": "gemini-flash-latest",
    "apiKey": "AQ.Ab8RN6KpVbeVOaVM5LFgrVlATT4LDv2QuVSTDbKSd5ubG1lplw",
}

# Color de marca de la GUI — un solo lugar donde mantenerlo (usado por
# gui/theme.py).
BRAND_COLOR = "#b87619"
