@echo off
chcp 65001 >nul
title Actualizar informes - Asistente de Movilidad Internacional
rem Procesa los informes PDF nuevos o modificados de la carpeta de informes.
call "%~dp0_preparar.bat"
if errorlevel 1 goto fin
echo.
echo Buscando informes nuevos o modificados...
python ingesta.py
:fin
echo.
pause
