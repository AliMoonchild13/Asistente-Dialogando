"""
Configuración del Asistente de Movilidad Internacional.

NO edites este archivo: todos los valores se cambian en el archivo ".env"
que está en esta misma carpeta (contraseña de la base, clave de IA, carpeta de informes).
"""
import os
from pathlib import Path

RAIZ = Path(__file__).parent


def _leer_env():
    datos = {}
    archivo = RAIZ / ".env"
    if archivo.exists():
        for linea in archivo.read_text(encoding="utf-8-sig").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                clave, valor = linea.split("=", 1)
                datos[clave.strip()] = valor.strip().strip('"')
    return datos


_ENV = _leer_env()


def _valor(nombre, defecto=""):
    """Primero el archivo .env; si no está ahí, la variable de entorno; si no, el valor por defecto."""
    return _ENV.get(nombre) or os.getenv(nombre) or defecto


# --- PostgreSQL (los mismos valores que usa Docker al crear la base) ---
PG = dict(
    host="localhost",
    port=int(_valor("POSTGRES_PORT", "5433")),
    dbname=_valor("POSTGRES_DB", "movilidad_udec"),
    user=_valor("POSTGRES_USER", "postgres"),
    password=_valor("POSTGRES_PASSWORD"),
)

# --- Usuario de solo lectura con el que el asistente consulta la base de movilidad ---
PG_LECTOR = dict(PG, user="rag_lector", password=_valor("PG_LECTOR_PASSWORD", "cambiar_esta_clave"))

# --- Archivos de datos (se aceptan rutas completas, por ejemplo una carpeta de OneDrive) ---
ARCHIVO_EXCEL = _valor("ARCHIVO_EXCEL", "BD_UNION_MOVILIDAD_LIMPIA_con_fechas.xlsx")
if not os.path.isabs(ARCHIVO_EXCEL):
    ARCHIVO_EXCEL = str(RAIZ / ARCHIVO_EXCEL)
CARPETA_INFORMES = _valor("CARPETA_INFORMES") or str(RAIZ / "informes")

# --- Embeddings (se calculan en este equipo; el texto de los informes no sale) ---
MODELO_EMBEDDINGS = _valor("MODELO_EMBEDDINGS", "intfloat/multilingual-e5-base")
DIMENSION_EMBEDDINGS = 768      # debe coincidir con vector(768) en 03_esquema_rag.sql

# --- Modelo de lenguaje (cualquier servicio compatible con el formato de OpenAI) ---
LLM = dict(
    base_url=_valor("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
    api_key=_valor("LLM_API_KEY") or _valor("GROQ_API_KEY", "FALTA_LA_CLAVE"),
    modelo=_valor("LLM_MODELO", "openai/gpt-oss-120b"),
)

FRAGMENTOS_POR_PREGUNTA = 6
