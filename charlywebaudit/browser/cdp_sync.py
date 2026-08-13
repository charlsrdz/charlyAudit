"""
browser/cdp_sync.py — El mecanismo que elimina la carrera de "perder la
navegación inicial".

v0.1.0a — REDISEÑO del mecanismo de sincronización, tras un bug real
reportado en producción ("el navegador se cierra de forma inesperada" /
"no close frame received or sent"). Diagnóstico completo, validado
empíricamente con Chrome real y con el Chromium empaquetado de Playwright
(el mismo problema ocurre con ambos — no es específico de un navegador):

Playwright Test lanza el navegador con `--remote-debugging-pipe` — es
decir, abre SU PROPIA conexión CDP independiente de la nuestra (que
conecta por `--remote-debugging-port`). Cuando esta clase arma
`Target.setAutoAttach` con `waitForDebuggerOnStart: true`, la pestaña
nueva queda pausada — pero SOLO para sesiones que no la hayan liberado
todavía. Playwright, con SU PROPIA sesión, también recibe el aviso de
pestaña pausada y la libera de inmediato como parte de su arranque
interno normal — completamente ajeno a que nosotros quisiéramos retener
esa pausa. El resultado: la carrera la gana Playwright, no nosotros — el
spec puede terminar de correr ANTES de que hayamos llamado a
`release()`, y cuando por fin lo llamamos (o intentamos usar la
conexión), el navegador ya cerró esa pestaña/proceso, produciendo
exactamente "no close frame received or sent". Confirmado reproduciendo
el problema con logging en vivo: el mensaje "1 passed" de Playwright
aparecía ANTES de que nuestro código llamara release().

**El mecanismo nuevo**: en vez de depender del estado "pausado" de la
pestaña (que es por-sesión, no realmente global), se usa el dominio
`Fetch` de CDP para interceptar la petición de red REAL de la primera
navegación — esto SÍ es un bloqueo a nivel de red, efectivo sin importar
qué sesión CDP disparó la navegación. Validado empíricamente: con
`Fetch.enable` armado, `Fetch.requestPaused` se dispara para la
navegación real y el test de Playwright quedó genuinamente detenido
(sin avanzar) hasta que se llamó `Fetch.continueRequest` — a diferencia
del mecanismo anterior, que Playwright podía sortear sin que nos
diéramos cuenta.

El pausado por depurador (`waitForDebuggerOnStart`) se conserva como
PRIMERA CAPA — no como el mecanismo de sincronización en sí, sino para
tener una ventana de tiempo garantizada, entre la creación de la pestaña
y su primera navegación, en la que armar `Fetch.enable` ANTES de que
cualquier request pueda dispararse. Los comandos de configuración de
dominio (Network.enable/Fetch.enable) sí se pueden enviar mientras el
target está pausado por depurador — solo la ejecución de JS/navegación
queda congelada, no la configuración de CDP en sí.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import websockets
from websockets.exceptions import ConnectionClosed

from ..errors import BrowserLaunchError, TargetTabNotFoundError


class CDPClient:
    """Cliente CDP mínimo: solo lo que este mecanismo necesita (Target/Runtime/
    Network/Fetch). No pretende ser un cliente CDP de propósito general."""

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
        hint="Puede que Chromium haya tardado en arrancar o que el puerto esté ocupado por otro proceso.",
    )


@dataclass
class PausedTarget:
    """La pestaña nueva del spec, con su primera navegación real interceptada
    a nivel de red — no "pausada" en el sentido del depurador (ver docstring
    del módulo para por qué ese mecanismo, usado hasta v0.0.9, no era
    realmente confiable)."""

    client: CDPClient
    session_id: str
    target_id: str
    browser_context_id: str | None = None
    """El contexto de navegador (ventana) al que pertenece esta pestaña.

    Nota importante de v0.1.0a: NO se debe usar este valor para forzar la
    página de la extensión (`ExtensionPage.open`) al mismo contexto — se
    probó exactamente eso y la extensión dejó de cargar ahí
    (`chrome-error://chromewebdata/`): Chrome solo habilita
    extensiones cargadas por línea de comandos (`--load-extension`) en el
    contexto de navegador POR DEFECTO — un contexto adicional creado por
    Playwright Test (que es donde vive la pestaña del spec) no hereda esa
    extensión. Ver `browser/extension_page.py` y el README (sección
    "Por qué hay dos ventanas") para el detalle completo de esta
    restricción real de Chrome, que no se puede evitar sin tocar el
    spec del usuario."""

    _request_id: str | None = None
    """El ID de la petición de red interceptada (dominio Fetch) — lo usa
    `release()` para dejarla continuar. `None` si por algún motivo no se
    pudo armar la intercepción (ver fallback en `BrowserSync.wait_for_new_page`)."""

    async def evaluate(self, expression: str):
        res = await self.client.send("Runtime.evaluate", {"expression": expression, "returnByValue": True}, session_id=self.session_id)
        return res.get("result", {}).get("result", {}).get("value")

    async def release(self) -> None:
        """Deja continuar la navegación real interceptada — a partir de aquí
        el `page.goto(...)` del spec del usuario progresa con normalidad."""
        if self._request_id:
            await self.client.send("Fetch.continueRequest", {"requestId": self._request_id}, session_id=self.session_id)
        else:
            # Fallback: si no se pudo armar Fetch (ver wait_for_new_page),
            # se recurre al mecanismo antiguo — mejor que no liberar nada.
            await self.client.send("Runtime.runIfWaitingForDebugger", session_id=self.session_id)


class BrowserSync:
    """Punto de entrada de alto nivel: conecta al navegador orquestado y arma
    la sincronización, lista para capturar la primera pestaña nueva."""

    def __init__(self, cdp_port: int) -> None:
        self.cdp_port = cdp_port
        self.client: CDPClient | None = None

    async def connect(self) -> None:
        await wait_for_cdp_ready(self.cdp_port)
        ws_url = _get_browser_ws_url(self.cdp_port)
        self.client = CDPClient(ws_url)
        await self.client.connect()
        # El pausado por depurador se usa SOLO como ventana de tiempo para
        # armar Fetch.enable antes de que cualquier navegacion pueda
        # dispararse (ver docstring del modulo) — no como el mecanismo de
        # sincronizacion real.
        await self.client.send("Target.setDiscoverTargets", {"discover": True})
        await self.client.send(
            "Target.setAutoAttach",
            {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True},
        )

    async def wait_for_new_page(self, timeout: float = 30) -> PausedTarget:
        """Espera la primera pestaña nueva de tipo 'page' — la que el spec del
        usuario está a punto de usar — arma la intercepción de red sobre su
        primera navegación real, y la devuelve lista para que el llamador
        haga su trabajo mientras esa navegación sigue retenida."""
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
        session_id = params["sessionId"]
        target_id = params["targetInfo"]["targetId"]
        browser_context_id = params["targetInfo"].get("browserContextId")

        # Mientras el target sigue pausado por depurador (nuestra sesion no
        # lo ha liberado todavia), armamos Fetch.enable — esto SI se puede
        # hacer con el target pausado, solo la ejecucion de JS/navegacion
        # queda congelada, no la configuracion de dominios CDP.
        request_id: str | None = None
        try:
            await self.client.send("Network.enable", session_id=session_id)
            await self.client.send(
                "Fetch.enable",
                {"patterns": [{"urlPattern": "*", "resourceType": "Document"}]},
                session_id=session_id,
            )
            # Ahora si liberamos NUESTRA pausa por depurador — la sesion de
            # Playwright liberara la suya por su cuenta como siempre (ver
            # docstring del modulo), pero ya no importa: la navegacion real
            # quedara retenida por Fetch, sin importar cual sesion la dispare.
            await self.client.send("Runtime.runIfWaitingForDebugger", session_id=session_id)
            fetch_ev = await self.client.wait_event(
                "Fetch.requestPaused",
                predicate=lambda p: p.get("resourceType") == "Document",
                timeout=timeout,
            )
            request_id = fetch_ev["params"]["requestId"]
        except TimeoutError:
            # No deberia pasar en el uso normal (siempre hay una navegacion
            # real que interceptar), pero si pasa, es mejor seguir sin la
            # intercepcion de red (release() cae a su fallback) que fallar
            # toda la corrida por esto.
            request_id = None

        return PausedTarget(
            client=self.client,
            session_id=session_id,
            target_id=target_id,
            browser_context_id=browser_context_id,
            _request_id=request_id,
        )

    async def close(self) -> None:
        if self.client:
            await self.client.close()
