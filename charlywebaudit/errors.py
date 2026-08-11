"""
errors.py — Excepciones propias de RedGpsWebAudit.

Todas heredan de CharlyWebAuditError con un `hint` opcional: un consejo
accionable en una línea, pensado para mostrarse tal cual en el panel de
error de la UI (ver ui/theme.py -> print_error) en vez de un traceback crudo.
"""

from __future__ import annotations


class CharlyWebAuditError(Exception):
    """Base de toda excepción propia. `hint` es un consejo accionable corto."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class ChromiumNotInstalledError(CharlyWebAuditError):
    """Chromium (el que gestiona Playwright) no está instalado."""


class ChromiumInstallFailedError(CharlyWebAuditError):
    """La instalación de Chromium se intentó y falló."""


class NodeNotFoundError(CharlyWebAuditError):
    """No se encontró Node.js/npm en el PATH — requerido para correr specs
    de @playwright/test tal cual las escribe el usuario."""


class PlaywrightTestNotAvailableError(CharlyWebAuditError):
    """Node existe, pero no se pudo resolver/instalar @playwright/test."""


class ExtensionNotFoundError(CharlyWebAuditError):
    """No se encontró el build de la extensión CharlyAudit en la ruta configurada."""


class BrowserLaunchError(CharlyWebAuditError):
    """El navegador orquestado no llegó a levantar o el puerto CDP no respondió."""


class SpecNotFoundError(CharlyWebAuditError):
    """El archivo .spec.ts/.spec.js configurado no existe o no es legible."""


class TargetTabNotFoundError(CharlyWebAuditError):
    """No se pudo identificar con certeza la pestaña que abrió el spec del
    usuario dentro del navegador orquestado."""


class RecordingError(CharlyWebAuditError):
    """Fallo al iniciar/detener la grabación desde el panel de la extensión."""


class AssistantError(CharlyWebAuditError):
    """Fallo al pedir un análisis a un ámbito del Asistente IA."""


class ConfigError(CharlyWebAuditError):
    """La configuración persistida es ilegible o inválida."""
