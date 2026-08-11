#!/usr/bin/env python3
"""
build/build_installer.py — Construye el binario standalone de charlyWebAudit
con PyInstaller (v0.0.2, punto 4 del pedido).

Este MISMO script produce el binario correcto para el sistema operativo en
el que se corre — es el propio código el que es multiplataforma, no un
único artefacto binario: PyInstaller empaqueta el intérprete de Python y
las dependencias para el SO donde se ejecuta, no compila cruzado. Para
tener los tres binarios (Linux/macOS/Windows) hay que correr este mismo
script una vez en cada sistema operativo — no existe una forma de generar
los tres desde una sola máquina.

Uso:
    pip install -e ".[build]"
    python build/build_installer.py

Produce:
    dist/charlywebaudit          (Linux/macOS, ejecutable único)
    dist/charlywebaudit.exe      (Windows, ejecutable único)
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "charlywebaudit"


def _data_sep() -> str:
    # PyInstaller usa separadores DISTINTOS para --add-data segun el SO:
    # "origen;destino" en Windows, "origen:destino" en Linux/macOS. Es la
    # unica diferencia real entre plataformas en este script — todo lo
    # demas es el mismo comando.
    return ";" if platform.system() == "Windows" else ":"


def _ensure_package_properly_installed() -> None:
    """PyInstaller necesita que `charlywebaudit` sea un paquete instalado
    normalmente (no editable) para resolver correctamente el módulo
    `charlywebaudit.__main__` — confirmado en validación real: sin esto,
    PyInstaller copia los archivos de `--add-data` sin problema (son solo
    archivos, no requieren resolución de import), pero el binario resultante
    falla al arrancar con `ModuleNotFoundError: No module named
    'charlywebaudit.__main__'`, un error que además no aparece durante el
    build en sí — solo al ejecutar el binario ya construido. Por eso esta
    verificación corre ANTES de invocar PyInstaller, no después."""
    try:
        import charlywebaudit
    except ImportError:
        print(
            "charlywebaudit no está instalado. Corre primero:\n"
            "    pip install .\n"
            "(NO 'pip install -e .' — una instalación editable confunde el análisis\n"
            "estático de PyInstaller y produce un binario que compila pero falla al arrancar.)",
            file=sys.stderr,
        )
        sys.exit(1)

    if not getattr(charlywebaudit, "__file__", None):
        print(
            "charlywebaudit se resolvió como paquete de namespace implícito (sin __file__), "
            "no como una instalación real.\nCorre primero:\n"
            "    pip install .\n"
            "(NO 'pip install -e .') y vuelve a intentarlo.",
            file=sys.stderr,
        )
        sys.exit(1)

    pkg_file = Path(charlywebaudit.__file__).resolve()
    if ROOT in pkg_file.parents:
        # El import resolvio al checkout local (comportamiento normal si se
        # corre este script desde la raiz del repo sin haber instalado nada
        # todavia) — no es un error de Python, pero SI produce el binario
        # roto descrito arriba. Se detecta comparando la ruta resuelta
        # contra la raiz del proyecto.
        print(
            "charlywebaudit se resolvió al código fuente local, no a una instalación real.\n"
            "Corre primero:\n"
            "    pip install .\n"
            "(NO 'pip install -e .') y vuelve a intentarlo.",
            file=sys.stderr,
        )
        sys.exit(1)


def _pystray_hidden_imports() -> list[str]:
    """pystray elige su backend en tiempo de ejecución según el sistema
    operativo (`pystray/__init__.py` prueba varios módulos internos en
    orden) — PyInstaller, al analizar estáticamente, no siempre sigue esa
    selección dinámica y puede omitir el backend real que se termina
    usando. Se declara explícitamente el que corresponde a la plataforma
    en la que se está construyendo (no se puede compilar cruzado, así que
    esto siempre coincide con el sistema operativo real del build)."""
    system = platform.system()
    args: list[str] = []
    if system == "Windows":
        args += ["--hidden-import", "pystray._win32"]
    elif system == "Darwin":
        args += ["--hidden-import", "pystray._darwin"]
    else:
        args += ["--hidden-import", "pystray._xorg", "--hidden-import", "pystray._appindicator"]
    return args


def main() -> None:
    if not PACKAGE.is_dir():
        print(f"No se encontró el paquete en {PACKAGE}", file=sys.stderr)
        sys.exit(1)

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller no está instalado. Corre: pip install -e \".[build]\"", file=sys.stderr)
        sys.exit(1)

    _ensure_package_properly_installed()


    sep = _data_sep()
    vendor_src = PACKAGE / "vendor"
    templates_src = PACKAGE / "report" / "templates"

    gui_assets_src = PACKAGE / "gui" / "assets"

    args = [
        "pyinstaller",
        "--name", "charlywebaudit",
        "--onefile",
        "--console",
        "--clean",
        "--noconfirm",
        # Datos empaquetados: la extension CharlyAudit, la plantilla del
        # reporte HTML, los iconos de la GUI, y los recursos internos de
        # tkinterweb (trae su propia hoja de estilos/combobox en .tcl que
        # PyInstaller no detecta por analisis estatico solo, al no ser
        # codigo Python). bundled_extension_path() (__main__.py) ya sabe
        # resolver esta ruta dentro de un binario armado con PyInstaller
        # (via sys._MEIPASS) o corriendo desde codigo fuente normal.
        "--add-data", f"{vendor_src}{sep}charlywebaudit/vendor",
        "--add-data", f"{templates_src}{sep}charlywebaudit/report/templates",
        "--add-data", f"{gui_assets_src}{sep}charlywebaudit/gui/assets",
        "--collect-data", "tkinterweb",
        # questionary/prompt_toolkit resuelven algunos submodulos de forma
        # dinamica — PyInstaller no siempre los detecta por analisis
        # estatico solo, se declaran explicitamente para evitar un binario
        # que arranca pero falla al mostrar el menu interactivo.
        "--hidden-import", "prompt_toolkit.output.win32",
        "--hidden-import", "prompt_toolkit.output.windows10",
        "--hidden-import", "prompt_toolkit.input.win32",
        *_pystray_hidden_imports(),
        str(ROOT / "build" / "entrypoint.py"),
    ]

    print("Corriendo:", " ".join(args))
    subprocess.run(args, cwd=str(ROOT), check=True)

    binary_name = "charlywebaudit.exe" if platform.system() == "Windows" else "charlywebaudit"
    binary_path = ROOT / "dist" / binary_name
    if binary_path.exists():
        size_mb = binary_path.stat().st_size / (1024 * 1024)
        print(f"\nListo: {binary_path} ({size_mb:.1f} MB)")
    else:
        print(f"\nPyInstaller terminó pero no se encontró el binario esperado en {binary_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
