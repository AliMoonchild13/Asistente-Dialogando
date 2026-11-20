"""
Servidor web del Asistente de Movilidad Internacional.
Sirve la interfaz (carpeta web/) y la conecta con el agente (agente.py).

Ejecutar:   python api.py
Se abre en: http://localhost:8000
"""
import csv
import io
import json
import re
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import pandas as pd
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agente
import consultas

CARPETA = Path(__file__).parent
WEB = CARPETA / "web"
ARCHIVO_OPINIONES = CARPETA / "opiniones.csv"
PUERTO = 8000

app = FastAPI(title="Asistente de Movilidad Internacional", docs_url=None, redoc_url=None)


class Pregunta(BaseModel):
    pregunta: str
    historial: list[list[str]] = []        # [[pregunta, respuesta], ...] de la conversación actual


class Tabla(BaseModel):
    columnas: list[str]
    filas: list[list]


class Opinion(BaseModel):
    pregunta: str
    respuesta: str
    valor: str                             # "util" o "no_util"


def _tabla_a_json(df: pd.DataFrame):
    datos = json.loads(df.to_json(orient="split", date_format="iso", force_ascii=False))
    filas = [[v[:10] if isinstance(v, str) and re.match(r"^\d{4}-\d\d-\d\dT00:00:00", v) else v for v in fila]
             for fila in datos["data"]]
    return {"columnas": [str(c) for c in datos["columns"]], "filas": filas}


def _pasos_legibles(resultado):
    """Convierte los pasos del agente en texto para la sección '¿Cómo se obtuvo esta respuesta?' (sin SQL)."""
    pasos = []
    for p in resultado["pasos"]:
        d = p["detalle"]
        if not p["ok"]:
            detalle = "El primer intento no funcionó, así que el asistente lo ajustó y volvió a intentarlo."
        elif "consulta" in d:
            filtros = [f"{k.replace('_', ' ')}: {v}" for k, v in d.items() if k != "consulta"]
            detalle = "Buscó los fragmentos más relacionados" + (
                f" (filtros: {', '.join(filtros)})." if filtros else " en todos los informes disponibles.")
        else:
            detalle = "Consultó los registros de movilidad internacional para obtener las cifras."
        titulo = p["accion"] + (f": {d['consulta']}" if "consulta" in d else "")
        pasos.append({"titulo": titulo, "detalle": detalle})
    if pasos:
        partes = []
        if resultado["tablas"]:
            partes.append("los datos de la base")
        if resultado["fuentes"]:
            partes.append(f"{len(resultado['fuentes'])} fragmentos de informes")
        pasos.append({"titulo": "Redactó la respuesta",
                      "detalle": "Usó " + " y ".join(partes or ["la información obtenida"]) + "."})
    return pasos


@app.post("/api/preguntar")
def preguntar(p: Pregunta):
    texto = p.pregunta.strip()
    if not texto:
        return JSONResponse({"error": "vacia"}, status_code=400)
    try:
        r = agente.responder(texto, [tuple(h) for h in p.historial if len(h) == 2])
    except Exception as e:
        mensaje = str(e)
        if "429" in mensaje or "rate limit" in mensaje.lower():
            m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", mensaje)
            espera = int(int(m.group(1) or 0) * 60 + float(m.group(2))) + 1 if m else 60
            return JSONResponse({"error": "limite", "espera": min(max(espera, 5), 600)}, status_code=429)
        print("ERROR:", mensaje)
        return JSONResponse({"error": "fallo", "detalle": mensaje.splitlines()[0][:300]}, status_code=500)
    return {
        "respuesta": r["respuesta"],
        "tablas": [_tabla_a_json(t) for t in r["tablas"] if not t.empty],
        "fuentes": [{"archivo": f["archivo"], "seccion": f["campo"], "texto": f["contenido"]} for f in r["fuentes"]],
        "pasos": _pasos_legibles(r),
    }


@app.post("/api/exportar")
def exportar(t: Tabla):
    salida = io.BytesIO()
    pd.DataFrame(t.filas, columns=t.columnas).to_excel(salida, index=False, sheet_name="Resultados")
    nombre = f"movilidad_{datetime.now():%Y%m%d_%H%M}.xlsx"
    return Response(salida.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.post("/api/opinion")
def opinion(o: Opinion):
    """Guarda la valoración (útil / no útil) en opiniones.csv, para evaluar el sistema."""
    nuevo = not ARCHIVO_OPINIONES.exists()
    with open(ARCHIVO_OPINIONES, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if nuevo:
            w.writerow(["fecha", "valoracion", "pregunta", "respuesta"])
        w.writerow([f"{datetime.now():%Y-%m-%d %H:%M}", o.valor, o.pregunta, o.respuesta[:2000]])
    return {"ok": True}


@app.get("/")
def inicio():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=WEB), name="web")


def _precargar():
    """Carga el modelo de embeddings al iniciar, para que la primera pregunta no tarde."""
    try:
        consultas.vectorizar(["inicio"], es_pregunta=True)
        print("Modelo de embeddings listo.")
    except Exception as e:
        print("No se pudo precargar el modelo de embeddings:", e)


if __name__ == "__main__":
    threading.Thread(target=_precargar, daemon=True).start()
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PUERTO}")).start()
    print(f"Asistente disponible en http://localhost:{PUERTO}  (Ctrl + C para cerrar)")
    uvicorn.run(app, host="127.0.0.1", port=PUERTO, log_level="warning")
