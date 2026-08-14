"""
timezone_utils.py — Punto 1 del pedido: el Dashboard mostraba las fechas
en UTC (la hora en la que se guarda cada `RunRecord`, ver `history.py`),
distinta de la hora de la máquina del usuario. Este módulo detecta la
zona horaria del sistema por defecto, y permite convertir cualquier
timestamp UTC guardado a la zona horaria que corresponda mostrar —
la detectada automáticamente, o la que el usuario elija explícitamente
(ver `AppConfig.timezone`, `gui/views/dashboard_view.py`).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone as _dt_timezone
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

# Lista curada de zonas comunes para el selector — el universo completo de
# `available_timezones()` tiene ~600 entradas, poco práctico para un combo;
# quien necesite una zona fuera de esta lista puede escribirla a mano (el
# combo no es de solo-lectura), validada contra el universo real de todas
# formas.
COMMON_TIMEZONES = [
    "America/Mexico_City",
    "America/Bogota",
    "America/Lima",
    "America/Santiago",
    "America/Argentina/Buenos_Aires",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/Madrid",
    "Europe/London",
    "Europe/Paris",
    "UTC",
]


def detect_local_timezone() -> str:
    """Detecta la zona horaria del sistema — intenta obtener un nombre
    IANA real primero (`/etc/timezone`, o resolviendo el symlink de
    `/etc/localtime`, la forma estándar en Linux/macOS); si no puede (por
    ejemplo, en Windows, o un sistema sin esa información disponible), cae
    a un offset fijo calculado de la hora local actual — la HORA que
    muestra siempre es correcta de todas formas, aunque le falte un
    nombre IANA propiamente dicho."""
    try:
        tz_path = Path("/etc/timezone")
        if tz_path.is_file():
            name = tz_path.read_text(encoding="utf-8").strip()
            if name and name in available_timezones():
                return name
    except OSError:
        pass

    try:
        localtime = Path("/etc/localtime")
        if localtime.is_symlink():
            target = os.readlink(localtime)
            if "zoneinfo/" in target:
                name = target.split("zoneinfo/", 1)[-1]
                if name in available_timezones():
                    return name
    except OSError:
        pass

    # Respaldo universal: offset fijo desde la hora local del sistema —
    # funciona en cualquier plataforma, sin depender de archivos que
    # pueden no existir (Windows, contenedores minimalistas).
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    h, m = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{h:02d}:{m:02d}"


def resolve_tzinfo(tz_name: str):
    """Convierte un nombre de zona (IANA real, o el formato de respaldo
    `UTC±HH:MM` que devuelve `detect_local_timezone()`) en un `tzinfo`
    utilizable. Si el nombre no se puede resolver de ninguna forma, cae a
    UTC — nunca lanza, para que un dato guardado con una zona vieja/rara
    no rompa el Dashboard."""
    if tz_name.startswith("UTC+") or tz_name.startswith("UTC-"):
        try:
            sign = 1 if tz_name[3] == "+" else -1
            h, m = tz_name[4:].split(":")
            return _dt_timezone(sign * timedelta(hours=int(h), minutes=int(m)))
        except (ValueError, IndexError):
            return _dt_timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return _dt_timezone.utc


def format_local(iso_utc: str, tz_name: str, *, with_seconds: bool = True) -> str:
    """Convierte un timestamp ISO 8601 en UTC (el formato en que
    `history.now_iso()` guarda cada corrida) a la zona horaria indicada,
    devuelto como texto legible ("2026-08-13 16:08:21"). Nunca lanza —
    ante cualquier timestamp mal formado, devuelve el string original tal
    cual, para no romper la tabla del Dashboard por un solo dato raro."""
    try:
        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt_timezone.utc)
        local_dt = dt.astimezone(resolve_tzinfo(tz_name))
        fmt = "%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M"
        return local_dt.strftime(fmt)
    except (ValueError, TypeError):
        return iso_utc
