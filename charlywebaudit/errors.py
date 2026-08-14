"""
errors.py — Excepciones propias de charlyWebAudit.

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
    """Google Chrome (canal estable) no está instalado."""


class NodeNotFoundError(CharlyWebAuditError):
    """No se encontró Node.js/npm en el PATH — requerido para correr specs
    de @playwright/test tal cual las escribe el usuario."""


class PlaywrightTestNotAvailableError(CharlyWebAuditError):
    """Node existe, pero no se pudo resolver/instalar @playwright/test."""


class BrowserLaunchError(CharlyWebAuditError):
    """El navegador orquestado no llegó a levantar o el puerto CDP no respondió."""


class SpecNotFoundError(CharlyWebAuditError):
    """El archivo .spec.ts/.spec.js configurado no existe o no es legible."""


class AssistantError(CharlyWebAuditError):
    """Fallo al pedir un análisis por IA de los resultados de Playwright."""


class ConfigError(CharlyWebAuditError):
    """La configuración persistida es ilegible o inválida."""
