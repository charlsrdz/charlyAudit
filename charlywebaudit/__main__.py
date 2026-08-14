"""
__main__.py — Punto de entrada de charlyWebAudit.

v0.1.7 — se retira POR COMPLETO el soporte de la extensión CharlyAudit.
La grabación nunca llegó a funcionar de forma confiable: investigado a
fondo en v0.1.5, la pestaña del spec de Playwright y el panel lateral de
la extensión viven en `browserContextId` distintos (confirmado con CDP
real, `Target.getTargets`) — una restricción de aislamiento de Chrome
entre contextos de navegador, no un problema de sincronización. Mantener
esa integración parcialmente rota agregaba complejidad real (pausa de
navegación, Chromium como dependencia aparte, todo el código de
orquestación de la extensión) sin aportar el valor prometido. Ver
`docs/roadmap-charlyaudit-nativo.md` para el plan de reimplementar esas
capacidades de forma nativa en Python, sin depender de una extensión de
Chrome.

`run_audit()` vuelve al motor simple (desde v0.1.1): lanza el navegador
(Google Chrome, canal estable — única opción desde v0.1.7, ya no hace
falta Chromium para nada), corre el spec de Playwright tal cual el
usuario lo escribió, y arma el reporte — con telemetría del navegador
(`browser/telemetry.py`) y, si el Asistente IA está configurado, un
análisis directo de los resultados de Playwright vía la API del
proveedor configurado (`ai_playwright.py`, sin ninguna extensión de por
medio).
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time as _time
from pathlib import Path

import questionary

from .ai_playwright import analyze_playwright_run
from .browser.chromium import ensure_browser
from .browser.launcher import RunOrchestrator
from .browser.telemetry import BrowserTelemetry
from .config import AppConfig, load_config, save_config
from .constants import APP_NAME, APP_VERSION
from .dependencies import ensure_dependencies
from .errors import AssistantError, CharlyWebAuditError
from .history import RunRecord, append_run_record, make_run_id, now_iso, reports_dir as history_reports_dir
from .report.builder import build_combined_report, CombinedReport
from .report.html import save_report
from .runner.test_exec import parse_report, wait_for_process
from .ui.menu import main_menu_loop
from .reporter import Reporter
from .ui.theme import CliReporter, QUESTIONARY_STYLE, console, print_error


async def run_audit(
    cfg: AppConfig,
    *,
    reporter: Reporter | None = None,
    ask_save_path=None,
    confirm_install=None,
    test_name: str | None = None,
) -> CombinedReport:
    """Motor de orquestación. `reporter`/`ask_save_path`/`confirm_install`/
    `test_name`: para que la GUI reuse este mismo motor sin duplicar nada
    (`confirm_install` no tiene efecto propio — `ensure_browser()` no
    instala nada automáticamente, se conserva el parámetro solo por
    compatibilidad de firma con `dependencies.ensure_dependencies`, que sí
    lo usa)."""
    run_started_at = _time.monotonic()
    run_started_at_iso = now_iso()
    reporter = reporter or CliReporter()
    if ask_save_path is None:
        ask_save_path = _default_ask_save_path

    spec_path = Path(cfg.test.spec_path)
    resolved_test_name = test_name or spec_path.stem

    reporter.section("Prerrequisitos")
    channel = ensure_browser(reporter=reporter, confirm=confirm_install)
    # Validación completa de dependencias (Python/Node/Chrome/@playwright-test
    # local) — con instalación en vivo ofrecida donde es segura y contenida
    # (ver dependencies.py).
    ensure_dependencies(spec_dir=str(spec_path.parent), reporter=reporter, confirm=confirm_install)

    work_dir = Path(tempfile.mkdtemp(prefix="charlywebaudit-"))
    reporter.info(f"Directorio de trabajo de esta corrida: {work_dir}")

    orchestrator = RunOrchestrator(
        spec_path=spec_path,
        target_url=cfg.test.url,
        headers=cfg.test.headers,
        work_dir=work_dir,
        channel=channel,
    )

    run = None
    telemetry: BrowserTelemetry | None = None
    try:
        reporter.section("Lanzando navegador + spec")
        run = orchestrator.launch()
        reporter.success(f"Navegador lanzado (PID {run.process.pid}) — el spec ya está corriendo.")

        # Telemetria pasiva (punto 3 del pedido v0.1.1) — se conecta por su
        # cuenta, con su propia espera/reintento; si no logra conectar, no
        # bloquea la corrida (solo significa que no hay telemetria que
        # ofrecer).
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

        # Análisis por IA directo de los resultados de Playwright — llama a
        # la API del proveedor configurado, sin ninguna extensión de por
        # medio (ver ai_playwright.py). Un fallo acá nunca debe interrumpir
        # la corrida — el resultado real de Playwright ya está confirmado
        # antes de intentar esto.
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

        report = build_combined_report(
            url=cfg.test.url,
            spec_path=str(spec_path),
            playwright_result=pw_result,
            browser_outcome=browser_outcome_value,
            browser_outcome_description=browser_outcome_description,
            ai_analysis_html=ai_analysis_html,
            test_name=resolved_test_name,
            headers=cfg.test.headers,
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
                report_path=str(auto_report_path) if auto_report_path else None,
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
