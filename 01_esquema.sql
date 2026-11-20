-- =====================================================================
-- Base de datos de Movilidad Internacional
-- Oficina Dialogando con el Mundo - Universidad de Cundinamarca
-- Ejecutar conectado a la base de datos destino (ej. movilidad_udec):
--   psql -U postgres -d movilidad_udec -f 01_esquema.sql
-- ADVERTENCIA: borra y recrea el esquema "movilidad".
-- =====================================================================

DROP SCHEMA IF EXISTS movilidad CASCADE;
CREATE SCHEMA movilidad;
SET search_path TO movilidad;

-- ---------- Catálogos ----------
CREATE TABLE facultad (
    id      SERIAL PRIMARY KEY,
    nombre  VARCHAR(150) NOT NULL UNIQUE
);

CREATE TABLE programa_academico (
    id           SERIAL PRIMARY KEY,
    nombre       VARCHAR(150) NOT NULL,
    id_facultad  INT NOT NULL REFERENCES facultad(id),
    UNIQUE (nombre, id_facultad)
);

CREATE TABLE unidad_regional (
    id      SERIAL PRIMARY KEY,
    nombre  VARCHAR(80) NOT NULL UNIQUE
);

-- Estudiante Embajador, Casa Cundinamarca, Voluntariado Internacional, Docente Embajador
CREATE TABLE programa_movilidad (
    id      SERIAL PRIMARY KEY,
    nombre  VARCHAR(80) NOT NULL UNIQUE
);

-- Saliente Estudiante, Entrante Estudiante, Saliente GCA, Entrante GCA
CREATE TABLE tipo_movilidad (
    id        SERIAL PRIMARY KEY,
    nombre    VARCHAR(80) NOT NULL UNIQUE,
    sentido   VARCHAR(10) NOT NULL CHECK (sentido IN ('Entrante', 'Saliente'))
);

CREATE TABLE tipo_documento (
    id      SERIAL PRIMARY KEY,
    codigo  VARCHAR(20) NOT NULL UNIQUE
);

CREATE TABLE pais (
    id      SERIAL PRIMARY KEY,
    nombre  VARCHAR(80) NOT NULL UNIQUE
);

CREATE TABLE ies (
    id      SERIAL PRIMARY KEY,
    nombre  VARCHAR(250) NOT NULL UNIQUE
);

-- ---------- Personas ----------
CREATE TABLE persona (
    id                 SERIAL PRIMARY KEY,
    nombres_apellidos  VARCHAR(200) NOT NULL,
    id_tipo_documento  INT NOT NULL REFERENCES tipo_documento(id),
    documento          VARCHAR(30) NOT NULL,
    UNIQUE (id_tipo_documento, documento)
);

-- ---------- Hechos: una fila por movilidad ----------
CREATE TABLE registro_movilidad (
    id                     SERIAL PRIMARY KEY,
    id_persona             INT NOT NULL REFERENCES persona(id),
    periodo                VARCHAR(6) NOT NULL CHECK (periodo ~ '^\d{4}-[12]$'),
    id_programa_academico  INT NOT NULL REFERENCES programa_academico(id),
    id_unidad_regional     INT NOT NULL REFERENCES unidad_regional(id),
    id_programa_movilidad  INT NOT NULL REFERENCES programa_movilidad(id),
    id_tipo_movilidad      INT NOT NULL REFERENCES tipo_movilidad(id),
    id_pais_origen         INT NOT NULL REFERENCES pais(id),
    id_pais_destino        INT NOT NULL REFERENCES pais(id),
    id_ies_origen          INT NOT NULL REFERENCES ies(id),
    id_ies_destino         INT NOT NULL REFERENCES ies(id),
    fecha_inicio           DATE,          -- NULL: el Excel no tenía el dato
    fecha_fin              DATE,
    fila_excel             INT,           -- trazabilidad con el archivo original
    CHECK (fecha_fin IS NULL OR fecha_inicio IS NULL OR fecha_fin >= fecha_inicio)
);

CREATE INDEX ix_mov_periodo  ON registro_movilidad(periodo);
CREATE INDEX ix_mov_persona  ON registro_movilidad(id_persona);
CREATE INDEX ix_mov_programa ON registro_movilidad(id_programa_movilidad);

-- ---------- Vista plana (Power BI y consultas del RAG) ----------
-- No incluye el número de documento (datos personales, Ley 1581 de 2012).
CREATE VIEW v_movilidad AS
SELECT  r.id,
        r.periodo,
        LEFT(r.periodo, 4)::INT          AS anio,
        f.nombre                         AS facultad,
        pa.nombre                        AS programa_academico,
        ur.nombre                        AS unidad_regional,
        pm.nombre                        AS programa_movilidad,
        tm.nombre                        AS tipo_movilidad,
        tm.sentido,
        p.nombres_apellidos,
        td.codigo                        AS tipo_documento,
        po.nombre                        AS pais_origen,
        pd.nombre                        AS pais_destino,
        io.nombre                        AS ies_origen,
        idd.nombre                       AS ies_destino,
        r.fecha_inicio,
        r.fecha_fin
FROM registro_movilidad r
JOIN persona            p   ON p.id  = r.id_persona
JOIN tipo_documento     td  ON td.id = p.id_tipo_documento
JOIN programa_academico pa  ON pa.id = r.id_programa_academico
JOIN facultad           f   ON f.id  = pa.id_facultad
JOIN unidad_regional    ur  ON ur.id = r.id_unidad_regional
JOIN programa_movilidad pm  ON pm.id = r.id_programa_movilidad
JOIN tipo_movilidad     tm  ON tm.id = r.id_tipo_movilidad
JOIN pais               po  ON po.id = r.id_pais_origen
JOIN pais               pd  ON pd.id = r.id_pais_destino
JOIN ies                io  ON io.id = r.id_ies_origen
JOIN ies                idd ON idd.id = r.id_ies_destino;

-- ---------- Usuario de solo lectura para el RAG (text-to-SQL) ----------
-- Descomentar y cambiar la contraseña:
-- CREATE ROLE rag_lector LOGIN PASSWORD 'cambiar_esta_clave';
-- GRANT USAGE ON SCHEMA movilidad TO rag_lector;
-- GRANT SELECT ON movilidad.v_movilidad TO rag_lector;
