"""
Interfaz del sistema RAG — Oficina Dialogando con el Mundo.
Ejecutar:  streamlit run app.py
"""
import warnings

import pandas as pd
import psycopg2
import streamlit as st

import config
from consultas import responder_informes, responder_movilidad

warnings.filterwarnings("ignore", message=".*pandas only supports SQLAlchemy.*")

st.set_page_config(page_title="Consulta de Movilidad Internacional", page_icon="🌎", layout="wide")
st.title("🌎 Consulta de Movilidad Internacional")
st.caption("Oficina Dialogando con el Mundo — Universidad de Cundinamarca")


@st.cache_data(ttl=600)
def opciones_filtros():
    with psycopg2.connect(**config.PG) as con:
        inf = pd.read_sql("SELECT DISTINCT programa_movilidad, periodo FROM movilidad.informe", con)
    return (sorted(inf["programa_movilidad"].dropna().unique()),
            sorted(inf["periodo"].dropna().unique()))


tab_informes, tab_movilidad = st.tabs(["📄 Consultar informes", "📊 Consultar base de movilidad"])

# ------------------------------------------------------------------ informes (RAG)
with tab_informes:
    st.write("Pregunta sobre lo que cuentan los participantes en sus informes finales "
             "(impacto, fortalezas, oportunidades de mejora, observaciones...).")
    programas, periodos = opciones_filtros()
    c1, c2, c3, c4 = st.columns(4)
    programa = c1.selectbox("Programa", ["Todos"] + programas)
    sentido = c2.selectbox("Sentido", ["Todos", "Saliente", "Entrante"])
    desde = c3.selectbox("Desde", ["—"] + periodos)
    hasta = c4.selectbox("Hasta", ["—"] + periodos)

    pregunta = st.text_input("Pregunta", placeholder="¿Qué oportunidades de mejora mencionan los estudiantes salientes?")
    if st.button("Buscar en informes", type="primary") and pregunta:
        with st.spinner("Buscando en los informes..."):
            try:
                respuesta, fuentes = responder_informes(
                    pregunta,
                    programa=None if programa == "Todos" else programa,
                    sentido=None if sentido == "Todos" else sentido,
                    periodo_desde=None if desde == "—" else desde,
                    periodo_hasta=None if hasta == "—" else hasta)
            except Exception as e:
                st.error(f"No se pudo responder: {e}")
                st.stop()
        st.markdown(respuesta)
        if not fuentes.empty:
            st.subheader("Fuentes")
            for n, f in enumerate(fuentes.itertuples(), start=1):
                with st.expander(f"[{n}] {f.archivo} — {f.campo} (similitud {f.similitud:.2f})"):
                    st.write(f.contenido)

# ------------------------------------------------------------------ base de movilidad (texto a SQL)
with tab_movilidad:
    st.write("Pregunta por cifras y registros de la base de movilidad internacional.")
    pregunta_bd = st.text_input("Pregunta", key="bd",
                                placeholder="¿Cuántas movilidades salientes hubo por país de destino en 2025?")
    if st.button("Consultar base", type="primary") and pregunta_bd:
        with st.spinner("Consultando..."):
            try:
                sql, datos, resumen = responder_movilidad(pregunta_bd)
            except Exception as e:
                st.error(f"No se pudo responder: {e}")
                st.stop()
        if resumen:
            st.markdown(resumen)
        st.dataframe(datos, use_container_width=True, hide_index=True)
        st.caption(f"{len(datos)} filas")
        numericas = datos.select_dtypes("number").columns
        if len(datos.columns) == 2 and len(numericas) == 1 and 1 < len(datos) <= 40:
            st.bar_chart(datos.set_index(datos.columns[0]))
        with st.expander("Ver consulta SQL generada"):
            st.code(sql, language="sql")
