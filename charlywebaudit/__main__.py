"""
__main__.py — Punto de entrada de charlyWebAudit.

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

import questionary
import websockets

from .browser.chromium import ensure_chromium
from .browser.extension_page import ExtensionPage, SIDEPANEL_URL
from .browser.launcher import RunOrchestrator
from .browser.node_check import ensure_node, ensure_playwright_test
from .config import AppConfig, load_config, save_config
from .constants import APP_NAME, APP_VERSION
from .errors import CharlyWebAuditError
from .report.builder import build_combined_report, CombinedReport
from .report.html import save_report
from .runner.assistant import analyze_all_scopes
from .runner import seed
from .runner.recorder import start_recording, stop_recording, wait_until_recording
from .runner.test_exec import parse_report, wait_for_process
from .ui.menu import main_menu_loop
from .reporter import Reporter
from .ui.theme import CliReporter, QUESTIONARY_STYLE, console, print_error, print_info, print_section, print_success, print_warning


def bundled_extension_path() -> Path:
    """Ruta de la copia de CharlyAudit empaquetada dentro de charlyWebAudit
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
                hint="Cierra otras pestañas/ventanas de Chrome antes de correr charlyWebAudit.",
            )
    raise CharlyWebAuditError(
        f"No se pudo identificar la pestaña del spec tras liberar la pausa (vistas: {last_all}).",
        hint="El spec pudo cerrar su propia pestaña muy rápido, o falló antes de navegar.",
    )


async def run_audit(
    cfg: AppConfig,
    *,
    reporter: Reporter | None = None,
    ask_save_path=None,
    confirm_install=None,
    test_name: str | None = None,
) -> CombinedReport:
    """Motor de orquestación completo — ya no sabe nada de `rich` ni de
    cómo se pregunta dónde guardar el reporte, ni cómo se confirma instalar
    Chromium: todo eso vive detrás de `reporter` (ver reporter.py),
    `ask_save_path` y `confirm_install`, para que la GUI (v0.0.5) pueda
    reusar este mismo motor sin duplicar nada.

    `ask_save_path(default_name: str) -> str | None`: por defecto (CLI) usa
    el mismo prompt de `questionary` de siempre. La GUI puede pasar su
    propio diálogo de "Guardar como", o `None`/una función que devuelve
    `None` para no guardar y solo quedarse con el `CombinedReport` devuelto
    (para mostrarlo en pantalla, por ejemplo).

    `confirm_install(question: str) -> bool`: por defecto (CLI) un prompt de
    `questionary`. Bug real corregido en v0.0.8: `questionary` no es seguro
    de llamar desde el hilo en segundo plano de `AsyncBridge` (ver
    `browser/chromium.py`) — la GUI DEBE pasar su propia versión thread-safe
    (ver `gui/dialogs.py`), nunca dejar el valor por defecto.

    `test_name`: punto 5 del pedido v0.1.0a — el nombre bajo el que esta
    corrida queda registrada en el historial/dashboard. Si no se da, se usa
    el nombre del archivo .spec.ts (siempre hay un nombre legible).
    """
    import time as _time
    from .history import now_iso

    run_started_at = _time.monotonic()
    run_started_at_iso = now_iso()
    reporter = reporter or CliReporter()
    if ask_save_path is None:
        ask_save_path = _default_ask_save_path

    reporter.section("Prerrequisitos")
    ensure_chromium(reporter=reporter, confirm=confirm_install)
    node_v, npm_v = ensure_node()
    reporter.success(f"Node {node_v} · npm {npm_v} detectados.")

    extension_path = _resolve_extension_path(cfg)
    spec_path = Path(cfg.test.spec_path)
    resolved_test_name = test_name or spec_path.stem

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
    run = None  # bug real corregido en v0.0.2: si launch() fallaba, el finally
    # de abajo intentaba usar `run` sin haberse asignado nunca, enmascarando
    # el error original con un NameError y dejando work_dir sin limpiar.
    try:
        reporter.section("Lanzando navegador + spec")
        run = await orchestrator.launch()
        reporter.success("Pestaña del spec detectada y pausada — todavía no ha navegado.")

        sidepanel = await ExtensionPage.open(run.sync.client, SIDEPANEL_URL, timeout=30)
        reporter.success("Panel lateral abierto.")

        reporter.info("Aplicando configuración de captura (variables vigiladas)…")
        await seed.seed_pre_release(sidepanel, cfg)

        # v0.1.0a: la navegación real del spec está retenida a nivel de RED
        # (dominio Fetch, ver browser/cdp_sync.py) — no por el estado
        # "pausado" del target, que resultó no ser confiable (Playwright
        # Test libera su propia sesión de forma independiente, sin importar
        # la nuestra — ver el docstring de cdp_sync.py para el diagnóstico
        # completo). Esto es más robusto que el mecanismo anterior, pero la
        # secuencia de identificar la pestaña + activar grabación sigue
        # siendo lo primero que se hace tras liberar, sin esperas
        # artificiales de por medio.
        reporter.info("Liberando la navegación retenida — el spec empieza a cargar la página ahora.")
        await run.paused_target.release()

        # v0.1.0a: degradación elegante desde aquí — un spec muy corto puede
        # terminar (y Playwright Test cierra TODO el navegador al hacerlo,
        # no solo su contexto) antes de que la identificación de pestaña +
        # activación de grabación termine. Si la conexión se cae en
        # cualquier punto desde aquí, igual esperamos el resultado real de
        # Playwright (wait_for_process no depende de esta conexión) — el
        # usuario recibe un reporte con los resultados de Playwright y un
        # aviso claro, nunca un traceback crudo.
        recording_active = False
        try:
            target_tab_id = await _identify_tab_id(sidepanel)
            reporter.info(f"Pestaña del spec identificada (tabId={target_tab_id}).")

            await start_recording(sidepanel, target_tab_id)
            await wait_until_recording(sidepanel)
            reporter.success("Grabación activa.")
            recording_active = True

            # El resto de la configuracion (Asistente IA, paleta) no afecta que
            # se captura durante la grabacion — se aplica ahora, sin presion.
            reporter.info("Aplicando configuración del Asistente IA y la paleta…")
            await seed.seed_post_release(sidepanel, cfg)
            reporter.success("Configuración completa.")
        except (websockets.exceptions.ConnectionClosed, ConnectionError, OSError) as exc:
            reporter.warning(
                "El navegador se cerró antes de poder activar la grabación de CharlyAudit "
                "(el spec terminó más rápido de lo que tomó identificar la pestaña — más "
                "probable en specs muy cortos). El reporte va a incluir los resultados de "
                f"Playwright, sin grabación ni análisis del Asistente. Detalle: {exc}"
            )

        reporter.section("Ejecutando el spec de Playwright")
        exit_code, raw_stdout = await wait_for_process(run.process, timeout=600)
        reporter.raw(raw_stdout)

        pw_result = parse_report(run.report_json_path, exit_code, raw_stdout)
        if pw_result.all_passed:
            reporter.success(f"Playwright: {pw_result.passed} caso(s) pasaron.")
        else:
            reporter.warning(f"Playwright: {pw_result.failed} caso(s) fallaron, {pw_result.passed} pasaron.")

        # Mismo criterio de degradación elegante que arriba — ver docstring
        # del módulo cdp_sync.py para el diagnóstico completo de por qué el
        # navegador puede cerrarse en esta ventana.
        assistant_responses: dict[str, str] = {}
        if recording_active:
            try:
                await stop_recording(sidepanel)
                reporter.success("Grabación detenida.")

                reporter.section("Análisis del Asistente IA (15 ámbitos, uno a la vez)")
                assistant_responses = await analyze_all_scopes(sidepanel, source="live")
                reporter.success("Los 15 ámbitos fueron analizados.")
            except (websockets.exceptions.ConnectionClosed, ConnectionError, OSError) as exc:
                reporter.warning(
                    "El navegador se cerró antes de poder completar el análisis del Asistente "
                    "(Playwright Test cierra el navegador apenas termina el spec — a veces ocurre "
                    "más rápido de lo que el Asistente tarda en responder). El reporte incluye los "
                    f"resultados de Playwright, sin el análisis de los 15 ámbitos. Detalle: {exc}"
                )

        report = build_combined_report(
            url=cfg.test.url,
            spec_path=str(spec_path),
            playwright_result=pw_result,
            assistant_responses=assistant_responses,
        )

        # Punto 4 del pedido v0.1.0a ("garantiza los reportes completos"):
        # el reporte se guarda SIEMPRE en un directorio propio, sin depender
        # de que el usuario recuerde pedirlo — ask_save_path (abajo) sigue
        # ofreciendo una copia adicional donde el usuario quiera, pero esta
        # copia interna es la que alimenta el historial/dashboard (punto 5),
        # que necesita poder abrir el detalle completo de cualquier corrida
        # pasada, se haya guardado explícitamente o no.
        from .history import RunRecord, append_run_record, make_run_id, reports_dir as history_reports_dir

        reports_dir = history_reports_dir()
        auto_report_name = f"{resolved_test_name}-{run_started_at_iso}.html".replace(" ", "_").replace(":", "-")
        auto_report_path = reports_dir / auto_report_name
        try:
            save_report(report, auto_report_path)
        except OSError:
            auto_report_path = None  # best-effort: el historial puede quedar sin enlace a detalle, no debe romper la corrida

        duration_ms = int((_time.monotonic() - run_started_at) * 1000)
        append_run_record(
            RunRecord(
                id=make_run_id(),
                test_name=resolved_test_name,
                spec_path=str(spec_path),
                url=cfg.test.url,
                started_at=run_started_at_iso,
                duration_ms=duration_ms,
                passed=pw_result.passed,
                failed=pw_result.failed,
                skipped=pw_result.skipped,
                all_passed=pw_result.all_passed,
                assistant_analysis_complete=bool(assistant_responses),
                report_path=str(auto_report_path) if auto_report_path else None,
            )
        )

        default_name = f"reporte-{spec_path.stem}.html"
        dest_raw = ask_save_path(default_name)
        if dest_raw:
            dest = Path(dest_raw).expanduser().resolve()
            saved_at = save_report(report, dest)
            reporter.success(f"Reporte guardado en: {saved_at}")

        return report

    finally:
        if sidepanel:
            try:
                await sidepanel.close()
            except (websockets.exceptions.ConnectionClosed, ConnectionError, OSError):
                pass  # el navegador ya se cerro por su cuenta — no hay nada que cerrar
        if run is not None:
            try:
                await run.sync.close()
            except (websockets.exceptions.ConnectionClosed, ConnectionError, OSError):
                pass
            if run.process.poll() is None:
                run.process.kill()
            run.config_path.unlink(missing_ok=True)  # vive en el proyecto del usuario, no en work_dir (ver launcher.py)
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
    import argparse

    parser = argparse.ArgumentParser(prog=APP_NAME.lower(), add_help=True)
    parser.add_argument("--version", action="store_true", help="Muestra la versión y termina.")
    parser.add_argument("--gui", action="store_true", help="Abre la interfaz gráfica en vez del menú de terminal.")
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

    def _on_run(cfg: AppConfig) -> None:
        try:
            asyncio.run(run_audit(cfg))
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
