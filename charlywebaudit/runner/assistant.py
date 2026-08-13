"""
runner/assistant.py — Punto 5 de la decisión confirmada: activa cada uno de
los 15 ámbitos del Asistente UNO A LA VEZ (no todos juntos), pide un
análisis de cada uno, y devuelve las 15 respuestas para que report/builder.py
las una con el resultado de Playwright en un solo reporte.

v0.1.5: reintegrado al flujo principal (modo con extensión, ver
`__main__.run_audit` y `cfg.test.use_extension`). Se agregó
`get_kpis_html`: extrae los KPIs de la sesión grabada (punto 4 del
pedido) para incluirlos también en el reporte final.

Los chips de ámbito no tienen `id` individual — se seleccionan por posición
(`#scopes button.scope`), en el mismo orden en que se declaran en SCOPES de
context-bridge.js — el mismo orden que ASSISTANT_SCOPES en constants.py.
"""

from __future__ import annotations

import asyncio

from ..constants import ASSISTANT_SCOPES, SCOPE_LABELS
from ..errors import AssistantError
from ..browser.extension_page import ExtensionPage

_DEFAULT_PROMPT_TEMPLATE = (
    "Analiza en detalle el ámbito '{label}' de esta sesión grabada. "
    "Sé exhaustivo: no omitas datos, cifras ni hallazgos disponibles en este ámbito."
)


async def _scope_chip_count(ext: ExtensionPage) -> int:
    return await ext.evaluate("document.querySelectorAll('#scopes button.scope').length")


async def _set_scope_active(ext: ExtensionPage, index: int, active: bool) -> None:
    """Activa/desactiva el chip en la posición `index`, solo si su estado
    actual difiere del deseado (evita un clic — y por tanto un toggle— de más)."""
    current = await ext.evaluate(
        f"document.querySelectorAll('#scopes button.scope')[{index}]?.getAttribute('aria-pressed') === 'true'"
    )
    if current != active:
        await ext.evaluate(f"document.querySelectorAll('#scopes button.scope')[{index}]?.click()")


async def _deactivate_all_scopes(ext: ExtensionPage, total: int) -> None:
    for i in range(total):
        await _set_scope_active(ext, i, False)


async def _wait_for_response(ext: ExtensionPage, *, prev_ai_count: int, timeout: float = 120) -> str:
    """Espera a que la respuesta termine de verdad y devuelve su HTML.

    Importante: el elemento #typing desaparece casi de inmediato (se
    reemplaza por la burbuja de streaming vacía apenas arranca la
    respuesta) — no sirve como señal de "terminado". La señal real y
    visible en el DOM es el texto del botón #send: "■" mientras hay una
    respuesta en curso (streaming), "➤" cuando termina de verdad — así lo
    hace `setBusy()` en sidepanel.js. Confirmado contra la extensión real
    con una consulta real a Gemini."""
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout:
        send_label = await ext.evaluate("document.getElementById('send')?.textContent")
        busy = send_label == "■"
        ai_count = await ext.evaluate("document.querySelectorAll('.msg.msg--ai').length")
        err_count = await ext.evaluate("document.querySelectorAll('.msg.msg--err').length")
        if not busy and ai_count > prev_ai_count:
            return await ext.evaluate("document.querySelector('.msg.msg--ai:last-of-type').innerHTML")
        if not busy and err_count > 0:
            last_err = await ext.evaluate("document.querySelector('.msg.msg--err:last-of-type')?.textContent")
            raise AssistantError(f"El Asistente respondió con un error: {last_err}")
        await asyncio.sleep(0.4)
    raise AssistantError(
        "El Asistente no respondió dentro del tiempo esperado.",
        hint="Puede ser un proveedor/modelo lento, o una API key inválida — revisa la configuración.",
    )


async def get_kpis_html(ext: ExtensionPage, *, timeout: float = 10) -> str | None:
    """Punto 4 del pedido v0.1.5: extrae el HTML de los KPIs de la sesión
    grabada (`#tl-kpis`, en la pestaña QA/Timeline) — se renderiza vía JS
    de la propia extensión al expandir `#tl-kpis-toggle`, así que hay que
    esperar a que el contenido aparezca antes de leerlo. Devuelve `None`
    si no se pudo extraer (no debe abortar el resto del reporte por esto)."""
    try:
        await ext.click("#tab-btn-qa")
        expanded = await ext.evaluate("document.getElementById('tl-kpis-toggle')?.getAttribute('aria-expanded') === 'true'")
        if not expanded:
            await ext.click("#tl-kpis-toggle")
        t0 = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - t0 < timeout:
            html = await ext.evaluate("document.getElementById('tl-kpis')?.innerHTML")
            if html and html.strip():
                return html
            await asyncio.sleep(0.3)
    except Exception:  # noqa: BLE001 — los KPIs son un extra del reporte, nunca deben abortar la corrida
        return None
    return None


async def analyze_all_scopes(
    ext: ExtensionPage,
    *,
    source: str = "live",
    prompt_template: str = _DEFAULT_PROMPT_TEMPLATE,
    per_scope_timeout: float = 120,
) -> dict[str, str]:
    """Devuelve {scope_id: respuesta_html}, uno por cada ámbito, en el mismo
    orden que ASSISTANT_SCOPES. Un fallo en un ámbito no aborta el resto —
    se registra el error como el "análisis" de ese ámbito y se continúa, para
    no perder los otros 14 por un problema puntual."""
    await ext.click("#tab-btn-assistant")
    await ext.wait_for_selector("#scopes")

    total = await _scope_chip_count(ext)
    if total != len(ASSISTANT_SCOPES):
        raise AssistantError(
            f"Se esperaban {len(ASSISTANT_SCOPES)} ámbitos pero la extensión expone {total}.",
            hint="La extensión pudo actualizar su lista de ámbitos — revisa ASSISTANT_SCOPES en constants.py.",
        )

    # Fuente del contexto (Temporal/Importado) — coherente con la sesion que
    # se acaba de grabar.
    src_btn = "#ctx-src-live" if source == "live" else "#ctx-src-imported"
    await ext.click(src_btn)

    await _deactivate_all_scopes(ext, total)

    results: dict[str, str] = {}
    for i, scope_id in enumerate(ASSISTANT_SCOPES):
        label = SCOPE_LABELS[scope_id]
        await _set_scope_active(ext, i, True)
        await asyncio.sleep(0.15)  # deja que previewContextSize() recalcule

        prev_ai_count = await ext.evaluate("document.querySelectorAll('.msg.msg--ai').length")
        await ext.fill("#input", prompt_template.format(label=label))
        await ext.click("#send")

        try:
            html = await _wait_for_response(ext, prev_ai_count=prev_ai_count, timeout=per_scope_timeout)
            results[scope_id] = html
        except AssistantError as exc:
            results[scope_id] = f'<p class="charly-error">Fallo al analizar este ámbito: {exc.message}</p>'

        await _set_scope_active(ext, i, False)  # aisla: el siguiente ambito arranca limpio

    return results
