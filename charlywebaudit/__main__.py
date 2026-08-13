"""
__main__.py — Punto de entrada de charlyWebAudit.

v0.1.1 — motor simplificado: sin la extensión, `run_audit()` lanza el
navegador, corre el spec de Playwright tal cual, y arma el reporte —
telemetría del navegador incluida (ver `browser/telemetry.py`).

v0.1.5 — la extensión CharlyAudit se reintegra como OPCIÓN
(`cfg.test.use_extension`, ver `config.py`) — nunca como comportamiento
por defecto obligatorio, para no arriesgar la confiabilidad ya lograda
del flujo simple. Cuando se pide, `run_audit()` hace lo que hacía hasta
v0.1.0a2: pausa la primera navegación (a nivel de red, no de depurador —
ver `browser/cdp_sync.py` para el porqué), abre el panel lateral, siembra
la configuración (incluido el dominio permitido, extraído de la URL de
la prueba — punto 1 del pedido), graba, y al terminar el spec, pide el
análisis de los 15 ámbitos y extrae los KPIs de la sesión — todo se une
en el mismo reporte final (`extension_used=True`).
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time as _time
from pathlib import Path

import questionary
from websockets.exceptions import ConnectionClosed

from .browser.chromium import ensure_browser
from .browser.cdp_sync import BrowserSync
from .browser.extension_page import ExtensionPage, SIDEPANEL_URL
from .browser.launcher import RunOrchestrator
from .dependencies import ensure_dependencies
from .browser.telemetry import BrowserTelemetry
from .config import AppConfig, load_config, save_config
from .constants import APP_NAME, APP_VERSION, VENDOR_EXTENSION_DIR
from .ai_playwright import analyze_playwright_run
from .errors import AssistantError, CharlyWebAuditError, ExtensionNotFoundError
from .history import RunRecord, append_run_record, make_run_id, now_iso, reports_dir as history_reports_dir
from .report.builder import build_combined_report, CombinedReport
from .report.html import save_report
from .runner.assistant import analyze_all_scopes, get_kpis_html
from .runner.recorder import start_recording, stop_recording, wait_until_recording
from .runner.seed import seed_domain_allowlist, seed_pre_release, seed_post_release
from .runner.test_exec import parse_report, wait_for_process
from .ui.menu import main_menu_loop
from .reporter import Reporter
from .ui.theme import CliReporter, QUESTIONARY_STYLE, console, print_error


def _resolve_extension_path() -> Path:
    """Ruta al build de la extensión CharlyAudit incluido con
    charlyWebAudit — mismo directorio tanto en desarrollo como empaquetado
    (ver build/build_installer.py, --add-data)."""
    if not VENDOR_EXTENSION_DIR.is_dir():
        raise ExtensionNotFoundError(
            f"No se encontró el build de la extensión CharlyAudit en: {VENDOR_EXTENSION_DIR}",
            hint="Esto no debería pasar en una instalación normal — reinstala charlyWebAudit.",
        )
    return VENDOR_EXTENSION_DIR


async def _identify_tab_id(ext: ExtensionPage, *, timeout: float = 10) -> int:
    """Encuentra el `tabId` de la pestaña del spec (no la del propio panel
    lateral) — la única pestaña de tipo normal, sin URL de extensión,
    entre las que `chrome.tabs.query` puede ver.

    Hallazgo real, investigado a fondo (v0.1.5): la pestaña del spec vive
    en un `browserContextId` DISTINTO al del panel lateral de la extensión
    — confirmado con CDP real (`Target.getTargets`). `chrome.tabs.query`,
    llamado desde el panel lateral, está limitado al mismo contexto de
    navegador donde la extensión corre — no puede ver tabs en un contexto
    distinto, es una restricción de aislamiento de Chrome, no un problema
    de sincronización o de timing. En la práctica, esto significa que la
    grabación de CharlyAudit puede no lograr identificar la pestaña del
    spec — si eso pasa, el resto de la corrida sigue con normalidad
    (degradación elegante, ver `run_audit`), solo sin grabación ni
    análisis del Asistente para esa corrida específica."""
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout:
        tab_id = await ext.evaluate(
            "(async () => { "
            "const tabs = await chrome.tabs.query({}); "
            "const t = tabs.find(t => t.url && !t.url.startsWith('chrome-extension://') && !t.url.startsWith('chrome://')); "
            "return t ? t.id : null; "
            "})()",
            await_promise=True,
        )
        if tab_id is not None:
            return tab_id
        await asyncio.sleep(0.3)
    raise ExtensionNotFoundError(
        "No se pudo identificar la pestaña del spec desde la extensión.",
        hint="La pestaña del spec y el panel lateral de la extensión pueden estar en contextos de "
        "navegador distintos (una restricción real de aislamiento de Chrome, confirmada con CDP) — "
        "la grabación de CharlyAudit no está disponible para esta corrida específica. El resto del "
        "reporte (resultados de Playwright) sigue siendo válido.",
    )


async def run_audit(
    cfg: AppConfig,
    *,
    reporter: Reporter | None = None,
    ask_save_path=None,
    confirm_install=None,
    test_name: str | None = None,
) -> CombinedReport:
    """Motor de orquestación — ver docstring del módulo para el rediseño de
    v0.1.1. `reporter`/`ask_save_path`/`confirm_install`/`test_name`: igual
    que en versiones anteriores, para que la GUI reuse este mismo motor sin
    duplicar nada (`confirm_install` ya no tiene efecto desde v0.1.0a2 —
    `ensure_browser()` no instala nada automáticamente, se conserva el
    parámetro solo por compatibilidad de firma)."""
    run_started_at = _time.monotonic()
    run_started_at_iso = now_iso()
    reporter = reporter or CliReporter()
    if ask_save_path is None:
        ask_save_path = _default_ask_save_path

    spec_path = Path(cfg.test.spec_path)
    resolved_test_name = test_name or spec_path.stem

    use_extension = cfg.test.use_extension

    reporter.section("Prerrequisitos")
    try:
        channel = ensure_browser(reporter=reporter, confirm=confirm_install, need_extension=use_extension)
    except CharlyWebAuditError as exc:
        if not use_extension:
            raise  # sin extension de por medio, esto SI es bloqueante (no hay navegador)
        reporter.warning(
            f"No se pudo preparar el navegador para la extensión CharlyAudit ({exc.message}) — "
            "la corrida sigue solo con Playwright, sin la extensión."
        )
        use_extension = False
        channel = ensure_browser(reporter=reporter, confirm=confirm_install, need_extension=False)
    # Validación completa de dependencias (Python/Node/Chrome/@playwright-test
    # local) — con instalación en vivo ofrecida donde es segura y contenida
    # (ver dependencies.py). Reemplaza el chequeo anterior
    # (`ensure_playwright_test`, que solo confirmaba que el CLI de
    # 'npx playwright' corriera — un bug real reportado en producción:
    # eso puede funcionar vía una instalación GLOBAL de @playwright/test,
    # mientras que el config que generamos, que vive junto al spec, solo
    # resuelve el paquete si está instalado LOCALMENTE).
    ensure_dependencies(spec_dir=str(spec_path.parent), reporter=reporter, confirm=confirm_install)

    work_dir = Path(tempfile.mkdtemp(prefix="charlywebaudit-"))
    reporter.info(f"Directorio de trabajo de esta corrida: {work_dir}")

    extension_path = None
    if use_extension:
        try:
            extension_path = _resolve_extension_path()
        except CharlyWebAuditError as exc:
            reporter.warning(f"No se pudo preparar la extensión CharlyAudit ({exc.message}) — la corrida sigue solo con Playwright.")
            use_extension = False
    orchestrator = RunOrchestrator(
        spec_path=spec_path,
        extension_path=extension_path,
        target_url=cfg.test.url,
        headers=cfg.test.headers,
        work_dir=work_dir,
        channel=channel,
    )

    run = None
    telemetry: BrowserTelemetry | None = None
    sync: BrowserSync | None = None
    sidepanel: ExtensionPage | None = None
    recording_active = False
    try:
        reporter.section("Lanzando navegador + spec")
        run = orchestrator.launch()
        reporter.success(f"Navegador lanzado (PID {run.process.pid}) — el spec ya está corriendo.")

        if use_extension:
            # v0.1.5: la navegación real del spec se retiene a nivel de RED
            # (dominio Fetch, no el estado "pausado" del target — ver el
            # docstring de cdp_sync.py para el diagnóstico completo de por
            # qué el mecanismo anterior, basado en depurador, no era
            # confiable) mientras se abre el panel lateral y se siembra la
            # configuración de captura, incluido el dominio permitido
            # (punto 1 del pedido).
            sync = BrowserSync(run.cdp_port)
            await sync.connect()
            paused_target = await sync.wait_for_new_page(timeout=45)

            sidepanel = await ExtensionPage.open(sync.client, SIDEPANEL_URL, timeout=30)
            reporter.success("Panel lateral de CharlyAudit abierto.")

            reporter.info("Aplicando dominio permitido y configuración de captura…")
            await seed_domain_allowlist(sidepanel, cfg)
            await seed_pre_release(sidepanel, cfg)

            reporter.info("Liberando la navegación retenida — el spec empieza a cargar la página ahora.")
            await paused_target.release()

            try:
                target_tab_id = await _identify_tab_id(sidepanel)
                reporter.info(f"Pestaña del spec identificada (tabId={target_tab_id}).")
                await start_recording(sidepanel, target_tab_id)
                await wait_until_recording(sidepanel)
                reporter.success("Grabación de CharlyAudit activa.")
                recording_active = True

                reporter.info("Aplicando configuración del Asistente IA y la paleta…")
                await seed_post_release(sidepanel, cfg)
            except ExtensionNotFoundError as exc:
                reporter.warning(
                    f"{exc.message} {exc.hint or ''}"
                )
            except (ConnectionClosed, ConnectionError, OSError) as exc:
                reporter.warning(
                    "El navegador se cerró antes de poder activar la grabación de CharlyAudit "
                    f"(el spec terminó más rápido de lo esperado). El reporte va a incluir los "
                    f"resultados de Playwright, sin grabación ni análisis del Asistente. Detalle: {exc}"
                )

        # Telemetria pasiva (punto 3 del pedido) — se conecta por su cuenta,
        # con su propia espera/reintento; si no logra conectar, no bloquea
        # la corrida (solo significa que no hay telemetria que ofrecer).
        # Es una conexion CDP SEPARADA de `sync` (usada arriba solo para la
        # extension) — CDP admite varios clientes simultaneos sin problema.
        telemetry = BrowserTelemetry(run.cdp_port, reporter=reporter)
        telemetry_connected = await telemetry.start()
        if telemetry_connected:
            reporter.info("Telemetría del navegador conectada — se va a reportar si se cierra de forma inesperada.")
        else:
            reporter.warning("No se pudo conectar la telemetría del navegador (la corrida continúa igual, sin ese dato).")

        reporter.section("Ejecutando el spec de Playwright")
        exit_code, raw_stdout = await wait_for_process(run.process, timeout=600)
        reporter.raw(raw_stdout)

        pw_result = parse_report(run.report_json_path, exit_code, raw_stdout)
        if pw_result.all_passed:
            reporter.success(f"Playwright: {pw_result.passed} caso(s) pasaron.")
        else:
            reporter.warning(f"Playwright: {pw_result.failed} caso(s) fallaron, {pw_result.passed} pasaron.")

        # Puntos 1 y 2 del pedido: análisis por IA de los resultados de
        # Playwright, llamando directamente a la API del proveedor
        # configurado — NUNCA vía la extensión (ver ai_playwright.py), así
        # que funciona aunque CharlyAudit no esté disponible en este
        # sistema. Punto 3: un fallo acá (sin credenciales, sin red, la
        # API caída) nunca debe interrumpir la corrida — el resultado real
        # de Playwright ya está confirmado antes de intentar esto.
        ai_analysis_html: str | None = None
        if cfg.assistant.configured:
            reporter.section("Análisis por IA del resultado de Playwright")
            try:
                ai_analysis_html = await analyze_playwright_run(
                    cfg.assistant, pw_result=pw_result, raw_stdout=raw_stdout, spec_path=str(spec_path)
                )
                reporter.success("Análisis de IA generado.")
            except AssistantError as exc:
                reporter.warning(f"No se pudo generar el análisis de IA: {exc.message}")
            except Exception as exc:  # noqa: BLE001 — nunca debe interrumpir la corrida por esto
                reporter.warning(f"No se pudo generar el análisis de IA (error inesperado): {exc}")

        browser_outcome_value: str | None = None
        browser_outcome_description: str | None = None
        if telemetry is not None:
            browser_report = await telemetry.stop(exit_code=exit_code, raw_stdout=raw_stdout)
            telemetry = None  # ya se detuvo — el finally no necesita hacerlo de nuevo
            browser_outcome_value = browser_report.outcome.value
            browser_outcome_description = browser_report.description
            if browser_report.outcome.value == "closed_unexpectedly":
                reporter.warning(browser_report.description)
            else:
                reporter.info(browser_report.description)

        # Punto 4 del pedido v0.1.5: KPIs + los 15 ámbitos del Asistente,
        # incluidos en el mismo reporte final. Mismo criterio de
        # degradación elegante que el resto del pipeline — si el navegador
        # se cierra antes de tiempo, el reporte sigue siendo válido, solo
        # sin estos datos extra.
        assistant_responses: dict[str, str] = {}
        kpis_html: str | None = None
        if recording_active and sidepanel is not None:
            try:
                await stop_recording(sidepanel)
                reporter.success("Grabación detenida.")
                kpis_html = await get_kpis_html(sidepanel)

                reporter.section("Análisis del Asistente IA (15 ámbitos, uno a la vez)")
                assistant_responses = await analyze_all_scopes(sidepanel, source="live")
                reporter.success("Los 15 ámbitos fueron analizados.")
            except (ConnectionClosed, ConnectionError, OSError) as exc:
                reporter.warning(
                    "El navegador se cerró antes de poder completar el análisis del Asistente. "
                    f"El reporte incluye los resultados de Playwright y la grabación, sin el "
                    f"análisis de los 15 ámbitos. Detalle: {exc}"
                )

        report = build_combined_report(
            url=cfg.test.url,
            spec_path=str(spec_path),
            playwright_result=pw_result,
            assistant_responses=assistant_responses,
            extension_used=recording_active,
            browser_outcome=browser_outcome_value,
            browser_outcome_description=browser_outcome_description,
            kpis_html=kpis_html,
            ai_analysis_html=ai_analysis_html,
        )

        # El reporte se guarda SIEMPRE en un directorio propio, sin depender
        # de que el usuario recuerde pedirlo — ask_save_path (abajo) sigue
        # ofreciendo una copia adicional donde el usuario quiera, pero esta
        # copia interna es la que alimenta el historial/dashboard, que
        # necesita poder abrir el detalle completo de cualquier corrida
        # pasada, se haya guardado explícitamente o no.
        reports_dir = history_reports_dir()
        safe_test_name = _sanitize_filename_component(resolved_test_name)
        auto_report_name = f"{safe_test_name}-{run_started_at_iso}.html".replace(" ", "_").replace(":", "-")
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
                use_extension=recording_active,
                browser_outcome=browser_outcome_value,
                duration_s=round(duration_ms / 1000, 1),
            )
        )

        default_name = f"reporte-{spec_path.stem}.html"
        dest_raw = await ask_save_path(default_name)
        if dest_raw:
            dest = Path(dest_raw).expanduser().resolve()
            saved_at = save_report(report, dest)
            reporter.success(f"Reporte guardado en: {saved_at}")

        return report

    finally:
        if sidepanel is not None:
            try:
                await sidepanel.close()
            except (ConnectionClosed, ConnectionError, OSError):
                pass
        if sync is not None:
            try:
                await sync.close()
            except (ConnectionClosed, ConnectionError, OSError):
                pass
        if telemetry is not None:
            try:
                await telemetry.stop()
            except Exception:
                pass
        if run is not None:
            if run.process.poll() is None:
                run.process.kill()
            run.config_path.unlink(missing_ok=True)  # vive en el proyecto del usuario, no en work_dir (ver launcher.py)
        shutil.rmtree(work_dir, ignore_errors=True)


def _sanitize_filename_component(name: str) -> str:
    """Bug de seguridad real corregido: `resolved_test_name` puede venir del
    nombre que el usuario escribió libremente en el formulario del
    Catálogo (`config.TestCase.name`) — sin sanear, algo como
    '../../../tmp/x' se usaba tal cual en el nombre del archivo del
    reporte auto-guardado, escapando por completo `reports_dir` (confirmado
    con una prueba real antes de este fix: el archivo terminaba en /tmp/
    en vez de la carpeta de reportes). Se queda solo con caracteres seguros
    para un nombre de archivo en cualquier sistema operativo."""
    import re

    cleaned = re.sub(r"[^\w\-. ]", "_", name).strip(". ")
    return cleaned or "prueba"


async def _default_ask_save_path(default_name: str) -> str | None:
    """Comportamiento original de la CLI (v0.0.1/v0.0.2): prompt interactivo
    de questionary. Preservado tal cual para no cambiar nada del flujo CLI
    existente.

    Bug real corregido: llamar `questionary.ask()` de forma síncrona desde
    dentro de `run_audit()` (que corre bajo `asyncio.run()`) es el mismo
    tipo de conflicto de event loop que ya se corrigió una vez para
    `ensure_chromium()` en v0.0.8 — pero nunca se aplicó acá. Confirmado
    con un reporte real: según la versión de `prompt_toolkit` instalada,
    esto puede lanzar un `RuntimeError` limpio, o — el caso real
    reportado, con una versión distinta — devolver en silencio una
    corrutina sin ejecutar, que después rompía `Path(dest_raw)` con
    exactamente el error que se reportó. Se ejecuta en un hilo aparte (vía
    `run_in_executor`), que no tiene ningún event loop propio con el que
    pueda chocar."""
    loop = asyncio.get_event_loop()
    dest_raw = await loop.run_in_executor(
        None,
        lambda: questionary.path(
            "¿Dónde guardar el reporte HTML?", default=default_name, style=QUESTIONARY_STYLE
        ).ask(),
    )
    return dest_raw or default_name


def _parse_args(argv: list[str]):
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
