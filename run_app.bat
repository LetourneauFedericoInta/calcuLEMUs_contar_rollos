@echo off
title Conteo de Rollizos - Web Application
echo ==========================================================
echo       INICIANDO APLICACION - CONTEO DE ROLLIZOS
echo ==========================================================
echo.

:: Check if the virtual environment folder exists
if not exist ".venv" (
    echo [ERROR] No se encuentra el entorno virtual ".venv" en esta carpeta.
    echo Por favor ejecute este archivo desde el directorio raiz del proyecto.
    echo.
    pause
    exit /b
)

echo [1/2] Abriendo su navegador predeterminado en: http://127.0.0.1:8000
:: Opens the default browser asynchronously so it doesn't block the terminal
start "" "http://127.0.0.1:8000"

echo [2/2] Iniciando el servidor FastAPI (Uvicorn)...
echo.
echo ==========================================================
echo        EL SERVIDOR ESTA EN MARCHA Y ACTIVO
echo    Para apagar el servidor, cierre esta ventana (terminal)
echo                o presione CTRL + C.
echo ==========================================================
echo.

:: Run the FastAPI server using the isolated virtual environment Python interpreter
.venv\Scripts\python run.py

pause
