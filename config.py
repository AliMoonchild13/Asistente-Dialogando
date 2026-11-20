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
    host=_valor("POSTGRES_HOST", "localhost"),      # dentro de Docker es "postgres"
    port=int(_valor("POSTGRES_PORT", "5433")),
    dbname=_valor("POSTGRES_DB", "movilidad_udec"),
    user=_valor("POSTGRES_USER", "postgres"),
    password=_valor("POSTGRES_PASSWORD"),
)

# --- Usuario de solo lectura con el que el asistente consulta la base de movilidad ---
PG_LECTOR = dict(PG, user="rag_lector", password=_valor("PG_LECTOR_PASSWORD", "cambiar_esta_clave"))

# --- Archivos de datos (se aceptan rutas completas, por ejemplo una carpeta de OneDrive) ---
# Dentro de Docker estas rutas las fija compose.yml (/datos/...); fuera de Docker se usan las de aquí.
EN_DOCKER = _valor("EN_DOCKER") == "1"


def _carpeta(nombre, defecto):
    ruta = _valor(nombre) or str(RAIZ / defecto)
    return ruta if os.path.isabs(ruta) else str(RAIZ / ruta)


# Excel de movilidad: se toma el Excel más reciente de esta carpeta (no importa el nombre).
# Compatibilidad: si ARCHIVO_EXCEL apunta a un archivo, se usa ese.
CARPETA_EXCEL = _carpeta("CARPETA_EXCEL", "datos/excel")
ARCHIVO_EXCEL = _valor("ARCHIVO_EXCEL")
if ARCHIVO_EXCEL and not os.path.isabs(ARCHIVO_EXCEL):
    ARCHIVO_EXCEL = str(RAIZ / ARCHIVO_EXCEL)
CARPETA_INFORMES = _carpeta("CARPETA_INFORMES", "datos/informes")
CARPETA_SALIDA = _carpeta("CARPETA_SALIDA", "datos/salida")          # reportes, opiniones, registro
CARPETA_RESPALDOS = _carpeta("CARPETA_RESPALDOS", "datos/respaldos")  # copias de seguridad de la base
CARPETA_POWERBI = _carpeta("CARPETA_POWERBI", "datos/powerbi")        # archivo que lee Power BI
MAX_RESPALDOS = int(_valor("MAX_RESPALDOS", "15"))                     # se conservan los más recientes
INTERVALO_REVISION_MIN = int(_valor("INTERVALO_REVISION_MIN", "10"))   # cada cuánto se buscan cambios

# --- Embeddings (se calculan en este equipo; el texto de los informes no sale) ---
MODELO_EMBEDDINGS = _valor("MODELO_EMBEDDINGS", "intfloat/multilingual-e5-base")
DIMENSION_EMBEDDINGS = 768      # debe coincidir con vector(768) en 03_esquema_rag.sql

# --- Modelo de lenguaje (cualquier servicio compatible con el formato de OpenAI) ---
LLM = dict(
    base_url=_valor("LLM_BASE_URL", "http://ollama:11434/v1"),
    api_key=_valor("LLM_API_KEY") or _valor("GROQ_API_KEY", "FALTA_LA_CLAVE"),
    modelo=_valor("LLM_MODELO", "llama3.1:8b"),
)


def _es_ollama(url):
    return bool(url) and (":11434" in url or "ollama" in url)


# Ollama (modelo local) no usa clave
LLM_ES_OLLAMA = _es_ollama(LLM["base_url"])
if LLM_ES_OLLAMA and LLM["api_key"] in ("", "FALTA_LA_CLAVE", "PEGAR_AQUI_LA_CLAVE"):
    LLM["api_key"] = "ollama"

# --- Modelo de respaldo: se usa solo si el principal falla (sin internet, límite alcanzado, caído) ---
LLM_RESPALDO = dict(
    base_url=_valor("LLM_RESPALDO_BASE_URL"),
    api_key=_valor("LLM_RESPALDO_API_KEY") or "ollama",
    modelo=_valor("LLM_RESPALDO_MODELO"),
)
HAY_RESPALDO = bool(LLM_RESPALDO["base_url"] and LLM_RESPALDO["modelo"])
# Modelos de Ollama que hay que tener descargados (principal y/o respaldo)
MODELOS_OLLAMA = [(c["base_url"], c["modelo"]) for c in (LLM, LLM_RESPALDO)
                  if c["modelo"] and _es_ollama(c["base_url"])]

FRAGMENTOS_POR_PREGUNTA = 6
