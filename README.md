# Asistente de Movilidad Internacional

Oficina Dialogando con el Mundo – Universidad de Cundinamarca.  
Sistema de información con base de datos PostgreSQL, Power BI y un agente de IA con RAG para consultar las cifras de movilidad y los informes finales de los participantes.

## Versión v3.1: Despliegue en servidor Ubuntu con HTTPS

- Nginx como proxy inverso con HTTPS y clave de acceso (compose.server.yml, nginx/asistente.conf).
- Dockerfile: repositorios por HTTPS y reintentos en la construcción.

## Datos y claves

Este repositorio **no** contiene datos personales (Excel, informes, respaldos) ni claves.
La configuración se copia de `.env.example` a `.env`, que está excluido de Git.
