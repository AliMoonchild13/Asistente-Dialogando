"""
Lee los informes PDF de la carpeta (sincronizada desde SharePoint), los fragmenta por campo,
calcula los embeddings y los guarda en PostgreSQL.

Uso:
    python ingesta.py                    # usa CARPETA_INFORMES del archivo .env
    python ingesta.py "D:\\ruta\\informes" # o una carpeta específica

Se puede ejecutar las veces que quiera: solo procesa informes nuevos o modificados
y borra de la base los que ya no están en la carpeta.
"""
import hashlib
import sys
import unicodedata
from pathlib import Path

import psycopg2

import config
from consultas import a_pgvector, vectorizar
from extraer import datos_del_nombre, dividir, extraer_campos


def sin_tildes(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def buscar_registro(cur, datos):
    """Enlaza el informe con su fila en registro_movilidad (programa + periodo + sentido + nombre)."""
    if not (datos["periodo"] and datos["persona"]):
        return None
    cur.execute("""
        SELECT r.id, p.nombres_apellidos
        FROM movilidad.registro_movilidad r
        JOIN movilidad.persona p            ON p.id  = r.id_persona
        JOIN movilidad.programa_movilidad pm ON pm.id = r.id_programa_movilidad
        JOIN movilidad.tipo_movilidad tm    ON tm.id = r.id_tipo_movilidad
        WHERE r.periodo = %s
          AND (%s IS NULL OR pm.nombre = %s)
          AND (%s IS NULL OR tm.sentido = %s)""",
        (datos["periodo"], datos["programa_movilidad"], datos["programa_movilidad"],
         datos["sentido"], datos["sentido"]))
    partes = sin_tildes(datos["persona"]).split()
    candidatos = [rid for rid, nombre in cur.fetchall()
                  if all(p in sin_tildes(nombre).split() for p in partes)]
    return candidatos[0] if len(candidatos) == 1 else None


def procesar(cur, ruta, hash_archivo):
    datos = datos_del_nombre(ruta.name)
    campos = extraer_campos(ruta)
    if not campos:
        print(f"  ! Sin texto (¿PDF escaneado?): {ruta.name}")
        return 0

    generales = next((c["texto"] for c in campos if c["campo"] == "Datos generales"), "")
    universidad = pais = None
    for parte in generales.rstrip(".").split(". "):
        clave, _, valor = parte.partition(": ")
        universidad = valor if clave == "Universidad" else universidad
        pais = valor if clave == "País" else pais

    cur.execute("""
        INSERT INTO movilidad.informe (archivo, ruta, hash_sha256, programa_movilidad, periodo, sentido,
                                       universidad, pais, id_registro_movilidad)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (ruta.name, str(ruta), hash_archivo, datos["programa_movilidad"], datos["periodo"],
         datos["sentido"], universidad, pais, buscar_registro(cur, datos)))
    id_informe = cur.fetchone()[0]

    fragmentos = [(c["campo"], trozo) for c in campos for trozo in dividir(c["texto"])]
    # El embedding incluye el nombre del campo para que la búsqueda lo tenga en cuenta
    vectores = vectorizar([f"{campo}: {texto}" for campo, texto in fragmentos])
    for orden, ((campo, texto), v) in enumerate(zip(fragmentos, vectores)):
        cur.execute("""INSERT INTO movilidad.informe_fragmento (id_informe, orden, campo, contenido, embedding)
                       VALUES (%s, %s, %s, %s, %s::vector)""", (id_informe, orden, campo, texto, a_pgvector(v)))
    return len(fragmentos)


def main(carpeta):
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        sys.exit(f"No existe la carpeta: {carpeta}")
    pdfs = sorted(p for p in carpeta.rglob("*") if p.suffix.lower() == ".pdf")
    print(f"{len(pdfs)} PDF encontrados en {carpeta}")

    with psycopg2.connect(**config.PG) as con, con.cursor() as cur:
        cur.execute("SELECT ruta, hash_sha256 FROM movilidad.informe")
        en_base = dict(cur.fetchall())
        if en_base and not pdfs:
            # Carpeta vacía o sin sincronizar: no se borran los informes que ya estaban cargados
            raise RuntimeError(f"La carpeta de informes está vacía ({carpeta}) y la base tiene {len(en_base)} "
                               "informes. No se borró nada. Revisa que la carpeta esté bien sincronizada.")
        nuevos = omitidos = errores = 0

        for ruta in pdfs:
            h = hashlib.sha256(ruta.read_bytes()).hexdigest()
            if en_base.get(str(ruta)) == h:
                omitidos += 1
                continue
            cur.execute("DELETE FROM movilidad.informe WHERE ruta = %s", (str(ruta),))  # si cambió
            try:
                cur.execute("SAVEPOINT informe")
                n = procesar(cur, ruta, h)
                print(f"  + {ruta.name}: {n} fragmentos")
                nuevos += 1
            except Exception as e:           # un PDF dañado no detiene la carga de los demás
                cur.execute("ROLLBACK TO SAVEPOINT informe")
                print(f"  ! Error en {ruta.name}: {e}")
                errores += 1

        rutas_actuales = [str(p) for p in pdfs]
        cur.execute("DELETE FROM movilidad.informe WHERE NOT (ruta = ANY(%s))", (rutas_actuales,))
        borrados = cur.rowcount

        # Reintenta enlazar los informes que aún no tienen registro (por ejemplo, si se corrigió el Excel)
        cur.execute("SELECT id, archivo FROM movilidad.informe WHERE id_registro_movilidad IS NULL")
        reenlazados = 0
        for id_informe, archivo in cur.fetchall():
            id_registro = buscar_registro(cur, datos_del_nombre(archivo))
            if id_registro:
                cur.execute("UPDATE movilidad.informe SET id_registro_movilidad = %s WHERE id = %s",
                            (id_registro, id_informe))
                reenlazados += 1
        if reenlazados:
            print(f"Informes enlazados ahora con su registro: {reenlazados}")

        cur.execute("SELECT COUNT(*), COUNT(id_registro_movilidad) FROM movilidad.informe")
        total, enlazados = cur.fetchone()

    print(f"\nNuevos o actualizados: {nuevos} | Sin cambios: {omitidos} | Con error: {errores} | "
          f"Eliminados: {borrados}")
    print(f"Informes en la base: {total} ({enlazados} enlazados con su registro de movilidad)")
    return {"nuevos": nuevos, "sin_cambios": omitidos, "errores": errores, "eliminados": borrados,
            "total": total, "enlazados": enlazados}


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else config.CARPETA_INFORMES)
