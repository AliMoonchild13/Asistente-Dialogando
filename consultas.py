"""Búsqueda en informes (RAG) y preguntas sobre la base de movilidad (texto a SQL)."""
import re
from functools import lru_cache

import pandas as pd
import psycopg2
from openai import OpenAI

import config

# ------------------------------------------------------------------ embeddings
@lru_cache(maxsize=1)
def _modelo():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(config.MODELO_EMBEDDINGS)


def vectorizar(textos, es_pregunta=False):
    """Los modelos E5 requieren el prefijo 'query: ' o 'passage: '."""
    prefijo = "query: " if es_pregunta else "passage: "
    vectores = _modelo().encode([prefijo + t for t in textos], normalize_embeddings=True,
                                batch_size=16, show_progress_bar=False)
    return [v.tolist() for v in vectores]


def a_pgvector(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


# ------------------------------------------------------------------ LLM
@lru_cache(maxsize=1)
def _cliente():
    return OpenAI(base_url=config.LLM["base_url"], api_key=config.LLM["api_key"])


def preguntar_llm(sistema, usuario, temperatura=0.1):
    r = _cliente().chat.completions.create(
        model=config.LLM["modelo"], temperature=temperatura,
        messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}])
    return r.choices[0].message.content.strip()


# ------------------------------------------------------------------ RAG sobre informes
def buscar_fragmentos(pregunta, programa=None, sentido=None, periodo_desde=None, periodo_hasta=None, k=None):
    k = k or config.FRAGMENTOS_POR_PREGUNTA
    filtros, params = [], []
    for columna, valor in (("i.programa_movilidad", programa), ("i.sentido", sentido)):
        if valor:
            filtros.append(f"{columna} = %s")
            params.append(valor)
    if periodo_desde:
        filtros.append("i.periodo >= %s")
        params.append(periodo_desde)
    if periodo_hasta:
        filtros.append("i.periodo <= %s")
        params.append(periodo_hasta)
    donde = ("WHERE " + " AND ".join(filtros)) if filtros else ""

    sql = f"""
        SELECT i.archivo, i.programa_movilidad, i.periodo, i.sentido, i.universidad, i.pais,
               f.campo, f.contenido, 1 - (f.embedding <=> %s::vector) AS similitud
        FROM movilidad.informe_fragmento f
        JOIN movilidad.informe i ON i.id = f.id_informe
        {donde}
        ORDER BY f.embedding <=> %s::vector
        LIMIT %s"""
    v = a_pgvector(vectorizar([pregunta], es_pregunta=True)[0])
    with psycopg2.connect(**config.PG) as con:
        return pd.read_sql(sql, con, params=[v, *params, v, k])


SISTEMA_INFORMES = """Eres el asistente de la Oficina Dialogando con el Mundo de la Universidad de Cundinamarca.
Respondes preguntas sobre los informes que entregan estudiantes y docentes al terminar su movilidad internacional.
Reglas:
- Usa SOLO la información de los fragmentos entregados. Si no alcanza para responder, dilo claramente.
- Cita la fuente de cada afirmación con su número entre corchetes, por ejemplo [1] o [2][4].
- Responde en español, de forma clara y breve. Si la pregunta pide un panorama general, agrupa las ideas comunes."""


def responder_informes(pregunta, **filtros):
    frag = buscar_fragmentos(pregunta, **filtros)
    if frag.empty:
        return "No encontré informes que coincidan con los filtros seleccionados.", frag
    contexto = "\n\n".join(
        f"[{n}] Informe {r.programa_movilidad} {r.periodo} ({r.sentido}), "
        f"{r.universidad or 'universidad no indicada'}, {r.pais or ''} — Campo: {r.campo}\n{r.contenido}"
        for n, r in enumerate(frag.itertuples(), start=1))
    respuesta = preguntar_llm(SISTEMA_INFORMES, f"Fragmentos:\n{contexto}\n\nPregunta: {pregunta}")
    return respuesta, frag


# ------------------------------------------------------------------ texto a SQL (base de movilidad)
COLUMNAS_VISTA = """movilidad.v_movilidad (una fila por movilidad):
  id INT, periodo TEXT ('2023-2'), anio INT, facultad TEXT, programa_academico TEXT, unidad_regional TEXT,
  programa_movilidad TEXT, tipo_movilidad TEXT, sentido TEXT ('Entrante' o 'Saliente'),
  nombres_apellidos TEXT, tipo_documento TEXT, pais_origen TEXT, pais_destino TEXT,
  ies_origen TEXT, ies_destino TEXT, fecha_inicio DATE (puede ser NULL), fecha_fin DATE (puede ser NULL)"""

CATEGORICAS = ["programa_movilidad", "tipo_movilidad", "sentido", "facultad", "programa_academico",
               "unidad_regional", "pais_origen", "pais_destino"]


@lru_cache(maxsize=1)
def _valores_validos():
    """Valores exactos de cada columna, para que el LLM escriba bien los filtros."""
    partes = []
    with psycopg2.connect(**config.PG_LECTOR) as con, con.cursor() as cur:
        for c in CATEGORICAS:
            cur.execute(f"SELECT DISTINCT {c} FROM movilidad.v_movilidad ORDER BY 1")
            partes.append(f"- {c}: " + "; ".join(str(v[0]) for v in cur.fetchall()))
        cur.execute("SELECT MIN(periodo), MAX(periodo) FROM movilidad.v_movilidad")
        partes.append("- periodo: desde %s hasta %s" % cur.fetchone())
    return "\n".join(partes)


SISTEMA_SQL = """Eres un experto en PostgreSQL. Convierte la pregunta en UNA sola consulta SELECT sobre esta vista:
{columnas}

Valores exactos disponibles:
{valores}

Reglas:
- Usa solo la vista movilidad.v_movilidad. Solo SELECT (o WITH ... SELECT). Nada de INSERT, UPDATE, DELETE, etc.
- Para textos usa los valores exactos de la lista. Para universidades usa ILIKE '%...%'.
- Cada fila es una movilidad: para contar movilidades usa COUNT(*). Usa COUNT(DISTINCT nombres_apellidos) solo si la pregunta pide explícitamente contar personas.
- Pon alias claros en español a las columnas calculadas (por ejemplo AS total).
- Responde SOLO con el SQL, sin explicaciones ni ```."""

PROHIBIDAS = re.compile(r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|"
                        r"execute|call|do|set|reset|vacuum|pg_\w+)\b", re.I)


def generar_sql(pregunta):
    sistema = SISTEMA_SQL.format(columnas=COLUMNAS_VISTA, valores=_valores_validos())
    sql = preguntar_llm(sistema, pregunta, temperatura=0)
    sql = re.sub(r"^```(sql)?|```$", "", sql.strip(), flags=re.I).strip().rstrip(";").strip()
    return sql


def validar_sql(sql):
    if not re.match(r"^(select|with)\b", sql, re.I):
        raise ValueError("La consulta generada no es un SELECT.")
    if ";" in sql:
        raise ValueError("La consulta generada tiene más de una instrucción.")
    if PROHIBIDAS.search(sql):
        raise ValueError("La consulta generada contiene instrucciones no permitidas.")


def ejecutar_sql(sql, limite=1000):
    validar_sql(sql)
    with psycopg2.connect(**config.PG_LECTOR) as con:
        con.set_session(readonly=True)          # doble protección además de los permisos del usuario
        return pd.read_sql(f"SELECT * FROM ({sql}) AS consulta LIMIT {limite}", con)


def responder_movilidad(pregunta):
    sql = generar_sql(pregunta)
    datos = ejecutar_sql(sql)
    resumen = None
    # Solo se envían al LLM resultados agregados y pequeños (sin nombres de personas)
    if 0 < len(datos) <= 30 and "nombres_apellidos" not in datos.columns:
        resumen = preguntar_llm(
            "Explica en español, en 1 a 3 frases, lo que muestran estos datos como respuesta a la pregunta. "
            "No inventes nada que no esté en la tabla.",
            f"Pregunta: {pregunta}\n\nResultado:\n{datos.to_string(index=False)}")
    return sql, datos, resumen
