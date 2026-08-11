"""
runner/recorder.py — Inicia/detiene la grabación en la pestaña correcta.

Usa el `tabId` explícito que se agregó a la acción "toggle" del service
worker de CharlyAudit (v2.6.2) — sin eso, la grabación habría apuntado a la
pestaña que aloja nuestro propio panel lateral (la que efectivamente envía
el mensaje), no a la pestaña del spec del usuario. Validado en vivo: la
grabación queda en el tabId correcto incluso cuando el mensaje se dispara
desde una pestaña distinta.
"""

from __future__ import annotations

import asyncio

from ..errors import RecordingError
from ..browser.extension_page import ExtensionPage


async def _send_control(ext: ExtensionPage, action: str, **extra) -> dict:
    payload = {"channel": "qa-control", "action": action, **extra}
    result = await ext.evaluate(
        f"(async () => {{ return await chrome.runtime.sendMessage({_to_js(payload)}); }})()",
        await_promise=True,
    )
    if not result or not result.get("ok"):
        raise RecordingError(
            f"La acción '{action}' de la extensión falló: {(result or {}).get('error', 'sin detalle')}"
        )
    return result


def _to_js(payload: dict) -> str:
    import json

    return json.dumps(payload)


async def start_recording(ext: ExtensionPage, tab_id: int, *, confirm_timeout: float = 10) -> dict:
    """Inicia la grabación en `tab_id` y espera a que la extensión confirme
    que la inyección real ocurrió (campo `inyectado` de la respuesta) —
    no basta con que el mensaje responda `ok`, la inyección en sí puede
    fallar en páginas restringidas (chrome://, la Web Store, etc.)."""
    result = await _send_control(ext, "toggle", tabId=tab_id)
    if not result.get("inyectado", True):
        raise RecordingError(
            "La grabación se activó pero la inyección en la pestaña no se confirmó.",
            hint="Algunas páginas (chrome://, la Web Store) no permiten inyección de extensiones.",
        )
    return result


async def stop_recording(ext: ExtensionPage) -> dict:
    return await _send_control(ext, "toggle")


async def get_state(ext: ExtensionPage) -> dict:
    return await _send_control(ext, "getState")


async def wait_until_recording(ext: ExtensionPage, *, timeout: float = 10) -> None:
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout:
        state = await get_state(ext)
        if state.get("isRecording"):
            return
        await asyncio.sleep(0.2)
    raise RecordingError("La grabación no se confirmó activa dentro del tiempo esperado.")
