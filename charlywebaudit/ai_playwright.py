"""
ai_playwright.py — análisis por IA de los resultados de Playwright,
llamando directamente a la API del proveedor configurado (Gemini u
OpenAI).

Un solo llamado a la API pide DOS cosas en una misma respuesta (evita
duplicar costo/latencia de una segunda llamada):

1. Un análisis del resultado — sin dejar que las fallas acaparen la
   atención. A pedido explícito: cuando un test falla por una aserción
   que capturó datos reales (errores JS, advertencias de red, etc.), esos
   datos SON hallazgos válidos sobre el sitio probado, no solo "el test
   falló" — el prompt pide explícitamente extraerlos y explicarlos en
   lenguaje claro, además de destacar lo que sí funcionó.
2. Recomendaciones concretas para mejorar el script de Playwright en sí
   (mejores esperas, selectores más robustos, aserciones más claras,
   etc.) — con el código fuente del spec como contexto real cuando está
   disponible, no solo el resultado de la corrida.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path

from .config import AssistantConfig
from .errors import AssistantError
from .runner.test_exec import PlaywrightRunResult

_MAX_STDOUT_CHARS = 8000
_MAX_SPEC_CHARS = 6000
_REQUEST_TIMEOUT = 60

_PROMPT_TEMPLATE = """\
Sos un ingeniero de QA senior revisando el resultado de una corrida de \
Playwright. Respondé en español, en HTML simple (usá <h4>, <p>, <ul>/<li>, \
<strong> — nada de markdown, nada de <html>/<body>).

Reglas importantes:
- NO dejes que las fallas acaparen toda la atención. Si el test capturó \
datos reales dentro de una aserción que falló (por ejemplo, errores de \
JS, advertencias de red, recursos con 404, contenido mixto HTTP/HTTPS), \
esos datos SON hallazgos válidos sobre el sitio probado — extraelos y \
explicalos en lenguaje claro, como hallazgos, no solo como "el test \
falló".
- Mencioná explícitamente lo que sí funcionó (navegación exitosa, \
elementos encontrados, pasos que se completaron) con el mismo nivel de \
detalle que lo que falló — un reporte balanceado, no solo negativo.

Estructura tu respuesta en exactamente dos secciones:
<h4>Análisis del resultado</h4>
(qué funcionó, qué no, y qué hallazgos reales aparecen en los datos \
capturados — aunque estén dentro de un test fallido)

<h4>Recomendaciones para el script</h4>
(sugerencias concretas y accionables para mejorar el script de Playwright \
en sí — esperas, selectores, aserciones, estructura)

=== RESULTADO DE PLAYWRIGHT ===
{summary}

=== SALIDA REAL (stdout/stderr) ===
{raw_stdout}
{spec_section}"""


def _build_prompt(pw_result: PlaywrightRunResult, raw_stdout: str, spec_source: str | None) -> str:
    summary = (
        f"{pw_result.passed} caso(s) pasaron, {pw_result.failed} fallaron, "
        f"{pw_result.skipped} omitidos. Duración total: {pw_result.duration_ms / 1000:.1f}s."
    )
    trimmed_stdout = raw_stdout[-_MAX_STDOUT_CHARS:]
    spec_section = ""
    if spec_source:
        spec_section = (
            "\n=== CÓDIGO FUENTE DEL SPEC (para las recomendaciones) ===\n"
            + spec_source[:_MAX_SPEC_CHARS]
        )
    return _PROMPT_TEMPLATE.format(summary=summary, raw_stdout=trimmed_stdout, spec_section=spec_section)


def _read_spec_source(spec_path: str) -> str | None:
    try:
        return Path(spec_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _call_gemini(model: str, api_key: str, prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as r:
        data = json.loads(r.read())
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise AssistantError(f"Respuesta inesperada de Gemini: {data}") from exc


def _call_openai(model: str, api_key: str, prompt: str) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as r:
        data = json.loads(r.read())
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise AssistantError(f"Respuesta inesperada de OpenAI: {data}") from exc


def _call_provider_sync(cfg: AssistantConfig, prompt: str) -> str:
    try:
        if cfg.provider == "gemini":
            return _call_gemini(cfg.model, cfg.api_key, prompt)
        if cfg.provider == "openai":
            return _call_openai(cfg.model, cfg.api_key, prompt)
        raise AssistantError(f"Proveedor de IA no reconocido: {cfg.provider!r}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise AssistantError(f"La API de {cfg.provider} respondió {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise AssistantError(f"No se pudo conectar con la API de {cfg.provider}: {exc.reason}") from exc


async def analyze_playwright_run(
    cfg: AssistantConfig, *, pw_result: PlaywrightRunResult, raw_stdout: str, spec_path: str | None = None
) -> str:
    """Punto 1/2 del pedido: pide a la IA un análisis balanceado del
    resultado (sin que las fallas acaparen la atención) + recomendaciones
    concretas para mejorar el script — todo en una sola llamada HTTP
    directa a la API del proveedor, sin la extensión. Lanza
    `AssistantError` si algo falla (credenciales, red, respuesta
    inesperada) — quien llama decide cómo degradar (ver `__main__.py`,
    nunca debe interrumpir la corrida real de Playwright)."""
    if not cfg.configured or not cfg.api_key:
        raise AssistantError("El Asistente IA no está configurado (falta la API key).")

    spec_source = _read_spec_source(spec_path) if spec_path else None
    prompt = _build_prompt(pw_result, raw_stdout, spec_source)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call_provider_sync, cfg, prompt)
