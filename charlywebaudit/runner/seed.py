"""
runner/seed.py — Aplica la configuración de charlyWebAudit sobre la
extensión al inicio de cada corrida (Asistente IA, paleta de colores,
variables globales vigiladas, dominio permitido — v0.1.5).

v0.1.5: reintegrado al flujo principal como parte del modo con extensión
(`cfg.test.use_extension` — ver `__main__.run_audit`). Se agregó
`seed_domain_allowlist`: usa el dominio de la URL de la prueba para
configurar automáticamente el dominio permitido en la extensión.

Dado que el perfil de Chromium es efímero por corrida (ver la nota de
diseño en browser/config_gen.py — un spec con `import { test } from
'@playwright/test'` estándar no admite forzar un perfil persistente sin que
el spec importe un fixture propio), la "memoria" de la configuración de la
extensión no vive en el navegador: vive en el config.json de charlyWebAudit,
y se re-aplica aquí sobre la extensión recién cargada, cada vez. El efecto
para el usuario es el mismo que pidió (se configura una vez, se reutiliza
después) — solo que la reutilización ocurre a nivel de charlyWebAudit, no
del perfil del navegador.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..config import AppConfig
from ..browser.extension_page import ExtensionPage
from ..errors import RecordingError


def domain_from_url(url: str) -> str:
    """Extrae el dominio (host, sin puerto) de una URL — usado para
    configurar automáticamente el dominio permitido de la extensión a
    partir de la URL de la prueba (punto 1 del pedido v0.1.5)."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.hostname or url


async def seed_domain_allowlist(ext: ExtensionPage, cfg: AppConfig) -> None:
    """Punto 1 del pedido v0.1.5: usa el dominio de la URL de la prueba
    para establecerlo como dominio permitido en la extensión — vive en la
    misma sección de Ajustes que el Asistente/perfil (`#cfg-domains`,
    guardado con `#cfg-settings-save`, distinto botón del de Captura)."""
    if not cfg.test.url:
        return
    domain = domain_from_url(cfg.test.url)
    await ext.click("#open-settings")
    await ext.wait_for_selector("#cfg-domains")
    await ext.fill("#cfg-domains", domain)
    await ext.click("#cfg-settings-save")
    await ext.click("#close-settings")


async def seed_assistant_config(ext: ExtensionPage, cfg: AppConfig) -> None:
    await ext.click("#open-settings")
    await ext.wait_for_selector("#cfg-provider")
    await ext.select_option("#cfg-provider", cfg.assistant.provider)
    await ext.fill("#cfg-model", cfg.assistant.model)
    await ext.fill("#cfg-key", cfg.assistant.api_key)
    await ext.click("#cfg-save")
    await ext.click("#close-settings")


async def seed_palette(ext: ExtensionPage, cfg: AppConfig) -> None:
    await ext.click("#open-palette")
    await ext.wait_for_selector("#pal-brand")
    await ext.fill("#pal-brand", cfg.palette.brand)
    await ext.click("#pal-save")  # persiste en localStorage; #close-palette solo cierra sin guardar


async def seed_capture_config(ext: ExtensionPage, cfg: AppConfig) -> None:
    """Variables globales vigiladas: viven en la tarjeta de Captura de
    Auditoría (un textarea de una línea por variable)."""
    await ext.click("#tab-btn-qa")
    await ext.wait_for_selector("#act-cfg")
    await ext.click("#act-cfg")
    await ext.wait_for_selector("#cfg-globals")
    joined = "\n".join(cfg.capture.watched_globals)
    await ext.fill("#cfg-globals", joined)
    await ext.click("#cfg-capture-apply")


async def seed_pre_release(ext: ExtensionPage, cfg: AppConfig) -> None:
    """Solo la configuración de Captura (variables vigiladas) — lo único que
    de verdad afecta QUÉ se graba, por eso es lo único que debe aplicarse
    ANTES de liberar la pausa de la pestaña del spec. Rápido a propósito
    (un solo campo + un botón): Playwright Test tiene un vigilante interno
    con paciencia limitada — si el navegador queda "sin responder" (la
    pestaña pausada) demasiado tiempo, lo da por colgado y lo cierra."""
    try:
        await seed_capture_config(ext, cfg)
    except RecordingError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RecordingError(f"No se pudo aplicar la configuración de captura: {exc}") from exc


async def seed_post_release(ext: ExtensionPage, cfg: AppConfig) -> None:
    """Asistente IA y paleta — no afectan qué se captura durante la
    grabación (solo el análisis posterior y la apariencia visual), así que
    se aplican DESPUÉS de confirmar que la grabación ya está activa, sin
    ninguna presión de tiempo."""
    try:
        await seed_assistant_config(ext, cfg)
        await seed_palette(ext, cfg)
    except RecordingError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RecordingError(f"No se pudo aplicar la configuración del Asistente/paleta: {exc}") from exc


async def seed_all(ext: ExtensionPage, cfg: AppConfig) -> None:
    """Aplica los tres bloques en orden, sin la restricción de tiempo del
    flujo real de corrida — útil para pruebas o para aplicar configuración
    fuera de una corrida (p. ej. desde el menú de configuración)."""
    await seed_pre_release(ext, cfg)
    await seed_post_release(ext, cfg)
