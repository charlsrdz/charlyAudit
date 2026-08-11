"""
constants.py — Branding, versionado y valores por defecto de RedGpsWebAudit.

Todo lo que "no cambia salvo que alguien lo decida a propósito" vive aquí:
el ID fijo de la extensión CharlyAudit (derivado de la `key` que se agregó a
su manifest.json — ver extension_key/ en la raíz del repo para el par de
claves real), y la configuración semilla que se siembra una única vez en un
perfil de navegador nuevo.
"""

from __future__ import annotations

APP_NAME = "RedGpsWebAudit"
APP_SLUG = "redgpswebaudit"  # usado para rutas de config (platformdirs)
APP_VERSION = "0.0.9"
APP_TAGLINE = "Orquesta CharlyAudit + Playwright en una sola corrida auditada"

# --- Autor / créditos (sección Ayuda de la GUI, v0.0.6) ---------------------
AUTHOR_NAME = "RedGPS"
AUTHOR_DESCRIPTION = (
    "RedGpsWebAudit y CharlyAudit son herramientas internas de RedGPS "
    "para automatizar la auditoría de calidad de las plataformas de rastreo."
)
RELATED_PROJECT = "CharlyAudit"  # la extension de Chrome que esta herramienta orquesta

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

# --- Configuración semilla de la extensión (se aplica UNA sola vez, la
# primera vez que se usa un perfil de navegador nuevo; después queda
# persistida en el propio `chrome.storage.local` del perfil y ya no se
# vuelve a tocar salvo que el usuario la actualice explícitamente desde el
# menú de RedGpsWebAudit). -----------------------------------------------
DEFAULT_ASSISTANT_CONFIG = {
    "provider": "gemini",
    "model": "gemini-flash-latest",
    "apiKey": "AQ.Ab8RN6KpVbeVOaVM5LFgrVlATT4LDv2QuVSTDbKSd5ubG1lplw",
}

DEFAULT_PALETTE = {
    "--c-brand": "#5b6cff",  # azul índigo del sistema de diseño oficial (sidepanel.css)
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
