@echo off
chcp 65001 >nul
title Asistente de Movilidad Internacional
echo Iniciando el Asistente de Movilidad Internacional...
call "%~dp0_preparar.bat"
if errorlevel 1 goto error

echo.
echo ==============================================================
echo   El asistente se abre en el navegador: http://localhost:8000
echo.
echo   NO CIERRES ESTA VENTANA mientras lo estes usando.
echo   Para apagar el asistente, cierra esta ventana.
echo ==============================================================
echo.
python api.py
echo.
echo El asistente se detuvo. Si no lo cerraste tu, revisa el mensaje de arriba.
echo (Si dice que el puerto 8000 esta en uso, el asistente ya estaba abierto en otra ventana.)
pause
exit /b 0

:error
echo.
echo No se pudo iniciar el asistente. Revisa el mensaje de arriba.
pause
exit /b 1
