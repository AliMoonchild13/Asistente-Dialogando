"""
Limpia el Excel "BD - UNIÓN GENERAL MOVILIDAD" y lo carga en PostgreSQL.

Requisitos:
    pip install pandas openpyxl psycopg2-binary

Uso:
    1) Crear la base y el esquema:  psql -U postgres -d movilidad_udec -f 01_esquema.sql
    2) Revisar el archivo .env (usuario, contraseña y puerto de la base).
    3) python 02_cargar_excel.py "BD - UNIÓN MOVILIDAD GENERAL.xlsx"

Genera también "reporte_limpieza.xlsx" con lo que se corrigió, para revisarlo con la oficina.
"""
import os
import re
import sys
import unicodedata

import pandas as pd
import psycopg2

HOJA = "BD - UNIÓN GENERAL MOVILIDAD"

import config

CONEXION = config.PG          # la conexión se configura en el archivo .env

COLUMNAS = {
    "PERIODO": "periodo",
    "FACULTAD": "facultad",
    "PROGRAMA ACADÉMICO": "programa_academico",
    "UNIDAD REGIONAL": "unidad_regional",
    "MOVILIDAD": "programa_movilidad",
    "TIPO DE MOVILIDAD": "tipo_movilidad",
    "NOMBRES Y APELLIDOS": "nombres_apellidos",
    "TIPO DE DOCUMENTO": "tipo_documento",
    "DOCUMENTO": "documento",
    "CIUDAD/PAÍS DE ORIGEN": "pais_origen",
    "CIUDAD/PAÍS DESTINO": "pais_destino",
    "IES ORIGEN": "ies_origen",
    "IES DESTINO": "ies_destino",
    "FECHA INICIO": "fecha_inicio",
    "FECHA FINALIZACION": "fecha_fin",
}

# Columnas de catálogo: se unifican variantes (tildes, mayúsculas, espacios)
CATEGORICAS = ["facultad", "programa_academico", "unidad_regional", "programa_movilidad",
               "tipo_movilidad", "tipo_documento", "pais_origen", "pais_destino",
               "ies_origen", "ies_destino"]


# ---------------------------------------------------------------- limpieza
def espacios(valor):
    """Quita espacios al inicio/fin y espacios dobles."""
    if pd.isna(valor):
        return None
    return re.sub(r"\s+", " ", str(valor)).strip() or None


def clave(valor):
    """Clave de comparación: sin tildes, minúsculas, sin puntuación."""
    s = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def unificar(series_list):
    """Para variantes de un mismo valor elige la forma más frecuente.
    Recibe varias columnas que comparten catálogo (ej. país origen y destino)."""
    todos = pd.concat(series_list).dropna()
    conteo = todos.value_counts()
    canonico = {}
    for valor, _ in conteo.items():          # ordenado de mayor a menor frecuencia
        canonico.setdefault(clave(valor), valor)
    return {v: canonico[clave(v)] for v in conteo.index}


def fecha(valor):
    if pd.isna(valor) or (isinstance(valor, str) and not valor.strip()):
        return None
    if isinstance(valor, str):                # textos tipo 06/02/2025 (día/mes/año)
        return pd.to_datetime(valor.strip(), dayfirst=True).date()
    return pd.Timestamp(valor).date()


def documento(valor):
    if pd.isna(valor):
        return None
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)                    # 1015438127.0 -> 1015438127
    return re.sub(r"\s+", "", str(valor)).upper()


def limpiar(ruta):
    hojas = pd.ExcelFile(ruta).sheet_names
    hoja = HOJA if HOJA in hojas else hojas[0]      # si la hoja se renombró, usa la primera
    df = pd.read_excel(ruta, sheet_name=hoja, dtype=object)
    df = df[list(COLUMNAS)].rename(columns=COLUMNAS)       # descarta columnas sobrantes
    df["fila_excel"] = df.index + 2                         # +2: encabezado y base 1
    df = df.dropna(how="all", subset=list(COLUMNAS.values()))

    cambios = []
    for c in df.columns:
        if c in ("fecha_inicio", "fecha_fin", "fila_excel", "documento"):
            continue
        df[c] = df[c].map(espacios)

    # Unificar catálogos; país e IES comparten catálogo entre origen y destino
    grupos = [["facultad"], ["programa_academico"], ["unidad_regional"],
              ["programa_movilidad"], ["tipo_movilidad"], ["tipo_documento"],
              ["pais_origen", "pais_destino"], ["ies_origen", "ies_destino"]]
    for grupo in grupos:
        mapa = unificar([df[c] for c in grupo])
        if grupo == ["tipo_documento"]:
            mapa = {k: v.upper() for k, v in mapa.items()}
        for c in grupo:
            for orig, nuevo in mapa.items():
                n = (df[c] == orig).sum()
                if orig != nuevo and n:
                    cambios.append({"columna": c, "valor_original": orig,
                                    "valor_unificado": nuevo, "filas": n})
            df[c] = df[c].map(lambda v: mapa.get(v, v))

    df["documento"] = df["documento"].map(documento)
    df["fecha_inicio"] = df["fecha_inicio"].map(fecha)
    df["fecha_fin"] = df["fecha_fin"].map(fecha)

    faltantes = df[CATEGORICAS + ["periodo", "nombres_apellidos", "documento"]].isna().any(axis=1)
    if faltantes.any():
        raise ValueError(f"Filas con datos obligatorios vacíos: {df.loc[faltantes, 'fila_excel'].tolist()}")

    return df, pd.DataFrame(cambios)


# ---------------------------------------------------------------- carga
def catalogo(cur, tabla, valores, columna="nombre"):
    ids = {}
    for v in sorted(set(valores)):
        cur.execute(f"INSERT INTO movilidad.{tabla} ({columna}) VALUES (%s) RETURNING id", (v,))
        ids[v] = cur.fetchone()[0]
    return ids


def cargar(df):
    con = psycopg2.connect(**CONEXION)
    try:
        with con, con.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM movilidad.registro_movilidad")
            if cur.fetchone()[0]:
                raise RuntimeError("La tabla ya tiene datos. Vuelve a ejecutar 01_esquema.sql antes de cargar.")

            fac = catalogo(cur, "facultad", df["facultad"])
            prog = {}
            for (p, f) in sorted(set(zip(df["programa_academico"], df["facultad"]))):
                cur.execute("INSERT INTO movilidad.programa_academico (nombre, id_facultad) "
                            "VALUES (%s, %s) RETURNING id", (p, fac[f]))
                prog[(p, f)] = cur.fetchone()[0]
            ur = catalogo(cur, "unidad_regional", df["unidad_regional"])
            pm = catalogo(cur, "programa_movilidad", df["programa_movilidad"])
            tm = {}
            for t in sorted(set(df["tipo_movilidad"])):
                sentido = "Entrante" if t.lower().startswith("entrante") else "Saliente"
                cur.execute("INSERT INTO movilidad.tipo_movilidad (nombre, sentido) "
                            "VALUES (%s, %s) RETURNING id", (t, sentido))
                tm[t] = cur.fetchone()[0]
            td = catalogo(cur, "tipo_documento", df["tipo_documento"], columna="codigo")
            pais = catalogo(cur, "pais", pd.concat([df["pais_origen"], df["pais_destino"]]))
            ies = catalogo(cur, "ies", pd.concat([df["ies_origen"], df["ies_destino"]]))

            personas = {}
            for _, r in df.iterrows():
                k = (r["tipo_documento"], r["documento"])
                if k not in personas:              # misma persona con varias movilidades
                    cur.execute("INSERT INTO movilidad.persona (nombres_apellidos, id_tipo_documento, documento) "
                                "VALUES (%s, %s, %s) RETURNING id", (r["nombres_apellidos"], td[k[0]], k[1]))
                    personas[k] = cur.fetchone()[0]

                cur.execute("""
                    INSERT INTO movilidad.registro_movilidad
                    (id_persona, periodo, id_programa_academico, id_unidad_regional,
                     id_programa_movilidad, id_tipo_movilidad, id_pais_origen, id_pais_destino,
                     id_ies_origen, id_ies_destino, fecha_inicio, fecha_fin, fila_excel)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (personas[k], r["periodo"], prog[(r["programa_academico"], r["facultad"])],
                     ur[r["unidad_regional"]], pm[r["programa_movilidad"]], tm[r["tipo_movilidad"]],
                     pais[r["pais_origen"]], pais[r["pais_destino"]],
                     ies[r["ies_origen"]], ies[r["ies_destino"]],
                     r["fecha_inicio"], r["fecha_fin"], int(r["fila_excel"])))

            cur.execute("SELECT COUNT(*) FROM movilidad.registro_movilidad")
            cargados = cur.fetchone()[0]
            if cargados != len(df):              # si no cuadra, se revierte todo
                raise RuntimeError(f"Se esperaban {len(df)} registros y se cargaron {cargados}")
            return cargados, len(personas)
    finally:
        con.close()


if __name__ == "__main__":
    ruta = sys.argv[1] if len(sys.argv) > 1 else config.ARCHIVO_EXCEL
    df, cambios = limpiar(ruta)

    with pd.ExcelWriter("reporte_limpieza.xlsx") as w:
        cambios.to_excel(w, sheet_name="valores_unificados", index=False)
        df[df["fecha_inicio"].isna()][["fila_excel", "periodo", "programa_movilidad", "nombres_apellidos"]] \
            .to_excel(w, sheet_name="sin_fechas", index=False)
        nombres = df.groupby(["tipo_documento", "documento"])["nombres_apellidos"].nunique()
        dudosos = nombres[nombres > 1].reset_index()[["tipo_documento", "documento"]]
        df.merge(dudosos)[["fila_excel", "tipo_documento", "documento", "nombres_apellidos"]] \
            .to_excel(w, sheet_name="documento_con_varios_nombres", index=False)

    registros, personas = cargar(df)
    print(f"OK: {registros} movilidades y {personas} personas cargadas.")
    print(f"Valores unificados: {len(cambios)} (ver reporte_limpieza.xlsx)")
    print(f"Registros sin fechas: {df['fecha_inicio'].isna().sum()}")
