# Asistente de Movilidad Internacional

Oficina Dialogando con el Mundo – Universidad de Cundinamarca.  
Sistema de información con base de datos PostgreSQL, Power BI y un agente de IA con RAG para consultar las cifras de movilidad y los informes finales de los participantes.

## Versión v3.0: Despliegue completo en Docker, controles y Power BI

- Tres contenedores: base de datos (PostgreSQL + pgvector), IA local (Ollama) y aplicación; sin archivos .bat.
- Revisión automática cada 10 minutos, validación del Excel, copias de seguridad (respaldo.py) y protección del 20 %.
- Modelo principal (Groq) con respaldo automático (Ollama).
- Diagnóstico de informes sin enlazar y exportación automática para Power BI (exportar_powerbi.py).

## Datos y claves

Este repositorio **no** contiene datos personales (Excel, informes, respaldos) ni claves.
La configuración se copia de `.env.example` a `.env`, que está excluido de Git.
