"""
browser/cdp_sync.py — El mecanismo que elimina la carrera de "perder la
navegación inicial" (ver discusión previa sobre el matiz de sincronización).

VALIDADO end-to-end contra un spec real de @playwright/test lanzado por un
subproceso de Node: se confirmó que, mientras la pestaña está pausada, el
DOM nunca navega (verificado leyendo el documento en vivo vía CDP), y que al
liberar la pausa el propio test runner de Playwright completa la prueba con
éxito exactamente como si nunca hubiera existido la pausa.

Mecanismo (protocolo CDP puro, sin pasar por la capa alta de Playwright,
porque Playwright hace su propio auto-attach interno y no expone control
sobre `waitForDebuggerOnStart`):

  1. `Target.setAutoAttach` con `waitForDebuggerOnStart: true` — CUALQUIER
     pestaña nueva que se cree a partir de este momento queda congelada en
     el instante de su creación: no navega, no ejecuta nada.
  2. Se espera el evento `Target.attachedToTarget` de tipo "page" — esa es
     la pestaña que el spec del usuario está a punto de usar.
  3. El llamador (browser/launcher.py) hace lo que necesite mientras la
     pestaña sigue congelada (activar la grabación en la extensión, etc.).
  4. `Runtime.runIfWaitingForDebugger` — recién ahí la pestaña continúa; el
     `page.goto(...)` que sea la primera línea del spec ocurre DESPUÉS de
     que la grabación ya está activa, no en paralelo ni por casualidad.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import websockets

from ..errors import BrowserLaunchError, TargetTabNotFoundError


class CDPClient:
    """Cliente CDP mínimo: solo lo que este mecanismo necesita (Target/Runtime).
    No pretende ser un cliente CDP de propósito general."""

    def __init__(self, ws_url: str) -> None:
        self.ws_url = ws_url
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._events: asyncio.Queue = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None

    async def connect(self) -> None:
        self._ws = await websockets.connect(self.ws_url, max_size=None)
        self._reader_task = asyncio.create_task(self._reader())

    async def close(self) -> None:
        if self._ws:
            try:
                # El problema de 'no close frame' ocurre cuando intentamos hacer un cierre limpio
                # (handshake de websocket) con un navegador que ya cerró el socket por su cuenta
                # al morir. Cerramos el socket sin esperar handshake para evitar esto.
                await self._ws.close(code=1000) # Cierre normal
            except Exception:
                pass
        
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

    async def _reader(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        fut.set_result(msg)
                else:
                    await self._events.put(msg)
        except websockets.ConnectionClosed:
            pass  # el navegador puede cerrar la conexion al terminar; no es un error

    async def send(self, method: str, params: dict | None = None, session_id: str | None = None, *, retries: int = 2) -> dict:
        """Envía un comando CDP y espera su respuesta. Reintenta ante un
        timeout (no ante una desconexión real) — bajo contención de CDP
        (varios targets activos, navegación en curso en otro target) una
        respuesta individual puede tardar más de lo normal sin que la
        conexión esté realmente rota; un timeout aislado no siempre debe
        interpretarse como fallo duro."""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            self._id += 1
            mid = self._id
            payload: dict = {"id": mid, "method": method, "params": params or {}}
            if session_id:
                payload["sessionId"] = session_id
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._pending[mid] = fut
            try:
                await self._ws.send(json.dumps(payload))
                return await asyncio.wait_for(fut, timeout=15)
            except asyncio.TimeoutError as exc:
                last_exc = exc
                self._pending.pop(mid, None)
                if attempt < retries:
                    await asyncio.sleep(0.3 * (attempt + 1))
                    continue
            except websockets.ConnectionClosed:
                raise  # esto si es un fallo duro real: no tiene sentido reintentar
        raise last_exc

    async def wait_event(self, method: str, predicate=None, timeout: float = 20) -> dict:
        t0 = time.time()
        while True:
            remaining = timeout - (time.time() - t0)
            if remaining <= 0:
                raise TimeoutError(f"nunca llegó el evento CDP '{method}' en {timeout}s")
            ev = await asyncio.wait_for(self._events.get(), timeout=remaining)
            if ev.get("method") == method and (predicate is None or predicate(ev.get("params", {}))):
                return ev


def _get_browser_ws_url(port: int) -> str:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=5) as r:
            data = json.loads(r.read())
        return data["webSocketDebuggerUrl"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as exc:
        raise BrowserLaunchError(
            f"No se pudo obtener el endpoint CDP en el puerto {port}.",
            hint="¿El navegador orquestado sigue corriendo? Revisa que nada más use ese puerto.",
        ) from exc


async def wait_for_cdp_ready(port: int, timeout: float = 30) -> None:
    """Sondea el puerto CDP hasta que responde (el navegador terminó de arrancar)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=1)
            return
        except (urllib.error.URLError, ConnectionRefusedError):
            await asyncio.sleep(0.15)
    raise BrowserLaunchError(
        f"El navegador orquestado no respondió en el puerto CDP {port} tras {timeout}s.",
        hint="Puede que Chromium haya tardado en arrancar o que el puerto esté ocupado por otro proceso.",
    )


@dataclass
class PausedTarget:
    """Una pestaña nueva, congelada antes de ejecutar nada, lista para que el
    llamador haga su trabajo (activar grabación) antes de liberarla."""

    client: CDPClient
    session_id: str
    target_id: str

    async def evaluate(self, expression: str):
        res = await self.client.send("Runtime.evaluate", {"expression": expression, "returnByValue": True}, session_id=self.session_id)
        return res.get("result", {}).get("result", {}).get("value")

    async def release(self) -> None:
        """Libera la pausa: a partir de aquí la pestaña ejecuta con normalidad
        (el `page.goto(...)` del spec del usuario ocurre recién ahora)."""
        await self.client.send("Runtime.runIfWaitingForDebugger", session_id=self.session_id)


class BrowserSync:
    """Punto de entrada de alto nivel: conecta al navegador orquestado y arma
    el auto-attach con pausa, listo para capturar la primera pestaña nueva."""

    def __init__(self, cdp_port: int) -> None:
        self.cdp_port = cdp_port
        self.client: CDPClient | None = None

    async def connect(self) -> None:
        await wait_for_cdp_ready(self.cdp_port)
        ws_url = _get_browser_ws_url(self.cdp_port)
        self.client = CDPClient(ws_url)
        await self.client.connect()
        # A partir de este momento, CUALQUIER pestaña nueva queda congelada
        # hasta que alguien mande Runtime.runIfWaitingForDebugger sobre ella.
        await self.client.send("Target.setDiscoverTargets", {"discover": True})
        await self.client.send(
            "Target.setAutoAttach",
            {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True},
        )

    async def wait_for_new_page(self, timeout: float = 30) -> PausedTarget:
        """Espera la primera pestaña nueva de tipo 'page' — la que el spec del
        usuario está a punto de usar — y la devuelve ya pausada."""
        try:
            ev = await self.client.wait_event(
                "Target.attachedToTarget",
                predicate=lambda p: p.get("targetInfo", {}).get("type") == "page",
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise TargetTabNotFoundError(
                "No apareció ninguna pestaña nueva del spec dentro del tiempo esperado.",
                hint="Confirma que el spec efectivamente abre una página (page.goto) al iniciar.",
            ) from exc
        params = ev["params"]
        return PausedTarget(
            client=self.client,
            session_id=params["sessionId"],
            target_id=params["targetInfo"]["targetId"],
        )

    async def close(self) -> None:
        if self.client:
            await self.client.close()
