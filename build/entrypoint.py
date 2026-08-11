"""
build/entrypoint.py — Punto de entrada para PyInstaller.

Apuntar PyInstaller directamente a `charlywebaudit/__main__.py` lo ejecuta
como un script suelto, sin paquete padre — y sus imports relativos internos
(`from .browser.chromium import ...`) fallan con "attempted relative import
with no known parent package". Este archivo, fuera del paquete, importa
`charlywebaudit` normalmente (como lo haría cualquier código que lo use
como librería) y evita el problema de raíz.
"""

from charlywebaudit.__main__ import main

if __name__ == "__main__":
    main()
