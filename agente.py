"""
Agente de IA de la Oficina Dialogando con el Mundo.

A diferencia del flujo fijo de consultas.py, aquí el modelo DECIDE qué hacer:
recibe la pregunta, elige qué herramienta usar (o varias), revisa el resultado
y repite hasta tener lo necesario para responder.

Herramientas del agente:
  - consultar_base_movilidad: ejecuta un SELECT sobre la vista movilidad.v_movilidad (cifras y registros).
  - buscar_en_informes: búsqueda semántica (RAG) en los informes PDF de los participantes.
"""
import json
import re

import pandas as pd
import psycopg2

import config
from consultas import COLUMNAS_VISTA, _cliente, _valores_validos, a_pgvector, ejecutar_sql, vectorizar

MAX_PASOS = getattr(config, "MAX_PASOS_AGENTE", 5)          # vueltas máximas de razonamiento
FRAGMENTOS = getattr(config, "FRAGMENTOS_AGENTE", 4)        # fragmentos por búsqueda
MAX_FILAS_AL_MODELO = 30

HERRAMIENTAS = [
    {"type": "function", "function": {
        "name": "consultar_base_movilidad",
        "description": ("Ejecuta una consulta SQL de solo lectura sobre la base de datos de movilidad internacional. "
                        "Úsala para cifras, conteos, listados y comparaciones: cuántas movilidades, por país, por "
                        "programa, por año, por facultad, etc."),
        "parameters": {"type": "object", "required": ["sql"], "properties": {
            "sql": {"type": "string",
                    "description": "Una sola instrucción SELECT de PostgreSQL sobre movilidad.v_movilidad."}}}}},
    {"type": "function", "function": {
        "name": "buscar_en_informes",
        "description": ("Busca en los informes finales que entregan estudiantes y docentes después de su movilidad. "
                        "Úsala para preguntas cualitativas: experiencias, impacto, fortalezas, dificultades, "
                        "oportunidades de mejora, observaciones y recomendaciones."),
        "parameters": {"type": "object", "required": ["consulta"], "properties": {
            "consulta": {"type": "string", "description": "Lo que se quiere encontrar, en lenguaje natural."},
            "programa": {"type": "string", "enum": ["Estudiante Embajador", "Docente Embajador",
                                                    "Casa Cundinamarca", "Voluntariado Internacional"]},
            "sentido": {"type": "string", "enum": ["Entrante", "Saliente"]},
            "pais": {"type": "string", "description": "País de la universidad con la que se hizo la movilidad."},
            "periodo_desde": {"type": "string", "description": "Periodo inicial, formato 2024-1."},
            "periodo_hasta": {"type": "string", "description": "Periodo final, formato 2025-2."}}}}},
]

SISTEMA = """Eres el asistente de la Oficina Dialogando con el Mundo de la Universidad de Cundinamarca.
Respondes preguntas sobre la movilidad internacional de estudiantes y docentes usando tus herramientas.

Cómo trabajar:
- Decide qué herramienta necesitas. Para cifras y registros usa consultar_base_movilidad; para lo que cuentan los
  participantes en sus informes usa buscar_en_informes. Si la pregunta mezcla ambas cosas, usa las dos.
- Puedes llamar herramientas varias veces. Si una consulta falla o no trae resultados, corrígela e inténtalo de nuevo.
- Responde SOLO con lo que devuelvan las herramientas. Si no hay información suficiente, dilo; no inventes.
- Cuando uses fragmentos de informes, cita su número entre corchetes, por ejemplo [1] o [2][3].
- Responde en español, claro y breve. No menciones SQL, tablas ni nombres de herramientas en la respuesta.
- Si te saludan o preguntan algo ajeno a la movilidad internacional, responde brevemente sin usar herramientas.

Base de datos para consultar_base_movilidad:
{columnas}

Valores exactos disponibles:
{valores}

Reglas de SQL:
- Solo SELECT sobre movilidad.v_movilidad. Cada fila es una movilidad: para contar movilidades usa COUNT(*);
  usa COUNT(DISTINCT nombres_apellidos) solo si piden contar personas.
- Usa los valores exactos de la lista. Para universidades usa ILIKE '%...%'.
- Pon alias claros en español a las columnas calculadas."""


# ------------------------------------------------------------------ herramientas
def _buscar_en_informes(estado, consulta, programa=None, sentido=None, pais=None,
                        periodo_desde=None, periodo_hasta=None):
    filtros, params = [], []
    for condicion, valor in (("i.programa_movilidad = %s", programa), ("i.sentido = %s", sentido),
                             ("i.pais ILIKE %s", pais), ("i.periodo >= %s", periodo_desde),
                             ("i.periodo <= %s", periodo_hasta)):
        if valor:
            filtros.append(condicion)
            params.append(valor)
    donde = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    v = a_pgvector(vectorizar([consulta], es_pregunta=True)[0])
    sql = f"""
        SELECT f.id, i.archivo, i.programa_movilidad, i.periodo, i.sentido, i.universidad, i.pais,
               f.campo, f.contenido
        FROM movilidad.informe_fragmento f JOIN movilidad.informe i ON i.id = f.id_informe
        {donde}
        ORDER BY f.embedding <=> %s::vector LIMIT %s"""
    with psycopg2.connect(**config.PG) as con, con.cursor() as cur:
        cur.execute(sql, [*params, v, FRAGMENTOS])
        filas = cur.fetchall()
    if not filas:
        return "No se encontraron informes con esos filtros. Prueba con menos filtros."

    partes = []
    for fid, archivo, prog, periodo, sent, univ, pais_, campo, contenido in filas:
        ya = next((n for n, f in enumerate(estado["fuentes"], start=1) if f["id"] == fid), None)
        if ya is None:                      # numeración única en toda la respuesta
            estado["fuentes"].append({"id": fid, "archivo": archivo, "campo": campo, "contenido": contenido})
            ya = len(estado["fuentes"])
        partes.append(f"[{ya}] Informe {prog} {periodo} ({sent}), {univ or 'universidad no indicada'}, "
                      f"{pais_ or ''} — Campo: {campo}\n{contenido}")
    return "\n\n".join(partes)


def _consultar_base(estado, sql):
    sql = re.sub(r"^```(sql)?|```$", "", sql.strip(), flags=re.I).strip().rstrip(";").strip()
    datos = ejecutar_sql(sql)
    estado["tablas"].append(datos)
    if datos.empty:
        return "La consulta no devolvió filas."
    # Los nombres de personas no se envían al modelo: la tabla se le muestra directamente al usuario
    sin_conteos = re.sub(r"count\s*\(\s*distinct\s+nombres_apellidos\s*\)", "", sql, flags=re.I)
    if "nombres_apellidos" in sin_conteos.lower():
        return (f"La consulta devolvió {len(datos)} filas con nombres de personas. La tabla completa ya se le "
                "muestra al usuario en pantalla; en tu respuesta indica cuántos registros son, sin listar nombres.")
    texto = datos.head(MAX_FILAS_AL_MODELO).to_string(index=False)
    if len(datos) > MAX_FILAS_AL_MODELO:
        texto += f"\n... ({len(datos)} filas en total; el usuario ve la tabla completa)"
    return texto


EJECUTORES = {"consultar_base_movilidad": _consultar_base, "buscar_en_informes": _buscar_en_informes}
NOMBRES = {"consultar_base_movilidad": "Consultó la base de datos de movilidad",
           "buscar_en_informes": "Buscó en los informes"}


# ------------------------------------------------------------------ ciclo del agente
def _llamar_modelo(mensajes, con_herramientas=True):
    extra = dict(tools=HERRAMIENTAS, tool_choice="auto") if con_herramientas else {}
    r = _cliente().chat.completions.create(model=config.LLM["modelo"], temperature=0.1,
                                           messages=mensajes, **extra)
    return r.choices[0].message


def responder(pregunta, historial=None):
    """Devuelve dict con: respuesta (texto), pasos (qué hizo), fuentes (fragmentos citados), tablas (DataFrames).
    historial: lista de (pregunta, respuesta) anteriores, para preguntas de seguimiento."""
    estado = {"fuentes": [], "tablas": [], "pasos": []}
    mensajes = [{"role": "system",
                 "content": SISTEMA.format(columnas=COLUMNAS_VISTA, valores=_valores_validos())}]
    for p, r in (historial or [])[-2:]:                 # memoria corta: las dos últimas preguntas
        mensajes += [{"role": "user", "content": p}, {"role": "assistant", "content": r[:600]}]
    mensajes.append({"role": "user", "content": pregunta})

    for _ in range(MAX_PASOS):
        msg = _llamar_modelo(mensajes)
        if not msg.tool_calls:                          # el agente decidió que ya puede responder
            return dict(estado, respuesta=(msg.content or "").strip())

        mensajes.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in msg.tool_calls]})
        for c in msg.tool_calls:
            nombre = c.function.name
            try:
                args = json.loads(c.function.arguments or "{}")
                resultado = EJECUTORES[nombre](estado, **args)
                estado["pasos"].append({"accion": NOMBRES[nombre], "detalle": args, "ok": True})
            except Exception as e:                      # el error vuelve al agente para que corrija
                resultado = f"Error al ejecutar la herramienta: {str(e).splitlines()[0][:300]}"
                estado["pasos"].append({"accion": NOMBRES.get(nombre, nombre), "detalle": {}, "ok": False})
            mensajes.append({"role": "tool", "tool_call_id": c.id, "content": resultado})

    # Se acabaron los pasos: se le pide responder con lo que ya tiene
    mensajes.append({"role": "user", "content": "Responde ahora con la información que ya obtuviste."})
    msg = _llamar_modelo(mensajes, con_herramientas=False)
    return dict(estado, respuesta=(msg.content or "").strip())
