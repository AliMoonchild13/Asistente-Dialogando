"""
Aplicativo de carga: una sola página para subir el Excel de movilidad y los informes en PDF.

Solo GUARDA los archivos en sus carpetas. No procesa nada: el asistente los revisa cada
INTERVALO_REVISION_MIN minutos y aplica sus controles de siempre (validación, copia de
seguridad, protección del 20 %, aviso amarillo).

Corre en su propio contenedor (servicio "carga" de compose.yml), con la misma imagen del
asistente para no tener que construir otra. Página: http://localhost:8001 (en el servidor,
https://SERVIDOR/carga/ a través de Nginx, con su propia clave).
"""
import os
import re
import shutil
import sys
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/app")                          # código del asistente dentro de la imagen

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from extraer import datos_del_nombre
from validar_excel import ExcelInvalido, validar

CARPETA_EXCEL = Path(os.environ.get("CARPETA_EXCEL", "/datos/excel"))
CARPETA_INFORMES = Path(os.environ.get("CARPETA_INFORMES", "/datos/informes"))
MAX_EXCEL_MB = 20
MAX_PDF_MB = int(os.environ.get("CARGA_MAX_PDF_MB", "25"))
PAGINA = Path(__file__).with_name("index.html")

app = FastAPI(title="Carga de información", docs_url=None, redoc_url=None, openapi_url=None)


def _nombre_seguro(nombre, extension):
    """Deja solo el nombre del archivo (sin carpetas) y quita caracteres peligrosos."""
    nombre = unicodedata.normalize("NFC", Path(nombre.replace("\\", "/")).name)
    nombre = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "", nombre).strip(" .")
    if not nombre.lower().endswith(extension):
        raise HTTPException(400, f"Solo se aceptan archivos {extension}.")
    if len(nombre) <= len(extension):
        raise HTTPException(400, "El nombre del archivo no es válido.")
    return nombre[:200]


async def _guardar_temporal(request, carpeta, extension, max_mb, firma):
    """Recibe el archivo por partes en un temporal oculto de la misma carpeta (el asistente no lo ve)."""
    carpeta.mkdir(parents=True, exist_ok=True)
    temporal = carpeta / f".subiendo-{uuid.uuid4().hex}{extension}"
    limite, total, inicio = max_mb * 1024 * 1024, 0, b""
    try:
        with open(temporal, "wb") as f:
            async for parte in request.stream():
                total += len(parte)
                if total > limite:
                    raise HTTPException(413, f"El archivo supera el máximo de {max_mb} MB.")
                if len(inicio) < 8:
                    inicio += parte[:8]
                f.write(parte)
        if total == 0:
            raise HTTPException(400, "El archivo está vacío.")
        if not inicio.startswith(firma):
            raise HTTPException(400, f"El archivo no es un {extension} válido (puede estar dañado).")
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise
    return temporal


@app.get("/")
def pagina():
    return FileResponse(PAGINA, headers={"Cache-Control": "no-store"})


@app.put("/subir/excel")
async def subir_excel(request: Request, nombre: str):
    nombre = _nombre_seguro(nombre, ".xlsx")
    temporal = await _guardar_temporal(request, CARPETA_EXCEL, ".xlsx", MAX_EXCEL_MB, b"PK")
    try:
        registros = validar(temporal)               # mismos controles que usa el asistente
    except ExcelInvalido as e:
        temporal.unlink(missing_ok=True)
        raise HTTPException(422, f"El Excel no se guardó: {e}")
    except Exception as e:
        temporal.unlink(missing_ok=True)
        raise HTTPException(422, f"El Excel no se guardó: no se pudo leer ({str(e)[:200]}).")
    # Los Excel anteriores pasan a 'anteriores' (historial); en la carpeta queda solo el nuevo
    anteriores = CARPETA_EXCEL / "anteriores"
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    for viejo in CARPETA_EXCEL.iterdir():
        if viejo.is_file() and viejo.suffix.lower() in (".xlsx", ".xlsm") and not viejo.name.startswith((".", "~$")):
            anteriores.mkdir(exist_ok=True)
            shutil.move(str(viejo), str(anteriores / f"{sello}_{viejo.name}"))
    os.replace(temporal, CARPETA_EXCEL / nombre)
    return {"ok": True, "archivo": nombre,
            "mensaje": f"Excel guardado ({registros} registros). Se aplicará en la próxima revisión automática."}


@app.put("/subir/informe")
async def subir_informe(request: Request, nombre: str):
    nombre = _nombre_seguro(nombre, ".pdf")
    temporal = await _guardar_temporal(request, CARPETA_INFORMES, ".pdf", MAX_PDF_MB, b"%PDF")
    destino = CARPETA_INFORMES / nombre
    reemplazo = destino.exists()
    os.replace(temporal, destino)
    d = datos_del_nombre(nombre)
    faltan = [campo for campo, clave in (("programa", "programa_movilidad"), ("periodo", "periodo"),
                                         ("sentido", "sentido"), ("nombre de la persona", "persona"))
              if not d[clave]]
    aviso = ("Guardado, pero al nombre le falta: " + ", ".join(faltan) +
             ". Sin eso no se enlaza con su registro. Formato: PROGRAMA PERIODO SENTIDO NOMBRE.pdf") if faltan else ""
    return {"ok": True, "archivo": nombre, "reemplazo": reemplazo, "aviso": aviso}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="warning")
