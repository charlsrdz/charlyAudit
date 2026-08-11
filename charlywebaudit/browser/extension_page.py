"""
browser/extension_page.py — Interactúa con páginas de la extensión (panel
lateral) sobre la MISMA conexión CDP cruda ya usada para el mecanismo de
pausa (cdp_sync.py), en vez de abrir una segunda conexión de alto nivel
(p. ej. Playwright normal) que competiría por el control de
`Target.setAutoAttach` sobre el mismo navegador.

Nota de diseño: dado que popups nativos de extensión NO son automatizables
por CDP/Playwright (limitación conocida del propio protocolo), tanto el
popup como el panel lateral de CharlyAudit son, al final, páginas HTML
normales servidas desde `chrome-extension://<id>/...` — se navegan y
controlan exactamente igual que cualquier página.
"""

from __future__ import annotations

import asyncio
import json

from ..constants import CHARLYAUDIT_EXTENSION_ID
from ..errors import RecordingError
from .cdp_sync import CDPClient

SIDEPANEL_URL = f"chrome-extension://{CHARLYAUDIT_EXTENSION_ID}/src/sidepanel/sidepanel.html"


class ExtensionPage:
    """Una pestaña abierta sobre una página de la extensión, controlada por
    JS inyectado vía `Runtime.evaluate` — no simula clics de mouse con
    coordenadas de pantalla, invoca directamente los mismos manejadores que
    ya validamos en la propia extensión (equivalente a lo que un clic real
    dispararía, sin la fragilidad de depender de posiciones en pantalla)."""

    def __init__(self, client: CDPClient, session_id: str, target_id: str) -> None:
        self.client = client
        self.session_id = session_id
        self.target_id = target_id

    @classmethod
    async def open(cls, client: CDPClient, url: str, *, timeout: float = 15) -> "ExtensionPage":
        # Importante: la conexion ya tiene armado Target.setAutoAttach con
        # waitForDebuggerOnStart:true (ver BrowserSync.connect) para pausar
        # la pestana del spec del usuario — pero ese auto-attach aplica a
        # CUALQUIER target nuevo, incluida esta pagina de la extension que
        # estamos por crear. Por eso no usamos Target.attachToTarget manual
        # (crearia una sesion separada, distinta a la que el auto-attach ya
        # establecio, y los comandos enviados sobre la sesion equivocada se
        # pierden silenciosamente) — en su lugar, creamos el target y
        # esperamos el evento attachedToTarget que el propio auto-attach
        # dispara para el, y liberamos SU pausa antes de navegar.
        res = await client.send("Target.createTarget", {"url": "about:blank"})
        target_id = res["result"]["targetId"]

        ev = await client.wait_event(
            "Target.attachedToTarget",
            predicate=lambda p: p.get("targetInfo", {}).get("targetId") == target_id,
            timeout=timeout,
        )
        session_id = ev["params"]["sessionId"]

        page = cls(client, session_id, target_id)
        await client.send("Page.enable", session_id=session_id)
        # Esta pagina nace pausada por el mismo motivo que la del spec del
        # usuario — la liberamos de inmediato, a nosotros no nos interesa
        # pausarla, solo necesitabamos el mecanismo activo para la OTRA pestana.
        await client.send("Runtime.runIfWaitingForDebugger", session_id=session_id)
        await client.send("Page.navigate", {"url": url}, session_id=session_id)
        await page._wait_ready(timeout=timeout)
        return page

    async def _wait_ready(self, timeout: float = 15) -> None:
        t0 = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - t0 < timeout:
            state = await self.evaluate("document.readyState")
            if state == "complete":
                # Un ciclo extra: el JS de la propia extension (modulos ES)
                # puede seguir inicializando tras 'complete'.
                await asyncio.sleep(0.3)
                return
            await asyncio.sleep(0.2)
        raise RecordingError(f"La página de la extensión no terminó de cargar: {self.target_id}")

    async def evaluate(self, expression: str, *, await_promise: bool = False):
        res = await self.client.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
            session_id=self.session_id,
        )
        result = res.get("result", {})
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"].get("text", "error desconocido")
            raise RecordingError(f"Error evaluando JS en la extensión: {detail}")
        return result.get("result", {}).get("value")

    async def click(self, selector: str) -> None:
        ok = await self.evaluate(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)}); "
            f"if (!el) return false; el.click(); return true; }})()"
        )
        if not ok:
            raise RecordingError(f"No se encontró el elemento '{selector}' en la extensión.")

    async def fill(self, selector: str, value: str) -> None:
        ok = await self.evaluate(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)}); "
            f"if (!el) return false; "
            f"const setter = Object.getOwnPropertyDescriptor(el.__proto__, 'value').set; "
            f"setter.call(el, {json.dumps(value)}); "
            f"el.dispatchEvent(new Event('input', {{bubbles:true}})); "
            f"el.dispatchEvent(new Event('change', {{bubbles:true}})); "
            f"return true; }})()"
        )
        if not ok:
            raise RecordingError(f"No se encontró el campo '{selector}' en la extensión.")

    async def select_option(self, selector: str, value: str) -> None:
        await self.evaluate(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)}); "
            f"el.value = {json.dumps(value)}; el.dispatchEvent(new Event('change', {{bubbles:true}})); }})()"
        )

    async def text_content(self, selector: str) -> str | None:
        return await self.evaluate(
            f"(() => {{ const el = document.querySelector({json.dumps(selector)}); return el ? el.textContent : null; }})()"
        )

    async def wait_for_selector(self, selector: str, *, timeout: float = 15) -> None:
        t0 = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - t0 < timeout:
            found = await self.evaluate(f"!!document.querySelector({json.dumps(selector)})")
            if found:
                return
            await asyncio.sleep(0.2)
        raise RecordingError(f"El elemento '{selector}' nunca apareció en la extensión.")

    async def close(self) -> None:
        await self.client.send("Target.closeTarget", {"targetId": self.target_id})
