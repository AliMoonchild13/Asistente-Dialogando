"""
Actualiza la base de movilidad a partir del Excel SIN borrar todo:
  - agrega los registros nuevos,
  - corrige los que cambiaron (fechas, sede, programa, países, nombre...),
  - quita los que ya no están en el Excel.
Los registros que no cambian conservan su identificador, así que los informes
siguen enlazados y NO hay que volver a correr 03_esquema_rag.sql ni ingesta.py.

Uso:
    python actualizar_excel.py "ruta\\del\\Excel.xlsx"            (aplica los cambios)
    python actualizar_excel.py "ruta\\del\\Excel.xlsx" --simular  (solo muestra qué cambiaría)

Requiere que la base ya exista (01_esquema.sql + una primera carga con 02_cargar_excel.py).
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# Reutiliza la limpieza y la conexión de 02_cargar_excel.py (mismo criterio de limpieza)
_spec = importlib.util.spec_from_file_location("carga", Path(__file__).with_name("02_cargar_excel.py"))
carga = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(carga)

LIMITE_BORRADO = 0.20   # si se fuera a quitar más del 20 % de los registros, se detiene (¿Excel equivocado?)

CATALOGOS = [  # (tabla, columna en la tabla, columnas del Excel que usan ese catálogo)
    ("facultad", "nombre", ["facultad"]),
    ("unidad_regional", "nombre", ["unidad_regional"]),
    ("programa_movilidad", "nombre", ["programa_movilidad"]),
    ("tipo_movilidad", "nombre", ["tipo_movilidad"]),
    ("tipo_documento", "codigo", ["tipo_documento"]),
    ("pais", "nombre", ["pais_origen", "pais_destino"]),
    ("ies", "nombre", ["ies_origen", "ies_destino"]),
]

COLS = ["fila_excel", "periodo", "facultad", "programa_academico", "unidad_regional", "programa_movilidad",
        "tipo_movilidad", "nombres_apellidos", "tipo_documento", "documento", "pais_origen", "pais_destino",
        "ies_origen", "ies_destino", "fecha_inicio", "fecha_fin"]

# Un registro se reconoce por: persona + periodo + programa + tipo de movilidad + IES origen y destino.
# Si el Excel repite exactamente esa combinación, se numeran en orden (n = 1, 2, ...).
SQL_ACTUALIZAR = """
CREATE TEMP TABLE nuevo ON COMMIT DROP AS
SELECT s.fila_excel, s.periodo, s.fecha_inicio, s.fecha_fin,
       p.id AS id_persona, pa.id AS id_programa_academico, ur.id AS id_unidad_regional,
       pm.id AS id_programa_movilidad, tm.id AS id_tipo_movilidad,
       po.id AS id_pais_origen, pd.id AS id_pais_destino, io.id AS id_ies_origen, idd.id AS id_ies_destino
FROM stg s
JOIN movilidad.tipo_documento td    ON td.codigo = s.tipo_documento
JOIN movilidad.persona p            ON p.id_tipo_documento = td.id AND p.documento = s.documento
JOIN movilidad.facultad f           ON f.nombre = s.facultad
JOIN movilidad.programa_academico pa ON pa.nombre = s.programa_academico AND pa.id_facultad = f.id
JOIN movilidad.unidad_regional ur   ON ur.nombre = s.unidad_regional
JOIN movilidad.programa_movilidad pm ON pm.nombre = s.programa_movilidad
JOIN movilidad.tipo_movilidad tm    ON tm.nombre = s.tipo_movilidad
JOIN movilidad.pais po              ON po.nombre = s.pais_origen
JOIN movilidad.pais pd              ON pd.nombre = s.pais_destino
JOIN movilidad.ies io               ON io.nombre = s.ies_origen
JOIN movilidad.ies idd              ON idd.nombre = s.ies_destino;

CREATE TEMP TABLE par ON COMMIT DROP AS
WITH n AS (
  SELECT *, row_number() OVER (PARTITION BY id_persona, periodo, id_programa_movilidad, id_tipo_movilidad,
                                            id_ies_origen, id_ies_destino ORDER BY fila_excel) AS k
  FROM nuevo),
a AS (
  SELECT id, id_persona, periodo, id_programa_movilidad, id_tipo_movilidad, id_ies_origen, id_ies_destino,
         row_number() OVER (PARTITION BY id_persona, periodo, id_programa_movilidad, id_tipo_movilidad,
                                         id_ies_origen, id_ies_destino ORDER BY fila_excel, id) AS k
  FROM movilidad.registro_movilidad)
SELECT a.id AS id_actual, n.*
FROM n FULL JOIN a USING (id_persona, periodo, id_programa_movilidad, id_tipo_movilidad,
                          id_ies_origen, id_ies_destino, k);
"""


def hoja_del_excel(ruta):
    hojas = pd.ExcelFile(ruta).sheet_names
    return carga.HOJA if carga.HOJA in hojas else hojas[0]


def alinear_con_catalogos(cur, df):
    """Si un valor del Excel es una variante (tildes, mayúsculas) de uno que ya está en la base,
    usa el de la base para no crear duplicados."""
    for tabla, col, columnas in CATALOGOS:
        cur.execute(f"SELECT {col} FROM movilidad.{tabla}")
        existentes = {carga.clave(v): v for (v,) in cur.fetchall()}
        for c in columnas:
            df[c] = df[c].map(lambda v: existentes.get(carga.clave(v), v))
    cur.execute("SELECT pa.nombre, f.nombre FROM movilidad.programa_academico pa "
                "JOIN movilidad.facultad f ON f.id = pa.id_facultad")
    existentes = {(carga.clave(p), f): p for p, f in cur.fetchall()}
    df["programa_academico"] = [existentes.get((carga.clave(p), f), p)
                                for p, f in zip(df["programa_academico"], df["facultad"])]


def crear_catalogos_nuevos(cur):
    for tabla, col, columnas in CATALOGOS:
        if tabla == "tipo_movilidad":
            continue
        union = " UNION ".join(f"SELECT {c} FROM stg" for c in columnas)
        cur.execute(f"INSERT INTO movilidad.{tabla} ({col}) SELECT DISTINCT * FROM ({union}) v "
                    f"ON CONFLICT ({col}) DO NOTHING")
    cur.execute("""INSERT INTO movilidad.tipo_movilidad (nombre, sentido)
                   SELECT DISTINCT tipo_movilidad,
                          CASE WHEN lower(tipo_movilidad) LIKE 'entrante%%' THEN 'Entrante' ELSE 'Saliente' END
                   FROM stg ON CONFLICT (nombre) DO NOTHING""")
    cur.execute("""INSERT INTO movilidad.programa_academico (nombre, id_facultad)
                   SELECT DISTINCT s.programa_academico, f.id FROM stg s
                   JOIN movilidad.facultad f ON f.nombre = s.facultad
                   ON CONFLICT (nombre, id_facultad) DO NOTHING""")
    # Personas: nuevas se agregan; si cambió el nombre escrito en el Excel, se corrige
    cur.execute("""INSERT INTO movilidad.persona (nombres_apellidos, id_tipo_documento, documento)
                   SELECT DISTINCT ON (td.id, s.documento) s.nombres_apellidos, td.id, s.documento
                   FROM stg s JOIN movilidad.tipo_documento td ON td.codigo = s.tipo_documento
                   ORDER BY td.id, s.documento, s.fila_excel
                   ON CONFLICT (id_tipo_documento, documento) DO UPDATE
                   SET nombres_apellidos = EXCLUDED.nombres_apellidos
                   WHERE movilidad.persona.nombres_apellidos IS DISTINCT FROM EXCLUDED.nombres_apellidos""")
    return cur.rowcount


def main(ruta, simular=False):
    carga.HOJA = hoja_del_excel(ruta)
    df, _ = carga.limpiar(ruta)
    con = psycopg2.connect(**carga.CONEXION)
    try:
        with con, con.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM movilidad.registro_movilidad")
            antes = cur.fetchone()[0]
            if antes == 0:
                sys.exit("La base está vacía: haz la primera carga con 02_cargar_excel.py")

            alinear_con_catalogos(cur, df)
            cur.execute("""CREATE TEMP TABLE stg (fila_excel int, periodo text, facultad text,
                programa_academico text, unidad_regional text, programa_movilidad text, tipo_movilidad text,
                nombres_apellidos text, tipo_documento text, documento text, pais_origen text, pais_destino text,
                ies_origen text, ies_destino text, fecha_inicio date, fecha_fin date) ON COMMIT DROP""")
            filas = [tuple(None if pd.isna(v) else v for v in r) for r in df[COLS].itertuples(index=False)]
            execute_values(cur, "INSERT INTO stg VALUES %s", filas)

            personas = crear_catalogos_nuevos(cur)
            cur.execute(SQL_ACTUALIZAR)

            cur.execute("SELECT COUNT(*) FROM nuevo")
            if cur.fetchone()[0] != len(df):
                raise RuntimeError("No se pudieron relacionar todas las filas del Excel con los catálogos.")

            # 1. Corregir los que cambiaron
            cur.execute("""UPDATE movilidad.registro_movilidad r SET
                    id_programa_academico = p.id_programa_academico, id_unidad_regional = p.id_unidad_regional,
                    id_pais_origen = p.id_pais_origen, id_pais_destino = p.id_pais_destino,
                    fecha_inicio = p.fecha_inicio, fecha_fin = p.fecha_fin
                FROM par p WHERE p.id_actual = r.id AND p.fila_excel IS NOT NULL
                  AND (r.id_programa_academico, r.id_unidad_regional, r.id_pais_origen, r.id_pais_destino,
                       r.fecha_inicio, r.fecha_fin)
                      IS DISTINCT FROM
                      (p.id_programa_academico, p.id_unidad_regional, p.id_pais_origen, p.id_pais_destino,
                       p.fecha_inicio, p.fecha_fin)""")
            actualizados = cur.rowcount
            cur.execute("""UPDATE movilidad.registro_movilidad r SET fila_excel = p.fila_excel
                           FROM par p WHERE p.id_actual = r.id AND p.fila_excel IS NOT NULL
                             AND r.fila_excel IS DISTINCT FROM p.fila_excel""")

            # 2. Agregar los nuevos
            cur.execute("""INSERT INTO movilidad.registro_movilidad
                (id_persona, periodo, id_programa_academico, id_unidad_regional, id_programa_movilidad,
                 id_tipo_movilidad, id_pais_origen, id_pais_destino, id_ies_origen, id_ies_destino,
                 fecha_inicio, fecha_fin, fila_excel)
                SELECT id_persona, periodo, id_programa_academico, id_unidad_regional, id_programa_movilidad,
                       id_tipo_movilidad, id_pais_origen, id_pais_destino, id_ies_origen, id_ies_destino,
                       fecha_inicio, fecha_fin, fila_excel
                FROM par WHERE id_actual IS NULL ORDER BY fila_excel""")
            nuevos = cur.rowcount

            # 3. Quitar los que ya no están en el Excel
            cur.execute("SELECT id_actual FROM par WHERE fila_excel IS NULL")
            quitar = [i for (i,) in cur.fetchall()]
            if len(quitar) > LIMITE_BORRADO * antes and "--forzar" not in sys.argv:
                raise RuntimeError(
                    f"Se quitarían {len(quitar)} de {antes} registros. ¿Es el Excel correcto? "
                    "Si estás segura, vuelve a ejecutar agregando --forzar")
            if quitar:
                cur.execute("SELECT to_regclass('movilidad.informe') IS NOT NULL")
                if cur.fetchone()[0]:   # los informes enlazados quedan sin enlace, no se borran
                    cur.execute("UPDATE movilidad.informe SET id_registro_movilidad = NULL "
                                "WHERE id_registro_movilidad = ANY(%s)", (quitar,))
                cur.execute("DELETE FROM movilidad.registro_movilidad WHERE id = ANY(%s)", (quitar,))

            cur.execute("SELECT COUNT(*) FROM movilidad.registro_movilidad")
            despues = cur.fetchone()[0]
            if despues != len(df):
                raise RuntimeError(f"El Excel tiene {len(df)} registros y la base quedaría con {despues}.")

            print(f"Registros en el Excel: {len(df)}")
            print(f"  Nuevos: {nuevos} | Corregidos: {actualizados} | Quitados: {len(quitar)} | "
                  f"Sin cambios: {despues - nuevos - actualizados}")
            print(f"  Personas nuevas o con nombre corregido: {personas}")
            if simular:
                con.rollback()
                print("SIMULACIÓN: no se guardó ningún cambio.")
            else:
                print("Base actualizada.")
    finally:
        con.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    config = getattr(carga, "config", None)            # paquete instalable: ruta tomada del archivo .env
    ruta = args[0] if args else getattr(config, "ARCHIVO_EXCEL", None)
    if not ruta:
        sys.exit('Uso: python actualizar_excel.py "ruta del Excel.xlsx" [--simular]')
    try:
        main(ruta, simular="--simular" in sys.argv)
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        sys.exit(f"[NO SE HIZO NINGÚN CAMBIO] {e}")
