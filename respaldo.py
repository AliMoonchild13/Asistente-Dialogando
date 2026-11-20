"""
Copias de seguridad de la base de movilidad (esquema "movilidad": registros, catálogos e informes).

Se hace una copia automática ANTES de cada actualización del Excel y antes de una recarga completa.
Se conservan las MAX_RESPALDOS más recientes (ver .env).

Uso manual (dentro de Docker):
    docker compose exec app python respaldo.py                 -> crea una copia ahora
    docker compose exec app python respaldo.py listar          -> muestra las copias disponibles
    docker compose exec app python respaldo.py restaurar ARCHIVO.dump
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import config

CARPETA = Path(config.CARPETA_RESPALDOS)


def _conexion():
    pg = config.PG
    args = ["-h", pg["host"], "-p", str(pg["port"]), "-U", pg["user"], "-d", pg["dbname"]]
    return args, dict(os.environ, PGPASSWORD=pg["password"] or "")


def disponible():
    return shutil.which("pg_dump") is not None


def listar():
    return sorted(CARPETA.glob("movilidad_*.dump"), reverse=True) if CARPETA.is_dir() else []


def crear(motivo="manual", conservar=None):
    """Crea la copia y devuelve su ruta. Lanza RuntimeError si no se pudo.
    'conservar': una copia que no se debe borrar al rotar (la que se va a restaurar)."""
    if not disponible():
        raise RuntimeError("pg_dump no está instalado en este equipo.")
    CARPETA.mkdir(parents=True, exist_ok=True)
    archivo = CARPETA / f"movilidad_{datetime.now():%Y%m%d_%H%M%S}_{motivo}.dump"
    args, entorno = _conexion()
    r = subprocess.run(["pg_dump", *args, "-n", "movilidad", "-Fc", "-f", str(archivo)],
                       env=entorno, capture_output=True, text=True)
    if r.returncode != 0:
        archivo.unlink(missing_ok=True)
        raise RuntimeError(f"No se pudo crear la copia de seguridad: {r.stderr.strip()[:300]}")
    for viejo in listar()[config.MAX_RESPALDOS:]:
        if conservar is None or viejo.resolve() != Path(conservar).resolve():
            viejo.unlink(missing_ok=True)
    return archivo


def restaurar(nombre):
    archivo = Path(nombre) if Path(nombre).is_file() else CARPETA / nombre
    if not archivo.is_file():
        raise FileNotFoundError(f"No existe la copia: {nombre}")
    args, entorno = _conexion()
    r = subprocess.run(["pg_restore", "--list", str(archivo)], capture_output=True, text=True)
    if r.returncode != 0 or "SCHEMA - movilidad" not in r.stdout:
        raise RuntimeError(f"{archivo.name} no es una copia válida de la base de movilidad. No se cambió nada.")
    try:
        previa = crear("antes_de_restaurar", conservar=archivo)     # por si hay que deshacer la restauración
    except RuntimeError:
        existe = subprocess.run(["psql", *args, "-tAc", "SELECT to_regnamespace('movilidad') IS NOT NULL"],
                                env=entorno, capture_output=True, text=True).stdout.strip()
        if existe != "f":
            raise
        previa = None                                              # no había base que respaldar
    # Todo en una sola transacción: si algo falla, la base queda exactamente como estaba
    r = subprocess.run(["pg_restore", *args, "--clean", "--if-exists", "--no-owner", "--single-transaction",
                        str(archivo)], env=entorno, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Error al restaurar; la base quedó como estaba ({r.stderr.strip()[:300]})")
    # Se olvida qué Excel estaba aplicado: en la próxima revisión se vuelve a comparar con el de la carpeta
    estado = Path(config.CARPETA_SALIDA) / ".estado_carga.json"
    try:
        datos = json.loads(estado.read_text(encoding="utf-8"))
        datos.pop("excel", None)
        estado.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass
    return previa


if __name__ == "__main__":
    accion = sys.argv[1] if len(sys.argv) > 1 else "crear"
    try:
        if accion == "listar":
            copias = listar()
            print("\n".join(f"  {c.name}  ({c.stat().st_size / 1e6:.1f} MB)" for c in copias) or "No hay copias.")
        elif accion == "restaurar" and len(sys.argv) > 2:
            previa = restaurar(sys.argv[2])
            print(f"Base restaurada desde {sys.argv[2]}."
                  + (f" (La versión anterior quedó en {previa.name})" if previa else ""))
            print("OJO: en la próxima revisión se vuelve a aplicar el Excel que esté en la carpeta. "
                  "Si ese Excel causó el problema, corrígelo o reemplázalo antes.")
            print("Reinicia el asistente para que tome los datos restaurados: docker compose restart app")
        elif accion == "crear":
            print(f"Copia creada: {crear().name}")
        else:
            sys.exit(__doc__)
    except (RuntimeError, FileNotFoundError, subprocess.CalledProcessError) as e:
        sys.exit(f"[ERROR] {e}")
