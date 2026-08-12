"""
browser/launcher.py — Orquesta todo lo validado en cdp_sync.py + config_gen.py:

  1. Genera playwright.config.ts apuntando al spec del usuario, con la
     extensión cargada y el puerto CDP expuesto.
  2. Lanza `npx playwright test` como subproceso (Node) — el mismo camino
     que el usuario correría a mano, sin tocar su spec.
  3. Se conecta por CDP en paralelo y arma la pausa de sincronización.
  4. Identifica la pestaña del spec y la mantiene pausada hasta que el
     llamador confirme que ya está lista (grabación activa).

VALIDADO end-to-end contra el spec real adjuntado por el usuario y el build
real de CharlyAudit: el service worker de la extensión aparece exactamente
en `chrome-extension://<ID_FIJO>/...`, y el test corre contra el sitio real
de producción (encontró incluso una discrepancia real de mayúsculas en el
propio spec del usuario, prueba de que la ejecución es genuina).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..constants import CDP_PORT
from ..errors import BrowserLaunchError, SpecNotFoundError, TargetTabNotFoundError
from .cdp_sync import BrowserSync, PausedTarget
from .config_gen import write_playwright_config
from .platform_utils import find_xvfb_run, needs_virtual_display, resolve_npx


@dataclass
class LaunchedRun:
    """Todo lo que el resto del flujo necesita mientras la corrida está viva."""

    process: subprocess.Popen
    sync: BrowserSync
    paused_target: PausedTarget
    work_dir: Path
    report_json_path: Path
    config_path: Path
    """Ruta del playwright.config.ts generado — vive en spec_path.parent
    (ver nota real en RunOrchestrator.launch), no en work_dir, así que
    necesita limpiarse por separado al terminar la corrida en vez de
    quedar cubierto por el rmtree de work_dir."""


class RunOrchestrator:
    def __init__(
        self,
        *,
        spec_path: Path,
        extension_path: Path,
        target_url: str,
        headers: dict[str, str],
        work_dir: Path,
        cdp_port: int = CDP_PORT,
    ) -> None:
        if not spec_path.is_file():
            raise SpecNotFoundError(f"No se encontró el spec: {spec_path}")
        if not extension_path.is_dir():
            raise BrowserLaunchError(f"No se encontró el build de la extensión en: {extension_path}")

        self.spec_path = spec_path
        self.extension_path = extension_path
        self.target_url = target_url
        self.headers = headers
        self.work_dir = work_dir
        self.cdp_port = cdp_port

    async def launch(self) -> LaunchedRun:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        report_json_path = self.work_dir / "playwright-report.json"
        # Bug real corregido en v0.1.0a: el archivo de configuracion se
        # escribia en self.work_dir (un directorio temporal separado,
        # fuera del proyecto del usuario) — pero Node resuelve
        # `require('@playwright/test')` (y cualquier import) desde el
        # directorio del PROPIO ARCHIVO que hace el require, buscando
        # node_modules hacia arriba en el arbol de directorios, nunca desde
        # el cwd del proceso. Un config.ts fuera de esa jerarquia (como
        # work_dir, tipicamente /tmp/charlywebaudit-XXXXXX) SIEMPRE fallaba
        # con MODULE_NOT_FOUND, a menos que /tmp resultara ser, por
        # casualidad, ancestro del proyecto del usuario. Confirmado
        # reproduciendo el error real antes de este fix. El JSON del
        # reporte SI puede seguir en work_dir (se referencia por ruta
        # absoluta dentro del config, Node lo escribe ahi sin problema —
        # solo el propio archivo de config necesita vivir junto al proyecto).
        config_path = self.spec_path.parent / ".charlywebaudit.config.ts"
        write_playwright_config(
            config_path,
            spec_path=self.spec_path,
            extension_path=self.extension_path,
            headers=self.headers,
            cdp_port=self.cdp_port,
            json_report_path=report_json_path,
        )

        # El subproceso de Node corre exactamente lo que el usuario correría
        # a mano — sin ninguna modificacion a su spec. Corre desde la carpeta
        # del PROPIO spec (no desde nuestro work_dir): Node resuelve
        # modulos (incluido @playwright/test) desde el directorio del
        # archivo hacia arriba, no desde el cwd del proceso — el proyecto
        # del usuario ya tiene @playwright/test instalado ahi, que es de
        # donde debe resolverse (y por eso, ahora, el propio config.ts
        # tambien vive ahi — ver nota arriba).
        npx_path = resolve_npx()  # nunca el nombre desnudo "npx" (bug real en Windows, ver platform_utils.py)
        cmd = [npx_path, "playwright", "test", "--config", str(config_path)]
        if needs_virtual_display():
            xvfb = find_xvfb_run()
            if xvfb:
                # Linux sin entorno grafico, pero con Xvfb disponible: se
                # envuelve el comando automaticamente. CharlyAudit necesita
                # headless:false (las extensiones no cargan de forma fiable
                # en modo headless puro) — sin esto, un servidor/CI sin
                # display fallaria en cada corrida.
                cmd = [xvfb, "-a", *cmd]
        process = subprocess.Popen(
            cmd,
            cwd=str(self.spec_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        sync = BrowserSync(self.cdp_port)
        try:
            await sync.connect()
        except Exception as exc:
            process.kill()
            raise BrowserLaunchError(
                "No se pudo conectar al navegador orquestado por CDP.",
                hint="Revisa que el puerto no esté en uso por otro proceso.",
            ) from exc

        try:
            paused_target = await self._wait_for_correct_tab(sync, process)
        except Exception:
            # _wait_for_correct_tab ya mata el proceso en su propio camino de
            # fallo, pero la conexion CDP (sync) quedaba abierta — fuga real
            # corregida en v0.0.2.
            await sync.close()
            if process.poll() is None:
                process.kill()
            raise

        return LaunchedRun(
            process=process,
            sync=sync,
            paused_target=paused_target,
            work_dir=self.work_dir,
            report_json_path=report_json_path,
            config_path=config_path,
        )

    async def _wait_for_correct_tab(self, sync: BrowserSync, process: subprocess.Popen) -> PausedTarget:
        """Espera la pestaña nueva del spec. Como queda pausada ANTES de
        navegar, no podemos confirmar la URL leyendo el DOM todavía — pero sí
        podemos confirmar que es la ÚNICA pestaña nueva que aparece (la
        primera que abre el spec del usuario es, por construcción, la que
        nos interesa: es la que hace el primer `page.goto`)."""
        try:
            target = await sync.wait_for_new_page(timeout=45)
        except TargetTabNotFoundError:
            process.kill()
            raise
        if process.poll() is not None:
            # El proceso de Node ya termino (crash temprano) antes de que
            # pudieramos siquiera pausar su pestana.
            raise BrowserLaunchError(
                "El proceso de Playwright Test terminó antes de abrir una pestaña.",
                hint="Revisa la salida del proceso para más detalle (posible error de sintaxis en el spec).",
            )
        return target

    @staticmethod
    async def confirm_navigated_to_target(target: PausedTarget, expected_url: str, timeout: float = 15) -> bool:
        """Se llama DESPUÉS de liberar la pausa: confirma que la pestaña
        efectivamente navegó hacia la URL esperada (o algo que empieza igual,
        por si hay redirecciones de protocolo/trailing slash)."""
        import time

        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                current = await target.evaluate("location.href")
            except Exception:
                current = None
            if current and (current.startswith(expected_url) or expected_url.startswith(current.split("?")[0])):
                return True
            await asyncio.sleep(0.2)
        return False
