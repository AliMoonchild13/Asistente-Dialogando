@echo off
chcp 65001 >nul
title Recargar datos - Asistente de Movilidad Internacional
rem Vuelve a cargar el Excel de movilidad y todos los informes.
rem Se usa despues de corregir o actualizar el Excel.
call "%~dp0_preparar.bat"
if errorlevel 1 goto error

if not exist "%ARCHIVO_EXCEL%" (
  echo [ERROR] No se encuentra el Excel "%ARCHIVO_EXCEL%" en esta carpeta.
  echo         Copialo aqui o corrige el nombre en ARCHIVO_EXCEL dentro del archivo .env
  goto error
)

set "_hay="
for /f %%i in ('docker compose exec -T postgres psql -U %POSTGRES_USER% -d %POSTGRES_DB% -tAc "select 1 from movilidad.registro_movilidad limit 1" 2^>nul') do set "_hay=%%i"
if not defined _hay goto cargar
echo.
echo La base ya tiene datos. Se van a REEMPLAZAR por los del Excel "%ARCHIVO_EXCEL%"
echo y se volveran a procesar todos los informes.
choice /c SN /m "Deseas continuar"
if errorlevel 2 goto cancelado

:cargar
echo.
echo [1/4] Creando las tablas de movilidad...
docker compose exec -T postgres psql -U %POSTGRES_USER% -d %POSTGRES_DB% -v ON_ERROR_STOP=1 -q < 01_esquema.sql
if errorlevel 1 goto error
echo [2/4] Cargando el Excel...
python 02_cargar_excel.py
if errorlevel 1 goto error
echo [3/4] Creando las tablas de informes...
docker compose exec -T postgres psql -U %POSTGRES_USER% -d %POSTGRES_DB% -v ON_ERROR_STOP=1 -q < 03_esquema_rag.sql
if errorlevel 1 goto error
echo [4/4] Procesando los informes. La primera vez descarga un modelo de 1 GB y puede tardar...
python ingesta.py
if errorlevel 1 goto error

echo.
echo Datos cargados correctamente.
if not "%~1"=="sin_pausa" pause
exit /b 0

:cancelado
echo No se hizo ningun cambio.
if not "%~1"=="sin_pausa" pause
exit /b 0

:error
echo.
echo [ERROR] La carga de datos no termino. Revisa el mensaje de arriba.
if not "%~1"=="sin_pausa" pause
exit /b 1
