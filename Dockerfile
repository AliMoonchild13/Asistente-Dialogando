# Imagen del Asistente de Movilidad Internacional (API + agente + página web).
# La construye "docker compose up -d --build"; no hay que ejecutarla a mano.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/modelos \
    TZ=America/Bogota

# Cliente de PostgreSQL 17 (pg_dump / pg_restore) para las copias de seguridad.
# Debe ser de la misma versión que la base (pgvector/pgvector:pg17).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-17 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PyTorch solo para CPU (mucho más liviano que la versión con GPU) y luego el resto de librerías
COPY requirements.txt .
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install -r requirements.txt

# El modelo de embeddings se descarga al construir la imagen: después funciona sin internet
ARG MODELO_EMBEDDINGS=intfloat/multilingual-e5-base
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${MODELO_EMBEDDINGS}')"
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    EN_DOCKER=1

COPY . .

EXPOSE 8000
CMD ["python", "iniciar.py"]
