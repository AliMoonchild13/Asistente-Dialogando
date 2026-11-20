"""
Exporta los datos limpios de movilidad para Power BI.

Se ejecuta solo después de cada actualización del Excel (y al arrancar, si el archivo no existe).
Genera  movilidad_powerbi.xlsx  en CARPETA_POWERBI (ver .env), con dos hojas:
  movilidad   una fila por movilidad, desde la vista v_movilidad, con el nombre de la persona
              pero SIN número ni tipo de documento
  info        fecha de la última actualización y número de registros

Power BI se conecta a este archivo (Obtener datos > Excel, o > SharePoint si la carpeta está en SharePoint).

Uso manual (dentro de Docker):
    docker compose exec app python exportar_powerbi.py
"""
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg2

import config

ARCHIVO = Path(config.CARPETA_POWERBI) / "movilidad_powerbi.xlsx"

# Columnas de v_movilidad que se exportan. Van los nombres (la oficina los usa en sus informes), pero NO
# el número ni el tipo de documento. El archivo tiene datos personales: compartirlo solo con la oficina.
COLUMNAS = ["id", "periodo", "anio", "facultad", "programa_academico", "unidad_regional",
            "programa_movilidad", "tipo_movilidad", "sentido", "nombres_apellidos", "pais_origen", "pais_destino",
            "ies_origen", "ies_destino", "fecha_inicio", "fecha_fin"]


def exportar():
    """Crea o reemplaza el archivo y devuelve el número de registros exportados."""
    with psycopg2.connect(**config.PG) as con, con.cursor() as cur:
        cur.execute(f"SELECT {', '.join(COLUMNAS)} FROM movilidad.v_movilidad ORDER BY periodo, id")
        df = pd.DataFrame(cur.fetchall(), columns=COLUMNAS)
    for c in ("fecha_inicio", "fecha_fin"):
        df[c] = pd.to_datetime(df[c])
    info = pd.DataFrame({"actualizado": [datetime.now().replace(microsecond=0)], "registros": [len(df)]})

    ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
    temporal = ARCHIVO.with_name("~tmp_" + ARCHIVO.name)
    with pd.ExcelWriter(temporal) as escritor:
        df.to_excel(escritor, sheet_name="movilidad", index=False)
        info.to_excel(escritor, sheet_name="info", index=False)
    try:
        os.replace(temporal, ARCHIVO)        # se reemplaza de una vez: Power BI nunca lee un archivo a medias
    except PermissionError:
        temporal.unlink(missing_ok=True)
        raise RuntimeError(f"{ARCHIVO.name} está abierto en otro programa; ciérralo para que se actualice.")
    return len(df)


if __name__ == "__main__":
    print(f"Exportados {exportar()} registros a {ARCHIVO}")
