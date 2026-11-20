"""
Arranque del Asistente de Movilidad Internacional (lo ejecuta "Asistente de Movilidad.bat").

Cada vez que se abre:
  1. Revisa la configuración (.env).
  2. Espera a que la base de datos responda.
  3. Primera vez: crea las tablas y carga el Excel completo.
     Siguientes veces: si el Excel cambió, aplica solo los cambios (sin borrar nada).
  4. Procesa los informes PDF nuevos o modificados (los demás no se tocan).
  5. Abre el asistente en el navegador.

Si el paso 3 o el 4 fallan, avisa y abre el asistente igual con los datos que ya había.

Uso avanzado:
    python iniciar.py --verificar   solo revisa el archivo .env (lo usa el .bat antes de encender Docker)
    python iniciar.py --recargar    borra y vuelve a cargar todo desde cero (Excel e informes)
"""
import hashlib
import importlib.util
import json
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import config

RAIZ = Path(__file__).parent
ESTADO = RAIZ / ".estado_carga.json"          # recuerda qué versión del Excel se cargó por última vez
MARCADORES = ("CAMBIAR_ESTA_CLAVE", "PEGAR_AQUI_LA_CLAVE", "FALTA_LA_CLAVE", "")


def titulo(texto):
    print(f"\n=== {texto} ===", flush=True)


def verificar_configuracion():
    problemas = []
    if config.PG["password"] in MARCADORES:
        problemas.append("POSTGRES_PASSWORD: escribe una clave para la base de datos.")
    elif not config.PG["password"].isalnum():
        problemas.append("POSTGRES_PASSWORD: usa solo letras y números (sin espacios ni símbolos).")
    if config.LLM["api_key"] in MARCADORES:
        problemas.append("LLM_API_KEY: pega la clave del servicio de inteligencia artificial.")
    if problemas:
        print("\n[FALTA CONFIGURAR] Abre el archivo .env con el Bloc de notas y completa:")
        for p in problemas:
            print("  -", p)
        return False
    return True


def esperar_base(segundos=120):
    import psycopg2
    limite = time.time() + segundos
    while True:
        try:
            psycopg2.connect(**config.PG, connect_timeout=5).close()
            return
        except psycopg2.OperationalError as e:
            if "password authentication failed" in str(e):
                raise SystemExit("[ERROR] La clave de la base no coincide con la del archivo .env. "
                                 "Si cambiaste POSTGRES_PASSWORD después de instalar, vuelve a poner la anterior.")
            if time.time() > limite:
                raise SystemExit(f"[ERROR] La base de datos no respondió: {str(e).splitlines()[0]}")
            time.sleep(2)


def consulta(sql):
    import psycopg2
    with psycopg2.connect(**config.PG) as con, con.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def ejecutar_sql(archivo):
    import psycopg2
    with psycopg2.connect(**config.PG) as con, con.cursor() as cur:
        cur.execute((RAIZ / archivo).read_text(encoding="utf-8"))


def modulo(nombre_archivo):
    spec = importlib.util.spec_from_file_location(nombre_archivo.replace(".py", ""), RAIZ / nombre_archivo)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def huella(ruta):
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def leer_estado():
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_estado(**datos):
    estado = leer_estado()
    estado.update(datos)
    ESTADO.write_text(json.dumps(estado, indent=2), encoding="utf-8")


def preparar_base_movilidad(recargar=False):
    excel = Path(config.ARCHIVO_EXCEL)
    hay_tablas = consulta("SELECT to_regclass('movilidad.registro_movilidad') IS NOT NULL")
    hay_datos = hay_tablas and consulta("SELECT COUNT(*) FROM movilidad.registro_movilidad") > 0

    if recargar or not hay_datos:
        titulo("Cargando la base de movilidad" + (" desde cero" if recargar else " (primera vez)"))
        if not excel.exists():
            raise FileNotFoundError(f"No se encuentra el Excel: {excel}\n"
                                    "        Cópialo en la carpeta o corrige ARCHIVO_EXCEL en el archivo .env")
        ejecutar_sql("01_esquema.sql")            # también borra las tablas de informes
        modulo("02_cargar_excel.py").cargar_excel(str(excel), reporte=str(RAIZ / "reporte_limpieza.xlsx"))
        ejecutar_sql("03_esquema_rag.sql")
        guardar_estado(excel=huella(excel))
        return

    if not excel.exists():
        print(f"[AVISO] No se encuentra el Excel ({excel}). Se usan los datos que ya estaban cargados.")
        return
    nueva = huella(excel)
    if leer_estado().get("excel") == nueva:
        print("Base de movilidad: sin cambios en el Excel.")
        return
    titulo("El Excel cambió: actualizando la base de movilidad")
    modulo("actualizar_excel.py").main(str(excel))
    guardar_estado(excel=nueva)


def preparar_informes():
    if not consulta("SELECT to_regclass('movilidad.informe') IS NOT NULL"):
        ejecutar_sql("03_esquema_rag.sql")
    carpeta = Path(config.CARPETA_INFORMES)
    if not carpeta.is_dir():
        print(f"[AVISO] No existe la carpeta de informes: {carpeta}. Se usan los informes ya cargados.")
        return
    titulo("Revisando informes nuevos o modificados")
    modulo("ingesta.py").main(str(carpeta))


def paso_seguro(nombre, funcion, *args):
    """Si un paso de actualización falla, avisa y deja seguir con los datos que ya había."""
    try:
        funcion(*args)
        return True
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[AVISO] No se pudo completar: {nombre}.")
        print(f"        Motivo: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
        (RAIZ / "ultimo_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print("        El detalle quedó en ultimo_error.txt. El asistente se abrirá con los datos anteriores.")
        return False


def abrir_asistente():
    import socket
    import uvicorn
    import api
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", api.PUERTO)) == 0:
            print("\nEl asistente ya estaba abierto en otra ventana. Abriendo el navegador...")
            webbrowser.open(f"http://localhost:{api.PUERTO}")
            return
    titulo("Asistente listo")
    print(f"Se abre en el navegador: http://localhost:{api.PUERTO}")
    print("NO CIERRES ESTA VENTANA mientras uses el asistente. Para apagarlo, ciérrala.\n", flush=True)
    threading.Thread(target=api._precargar, daemon=True).start()
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{api.PUERTO}")).start()
    uvicorn.run(api.app, host="127.0.0.1", port=api.PUERTO, log_level="warning")


def main():
    if not verificar_configuracion():
        sys.exit(1)
    if "--verificar" in sys.argv:
        return
    titulo("Conectando con la base de datos")
    esperar_base()
    print("Base de datos disponible.")

    recargar = "--recargar" in sys.argv
    if recargar:
        try:                                        # al recargar a propósito, un error sí detiene todo
            preparar_base_movilidad(recargar=True)
        except FileNotFoundError as e:
            sys.exit(f"[ERROR] {e}")
    else:
        paso_seguro("actualizar la base de movilidad", preparar_base_movilidad)
    if not consulta("SELECT to_regclass('movilidad.registro_movilidad') IS NOT NULL"):
        sys.exit("[ERROR] La base de movilidad no está creada. Revisa el mensaje de arriba.")
    paso_seguro("procesar los informes", preparar_informes)
    if "--sin-abrir" in sys.argv:
        return
    abrir_asistente()


if __name__ == "__main__":
    main()
