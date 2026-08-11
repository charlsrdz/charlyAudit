"""
browser/node_check.py — Prerrequisito de Node.js/npm.

El script que el usuario adjunta (ítem 4 de la decisión: "aceptar un script
Playwright real y completo") es un spec de @playwright/test — se ejecuta con
el CLI de Node (`npx playwright test archivo.spec.ts`), no con la librería
`playwright` de Python. Por eso Node/npm son un prerrequisito real, distinto
del Chromium que gestiona Playwright Python.

A diferencia de Chromium, no instalamos Node automáticamente: se instala de
formas muy distintas según el sistema operativo (instalador oficial, nvm,
gestor de paquetes del SO...) y intentar automatizarlo sería más frágil que
útil. En su lugar: detectamos, y si falta, bloqueamos con instrucciones
claras de dónde conseguirlo.
"""

from __future__ import annotations

import subprocess

from ..errors import NodeNotFoundError, PlaywrightTestNotAvailableError
from .platform_utils import resolve_executable, resolve_npx


def _run_version(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def ensure_node() -> tuple[str, str]:
    """Devuelve (version_node, version_npm) o lanza NodeNotFoundError."""
    try:
        node_path = resolve_executable("node")
        npm_path = resolve_executable("npm")
    except NodeNotFoundError:
        raise NodeNotFoundError(
            "No se encontró Node.js/npm en el PATH del sistema.",
            hint="RedGpsWebAudit ejecuta los specs de @playwright/test con el CLI de Node — "
            "instala Node.js desde https://nodejs.org (versión LTS recomendada) y vuelve a intentarlo.",
        )
    node_version = _run_version([node_path, "--version"])
    npm_version = _run_version([npm_path, "--version"])
    if not node_version or not npm_version:
        raise NodeNotFoundError(
            "Se encontró Node.js/npm pero no respondieron correctamente.",
            hint="Verifica que tu instalación de Node.js no esté corrupta.",
        )
    return node_version, npm_version


def ensure_playwright_test(cwd: str) -> None:
    """Confirma que `npx playwright --version` resuelve dentro del directorio
    de trabajo dado — esto dispara la resolución/descarga normal de npx si
    @playwright/test no está instalado localmente ni en caché global."""
    npx_path = resolve_npx()
    try:
        proc = subprocess.run(
            [npx_path, "--yes", "playwright", "--version"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PlaywrightTestNotAvailableError(
            "Resolver @playwright/test vía npx tardó demasiado.",
            hint="Revisa tu conexión a internet; npx puede necesitar descargar el paquete la primera vez.",
        ) from exc
    if proc.returncode != 0:
        raise PlaywrightTestNotAvailableError(
            "No se pudo resolver @playwright/test.",
            hint=(proc.stderr or proc.stdout or "").strip()[:500]
            or "Corre 'npm install -D @playwright/test' en la carpeta de tu proyecto de pruebas.",
        )
