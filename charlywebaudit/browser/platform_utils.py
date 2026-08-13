"""
browser/platform_utils.py — Helpers multiplataforma (v0.0.2).

Centraliza lo que difiere entre Linux/macOS/Windows, para que el resto del
proyecto no tenga que repetir lógica de detección de plataforma dispersa.
"""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

from ..errors import NodeNotFoundError


def is_windows() -> bool:
    return platform.system() == "Windows"


def is_macos() -> bool:
    return platform.system() == "Darwin"


def is_linux() -> bool:
    return platform.system() == "Linux"


def resolve_executable(name: str, *, hint: str | None = None) -> str:
    """Resuelve un ejecutable vía PATH, devolviendo la ruta COMPLETA — nunca
    el nombre desnudo.

    Bug real corregido en v0.0.2: en Windows, los ejecutables instalados por
    npm (`npx`, `npm`, `node` en algunos setups) son en realidad scripts
    `.cmd`/`.bat` — invocar `subprocess.Popen(["npx", ...])` con el nombre
    desnudo (sin `shell=True`) puede fallar o comportarse de forma
    impredecible en Windows, porque `CreateProcess` no resuelve la extensión
    automáticamente como sí lo hace `cmd.exe`. `shutil.which()` SÍ resuelve
    la extensión correcta (`.cmd`/`.exe`/etc. según `PATHEXT`) — usar
    siempre la ruta que devuelve, nunca el nombre desnudo, evita el problema
    de raíz sin necesitar `shell=True` en ningún sistema operativo.
    """
    resolved = shutil.which(name)
    if not resolved:
        raise NodeNotFoundError(
            f"No se encontró '{name}' en el PATH del sistema.",
            hint=hint or "Instala Node.js desde https://nodejs.org (incluye npm/npx) y vuelve a intentarlo.",
        )
    return resolved


def resolve_npx() -> str:
    return resolve_executable(
        "npx",
        hint="charlyWebAudit necesita 'npx' (viene con Node.js) para correr specs de @playwright/test — "
        "instala Node.js desde https://nodejs.org y vuelve a intentarlo.",
    )


def needs_virtual_display() -> bool:
    """Detecta si estamos en Linux sin un display disponible — el caso
    típico de un servidor/CI/contenedor sin entorno gráfico. CharlyAudit
    necesita `headless: false` (las extensiones de Chrome no cargan de
    forma fiable en modo headless puro), lo que requiere un display real
    o uno virtual (Xvfb). En macOS/Windows, correr de forma interactiva
    siempre implica una sesión gráfica real — esta condición nunca aplica
    ahí."""
    if not is_linux():
        return False
    return not os.environ.get("DISPLAY")


def find_xvfb_run() -> str | None:
    return shutil.which("xvfb-run")
