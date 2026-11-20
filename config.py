"""Configuración del sistema RAG. Cambia los valores aquí o con variables de entorno."""
import os

# --- PostgreSQL (usuario principal: ingesta y búsqueda de informes) ---
PG = dict(
    host=os.getenv("PGHOST", "localhost"),
    port=int(os.getenv("PGPORT", "5432")),
    dbname=os.getenv("PGDATABASE", "movilidad_udec"),
    user=os.getenv("PGUSER", "postgres"),
    password=os.getenv("PGPASSWORD", "postgres"),
)

# --- Usuario de solo lectura para las preguntas sobre la base de movilidad ---
PG_LECTOR = dict(PG, user=os.getenv("PG_LECTOR_USER", "rag_lector"),
                 password=os.getenv("PG_LECTOR_PASSWORD", "cambiar_esta_clave"))

# --- Carpeta de informes (la carpeta de SharePoint sincronizada con OneDrive) ---
CARPETA_INFORMES = os.getenv("CARPETA_INFORMES", r"C:\Users\TU_USUARIO\Universidad de Cundinamarca\Informes")

# --- Embeddings (se ejecutan en tu equipo; el texto de los informes no sale) ---
MODELO_EMBEDDINGS = os.getenv("MODELO_EMBEDDINGS", "intfloat/multilingual-e5-base")
DIMENSION_EMBEDDINGS = 768      # debe coincidir con vector(768) en 03_esquema_rag.sql

# --- LLM: "groq" (gratis, en la nube) u "ollama" (local) ---
PROVEEDOR_LLM = os.getenv("PROVEEDOR_LLM", "groq")
LLM = {
    "groq": dict(base_url="https://api.groq.com/openai/v1",
                 api_key=os.getenv("GROQ_API_KEY", "PEGA_AQUI_TU_API_KEY"),
                 modelo="llama-3.3-70b-versatile"),
    "ollama": dict(base_url="http://localhost:11434/v1",
                   api_key="ollama",
                   modelo=os.getenv("OLLAMA_MODELO", "llama3.1:8b")),
}[PROVEEDOR_LLM]

FRAGMENTOS_POR_PREGUNTA = 6
