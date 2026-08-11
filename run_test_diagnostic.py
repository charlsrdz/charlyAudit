import asyncio
from charlywebaudit.__main__ import run_audit
from charlywebaudit.config import AppConfig

# Configurar minimamente
cfg = AppConfig()
cfg.test.spec_path = "/home/lap140/Descargas/example.spec.ts"
cfg.test.url = "https://plataforma.redgps.com/login"

# Correr directamente
asyncio.run(run_audit(cfg))
