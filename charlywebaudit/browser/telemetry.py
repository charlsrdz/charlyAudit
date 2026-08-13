"""
browser/telemetry.py — Punto 3 del pedido v0.1.1: rastrear el estado real
del navegador durante una corrida, sin orquestar nada sobre él (a
diferencia del mecanismo de sincronización que usaba la extensión, ya
retirado del flujo principal — ver __main__.run_audit y el punto 1 del
pedido). Se conecta por CDP de forma puramente PASIVA: solo observa,
nunca pausa ni retiene nada.

Nota de diseño importante, encontrada validando esto con un cierre real
(matando Chrome a mano a mitad de una corrida): el diseño original
intentaba distinguir "cierre normal" de "cierre inesperado" comparando
EN QUÉ MOMENTO nuestra propia telemetría detectó la desconexión contra en
qué momento el proceso de Node/Playwright Test terminó — pero estas son
DOS relojes independientes con retrasos de detección impredecibles entre
sí: en una prueba real, Node notició la muerte de Chrome casi al
instante (tiene acceso directo al proceso hijo, vía las APIs del sistema
operativo), mientras que nuestra propia detección por CDP (que solo
puede notar la desconexión cuando un comando se queda sin respuesta) tardó
segundos más — dando una comparación de timestamps que, aunque cada uno
era correcto en sí mismo, llevaba a una conclusión incorrecta ("cierre
normal" para un cierre que en realidad fue forzado a mano). Por eso el
diseño actual NO compara timestamps de detección — usa lo que Playwright
Test mismo reportó (código de salida, si el reporte JSON se generó
correctamente) como la señal principal, con la telemetría CDP como
corroboración de si la conexión se mantuvo viva o se perdió en algún
momento. Ver `evaluate_browser_outcome()`, que combina ambas señales.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import websockets
from websockets.exceptions import ConnectionClosed

from .cdp_sync import BrowserSync, wait_for_cdp_ready

if TYPE_CHECKING:
    from ..reporter import Reporter


# Timeout corto para los sondeos de salud de la conexion — un valor alto
# (el de 15s por defecto de CDPClient.send, pensado para operaciones CDP
# normales) haria que detectar una conexion realmente muerta tardara
# demasiado para que la telemetria sea util en tiempo real.
_HEALTH_CHECK_TIMEOUT = 2.0
_POLL_INTERVAL = 0.25
_CONSECUTIVE_FAILURES_THRESHOLD = 3
"""Cuántos sondeos de salud fallidos SEGUIDOS hacen falta antes de declarar
la conexión perdida — ver docstring de `_monitor_loop` para el bug real
que esto corrige (un solo sondeo lento durante tráfico CDP real no
significa que el navegador se cerró). Con el intervalo y timeout de
arriba, el peor caso para confirmar una desconexión real son unos ~6s
(3 intentos × 2s cada uno) — sigue siendo rápido para detectar un cierre
genuino, pero mucho más resistente a congestión transitoria."""


def _collect_diagnostic_snapshot() -> str:
    """Diagnóstico ampliado, capturado en el momento exacto en que se
    detecta que el navegador dejó de responder — memoria disponible del
    sistema y, en Linux, cualquier señal reciente de que el OOM-killer
    (el mecanismo del kernel que mata procesos cuando la memoria se agota)
    actuó. Todo esto es best-effort: si algo falla (permisos, plataforma
    sin /proc, sin journalctl/dmesg disponible), se omite en silencio —
    esto es información de diagnóstico adicional, nunca debe interrumpir
    ni hacer fallar la corrida real."""
    parts = []

    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    meminfo[key] = int(rest.strip().split()[0]) // 1024  # KB -> MB
        if "MemTotal" in meminfo and "MemAvailable" in meminfo:
            parts.append(f"Memoria disponible: {meminfo['MemAvailable']}MB de {meminfo['MemTotal']}MB.")
    except (OSError, ValueError, IndexError):
        pass  # no es Linux, o /proc/meminfo no esta disponible — no es critico

    try:
        import subprocess

        result = subprocess.run(
            ["dmesg", "-T"], capture_output=True, text=True, timeout=3, check=False
        )
        if result.returncode == 0:
            oom_lines = [
                line for line in result.stdout.splitlines()[-200:]
                if "out of memory" in line.lower() or "oom-killer" in line.lower() or "killed process" in line.lower()
            ]
            if oom_lines:
                parts.append("Señal de OOM-killer encontrada en el registro del sistema: " + oom_lines[-1].strip())
    except Exception:
        pass  # dmesg puede no estar disponible o requerir permisos — best-effort, no critico

    if not parts:
        return "(sin diagnóstico adicional disponible en este sistema)"
    return " ".join(parts)


class BrowserOutcome(Enum):
    NEVER_CONNECTED = "never_connected"
    """El puerto de depuración nunca respondió — el navegador no llegó a
    arrancar, o CDP no quedó disponible dentro del tiempo esperado."""
    CLOSED_NORMALLY = "closed_normally"
    """La prueba de Playwright terminó con normalidad (código de salida
    limpio o con fallos propios del test, no de infraestructura) — el
    cierre del navegador que sigue a eso es el esperado, no un problema."""
    CLOSED_UNEXPECTEDLY = "closed_unexpectedly"
    """La conexión con el navegador se perdió en algún momento de la
    corrida, Y la salida de Playwright Test muestra señales de una
    interrupción de infraestructura (no solo un test que falló
    normalmente) — el navegador se cerró solo, o alguien lo cerró
    manualmente, en medio de la corrida."""
    STILL_ALIVE = "still_alive"
    """La conexión seguía viva la última vez que se consultó (uso interno,
    mientras la corrida sigue en curso)."""


@dataclass
class BrowserTelemetryReport:
    outcome: BrowserOutcome
    connected_at: float | None
    """Segundos desde el inicio del monitoreo hasta que CDP respondió por
    primera vez, o None si nunca respondió."""
    connection_lost: bool
    """True si en algún momento de la corrida la conexión CDP dejó de
    responder — corroboración de la telemetría, no la señal principal
    (ver docstring del módulo para por qué)."""

    @property
    def description(self) -> str:
        if self.outcome is BrowserOutcome.NEVER_CONNECTED:
            return "El navegador nunca abrió el puerto de depuración — no se pudo confirmar que haya arrancado."
        if self.outcome is BrowserOutcome.CLOSED_UNEXPECTEDLY:
            return (
                "El navegador se cerró de forma inesperada durante la corrida — "
                "pudo cerrarse solo (un crash) o alguien lo cerró manualmente."
            )
        if self.connection_lost:
            return "El navegador se cerró al terminar la corrida (la conexión se perdió, pero la prueba ya había concluido con normalidad)."
        return "El navegador se mantuvo conectado durante toda la corrida, sin cortes detectados."


_INFRA_ERROR_MARKERS = (
    "target closed",
    "browser has disconnected",
    "protocol error",
    "browser has been closed",
    "connection closed",
    "econnrefused",
    "websocket error",
)


def evaluate_browser_outcome(
    *, connected: bool, connection_lost: bool, exit_code: int, raw_stdout: str
) -> BrowserOutcome:
    """Combina lo que Playwright Test reportó (señal principal — tiene
    acceso directo al proceso del navegador, su detección de una muerte
    real es más rápida y confiable que la nuestra por CDP) con la
    telemetría propia (corroboración) para decidir si el cierre fue
    normal o inesperado. Ver docstring del módulo para el porqué de este
    diseño."""
    if not connected:
        return BrowserOutcome.NEVER_CONNECTED
    if not connection_lost:
        return BrowserOutcome.CLOSED_NORMALLY
    lower = raw_stdout.lower()
    infra_failure = exit_code != 0 and any(marker in lower for marker in _INFRA_ERROR_MARKERS)
    return BrowserOutcome.CLOSED_UNEXPECTEDLY if infra_failure else BrowserOutcome.CLOSED_NORMALLY


class BrowserTelemetry:
    """Monitorea el navegador orquestado de forma puramente pasiva —
    conecta por CDP, sondea periódicamente que la conexión siga viva, y
    nunca envía ningún comando que module su comportamiento (a diferencia
    de `BrowserSync`, que sí lo hacía para sincronizar con la extensión).

    `reporter`: opcional — si se pasa, registra un pulso periódico
    ("el navegador sigue conectado a los Xs") y, si detecta una
    desconexión, un diagnóstico ampliado (memoria disponible del sistema,
    señales de OOM-killer en el registro del sistema si es Linux). Se
    agregó a pedido explícito, tras un reporte real de producción: un
    navegador que se cerraba solo tras aproximadamente un minuto, sin
    ningún registro previo que ayudara a entender por qué — con esto, la
    próxima vez que pase, va a quedar un rastro real de qué estaba
    pasando en el sistema en ese momento."""

    _HEARTBEAT_INTERVAL = 15.0  # segundos entre cada pulso registrado

    def __init__(self, cdp_port: int, reporter: "Reporter | None" = None) -> None:
        self.cdp_port = cdp_port
        self.reporter = reporter
        self._connected_at: float | None = None
        self._connection_lost = False
        self._t0 = time.monotonic()
        self._sync: BrowserSync | None = None
        self._monitor_task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    async def start(self, *, connect_timeout: float = 30) -> bool:
        """Intenta conectar — devuelve False si el navegador nunca abrió el
        puerto de depuración dentro del tiempo esperado (no es un error
        fatal para quien llama: solo significa que no hay telemetría que
        ofrecer, la corrida puede seguir su curso igual)."""
        try:
            await wait_for_cdp_ready(self.cdp_port, timeout=connect_timeout)
        except Exception:
            return False
        self._connected_at = time.monotonic() - self._t0
        self._sync = BrowserSync(self.cdp_port)
        try:
            await self._sync.connect()
        except Exception:
            return False
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        return True

    async def _monitor_loop(self) -> None:
        """Sondea la conexión cada 250ms — un `Target.getTargets` liviano
        con timeout corto (ver `_HEALTH_CHECK_TIMEOUT`) es suficiente para
        notar si la conexión sigue respondiendo.

        Bug real corregido, encontrado en producción: un solo sondeo que
        no responde a tiempo NO significa que el navegador se cerró —
        mientras un test corre de verdad, Chrome está ocupado procesando
        el tráfico CDP real de Playwright Test (navegación, DOM, red), y
        nuestro propio sondeo liviano puede quedar en cola detrás de eso
        y tardar más de `_HEALTH_CHECK_TIMEOUT` sin que la conexión esté
        realmente rota. Confirmado con un caso real: la telemetría avisó
        "el navegador dejó de responder" a los 17.9s, en plena mitad de
        una corrida que seguía perfectamente viva — el test terminó 8.5s
        después con un resultado real y válido. Ahora se exigen varios
        fallos CONSECUTIVOS (`_CONSECUTIVE_FAILURES_THRESHOLD`) antes de
        declarar la conexión perdida — un solo sondeo lento no dispara
        nada, y si el siguiente sondeo responde bien, el contador se
        reinicia (recuperación de un bache transitorio, no un cierre
        real)."""
        last_heartbeat = 0.0
        consecutive_failures = 0
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=_POLL_INTERVAL)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self._sync.client.send("Target.getTargets", retries=0, timeout=_HEALTH_CHECK_TIMEOUT)
            except (ConnectionClosed, ConnectionError, OSError, asyncio.TimeoutError):
                consecutive_failures += 1
                if consecutive_failures < _CONSECUTIVE_FAILURES_THRESHOLD:
                    continue  # puede ser solo congestion de CDP real — se reintenta antes de alarmar
                self._connection_lost = True
                if self.reporter:
                    elapsed = time.monotonic() - self._t0
                    self.reporter.warning(
                        f"El navegador dejó de responder a los {elapsed:.1f}s de iniciada la telemetría "
                        f"(sin respuesta en {_CONSECUTIVE_FAILURES_THRESHOLD} sondeos consecutivos). "
                        f"{_collect_diagnostic_snapshot()}"
                    )
                return
            else:
                consecutive_failures = 0  # el sondeo respondio bien -- se recupero de cualquier bache anterior

            elapsed = time.monotonic() - self._t0
            if self.reporter and elapsed - last_heartbeat >= self._HEARTBEAT_INTERVAL:
                last_heartbeat = elapsed
                self.reporter.info(f"Navegador sigue conectado ({elapsed:.0f}s transcurridos).")

    async def stop(self, *, exit_code: int = 0, raw_stdout: str = "") -> BrowserTelemetryReport:
        """Detiene el monitoreo y devuelve el resumen final. `exit_code`/
        `raw_stdout` son los del proceso de Playwright Test que ya
        terminó — se usan como la señal principal (ver
        `evaluate_browser_outcome`), no la telemetría propia sola."""
        self._stopped.set()
        if self._monitor_task:
            try:
                await asyncio.wait_for(self._monitor_task, timeout=3)
            except (asyncio.TimeoutError, Exception):
                pass

        connected = self._connected_at is not None

        # Verificacion final explicita: si el bucle de monitoreo no llego a
        # notar una desconexion real antes de que stop() lo detuviera, esta
        # ultima comprobacion la atrapa (evita un falso "conexion viva").
        if connected and not self._connection_lost and self._sync is not None:
            try:
                await self._sync.client.send("Target.getTargets", retries=0, timeout=_HEALTH_CHECK_TIMEOUT)
            except (ConnectionClosed, ConnectionError, OSError, asyncio.TimeoutError):
                self._connection_lost = True
                if self.reporter:
                    self.reporter.warning(f"El navegador ya no respondía al finalizar la corrida. {_collect_diagnostic_snapshot()}")

        if self._sync:
            try:
                await self._sync.close()
            except Exception:
                pass

        outcome = evaluate_browser_outcome(
            connected=connected, connection_lost=self._connection_lost, exit_code=exit_code, raw_stdout=raw_stdout
        )
        return BrowserTelemetryReport(outcome, self._connected_at, self._connection_lost)
