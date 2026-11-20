@echo off
chcp 65001 >nul
title Instalacion - Asistente de Movilidad Internacional
echo ==============================================================
echo   Instalacion del Asistente de Movilidad Internacional
echo ==============================================================
echo.
call "%~dp0_preparar.bat"
if errorlevel 1 goto error

echo.
echo [Paso 1 de 2] Instalando las librerias de Python. Puede tardar varios minutos...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] No se pudieron instalar las librerias. Revisa la conexion a internet.
  goto error
)

echo.
echo [Paso 2 de 2] Cargando la base de movilidad y los informes...
call "%~dp0Recargar datos.bat" sin_pausa
if errorlevel 1 goto error

echo.
echo ==============================================================
echo   Instalacion terminada.
echo   Para usar el asistente: doble clic en "Iniciar asistente.bat"
echo ==============================================================
pause
exit /b 0

:error
echo.
echo La instalacion NO termino. Corrige el problema indicado y vuelve a ejecutar este archivo.
pause
exit /b 1
