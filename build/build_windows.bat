@echo off
REM build/build_windows.bat — Wrapper de conveniencia para Windows.
REM
REM El trabajo real lo hace build_installer.py (el mismo script, sin
REM cambios, corre igual en los tres sistemas operativos) — esto solo evita
REM tener que escribir "python build\build_installer.py" a mano.
cd /d "%~dp0.."
python build\build_installer.py
