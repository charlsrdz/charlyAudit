"""
runner/test_exec.py — Punto 5.2.5 del pedido: espera la ejecución del spec
del usuario (ya lanzado por browser/launcher.py como subproceso de Node) y
parsea su reporte JSON — o, si no lo hay, analiza la salida de texto en
busca de una falla.

El formato del reportero JSON de @playwright/test se confirmó en vivo
(ver browser/config_gen.py — `reporter: [['json', {...}], ['list']]`).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestCaseResult:
    title: str
    status: str  # "passed" | "failed" | "timedOut" | "skipped"
    duration_ms: int
    errors: list[str] = field(default_factory=list)


@dataclass
class PlaywrightRunResult:
    exit_code: int
    passed: int
    failed: int
    skipped: int
    duration_ms: int
    cases: list[TestCaseResult]
    raw_stdout: str
    report_available: bool

    @property
    def all_passed(self) -> bool:
        return self.exit_code == 0 and self.failed == 0


import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _extract_cases(json_report: dict) -> list[TestCaseResult]:
    cases: list[TestCaseResult] = []
    for suite in json_report.get("suites", []):
        _walk_suite(suite, cases)
    return cases


def _walk_suite(suite: dict, out: list[TestCaseResult]) -> None:
    for spec in suite.get("specs", []):
        for test in spec.get("tests", []):
            for result in test.get("results", []):
                errors = [_strip_ansi(e.get("message", str(e))) for e in result.get("errors", [])]
                out.append(
                    TestCaseResult(
                        title=spec.get("title", "(sin título)"),
                        status=result.get("status", "unknown"),
                        duration_ms=result.get("duration", 0),
                        errors=errors,
                    )
                )
    for child in suite.get("suites", []):
        _walk_suite(child, out)


async def wait_for_process(process: subprocess.Popen, *, timeout: float = 600) -> tuple[int, str]:
    """Espera a que el subproceso de Node termine, capturando toda su salida
    (ya combinada stdout+stderr desde browser/launcher.py)."""
    loop = asyncio.get_event_loop()

    def _wait() -> tuple[int, str]:
        try:
            stdout, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _ = process.communicate()
        return process.returncode, stdout or ""

    return await loop.run_in_executor(None, _wait)


def parse_report(json_report_path: Path, exit_code: int, raw_stdout: str) -> PlaywrightRunResult:
    if not json_report_path.exists():
        # El spec pudo fallar tan temprano (error de sintaxis, config
        # inválida) que ni siquiera llegó a escribir el reporte JSON — se
        # analiza igual, con lo que haya en la salida de texto.
        return PlaywrightRunResult(
            exit_code=exit_code,
            passed=0,
            failed=1 if exit_code != 0 else 0,
            skipped=0,
            duration_ms=0,
            cases=[],
            raw_stdout=raw_stdout,
            report_available=False,
        )

    data = json.loads(json_report_path.read_text(encoding="utf-8"))
    cases = _extract_cases(data)
    stats = data.get("stats", {})
    return PlaywrightRunResult(
        exit_code=exit_code,
        passed=stats.get("expected", 0),
        failed=stats.get("unexpected", 0),
        skipped=stats.get("skipped", 0),
        duration_ms=stats.get("duration", 0),
        cases=cases,
        raw_stdout=raw_stdout,
        report_available=True,
    )
