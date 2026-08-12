"""
__main__.py — Punto de entrada de RedGpsWebAudit.

Ata todo lo demás en el orden que importa:

  1. Prerrequisitos (Chromium, Node) — nunca se avanza sin ellos.
  2. Menú interactivo (configurar prueba / Asistente / correr).
  3. Al correr: lanzar navegador+spec con la pestaña del spec PAUSADA,
     abrir el panel lateral como pestaña propia, y sembrar la
     configuración del Asistente/paleta/captura — todo esto SIN necesitar
     el tabId de la pestaña del spec todavía.

     Restricción real descubierta en validación: mientras la pestaña del
     spec está congelada (`waitForDebuggerOnStart`), `chrome.tabs.query()`
     no la ve — Chrome no la registra como pestaña "consultable" hasta que
     empieza a ejecutar algo. Además, el propio Playwright Test tiene un
     mecanismo interno de paciencia limitada: si el navegador queda sin
     responder demasiado tiempo, lo da por colgado y lo cierra. Por eso el
     diseño se ajustó: en vez de identificar el tabId y activar la
     grabación MIENTRAS la pestaña sigue pausada indefinidamente, se
     libera la pausa PRIMERO y, de inmediato (sondeo agresivo, sin
     esperas artificiales), se identifica el tabId y se activa la
     grabación — la ventana de riesgo se redujo de "indefinida" a
     "milisegundos de ida y vuelta CDP", que en la práctica es mucho
     menor al tiempo real que toma cargar cualquier página (DNS/TCP/TLS),
     así que en la práctica no se pierde nada del contenido real.
  4. Esperar a que el spec termine, detener la grabación, pedir análisis
     de los 15 ámbitos del Asistente, unir todo en un reporte HTML y
     ofrecer guardarlo.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
import argparse

import questionary

from .browser.chromium import ensure_chromium
from .browser.extension_page import ExtensionPage, SIDEPANEL_URL
from .browser.launcher import RunOrchestrator
from .browser.node_check import ensure_node, ensure_playwright_test
from .config import AppConfig, load_config, save_config, TestCase
from .constants import APP_NAME, APP_VERSION
from .errors import CharlyWebAuditError
from .report.builder import build_combined_report, build_summary_report, CombinedReport
from .report.html import save_report
from .runner.assistant import analyze_all_scopes
from .runner import seed
from .runner.recorder import start_recording, stop_recording, wait_until_recording
from .runner.test_exec import parse_report, wait_for_process
from .ui.menu import main_menu_loop
from .reporter import Reporter
from .ui.theme import CliReporter, QUESTIONARY_STYLE, console, print_error, print_info, print_section, print_success, print_warning


def bundled_extension_path() -> Path:
    """Ruta de la copia de CharlyAudit empaquetada dentro de RedGpsWebAudit
    (`vendor/charlyaudit/`) — funciona tanto corriendo desde código fuente
    (pip install) como desde un binario armado con PyInstaller, que extrae
    los datos empaquetados a un directorio temporal (`sys._MEIPASS`) en vez
    de vivir junto al código fuente."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "vendor" / "charlyaudit"


def _resolve_extension_path(cfg: AppConfig) -> Path:
    if cfg.browser.extension_path:
        p = Path(cfg.browser.extension_path)
        if p.is_dir() and (p / "manifest.json").is_file():
            return p
        print_warning(f"La ruta de la extensión guardada ya no existe: {p}")

    bundled = bundled_extension_path()
    if (bundled / "manifest.json").is_file():
        return bundled

    while True:
        raw = questionary.path(
            "Ruta del build de la extensión CharlyAudit (la carpeta con manifest.json):",
            style=QUESTIONARY_STYLE,
        ).ask()
        if raw is None:
            raise CharlyWebAuditError("Se requiere la ruta de la extensión para continuar.")
        p = Path(raw).expanduser().resolve()
        if (p / "manifest.json").is_file():
            cfg.browser.extension_path = str(p)
            save_config(cfg)
            return p
        print_warning(f"No se encontró manifest.json en: {p}")


async def _identify_tab_id(sidepanel: ExtensionPage, *, timeout: float = 10) -> int:
    """Identifica la pestaña del spec como "la que no es la del propio panel
    lateral". Se llama justo DESPUÉS de liberar la pausa (ver nota de diseño
    arriba): mientras la pestaña estaba congelada, chrome.tabs.query() no la
    veía en absoluto — recién se vuelve consultable cuando empieza a
    ejecutar algo. Sondeo agresivo (sin sleep artificial entre intentos, el
    propio round-trip CDP ya impone un margen) para minimizar la ventana."""
    own_id = await sidepanel.evaluate("(async () => (await chrome.tabs.getCurrent())?.id)()", await_promise=True)
    t0 = asyncio.get_event_loop().time()
    last_all: list = []
    while asyncio.get_event_loop().time() - t0 < timeout:
        all_tabs = await sidepanel.evaluate("(async () => (await chrome.tabs.query({})).map(t => t.id))()", await_promise=True)
        if not all_tabs:  # respuesta transitoria vacia/None: reintentar, no es un error duro
            await asyncio.sleep(0.1)
            continue
        last_all = all_tabs
        candidates = [t for t in all_tabs if t != own_id]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise CharlyWebAuditError(
                f"Hay más de una pestaña candidata ({candidates}) — no se puede identificar sin ambigüedad.",
                hint="Cierra otras pestañas/ventanas de Chrome antes de correr RedGpsWebAudit.",
            )
    raise CharlyWebAuditError(
        f"No se pudo identificar la pestaña del spec tras liberar la pausa (vistas: {last_all}).",
        hint="El spec pudo cerrar su propia pestaña muy rápido, o falló antes de navegar.",
    )


async def run_single_audit(
    cfg: AppConfig,
    test_case: TestCase,
    *,
    no_extension: bool = False,
    reporter: Reporter | None = None,
    ask_save_path=None,
    confirm_install=None,
) -> CombinedReport:
    # Adaptar cfg para esta prueba específica
    cfg.test.spec_path = test_case.spec_path
    cfg.test.url = test_case.url
    cfg.test.headers = test_case.headers
    
    report = await run_audit(cfg, no_extension=no_extension, reporter=reporter, ask_save_path=ask_save_path, confirm_install=confirm_install)
    
    # Asegurar guardar el JSON del reporte
    import shutil
    json_dest = f"reporte-{test_case.name}.json"
    # El reporte JSON está en orchestrator.report_json_path (disponible en run)
    # Pero no tenemos acceso fácil a 'run' aquí.
    # Tenemos que hacer que run_audit nos devuelva también la ruta del JSON.
    
    return report

async def run_audit(
    cfg: AppConfig,
    *,
    no_extension: bool = False,
    reporter: Reporter | None = None,
    ask_save_path=None,
    confirm_install=None,
) -> tuple[CombinedReport, Path]:
    """Motor de orquestación completo."""
    reporter = reporter or CliReporter()
    if ask_save_path is None:
        ask_save_path = _default_ask_save_path

    reporter.section("Prerrequisitos")
    ensure_chromium(reporter=reporter, confirm=confirm_install)
    node_v, npm_v = ensure_node()
    reporter.success(f"Node {node_v} · npm {npm_v} detectados.")

    # extension_path = _resolve_extension_path(cfg)
    extension_path = None if no_extension else _resolve_extension_path(cfg)
    spec_path = Path(cfg.test.spec_path)

    work_dir = Path(tempfile.mkdtemp(prefix="charlywebaudit-"))
    reporter.info(f"Directorio de trabajo de esta corrida: {work_dir}")

    reporter.info("Resolviendo @playwright/test (puede tardar la primera vez)…")
    ensure_playwright_test(cwd=str(spec_path.parent))

    orchestrator = RunOrchestrator(
        spec_path=spec_path,
        extension_path=extension_path,
        target_url=cfg.test.url,
        headers=cfg.test.headers,
        work_dir=work_dir,
    )

    sidepanel: ExtensionPage | None = None
    run = None
    try:
        reporter.section("Lanzando navegador + spec")
        run = await orchestrator.launch()
        reporter.success("Pestaña del spec detectada y pausada — todavía no ha navegado.")

        if extension_path:
            sidepanel = await ExtensionPage.open(run.sync.client, SIDEPANEL_URL, timeout=30)
            reporter.success("Panel lateral abierto.")

            reporter.info("Aplicando configuración de captura (variables vigiladas)…")
            await seed.seed_pre_release(sidepanel, cfg)

        # Liberar la pausa
        reporter.info("Liberando la pausa — el spec empieza a ejecutar ahora.")
        await run.paused_target.release()

        # Dar un respiro breve a Chromium para que termine de procesar la liberación
        # y estabilice la conexión antes de realizar la consulta intensiva.
        await asyncio.sleep(0.5)

        if sidepanel:
            # Identificar la pestaña mediante el panel lateral
            target_tab_id = await _identify_tab_id(sidepanel)
            reporter.info(f"Pestaña del spec identificada (tabId={target_tab_id}).")

            await start_recording(sidepanel, target_tab_id)
            await wait_until_recording(sidepanel)
            reporter.success("Grabación activa.")

            # El resto de la configuracion (Asistente IA, paleta) no afecta que
            # se captura durante la grabacion — se aplica ahora, sin presion.
            reporter.info("Aplicando configuración del Asistente IA y la paleta…")
            await seed.seed_post_release(sidepanel, cfg)
            reporter.success("Configuración completa.")
        else:
            reporter.info("Corriendo sin extensión: omitiendo grabación y asistente.")
            assistant_responses = {}

        reporter.section("Ejecutando el spec de Playwright")
        exit_code, raw_stdout = await wait_for_process(run.process, reporter=reporter, timeout=600)
        reporter.raw(raw_stdout)

        pw_result = parse_report(run.report_json_path, exit_code, raw_stdout)
        if pw_result.all_passed:
            reporter.success(f"Playwright: {pw_result.passed} caso(s) pasaron.")
        else:
            msg = f"Playwright: {pw_result.failed} caso(s) fallaron, {pw_result.passed} pasaron."
            reporter.warning(msg)
            # Si hay errores específicos, enviarlos como mensaje de error para que la GUI los resalte
            for case in pw_result.cases:
                if case.status != "passed":
                    for err in case.errors:
                        reporter.error(Exception(f"Falla en '{case.title}': {err}"))

        if sidepanel:
            await stop_recording(sidepanel)
            reporter.success("Grabación detenida.")

            reporter.section("Análisis del Asistente IA (15 ámbitos, uno a la vez)")
            assistant_responses = await analyze_all_scopes(sidepanel, source="live")
            reporter.success("Los 15 ámbitos fueron analizados.")
        else:
            assistant_responses = {}

        report = build_combined_report(
            url=cfg.test.url,
            spec_path=str(spec_path),
            playwright_result=pw_result,
            assistant_responses=assistant_responses,
        )

        default_name = f"reporte-{spec_path.stem}.html"
        dest_raw = ask_save_path(default_name)
        if dest_raw:
            dest = Path(dest_raw).expanduser().resolve()
            saved_at = save_report(report, dest)
            reporter.success(f"Reporte guardado en: {saved_at}")

        return report, run.report_json_path

    finally:
        # 1. Primero intentar cerrar recursos de navegador/CDP limpiamente
        if run is not None:
            if run.process.poll() is None:
                run.process.kill()
            
            # Cierre de CDP y recursos asociados
            try:
                await run.sync.close()
            except Exception:
                pass
        
        # 2. Finalmente cerrar el panel lateral (que depende de la conexión CDP)
        if sidepanel:
            try:
                await sidepanel.close()
            except Exception:
                pass
        
        shutil.rmtree(work_dir, ignore_errors=True)


def _default_ask_save_path(default_name: str) -> str | None:
    """Comportamiento original de la CLI (v0.0.1/v0.0.2): prompt interactivo
    de questionary. Preservado tal cual para no cambiar nada del flujo CLI
    existente."""
    dest_raw = questionary.path(
        "¿Dónde guardar el reporte HTML?", default=default_name, style=QUESTIONARY_STYLE
    ).ask()
    return dest_raw or default_name


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """CLI mínima: --version/--gui, sin dependencias extra más allá de
    argparse (librería estándar). No reemplaza el menú interactivo — cubre
    los casos donde alguien espera una respuesta no interactiva (scripts,
    verificar versión) o quiere la interfaz gráfica en vez del menú."""
    parser = argparse.ArgumentParser(prog=APP_NAME.lower(), add_help=True)
    parser.add_argument("--version", action="store_true", help="Muestra la versión y termina.")
    parser.add_argument("--gui", action="store_true", help="Abre la interfaz gráfica en vez del menú de terminal.")
    parser.add_argument("--no-extension", action="store_true", help="Corre sin cargar la extensión CharlyAudit.")
    ns = parser.parse_args(argv)
    if ns.version:
        console.print(f"{APP_NAME} v{APP_VERSION}")
        sys.exit(0)
    return ns


def main() -> None:
    ns = _parse_args(sys.argv[1:])

    if ns.gui:
        from .gui import main as gui_main  # punto de entrada seguro (ver gui/__init__.py) — misma logica que usa el entry point charlywebaudit-gui, una sola fuente de verdad

        gui_main()
        return

    def _on_run(cfg: AppConfig, test_case: TestCase | str | None = None) -> None:
        try:
            if test_case == "all":
                # Lógica para correr todo el catálogo
                results = []
                for case in cfg.test.test_cases:
                    console.print(f"[bold]Corriendo prueba:[/bold] {case.name}")
                    report = asyncio.run(run_single_audit(
                        cfg, case, 
                        no_extension=ns.no_extension, 
                        ask_save_path=lambda name: f"reporte-{case.name}.html"
                    ))
                    results.append(report)
                console.print("[bold]Todas las pruebas finalizadas. Generando informe general...[/bold]")
                # Generar informe general
                summary_html = build_summary_report(results)
                with open("informe-general.html", "w", encoding="utf-8") as f:
                    f.write(summary_html)
                console.print("[green]Informe general guardado en: informe-general.html[/green]")
            elif isinstance(test_case, TestCase):
                asyncio.run(run_single_audit(cfg, test_case, no_extension=ns.no_extension))
            else:
                asyncio.run(run_audit(cfg, no_extension=ns.no_extension))
        except CharlyWebAuditError as exc:
            print_error(exc)
        except KeyboardInterrupt:
            console.print("\n[yellow]Corrida cancelada por el usuario.[/]")
        except Exception as exc:  # noqa: BLE001 — ultima linea de defensa, nunca un traceback crudo
            print_error(exc)

    try:
        main_menu_loop(_on_run)
    except CharlyWebAuditError as exc:
        print_error(exc)
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Hasta luego.[/]")
        sys.exit(0)


if __name__ == "__main__":
    main()
