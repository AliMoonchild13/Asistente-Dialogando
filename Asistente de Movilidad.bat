@echo off
chcp 65001 >nul
title Asistente de Movilidad Internacional
cd /d "%~dp0"
echo ==============================================================
echo   Asistente de Movilidad Internacional
echo   Oficina Dialogando con el Mundo
echo ==============================================================

rem --- 1. Programas necesarios --------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] No se encuentra Python. Debe instalarlo la oficina de Sistemas
  echo         marcando la opcion "Add python.exe to PATH".
  goto error
)
docker --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] No se encuentra Docker Desktop. Debe instalarlo la oficina de Sistemas.
  goto error
)

rem --- 2. Configuracion (antes de crear la base por primera vez) ----
python iniciar.py --verificar
if errorlevel 1 goto error

rem --- 3. Librerias de Python (solo la primera vez o si cambian) ----
fc /b requirements.txt .librerias_instaladas >nul 2>&1
if errorlevel 1 (
  echo.
  echo Instalando las librerias de Python. La primera vez tarda varios minutos...
  python -m pip install --upgrade pip >nul 2>&1
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] No se pudieron instalar las librerias. Revisa la conexion a internet.
    goto error
  )
  copy /y requirements.txt .librerias_instaladas >nul
)

rem --- 4. Docker y base de datos --------------------------------------
docker info >nul 2>&1
if not errorlevel 1 goto docker_listo
echo.
echo Abriendo Docker Desktop. Puede tardar uno o dos minutos...
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a _n=0
:esperar_docker
timeout /t 3 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_listo
set /a _n+=1
if %_n% geq 60 (
  echo [ERROR] Docker Desktop no respondio. Abrelo desde el menu Inicio,
  echo         espera a que diga "Engine running" y vuelve a abrir este archivo.
  goto error
)
goto esperar_docker
:docker_listo
docker compose up -d
if errorlevel 1 (
  echo [ERROR] No se pudo encender la base de datos. Revisa el mensaje de arriba.
  goto error
)

rem --- 5. Datos y asistente ---------------------------------------------
python iniciar.py %*
if errorlevel 1 goto error
exit /b 0

:error
echo.
echo El asistente no pudo iniciar. Revisa el mensaje de arriba.
pause
exit /b 1
