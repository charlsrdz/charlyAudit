#!/usr/bin/env bash
# build/build_unix.sh — Wrapper de conveniencia para Linux/macOS.
#
# El trabajo real lo hace build_installer.py (el mismo script, sin cambios,
# corre igual en los tres sistemas operativos) — esto solo evita tener que
# escribir "python3 build/build_installer.py" a mano.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 build/build_installer.py
