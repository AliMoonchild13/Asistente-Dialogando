-- =====================================================================
-- Tablas del RAG (informes de movilidad) + usuario de solo lectura
-- Ejecutar DESPUÉS de 01_esquema.sql:
--   psql -U postgres -d movilidad_udec -f 03_esquema_rag.sql
-- Requiere la extensión pgvector instalada en el servidor.
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS vector;
SET search_path TO movilidad, public;   -- public: ahí queda el tipo "vector"

DROP TABLE IF EXISTS informe_fragmento, informe;

CREATE TABLE informe (
    id                     SERIAL PRIMARY KEY,
    archivo                TEXT NOT NULL,
    ruta                   TEXT NOT NULL UNIQUE,
    hash_sha256            CHAR(64) NOT NULL,
    programa_movilidad     VARCHAR(80),
    periodo                VARCHAR(6),
    sentido                VARCHAR(10),
    universidad            TEXT,
    pais                   VARCHAR(80),
    id_registro_movilidad  INT REFERENCES registro_movilidad(id),   -- NULL si no se encontró
    fecha_ingesta          TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE informe_fragmento (
    id          SERIAL PRIMARY KEY,
    id_informe  INT NOT NULL REFERENCES informe(id) ON DELETE CASCADE,
    orden       INT NOT NULL,
    campo       TEXT NOT NULL,        -- "Impacto Social", "Fortalezas", "Observaciones"...
    contenido   TEXT NOT NULL,
    embedding   vector(768) NOT NULL  -- misma dimensión que DIMENSION_EMBEDDINGS en config.py
);

CREATE INDEX ix_frag_embedding ON informe_fragmento USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ix_informe_filtros ON informe (programa_movilidad, periodo, sentido);

-- Usuario de solo lectura para las preguntas en lenguaje natural sobre la base de movilidad.
-- Cambia la contraseña y ponla también en config.py (PG_LECTOR).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_lector') THEN
        CREATE ROLE rag_lector LOGIN PASSWORD 'cambiar_esta_clave';
    END IF;
END $$;
GRANT USAGE ON SCHEMA movilidad TO rag_lector;
GRANT SELECT ON movilidad.v_movilidad TO rag_lector;
ALTER ROLE rag_lector SET statement_timeout = '10s';
ALTER ROLE rag_lector SET default_transaction_read_only = on;
