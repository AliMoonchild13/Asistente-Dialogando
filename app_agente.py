"""
Interfaz de chat del agente — Oficina Dialogando con el Mundo.
Ejecutar:  python -m streamlit run app_agente.py
"""
import warnings

import streamlit as st

from agente import responder

warnings.filterwarnings("ignore", message=".*pandas only supports SQLAlchemy.*")

st.set_page_config(page_title="Asistente de Movilidad Internacional", page_icon="🌎", layout="centered")
st.title("🌎 Asistente de Movilidad Internacional")
st.caption("Oficina Dialogando con el Mundo — Universidad de Cundinamarca")

if "chat" not in st.session_state:
    st.session_state.chat = []          # cada elemento: {"pregunta": ..., "resultado": {...}}


def mostrar(resultado):
    st.markdown(resultado["respuesta"] or "No pude generar una respuesta. Intenta reformular la pregunta.")

    for tabla in resultado["tablas"]:
        if tabla.empty:
            continue
        st.dataframe(tabla, width="stretch", hide_index=True)
        numericas = tabla.select_dtypes("number").columns
        if len(tabla.columns) == 2 and len(numericas) == 1 and 1 < len(tabla) <= 40:
            st.bar_chart(tabla.set_index(tabla.columns[0]))

    if resultado["fuentes"]:
        with st.expander(f"Fuentes ({len(resultado['fuentes'])} fragmentos de informes)"):
            for n, f in enumerate(resultado["fuentes"], start=1):
                st.markdown(f"**[{n}] {f['archivo']}** — {f['campo']}")
                st.write(f["contenido"])

    if resultado["pasos"]:
        with st.expander("¿Cómo se obtuvo esta respuesta?"):
            for n, p in enumerate(resultado["pasos"], start=1):
                filtros = {k: v for k, v in p["detalle"].items() if k not in ("sql", "consulta")}
                buscado = p["detalle"].get("consulta")
                texto = f"{n}. {p['accion']}"
                if buscado:
                    texto += f": *{buscado}*"
                if filtros:
                    texto += " (" + ", ".join(f"{k}: {v}" for k, v in filtros.items()) + ")"
                if not p["ok"]:
                    texto += " — no funcionó y lo intentó de otra forma"
                st.markdown(texto)


if not st.session_state.chat:
    st.info("Pregunta por cifras de movilidad, por lo que cuentan los participantes en sus informes, "
            "o por ambas cosas. Por ejemplo:\n\n"
            "- ¿Cuántas movilidades salientes hubo por país en 2025?\n"
            "- ¿Qué oportunidades de mejora mencionan los estudiantes embajadores?\n"
            "- ¿A qué país viajaron más estudiantes en 2025 y qué cuentan de esa experiencia?")

for turno in st.session_state.chat:
    with st.chat_message("user"):
        st.markdown(turno["pregunta"])
    with st.chat_message("assistant"):
        mostrar(turno["resultado"])

pregunta = st.chat_input("Escribe tu pregunta")
if pregunta:
    with st.chat_message("user"):
        st.markdown(pregunta)
    with st.chat_message("assistant"):
        with st.spinner("Analizando la pregunta..."):
            try:
                historial = [(t["pregunta"], t["resultado"]["respuesta"]) for t in st.session_state.chat]
                resultado = responder(pregunta, historial)
            except Exception as e:
                mensaje = str(e)
                if "429" in mensaje:
                    st.warning("Se alcanzó el límite de uso del servicio de IA. Espera un minuto e intenta de nuevo.")
                else:
                    st.error(f"No se pudo responder: {mensaje}")
                st.stop()
        mostrar(resultado)
    st.session_state.chat.append({"pregunta": pregunta, "resultado": resultado})

if st.session_state.chat and st.sidebar.button("Nueva conversación"):
    st.session_state.chat = []
    st.rerun()
