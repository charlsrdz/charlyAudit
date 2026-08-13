"""
browser/launcher.py — Lanza el navegador + spec, punto 1 del pedido v0.1.1:
solo Playwright, sin ninguna orquestación de extensión.

v0.1.1 — REDISEÑO radical, a pedido explícito ("al ejecutar una prueba
solo debe abrir el navegador y omitir todo lo que tenga que ver con la
extensión"). Hasta v0.1.0a2, `RunOrchestrator.launch()` establecía SU
PROPIA conexión CDP inmediatamente después de lanzar el proceso, para
pausar la primera pestaña y sincronizar con la extensión antes de dejarla
navegar — ese mecanismo (con toda su complejidad: pausa por depurador,
intercepción de red, etc.) es precisamente la causa raíz más probable del
error real reportado en producción ("No se pudo conectar al navegador
orquestado por CDP") con una versión más nueva de Node (v24.19.0): esa
conexión CDP inmediata competía con el propio arranque del navegador de
formas que en versiones anteriores de Node no se manifestaban.

Ahora `launch()` simplemente genera el config y lanza el subproceso de
Node — nada más. No abre ninguna conexión CDP propia. Quien necesite
observar el navegador (telemetría, ver `browser/telemetry.py`) se conecta
por su cuenta, DESPUÉS, con su propia lógica de espera/reintento — sin
que el lanzamiento en sí dependa de que esa conexión tenga éxito.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..constants import CDP_PORT
from ..errors import SpecNotFoundError
from .config_gen import write_playwright_config
from .platform_utils import find_xvfb_run, needs_virtual_display, resolve_npx


@dataclass
class LaunchedRun:
    """Todo lo que el resto del flujo necesita mientras la corrida está viva."""

    process: subprocess.Popen
    work_dir: Path
    report_json_path: Path
    config_path: Path
    """Ruta del playwright.config.ts generado — vive en spec_path.parent
    (Node resuelve módulos desde ahí, no desde work_dir — ver nota en
    `launch()`), así que necesita limpiarse por separado al terminar la
    corrida en vez de quedar cubierto por el rmtree de work_dir."""
    cdp_port: int
    """Puerto donde el navegador expone su protocolo de depuración — quien
    quiera telemetría (`browser/telemetry.py`) se conecta ahí por su
    cuenta; `launch()` en sí no lo usa para nada."""


class RunOrchestrator:
    def __init__(
        self,
        *,
        spec_path: Path,
        extension_path: Path | None = None,
        target_url: str,
        headers: dict[str, str],
        work_dir: Path,
        cdp_port: int = CDP_PORT,
        channel: str | None = None,
    ) -> None:
        if not spec_path.is_file():
            raise SpecNotFoundError(f"No se encontró el spec: {spec_path}")
        if extension_path is not None and not extension_path.is_dir():
            raise SpecNotFoundError(f"No se encontró el build de la extensión en: {extension_path}")

        self.spec_path = spec_path
        self.extension_path = extension_path
        """None en el flujo principal desde v0.1.1 (punto 1 del pedido) —
        se conserva la opción por si se reintroduce en el futuro."""
        self.target_url = target_url
        self.headers = headers
        self.work_dir = work_dir
        self.cdp_port = cdp_port
        self.channel = channel
        """'chrome' para usar Google Chrome del sistema, None para el
        Chromium gestionado por Playwright — ver browser/chromium.py,
        ensure_browser(), para la decisión."""

    def launch(self) -> LaunchedRun:
        """Genera el config y lanza el subproceso de Node — nada más. No es
        `async` a propósito: no hay ninguna espera de red involucrada aquí,
        solo escribir un archivo y lanzar un proceso (ambos síncronos y
        rápidos); la espera real (a que el spec termine) la maneja
        `runner/test_exec.wait_for_process`, por separado."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        report_json_path = self.work_dir / "playwright-report.json"
        # El archivo de configuracion vive en spec_path.parent, no en
        # work_dir: Node resuelve `require('@playwright/test')` (y
        # cualquier import) desde el directorio del PROPIO ARCHIVO que hace
        # el require, buscando node_modules hacia arriba en el arbol de
        # directorios, nunca desde el cwd del proceso — un config.ts fuera
        # de esa jerarquia (como work_dir, tipicamente
        # /tmp/charlywebaudit-XXXXXX) fallaria con MODULE_NOT_FOUND a menos
        # que /tmp resultara ser, por casualidad, ancestro del proyecto del
        # usuario (confirmado reproduciendo el error real). El JSON del
        # reporte SI puede seguir en work_dir (se referencia por ruta
        # absoluta dentro del config).
        config_path = self.spec_path.parent / ".charlywebaudit.config.ts"
        write_playwright_config(
            config_path,
            spec_path=self.spec_path,
            extension_path=self.extension_path,
            headers=self.headers,
            cdp_port=self.cdp_port,
            json_report_path=report_json_path,
            channel=self.channel,
        )

        # El subproceso de Node corre exactamente lo que el usuario correría
        # a mano — sin ninguna modificacion a su spec. Corre desde la
        # carpeta del PROPIO spec (no desde nuestro work_dir) por la misma
        # razon de resolucion de modulos explicada arriba.
        npx_path = resolve_npx()  # nunca el nombre desnudo "npx" (bug real en Windows, ver platform_utils.py)
        cmd = [npx_path, "playwright", "test", "--config", str(config_path)]
        if needs_virtual_display():
            xvfb = find_xvfb_run()
            if xvfb:
                # Linux sin entorno grafico, pero con Xvfb disponible: se
                # envuelve el comando automaticamente — sin esto, un
                # servidor/CI sin display fallaria en cada corrida
                # (headless:false es necesario; ver docstring del modulo).
                cmd = [xvfb, "-a", *cmd]
        process = subprocess.Popen(
            cmd,
            cwd=str(self.spec_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        return LaunchedRun(
            process=process,
            work_dir=self.work_dir,
            report_json_path=report_json_path,
            config_path=config_path,
            cdp_port=self.cdp_port,
        )
