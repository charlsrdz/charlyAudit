"""
constants.py — Branding, versionado y valores por defecto de charlyWebAudit.

Todo lo que "no cambia salvo que alguien lo decida a propósito" vive aquí:
el ID fijo de la extensión CharlyAudit (derivado de la `key` que se agregó a
su manifest.json — ver extension_key/ en la raíz del repo para el par de
claves real), y la configuración semilla que se siembra una única vez en un
perfil de navegador nuevo.
"""

from __future__ import annotations

APP_NAME = "RedGps Web Audit - Performance & Seguridad"
APP_SLUG = "charlywebaudit"  # usado para rutas de config (platformdirs)
APP_VERSION = "0.1.4redgps"
APP_TAGLINE = "Corre pruebas reales de Playwright y genera un reporte de auditoría de rendimiento y seguridad"

# --- Autor / créditos (sección Ayuda de la GUI) ---------------------
AUTHOR_NAME = "RedGPS"
AUTHOR_DESCRIPTION = (
    "RedGps Web Audit - Performance & Seguridad es una herramienta de auditoría de calidad "
    "y rendimiento desarrollada por RedGPS."
)
RELATED_PROJECT = "RedGps Web Audit"  # herramienta de auditoria

# ID de extensión FIJO: se deriva matemáticamente de la clave publica que se
# agregó al campo "key" de manifest.json de CharlyAudit (SHA256 de la clave
# publica DER, primeros 16 bytes mapeados a letras a-p — el mismo algoritmo
# que usa Chrome internamente). Mientras esa clave no cambie, este ID es
# estable entre instalaciones y entre corridas — ya no hace falta
# descubrirlo escuchando al service worker en cada arranque.
CHARLYAUDIT_EXTENSION_ID = "oohbidkmnljblaealbjcplggfnckkaed"

# Puerto de depuración remota fijo para el navegador orquestado. Fijo (no
# aleatorio) a propósito: simplifica que tanto el subproceso de Node/Playwright
# Test como nuestro propio proceso Python se conecten al MISMO navegador sin
# tener que pasarse el puerto por un canal adicional.
CDP_PORT = 9333

# --- Configuración semilla de la extensión (se aplica UNA sola vez) ---------
DEFAULT_ASSISTANT_CONFIG = {
    "provider": "gemini",
    "model": "gemini-flash-latest",
    "apiKey": "AQ.Ab8RN6KpVbeVOaVM5LFgrVlATT4LDv2QuVSTDbKSd5ubG1lplw",
}

DEFAULT_PALETTE = {
    "--c-brand": "#ec9c2f",
}

DEFAULT_CAPTURE_CONFIG = {
    "watchedGlobals": ["clientData"],
}

# Los 15 ámbitos de contexto del Asistente (ver SCOPES en context-bridge.js
# de CharlyAudit) — un solo lugar donde mantener esta lista si la extensión
# agrega/renombra ámbitos en el futuro.
ASSISTANT_SCOPES = [
    "metadata",
    "errors",
    "network",
    "console",
    "routes",
    "functions",
    "globals",
    "interactions",
    "audit",
    "replay",
    "performance",
    "security",
    "resources",
    "codeblocks",
    "headers",
]

SCOPE_LABELS = {
    "metadata": "Resumen",
    "errors": "Errores",
    "network": "Red",
    "console": "Consola",
    "routes": "Rutas",
    "functions": "Funciones",
    "globals": "Variables",
    "interactions": "Interacción",
    "audit": "Estructura",
    "replay": "Repetición",
    "performance": "Performance",
    "security": "Seguridad",
    "resources": "Recursos",
    "codeblocks": "Código",
    "headers": "Cabeceras",
}
