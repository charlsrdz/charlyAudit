"""
config.py — Configuración persistente de charlyWebAudit.

Vive como un único archivo JSON en la ruta estándar de configuración del
sistema operativo (via `platformdirs`) — no se inventa una ubicación propia.
Se separa en tres bloques, cada uno con su propio ciclo de vida:

  - `test`: URL, script Playwright, cabeceras personalizadas — se
    reconfigura en cada corrida si el usuario lo pide, pero se recuerda la
    última combinación como punto de partida.
  - `assistant`: proveedor/modelo/API key del Asistente IA — se pide UNA
    vez (ver 6.1.4 del pedido original) y de ahí en adelante solo se
    ofrece actualizar, nunca se vuelve a pedir desde cero.
  - `browser`: ruta del perfil persistente de Chromium y si ya se sembró
    la configuración por defecto de la extensión en él.

El archivo se escribe con permisos 600 (solo el usuario dueño puede leerlo)
porque contiene una API key en texto plano — no es cifrado real, pero evita
como mínimo que quede legible para cualquier otro usuario del sistema.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

from .constants import (
    APP_SLUG,
    DEFAULT_ASSISTANT_CONFIG,
)
from .errors import ConfigError

CONFIG_DIR = Path(user_config_dir(APP_SLUG, appauthor=False))
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class TestConfig:
    spec_path: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class TestCase:
    """Una prueba GUARDADA y nombrada — punto 5 del pedido v0.1.0a: el
    catálogo. A diferencia de `TestConfig` (la prueba "activa/rápida" de
    siempre, sin cambios), un `TestCase` vive en una lista con nombre
    propio, pensado para correr una y otra vez y comparar resultados en
    el tiempo (ver `history.py` para el registro de cada corrida)."""

    id: str
    name: str
    spec_path: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class AssistantConfig:
    configured: bool = False
    provider: str = DEFAULT_ASSISTANT_CONFIG["provider"]
    model: str = DEFAULT_ASSISTANT_CONFIG["model"]
    api_key: str = DEFAULT_ASSISTANT_CONFIG["apiKey"]


@dataclass
class AppConfig:
    test: TestConfig = field(default_factory=TestConfig)
    assistant: AssistantConfig = field(default_factory=AssistantConfig)
    test_catalog: list[TestCase] = field(default_factory=list)
    """Punto 5 del pedido v0.1.0a: pruebas guardadas con nombre, para
    correr repetidamente y comparar resultados en el dashboard."""
    timezone: str = ""
    """Punto 1 del pedido (mejora de Dashboard): zona horaria en la que se
    muestran las fechas del historial de corridas. Vacío = detectar
    automáticamente la del sistema (ver `timezone_utils.detect_local_timezone`)
    — el usuario puede elegir una distinta desde el Dashboard."""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AppConfig":
        cfg = AppConfig()
        for section_name, section_cls in (
            ("test", TestConfig),
            ("assistant", AssistantConfig),
        ):
            raw = data.get(section_name) or {}
            # Filtra claves desconocidas en vez de fallar — una version futura
            # puede agregar campos sin romper un config.json ya existente.
            known = {k: v for k, v in raw.items() if k in section_cls.__dataclass_fields__}
            setattr(cfg, section_name, section_cls(**known))
        cfg.test_catalog = [
            TestCase(**{k: v for k, v in tc.items() if k in TestCase.__dataclass_fields__})
            for tc in data.get("test_catalog", [])
        ]
        cfg.timezone = data.get("timezone", "")
        return cfg


def load_config() -> AppConfig:
    """Carga la configuración persistida, o devuelve una con valores por
    defecto (nunca falla si el archivo no existe todavía — ese es el estado
    normal en la primera ejecución)."""
    if not CONFIG_FILE.exists():
        return AppConfig()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ConfigError(
            f"No se pudo leer la configuración guardada en {CONFIG_FILE}: {exc}",
            hint="Si el archivo se corrompió, puedes eliminarlo y charlyWebAudit "
            "volverá a pedir la configuración desde cero.",
        ) from exc
    return AppConfig.from_dict(data)


def save_config(cfg: AppConfig) -> None:
    """Persiste la configuración, con permisos restringidos donde el sistema
    operativo lo soporte (contiene una API key en texto plano).

    Nota multiplataforma: en Windows, `os.chmod` con banderas POSIX no
    ofrece la misma protección que en Linux/macOS — el modelo de permisos
    de Windows es distinto (ACLs, no bits rwx), y `os.chmod` ahí solo puede
    alternar el atributo de solo-lectura como aproximación. Por eso el
    endurecimiento de permisos es un intento aparte, best-effort: si el
    archivo se escribió bien pero el ajuste de permisos falla o no aplica
    igual en esta plataforma, la corrida no debe abortar por eso — la
    escritura del archivo en sí es lo único crítico aquí."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(cfg.to_json(), encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"No se pudo guardar la configuración en {CONFIG_FILE}: {exc}",
            hint="Verifica permisos de escritura en la carpeta de configuración.",
        ) from exc
    try:
        os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 600 (aproximado en Windows)
    except OSError:
        pass  # best-effort: el archivo ya se guardo correctamente
