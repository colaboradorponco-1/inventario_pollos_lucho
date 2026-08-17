@echo off
chcp 65001 >nul
title Sistema de Inventario - Pollos Lucho
cd /d "%~dp0"

echo.
echo   SISTEMA DE INVENTARIO - POLLOS LUCHO
echo   ------------------------------
echo   Iniciando servidor local...
echo.

python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo   Instalando dependencias (solo la primera vez)...
    python -m pip install flask
)

start "" http://localhost:5000
python app.py

pause
