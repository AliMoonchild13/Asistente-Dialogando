"""
Mide el tiempo de respuesta del asistente con un banco de preguntas de prueba.

Hace cada pregunta a la API (igual que la página web), mide el tiempo total hasta recibir
la respuesta y guarda el resultado en datos/salida/tiempos_respuesta.csv.

Uso (dentro de Docker):
    docker compose exec app python medir_tiempos.py            -> cada pregunta 1 vez
    docker compose exec app python medir_tiempos.py 3          -> cada pregunta 3 veces

Ojo: cada pregunta consume tokens del servicio de IA (Groq). Con el plan gratuito,
conviene 1 o 2 repeticiones.
"""
import csv
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import config

URL = "http://localhost:8000/api/preguntar"
SALIDA = Path(config.CARPETA_SALIDA) / "tiempos_respuesta.csv"
PAUSA_ENTRE_PREGUNTAS = 5          # segundos, para no chocar con el límite por minuto de Groq

PREGUNTAS = [
    ("Cifras", "¿Cuántas movilidades salientes hubo por país en 2025?"),
    ("Cifras", "¿Cuántos estudiantes de Voluntariado Internacional viajaron en 2024?"),
    ("Informes", "¿Qué fortalezas mencionan los estudiantes en sus informes?"),
    ("Informes", "¿Qué dificultades mencionan los participantes de Voluntariado?"),
    ("Cifras e informes", "¿Cuántos estudiantes fueron a Ecuador y qué destacan de su experiencia?"),
    ("Cifras e informes", "Dame una tabla con el impacto mencionado en un informe de cada tipo de movilidad"),
    ("Fuera de tema", "¿Cuál es la capital de Francia?"),
]


def preguntar(texto):
    datos = json.dumps({"pregunta": texto, "historial": []}).encode("utf-8")
    req = urllib.request.Request(URL, data=datos, headers={"Content-Type": "application/json"})
    inicio = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            cuerpo = json.loads(r.read().decode("utf-8"))
            estado = "OK"
    except urllib.error.HTTPError as e:
        cuerpo, estado = {}, f"Error {e.code}"
    except Exception as e:
        cuerpo, estado = {}, f"Error: {str(e)[:80]}"
    segundos = time.perf_counter() - inicio
    modelo = "Respaldo (Ollama)" if cuerpo.get("respaldo") else ("Principal" if estado == "OK" else "-")
    return segundos, estado, modelo


def main():
    repeticiones = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    filas = []
    total = len(PREGUNTAS) * repeticiones
    n = 0
    for rep in range(1, repeticiones + 1):
        for tipo, texto in PREGUNTAS:
            n += 1
            segundos, estado, modelo = preguntar(texto)
            filas.append({"fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "repeticion": rep,
                          "tipo": tipo, "pregunta": texto, "segundos": round(segundos, 2),
                          "estado": estado, "modelo": modelo})
            print(f"[{n}/{total}] {segundos:6.1f} s  {estado:10} {modelo:18} {texto}", flush=True)
            if n < total:
                time.sleep(PAUSA_ENTRE_PREGUNTAS)

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not SALIDA.exists()
    with open(SALIDA, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]), delimiter=";")
        if nuevo:
            w.writeheader()
        w.writerows(filas)

    print("\nResumen (solo respuestas correctas):")
    print(f"{'Tipo':20} {'N':>3} {'Mín':>7} {'Mediana':>8} {'Prom.':>7} {'Máx':>7}")
    for tipo in dict.fromkeys(t for t, _ in PREGUNTAS):
        t = [x["segundos"] for x in filas if x["tipo"] == tipo and x["estado"] == "OK"]
        if t:
            print(f"{tipo:20} {len(t):>3} {min(t):>6.1f}s {statistics.median(t):>7.1f}s "
                  f"{statistics.mean(t):>6.1f}s {max(t):>6.1f}s")
    t = [x["segundos"] for x in filas if x["estado"] == "OK"]
    if t:
        print(f"{'TODAS':20} {len(t):>3} {min(t):>6.1f}s {statistics.median(t):>7.1f}s "
              f"{statistics.mean(t):>6.1f}s {max(t):>6.1f}s")
    errores = [x for x in filas if x["estado"] != "OK"]
    respaldo = [x for x in filas if x["modelo"].startswith("Respaldo")]
    if errores:
        print(f"\n{len(errores)} preguntas con error (no entran en el resumen).")
    if respaldo:
        print(f"{len(respaldo)} respuestas las dio el modelo de respaldo (más lento).")
    print(f"\nDetalle guardado en: {SALIDA}")


if __name__ == "__main__":
    main()
