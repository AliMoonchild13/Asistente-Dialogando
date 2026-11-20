"""
Arranque del Asistente de Movilidad Internacional.

Dentro de Docker lo ejecuta el contenedor "app" cada vez que se enciende (al prender el equipo).
  1. Revisa la configuración (.env) y espera a que la base de datos responda.
  2. Primera vez: crea las tablas y carga el Excel completo.
     Siguientes veces: si el Excel cambió, lo VALIDA, hace una copia de seguridad y aplica solo los cambios.
  3. Procesa los informes PDF nuevos o modificados.
  4. Si se usa el modelo local (Ollama), lo descarga la primera vez.
  5. Abre el asistente (http://localhost:8000) y cada INTERVALO_REVISION_MIN minutos vuelve a revisar
     si hay un Excel o informes nuevos, sin necesidad de reiniciar.

Si una actualización falla, el asistente sigue funcionando con los datos anteriores. El resultado de cada
revisión queda en datos/salida/registro_actualizaciones.log y se muestra como aviso en la página.

Mantenimiento (dentro de Docker):
    docker compose exec app python iniciar.py --recargar --sin-abrir   borra y vuelve a cargar todo
    docker compose exec app python iniciar.py --forzar --sin-abrir     aplica un Excel que quita más del 20 %
"""
import hashlib
import importlib.util
import json
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import config
import respaldo
from validar_excel import ExcelInvalido, buscar_excel, validar

RAIZ = Path(__file__).parent
SALIDA = Path(config.CARPETA_SALIDA)
ESTADO = SALIDA / ".estado_carga.json"                 # qué versión del Excel se cargó por última vez
AVISOS = SALIDA / "estado_actualizacion.json"          # lo lee la página para mostrar avisos
REGISTRO = SALIDA / "registro_actualizaciones.log"
MARCADORES = ("CAMBIAR_ESTA_CLAVE", "PEGAR_AQUI_LA_CLAVE", "FALTA_LA_CLAVE", "")
CANDADO = threading.Lock()                              # una sola actualización a la vez


def titulo(texto):
    print(f"\n=== {texto} ===", flush=True)


def verificar_configuracion():
    problemas = []
    if config.PG["password"] in MARCADORES:
        problemas.append("POSTGRES_PASSWORD: escribe una clave para la base de datos.")
    elif not config.PG["password"].isalnum():
        problemas.append("POSTGRES_PASSWORD: usa solo letras y números (sin espacios ni símbolos).")
    if not config.LLM_ES_OLLAMA and config.LLM["api_key"] in MARCADORES:
        problemas.append("LLM_API_KEY: pega la clave del servicio de inteligencia artificial "
                         "(o usa el modelo local Ollama).")
    if problemas:
        print("\n[FALTA CONFIGURAR] Abre el archivo .env con el Bloc de notas y completa:")
        for p in problemas:
            print("  -", p)
        return False
    return True


def esperar_base(segundos=180):
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


def leer_json(ruta):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_estado(**datos):
    estado = leer_json(ESTADO)
    estado.update(datos)
    ESTADO.write_text(json.dumps(estado, indent=2), encoding="utf-8")


def registrar(tipo, ok, mensaje, **extra):
    """Deja constancia en el registro y en el archivo de avisos que lee la página."""
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(REGISTRO, "a", encoding="utf-8") as f:
        f.write(f"{ahora} | {tipo} | {'OK' if ok else 'RECHAZADO/ERROR'} | {mensaje}\n")
    avisos = leer_json(AVISOS)
    avisos[tipo] = dict(fecha=ahora, ok=ok, mensaje=mensaje, **extra)
    AVISOS.write_text(json.dumps(avisos, indent=2, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ Excel → base de movilidad
def preparar_base_movilidad(recargar=False):
    excel, ignorados = buscar_excel(config.ARCHIVO_EXCEL or config.CARPETA_EXCEL)
    hay_tablas = consulta("SELECT to_regclass('movilidad.registro_movilidad') IS NOT NULL")
    hay_datos = hay_tablas and consulta("SELECT COUNT(*) FROM movilidad.registro_movilidad") > 0
    nueva = huella(excel)

    if not (recargar or not hay_datos) and leer_json(ESTADO).get("excel") == nueva:
        return False                                    # sin cambios: no se hace nada

    titulo(f"Revisando el Excel: {excel.name}")
    if ignorados:
        print(f"Hay {len(ignorados) + 1} Excel en la carpeta; se usa el más reciente ({excel.name}). "
              f"Ignorados: {', '.join(f.name for f in ignorados)}")
    try:
        registros = validar(excel)
    except ExcelInvalido as e:
        guardar_estado(excel_rechazado=nueva)           # no se vuelve a intentar hasta que cambie el archivo
        registrar("excel", False, str(e), archivo=excel.name)
        print(f"[EXCEL RECHAZADO] {e}\nNo se hizo ningún cambio en la base.")
        return False
    print(f"Excel válido: {registros} registros.")

    if hay_datos:                                       # copia de seguridad antes de modificar
        if respaldo.disponible():
            print(f"Copia de seguridad: {respaldo.crear('antes_de_actualizar').name}")
        elif config.EN_DOCKER:
            raise RuntimeError("No se pudo hacer la copia de seguridad (pg_dump no disponible). No se actualizó.")
        else:
            print("[AVISO] pg_dump no está instalado: se actualiza sin copia de seguridad.")

    if recargar or not hay_datos:
        titulo("Cargando la base de movilidad" + (" desde cero" if recargar else " (primera vez)"))
        ejecutar_sql("01_esquema.sql")                  # también borra las tablas de informes
        modulo("02_cargar_excel.py").cargar_excel(str(excel), reporte=str(SALIDA / "reporte_limpieza.xlsx"))
        ejecutar_sql("03_esquema_rag.sql")
        guardar_estado(excel=nueva)
        registrar("excel", True, f"Carga completa: {registros} registros.", archivo=excel.name)
        return True

    titulo("El Excel cambió: actualizando la base de movilidad")
    r = modulo("actualizar_excel.py").main(str(excel))
    guardar_estado(excel=nueva)
    registrar("excel", True, f"Nuevos: {r['nuevos']}, corregidos: {r['corregidos']}, quitados: {r['quitados']} "
                             f"(total {r['registros']}).", archivo=excel.name)
    return True


def revisar_excel(recargar=False):
    excel_previo = leer_json(ESTADO).get("excel_rechazado")
    try:
        excel, _ = buscar_excel(config.ARCHIVO_EXCEL or config.CARPETA_EXCEL)
        if not recargar and "--forzar" not in sys.argv and excel_previo and huella(excel) == excel_previo:
            return False                                # mismo archivo que ya se rechazó: no se reintenta
        return preparar_base_movilidad(recargar)
    except FileNotFoundError as e:
        if not consulta("SELECT to_regclass('movilidad.registro_movilidad') IS NOT NULL"):
            raise
        print(f"[AVISO] {e}. Se usan los datos que ya estaban cargados.")
        return False
    except Exception as e:
        try:                                            # no se reintenta hasta que el archivo cambie
            guardar_estado(excel_rechazado=huella(excel))
        except (OSError, NameError):
            pass
        registrar("excel", False, f"No se pudo aplicar el Excel: {str(e).splitlines()[0][:300]}. "
                                  "No se hizo ningún cambio.")
        raise


# ------------------------------------------------------------------ PDF → informes
def preparar_informes():
    if not consulta("SELECT to_regclass('movilidad.informe') IS NOT NULL"):
        ejecutar_sql("03_esquema_rag.sql")
    carpeta = Path(config.CARPETA_INFORMES)
    if not carpeta.is_dir():
        print(f"[AVISO] No existe la carpeta de informes: {carpeta}. Se usan los informes ya cargados.")
        return False
    try:
        r = modulo("ingesta.py").main(str(carpeta))
    except Exception as e:
        registrar("informes", False, str(e).splitlines()[0][:300])
        raise
    fallo_previo = leer_json(AVISOS).get("informes", {}).get("ok") is False
    if r["nuevos"] or r["eliminados"] or r["errores"] or fallo_previo:
        registrar("informes", r["errores"] == 0,
                  f"Nuevos o actualizados: {r['nuevos']}, eliminados: {r['eliminados']}, con error: {r['errores']} "
                  f"(total {r['total']}).")
    return bool(r["nuevos"] or r["eliminados"])


# ------------------------------------------------------------------ archivo para Power BI
def preparar_powerbi(hubo_cambio=False):
    """Exporta los datos limpios para Power BI (con nombres, sin documentos) cuando cambió el Excel o si el archivo todavía no existe."""
    import exportar_powerbi
    fallo_previo = leer_json(AVISOS).get("powerbi", {}).get("ok") is False     # p. ej. estaba abierto en Excel
    if not hubo_cambio and not fallo_previo and exportar_powerbi.ARCHIVO.exists():
        return
    try:
        n = exportar_powerbi.exportar()
    except Exception as e:
        registrar("powerbi", False, str(e).splitlines()[0][:300])
        raise
    registrar("powerbi", True, f"Exportados {n} registros a {exportar_powerbi.ARCHIVO.name}.")
    print(f"Archivo para Power BI actualizado: {n} registros.")


def paso_seguro(nombre, funcion, *args):
    """Si un paso de actualización falla, avisa y deja seguir con los datos que ya había."""
    try:
        return funcion(*args)
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[AVISO] No se pudo completar: {nombre}.")
        print(f"        Motivo: {str(e).splitlines()[0] if str(e) else type(e).__name__}")
        (SALIDA / "ultimo_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print("        El detalle quedó en ultimo_error.txt. Se siguen usando los datos anteriores.")
        return False


# ------------------------------------------------------------------ modelo local (Ollama)
def preparar_modelo_local():
    """Si el asistente usa Ollama (como principal o como respaldo), descarga el modelo la primera vez."""
    for base_url, modelo in config.MODELOS_OLLAMA:
        _preparar_ollama(base_url, modelo)


def _preparar_ollama(base_url, modelo):
    base = base_url.rstrip("/").removesuffix("/v1")
    limite = time.time() + 120
    while True:
        try:
            with urllib.request.urlopen(f"{base}/api/tags", timeout=5) as r:
                instalados = [m["name"] for m in json.load(r).get("models", [])]
            break
        except OSError:
            if time.time() > limite:
                print(f"[AVISO] El servicio de IA local (Ollama) no responde en {base}.")
                return
            time.sleep(3)
    if any(n == modelo or n == f"{modelo}:latest" for n in instalados):
        print(f"Modelo de IA local listo: {modelo}")
        return
    titulo(f"Descargando el modelo de IA {modelo} (solo la primera vez, puede tardar varios minutos)")
    pedido = urllib.request.Request(f"{base}/api/pull", data=json.dumps({"model": modelo}).encode(),
                                    headers={"Content-Type": "application/json"})
    ultimo = -10
    with urllib.request.urlopen(pedido, timeout=60) as r:
        for linea in r:
            dato = json.loads(linea)
            if "error" in dato:
                raise RuntimeError(dato["error"])
            if dato.get("total"):
                pct = int(dato.get("completed", 0) * 100 / dato["total"])
                if pct < ultimo:                        # el modelo viene en varias partes
                    ultimo = -10
                if pct >= ultimo + 10:
                    print(f"  {pct} %", flush=True)
                    ultimo = pct
    print(f"Modelo de IA local listo: {modelo}")


# ------------------------------------------------------------------ revisión periódica
def revisar_cambios():
    """Busca un Excel o informes nuevos; si hubo cambios, refresca los valores que usa el agente."""
    with CANDADO:
        cambio_excel = paso_seguro("actualizar la base de movilidad", revisar_excel)
        paso_seguro("exportar el archivo para Power BI", preparar_powerbi, cambio_excel)
        cambio = paso_seguro("procesar los informes", preparar_informes) or cambio_excel
    if cambio:
        import consultas
        consultas._valores_validos.cache_clear()       # el agente ve los países/programas nuevos


def vigilar():
    while True:
        time.sleep(config.INTERVALO_REVISION_MIN * 60)
        try:
            revisar_cambios()
        except Exception:
            traceback.print_exc()


def abrir_asistente():
    import socket
    import uvicorn
    import api
    host = "0.0.0.0" if config.EN_DOCKER else "127.0.0.1"   # en Docker solo se publica en 127.0.0.1 del equipo
    if not config.EN_DOCKER:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", api.PUERTO)) == 0:
                print("\nEl asistente ya estaba abierto. Abriendo el navegador...")
                webbrowser.open(f"http://localhost:{api.PUERTO}")
                return
    titulo("Asistente listo")
    print(f"Dirección: http://localhost:{api.PUERTO}", flush=True)
    threading.Thread(target=api._precargar, daemon=True).start()
    threading.Thread(target=paso_seguro, args=("preparar el modelo de IA local", preparar_modelo_local),
                     daemon=True).start()
    threading.Thread(target=vigilar, daemon=True).start()
    if not config.EN_DOCKER:
        print("NO CIERRES ESTA VENTANA mientras uses el asistente.\n", flush=True)
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{api.PUERTO}")).start()
    uvicorn.run(api.app, host=host, port=api.PUERTO, log_level="warning")


def main():
    if not verificar_configuracion():
        if config.EN_DOCKER:
            time.sleep(60)                              # evita reinicios seguidos mientras se corrige el .env
        sys.exit(1)
    if "--verificar" in sys.argv:
        return
    SALIDA.mkdir(parents=True, exist_ok=True)
    titulo("Conectando con la base de datos")
    esperar_base()
    print("Base de datos disponible.")

    if "--recargar" in sys.argv:
        with CANDADO:
            try:                                        # al recargar a propósito, un error sí detiene todo
                cambio_excel = revisar_excel(recargar=True)
            except (FileNotFoundError, ExcelInvalido) as e:
                sys.exit(f"[ERROR] {e}")
    else:
        with CANDADO:
            cambio_excel = paso_seguro("actualizar la base de movilidad", revisar_excel)
    if not consulta("SELECT to_regclass('movilidad.registro_movilidad') IS NOT NULL"):
        print("[ERROR] La base de movilidad no está creada: pon un Excel válido en la carpeta de datos.", flush=True)
        if config.EN_DOCKER:
            time.sleep(60)                              # Docker reinicia el contenedor y se vuelve a intentar
        sys.exit(1)
    paso_seguro("exportar el archivo para Power BI", preparar_powerbi, cambio_excel)
    with CANDADO:
        paso_seguro("procesar los informes", preparar_informes)
    if "--sin-abrir" in sys.argv:
        return
    abrir_asistente()


if __name__ == "__main__":
    main()
