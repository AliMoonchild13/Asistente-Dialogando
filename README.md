# Asistente de Movilidad Internacional

Oficina Dialogando con el Mundo – Universidad de Cundinamarca.  
Sistema de información con base de datos PostgreSQL, Power BI y un agente de IA con RAG para consultar las cifras de movilidad y los informes finales de los participantes.

## Versión v1.0: Prototipo inicial: base de datos y RAG con Streamlit

- Esquema relacional en PostgreSQL (01_esquema.sql) y vista v_movilidad.
- Depuración y carga del Excel de movilidad (02_cargar_excel.py).
- Esquema RAG con pgvector (03_esquema_rag.sql), lectura de informes por secciones (extraer.py) e ingesta con embeddings (ingesta.py).
- Interfaz en Streamlit con dos pestañas: cifras e informes (app.py).

## Datos y claves

Este repositorio **no** contiene datos personales (Excel, informes, respaldos) ni claves.
La configuración se copia de `.env.example` a `.env`, que está excluido de Git.
