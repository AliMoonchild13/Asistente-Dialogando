@echo off
rem ---------------------------------------------------------------------
rem  Pasos comunes de los demas .bat (no se ejecuta directamente):
rem  lee la configuracion, enciende Docker y espera a que la base responda.
rem ---------------------------------------------------------------------
cd /d "%~dp0"

if not exist ".env" (
  echo [ERROR] Falta el archivo .env en esta carpeta.
  exit /b 1
)
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"

if "%POSTGRES_PASSWORD%"=="" goto falta_clave_bd
if "%POSTGRES_PASSWORD%"=="CAMBIAR_ESTA_CLAVE" goto falta_clave_bd

python --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] No se encuentra Python. Instalalo desde python.org marcando "Add python.exe to PATH".
  exit /b 1
)
docker --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] No se encuentra Docker Desktop. Hay que instalarlo antes de continuar.
  exit /b 1
)

docker info >nul 2>&1
if not errorlevel 1 goto docker_listo
echo Abriendo Docker Desktop. Esto puede tardar uno o dos minutos...
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a _n=0
:esperar_docker
timeout /t 3 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_listo
set /a _n+=1
if %_n% geq 60 (
  echo [ERROR] Docker Desktop no respondio. Abrelo manualmente, espera a que diga "Engine running" y vuelve a intentar.
  exit /b 1
)
goto esperar_docker

:docker_listo
docker compose up -d
if errorlevel 1 (
  echo [ERROR] No se pudo encender la base de datos. Revisa el mensaje de arriba.
  exit /b 1
)
set /a _n=0
:esperar_bd
docker compose exec -T postgres pg_isready -h 127.0.0.1 -U %POSTGRES_USER% -d %POSTGRES_DB% >nul 2>&1
if not errorlevel 1 goto bd_lista
set /a _n+=1
if %_n% geq 40 (
  echo [ERROR] La base de datos no respondio. Ejecuta "docker compose logs" en esta carpeta para ver el motivo.
  exit /b 1
)
timeout /t 2 /nobreak >nul
goto esperar_bd

:bd_lista
exit /b 0

:falta_clave_bd
echo [ERROR] Abre el archivo .env con el Bloc de notas y escribe una clave en POSTGRES_PASSWORD.
exit /b 1
