"""
browser/config_gen.py — Genera el playwright.config.ts que hace posible todo
lo demás, sin que el usuario tenga que tocar ni una línea de su .spec.ts.

Nota de diseño importante: un spec que hace `import { test } from
'@playwright/test'` (el caso normal, y el del ejemplo que se adjuntó) usa
las fixtures POR DEFECTO de Playwright Test — que crean un perfil de
navegador EFÍMERO por corrida (`browserType.launch()` + `newContext()`).
No existe una forma de forzar un perfil persistente desde `playwright.config.ts`
sin que el spec importe un fixture personalizado — y eso violaría la regla
de no modificar el script del usuario. Por eso la extensión se carga vía
`launchOptions.args` (que sí aplica al perfil efímero de cada corrida), y la
"memoria" de la configuración de la extensión (Asistente/paleta/variables
vigiladas) la resuelve charlyWebAudit por su cuenta, re-aplicándola sobre la
extensión al inicio de cada corrida (ver runner/seed.py) en vez de confiar
en que el perfil del navegador la recuerde.
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
    headless: false,
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
    extension_path: Path,
    headers: dict[str, str],
    cdp_port: int = CDP_PORT,
    timeout_ms: int = 120_000,
    json_report_path: Path,
) -> str:
    """Devuelve el contenido de playwright.config.ts como texto. Todo valor
    dinámico se serializa con json.dumps (nunca interpolación de string
    cruda) — es la forma segura de incrustar valores arbitrarios del usuario
    (URLs, nombres de cabeceras, rutas con espacios) dentro de código TS/JS
    válido, sin arriesgarse a romper la sintaxis generada."""
    launch_args = [
        f"--disable-extensions-except={extension_path}",
        f"--load-extension={extension_path}",
        f"--remote-debugging-port={cdp_port}",
        # Perfil efimero pero aislado: evita que el estado de OTRO Chrome del
        # sistema (perfil por defecto del usuario) interfiera con la corrida.
        "--no-first-run",
        "--no-default-browser-check",
    ]
    return _TEMPLATE.format(
        test_dir=json.dumps(str(spec_path.parent)),
        test_match=json.dumps(spec_path.name),
        timeout_ms=timeout_ms,
        report_path=json.dumps(str(json_report_path)),
        headers=json.dumps(headers),
        launch_args=json.dumps(launch_args),
    )


def write_playwright_config(dest: Path, **kwargs) -> Path:
    dest.write_text(generate_playwright_config(**kwargs), encoding="utf-8")
    return dest
