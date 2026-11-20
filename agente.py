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
import time

import openai

import pandas as pd
import psycopg2

import config
from consultas import COLUMNAS_VISTA, _cliente, _valores_validos, a_pgvector, ejecutar_sql, vectorizar

MAX_PASOS = getattr(config, "MAX_PASOS_AGENTE", 5)          # vueltas máximas de razonamiento
FRAGMENTOS = getattr(config, "FRAGMENTOS_AGENTE", 4)        # fragmentos por búsqueda
MAX_FILAS_AL_MODELO = 30
MAX_CARACTERES_FRAGMENTO = 900
POR_PROGRAMA = 2                    # sin filtro de programa: fragmentos de CADA programa (búsqueda balanceada)      # al modelo le llega un extracto; el usuario ve el fragmento completo en las fuentes

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
            # Filtros opcionales: el modelo puede omitirlos o enviarlos vacíos (null)
            "programa": {"type": ["string", "null"], "enum": ["Estudiante Embajador", "Docente Embajador",
                                                              "Casa Cundinamarca", "Voluntariado Internacional", None]},
            "sentido": {"type": ["string", "null"], "enum": ["Entrante", "Saliente", None]},
            "pais": {"type": ["string", "null"], "description": "País de la universidad con la que se hizo la movilidad."},
            "periodo_desde": {"type": ["string", "null"], "description": "Periodo inicial, formato 2024-1."},
            "periodo_hasta": {"type": ["string", "null"], "description": "Periodo final, formato 2025-2."}}}}},
    {"type": "function", "function": {
        "name": "listar_informes",
        "description": ("Lista los informes finales cargados en el sistema (programa, periodo, sentido, universidad, "
                        "país y si están enlazados a la base de movilidad). Úsala para saber qué informes hay, "
                        "cuántos se han entregado o cuáles faltan; no sirve para leer su contenido."),
        "parameters": {"type": "object", "properties": {
            "programa": {"type": ["string", "null"], "enum": ["Estudiante Embajador", "Docente Embajador",
                                                              "Casa Cundinamarca", "Voluntariado Internacional", None]},
            "sentido": {"type": ["string", "null"], "enum": ["Entrante", "Saliente", None]},
            "pais": {"type": ["string", "null"], "description": "País de la universidad de la movilidad."},
            "periodo_desde": {"type": ["string", "null"], "description": "Periodo inicial, formato 2024-1."},
            "periodo_hasta": {"type": ["string", "null"], "description": "Periodo final, formato 2025-2."}}}}},
]

SISTEMA = """Eres el asistente de la Oficina Dialogando con el Mundo de la Universidad de Cundinamarca.
Respondes preguntas sobre la movilidad internacional de estudiantes y docentes usando tus herramientas.

Cómo trabajar:
- Decide qué herramienta necesitas. Para cifras y registros usa consultar_base_movilidad; para lo que cuentan los
  participantes en sus informes usa buscar_en_informes. Si la pregunta mezcla ambas cosas, usa las dos.
- Para saber qué informes hay o cuántos se entregaron usa listar_informes. El listado completo ya se le muestra
  al usuario en pantalla: no lo repitas, solo resume las cifras.
- Puedes llamar herramientas varias veces. Si una consulta falla o no trae resultados, corrígela e inténtalo de nuevo.
- buscar_en_informes SIN filtro de programa ya devuelve fragmentos de CADA programa que tiene informes: úsala así
  cuando pregunten "por programa" o comparen programas. Usa el filtro de programa solo si preguntan por uno.
- Si la herramienta devolvió fragmentos, ÚSALOS para responder (aunque sean pocos): no digas que no hay
  información. Un programa que no aparece en los resultados es porque no tiene informes cargados.
- Si piden una tabla, responde con una tabla en formato markdown.
- Responde SOLO con lo que devuelvan las herramientas. Si no hay información suficiente, dilo; no inventes.
- Cuando uses fragmentos de informes, cita su número entre corchetes, por ejemplo [1] o [2][3].
- Responde en español, claro y breve. No menciones SQL, tablas ni nombres de herramientas en la respuesta.
- Si te saludan, saluda y explica en una o dos frases qué puedes consultar, sin usar herramientas.
- Si preguntan algo AJENO a la movilidad internacional de la oficina (cultura general, otros temas, tareas,
  opiniones), NO lo respondas aunque sepas la respuesta: di amablemente que solo puedes ayudar con la información
  de movilidad internacional y de los informes de la oficina, y sugiere un ejemplo de pregunta. No uses
  conocimiento general para responder.

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
def _recortar(texto, n=MAX_CARACTERES_FRAGMENTO):
    """Menos texto por fragmento = menos tokens por pregunta (el plan gratuito limita los tokens por minuto)."""
    if len(texto) <= n:
        return texto
    corte = texto.rfind(" ", 0, n)
    return texto[:corte if corte > 0 else n] + "…"

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
    columnas = """f.id, i.archivo, i.programa_movilidad, i.periodo, i.sentido, i.universidad, i.pais,
               f.campo, f.contenido"""
    desde = "FROM movilidad.informe_fragmento f JOIN movilidad.informe i ON i.id = f.id_informe"
    if programa:            # un solo programa: los fragmentos más parecidos
        sql = f"SELECT {columnas} {desde} {donde} ORDER BY f.embedding <=> %s::vector LIMIT %s"
        valores = [*params, v, FRAGMENTOS]
    else:                   # sin programa: los más parecidos DE CADA programa, para no dejar ninguno por fuera
        sql = f"""
            SELECT id, archivo, programa_movilidad, periodo, sentido, universidad, pais, campo, contenido
            FROM (SELECT {columnas}, f.embedding <=> %s::vector AS distancia,
                         ROW_NUMBER() OVER (PARTITION BY i.programa_movilidad
                                            ORDER BY f.embedding <=> %s::vector) AS puesto
                  {desde} {donde}) x
            WHERE puesto <= %s ORDER BY distancia"""
        valores = [v, v, *params, POR_PROGRAMA]
    with psycopg2.connect(**config.PG) as con, con.cursor() as cur:
        cur.execute(sql, valores)
        filas = cur.fetchall()
    if not filas:
        if programa:
            return f"No hay informes cargados de {programa} con esos filtros."
        return "No se encontraron informes con esos filtros. Prueba con menos filtros."

    partes = []
    for fid, archivo, prog, periodo, sent, univ, pais_, campo, contenido in filas:
        ya = next((n for n, f in enumerate(estado["fuentes"], start=1) if f["id"] == fid), None)
        if ya is None:                      # numeración única en toda la respuesta
            estado["fuentes"].append({"id": fid, "archivo": archivo, "campo": campo, "contenido": contenido})
            ya = len(estado["fuentes"])
        partes.append(f"[{ya}] Informe {prog} {periodo} ({sent}), {univ or 'universidad no indicada'}, "
                      f"{pais_ or ''} — Campo: {campo}\n{_recortar(contenido)}")
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


def _listar_informes(estado, programa=None, sentido=None, pais=None, periodo_desde=None, periodo_hasta=None):
    filtros, params = [], []
    for condicion, valor in (("programa_movilidad = %s", programa), ("sentido = %s", sentido),
                             ("pais ILIKE %s", pais), ("periodo >= %s", periodo_desde),
                             ("periodo <= %s", periodo_hasta)):
        if valor:
            filtros.append(condicion)
            params.append(valor)
    donde = ("WHERE " + " AND ".join(filtros)) if filtros else ""
    sql = f"""SELECT regexp_replace(archivo, '\\.pdf$', '', 'i') AS informe, programa_movilidad, periodo, sentido, universidad, pais,
                     CASE WHEN id_registro_movilidad IS NULL THEN 'No' ELSE 'Sí' END AS en_base_de_movilidad
              FROM movilidad.informe {donde} ORDER BY periodo, programa_movilidad, archivo"""
    with psycopg2.connect(**config.PG) as con, con.cursor() as cur:
        cur.execute(sql, params)
        datos = pd.DataFrame(cur.fetchall(), columns=[c[0] for c in cur.description])
    estado["tablas"].append(datos)
    if datos.empty:
        return "No hay informes cargados con esos filtros."
    # Al modelo solo van cifras: los nombres de archivo llevan el nombre del estudiante
    resumen = datos.groupby(["programa_movilidad", "periodo"]).size().reset_index(name="informes")
    sin_enlace = int((datos["en_base_de_movilidad"] == "No").sum())
    return (f"Hay {len(datos)} informes cargados ({sin_enlace} no enlazados a un registro de la base de movilidad). "
            f"El listado completo ya se le muestra al usuario. Resumen por programa y periodo:\n"
            + resumen.to_string(index=False))


EJECUTORES = {"consultar_base_movilidad": _consultar_base, "buscar_en_informes": _buscar_en_informes,
              "listar_informes": _listar_informes}
NOMBRES = {"listar_informes": "Consultó el listado de informes",
           "consultar_base_movilidad": "Consultó la base de datos de movilidad",
           "buscar_en_informes": "Buscó en los informes"}


# ------------------------------------------------------------------ ciclo del agente
PAUSA_PRINCIPAL = 300          # si el modelo principal falla, se usa el respaldo durante 5 minutos
_principal_en_pausa_hasta = 0.0
# Fallas del SERVICIO (no de la pregunta): sin internet, demora, límite, clave inválida, caído, modelo retirado
FALLAS_DEL_SERVICIO = (openai.APIConnectionError, openai.RateLimitError, openai.AuthenticationError,
                       openai.PermissionDeniedError, openai.NotFoundError, openai.InternalServerError)


def _espera_por_minuto(error):
    """Si el límite alcanzado es POR MINUTO, devuelve cuántos segundos esperar; si es diario u otro, None."""
    texto = str(error)
    if not re.search(r"per minute|\(TPM\)|\(RPM\)", texto, re.I):
        return None
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", texto)
    segundos = (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else 10
    return segundos if segundos <= 30 else None


def _pedir(llm, mensajes, extra):
    for intento in range(2):
        try:
            inicio = time.time()
            r = _cliente(llm).chat.completions.create(model=llm["modelo"], temperature=0.1,
                                                      messages=mensajes, **extra)
            uso = getattr(r, "usage", None)
            print(f"[IA] {llm['modelo']}: {time.time() - inicio:.1f} s"
                  + (f", {uso.total_tokens} tokens" if uso else ""), flush=True)
            return r.choices[0].message
        except Exception as e:
            # A veces el modelo arma mal la llamada a una herramienta: se reintenta una vez
            if "tool_use_failed" not in str(e) or intento == 1:
                raise


def _llamar_modelo(mensajes, con_herramientas=True, estado=None):
    """Usa el modelo principal; si el servicio falla y hay respaldo configurado, responde con el respaldo."""
    global _principal_en_pausa_hasta
    extra = dict(tools=HERRAMIENTAS, tool_choice="auto") if con_herramientas else {}
    estado = estado if estado is not None else {}
    if not (config.HAY_RESPALDO and (estado.get("respaldo") or time.time() < _principal_en_pausa_hasta)):
        try:
            try:
                return _pedir(config.LLM, mensajes, extra)
            except openai.RateLimitError as e:
                espera = _espera_por_minuto(e)
                if espera is None:                      # límite diario: no sirve esperar
                    raise
                print(f"[IA] Límite por minuto del servicio: se espera {espera:.0f} s y se reintenta.", flush=True)
                time.sleep(espera + 0.5)
                return _pedir(config.LLM, mensajes, extra)
        except FALLAS_DEL_SERVICIO as e:
            if not config.HAY_RESPALDO:
                raise
            _principal_en_pausa_hasta = time.time() + PAUSA_PRINCIPAL
            print(f"[AVISO] El modelo principal no respondió ({type(e).__name__}); se usa el respaldo "
                  f"{config.LLM_RESPALDO['modelo']} durante {PAUSA_PRINCIPAL // 60} minutos.", flush=True)
    estado["respaldo"] = True
    return _pedir(config.LLM_RESPALDO, mensajes, extra)


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
        msg = _llamar_modelo(mensajes, estado=estado)
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
    msg = _llamar_modelo(mensajes, con_herramientas=False, estado=estado)
    return dict(estado, respuesta=(msg.content or "").strip())
