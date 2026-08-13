"""
browser/config_gen.py — Genera el playwright.config.ts que hace posible todo
lo demás, sin que el usuario tenga que tocar ni una línea de su .spec.ts.

v0.1.1 — punto 1 del pedido: el flujo principal de charlyWebAudit ya NO
carga la extensión CharlyAudit — se corre el spec de Playwright "a secas",
igual que cualquiera lo correría a mano, sin ningún argumento de extensión
en el navegador. `extension_path` queda como parámetro OPCIONAL (por si
se reintroduce en el futuro), pero el camino por defecto no lo usa.

Nota de diseño importante: un spec que hace `import { test } from
'@playwright/test'` (el caso normal, y el del ejemplo que se adjuntó) usa
las fixtures POR DEFECTO de Playwright Test — que crean un perfil de
navegador EFÍMERO por corrida (`browserType.launch()` + `newContext()`).

`channel`: 'chrome' para usar Google Chrome estable del sistema, o `None`
para el Chromium gestionado por Playwright (ver `browser/chromium.py` —
desde v0.1.0a2, charlyWebAudit usa exclusivamente Chrome).
`--remote-allow-origins=*`: las versiones modernas de Chrome rechazan la
conexión WebSocket de CDP con 403 Forbidden por defecto — confirmado
probando contra Chrome real — hace falta permitir el origen explícitamente
para que la telemetría del navegador (`browser/telemetry.py`) pueda
conectarse. El puerto de depuración solo escucha en localhost, así que "*"
es seguro aquí.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..constants import CDP_PORT

_TEMPLATE = """\
// Generado automaticamente por charlyWebAudit — no editar a mano.
// Se regenera en cada corrida; cualquier cambio manual se perderia.
import {{ defineConfig }} from '@playwright/test';

export default defineConfig({{
  testDir: {test_dir},
  testMatch: {test_match},
  timeout: {timeout_ms},
  reporter: [['json', {{ outputFile: {report_path} }}], ['list']],
  use: {{
    headless: false,{channel_line}
    extraHTTPHeaders: {headers},
    launchOptions: {{
      args: {launch_args},
    }},
  }},
}});
"""


def generate_playwright_config(
    *,
    spec_path: Path,
    extension_path: Path | None = None,
    headers: dict[str, str],
    cdp_port: int = CDP_PORT,
    timeout_ms: int = 120_000,
    json_report_path: Path,
    channel: str | None = None,
) -> str:
    """Devuelve el contenido de playwright.config.ts como texto. Todo valor
    dinámico se serializa con json.dumps (nunca interpolación de string
    cruda) — es la forma segura de incrustar valores arbitrarios del usuario
    (URLs, nombres de cabeceras, rutas con espacios) dentro de código TS/JS
    válido, sin arriesgarse a romper la sintaxis generada.

    `extension_path`: opcional desde v0.1.1 — el flujo principal no la usa
    (punto 1 del pedido: solo Playwright, sin la extensión). Si se pasa una
    ruta, se agregan los argumentos para cargarla (uso futuro opcional)."""
    launch_args = [
        f"--remote-debugging-port={cdp_port}",
        # Bug real corregido: Chrome moderno rechaza la conexion WebSocket de
        # CDP con 403 Forbidden por defecto ("Rejected an incoming WebSocket
        # connection...") — confirmado probando contra Chrome real. Sin esto,
        # la telemetria del navegador no podria conectarse en absoluto.
        "--remote-allow-origins=*",
        # Perfil efimero pero aislado: evita que el estado de OTRO Chrome del
        # sistema (perfil por defecto del usuario) interfiera con la corrida.
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if extension_path:
        launch_args = [
            f"--disable-extensions-except={extension_path}",
            f"--load-extension={extension_path}",
        ] + launch_args

    channel_line = f"\n    channel: {json.dumps(channel)}," if channel else ""
    return _TEMPLATE.format(
        test_dir=json.dumps(str(spec_path.parent)),
        test_match=json.dumps(spec_path.name),
        timeout_ms=timeout_ms,
        report_path=json.dumps(str(json_report_path)),
        headers=json.dumps(headers),
        launch_args=json.dumps(launch_args),
        channel_line=channel_line,
    )


def write_playwright_config(dest: Path, **kwargs) -> Path:
    dest.write_text(generate_playwright_config(**kwargs), encoding="utf-8")
    return dest
