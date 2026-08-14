"""
browser/cdp_sync.py — Cliente CDP mínimo, usado por `browser/telemetry.py`
para monitorear el navegador orquestado de forma pasiva.

v0.1.7 — se retira el mecanismo de pausa/intercepción de red
(`PausedTarget`, `BrowserSync.wait_for_new_page`) que existía
exclusivamente para sincronizar con la extensión CharlyAudit antes de
dejarla navegar — ya eliminada del proyecto (ver
`docs/roadmap-charlyaudit-nativo.md`). Lo que queda (`CDPClient`,
`wait_for_cdp_ready`, `BrowserSync.connect`/`close`) es genérico: conectar
al navegador, enviar comandos, cerrar — usado únicamente para telemetría
pasiva, nunca para modular el comportamiento del navegador.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request

import websockets
from websockets.exceptions import ConnectionClosed

from ..errors import BrowserLaunchError


class CDPClient:
    """Cliente CDP mínimo: solo lo que la telemetría necesita (enviar
    comandos y esperar su respuesta). No pretende ser un cliente CDP de
    propósito general."""

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
        if self._reader_task:
            self._reader_task.cancel()
        if self._ws:
            try:
                if hasattr(self._ws, "transport") and self._ws.transport:
                    self._ws.transport.close()
                else:
                    await self._ws.close()
            except Exception:
                pass  # el navegador puede haber cerrado la conexion ya — no hay nada mas que cerrar

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
        except ConnectionClosed:
            pass  # el navegador puede cerrar la conexion al terminar; no es un error

    async def send(
        self, method: str, params: dict | None = None, session_id: str | None = None, *, retries: int = 2, timeout: float = 15
    ) -> dict:
        """Envía un comando CDP y espera su respuesta. Reintenta ante un
        timeout (no ante una desconexión real) — bajo contención de CDP
        (varios targets activos, navegación en curso en otro target) una
        respuesta individual puede tardar más de lo normal sin que la
        conexión esté realmente rota; un timeout aislado no siempre debe
        interpretarse como fallo duro.

        `timeout`: 15s por defecto (operaciones CDP normales). Un valor
        bajo (1-2s) es necesario para sondeos de salud/telemetría —
        `kill -9` sobre el navegador no cierra el WebSocket con un cierre
        limpio, así que el timeout es la única señal disponible de que la
        conexión está realmente muerta (confirmado con una prueba real:
        con el timeout de 15s por defecto, un cierre inesperado del
        navegador tardaba hasta 15s en detectarse — demasiado lento para
        telemetría útil, ver browser/telemetry.py)."""
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
                return await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError as exc:
                last_exc = exc
                self._pending.pop(mid, None)
                if attempt < retries:
                    await asyncio.sleep(0.3 * (attempt + 1))
                    continue
            except ConnectionClosed:
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
        hint="Puede que el navegador haya tardado en arrancar o que el puerto esté ocupado por otro proceso.",
    )


class BrowserSync:
    """Conexión CDP básica al navegador orquestado — usada por
    `browser/telemetry.py` para sondear pasivamente que el navegador
    sigue respondiendo. Nunca modula el comportamiento del navegador."""

    def __init__(self, cdp_port: int) -> None:
        self.cdp_port = cdp_port
        self.client: CDPClient | None = None

    async def connect(self) -> None:
        await wait_for_cdp_ready(self.cdp_port)
        ws_url = _get_browser_ws_url(self.cdp_port)
        self.client = CDPClient(ws_url)
        await self.client.connect()

    async def close(self) -> None:
        if self.client:
            await self.client.close()
