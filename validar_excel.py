"""
Controles de seguridad del Excel de movilidad, ANTES de tocar la base de datos.

- buscar_excel(): toma el Excel más reciente de la carpeta, sin depender del nombre del archivo.
- validar(): revisa que el archivo abra, que tenga la hoja y las columnas esperadas,
  que los periodos y las fechas tengan un formato válido y que no le falten datos obligatorios.

Si algo falla se lanza ExcelInvalido con un mensaje claro y la base NO se modifica.

Uso manual (para revisar un archivo sin cargarlo):
    python validar_excel.py "ruta del Excel.xlsx"
"""
import importlib.util
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

_spec = importlib.util.spec_from_file_location("carga", Path(__file__).with_name("02_cargar_excel.py"))
carga = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(carga)

EXTENSIONES = (".xlsx", ".xlsm")
PERIODO = re.compile(r"^\d{4}-[12]$")
MAX_FILAS_EN_MENSAJE = 10


class ExcelInvalido(Exception):
    """El Excel no se puede cargar. El mensaje explica qué corregir."""


def _filas(lista):
    lista = [int(x) for x in lista]
    texto = ", ".join(map(str, lista[:MAX_FILAS_EN_MENSAJE]))
    return texto + (f" y {len(lista) - MAX_FILAS_EN_MENSAJE} más" if len(lista) > MAX_FILAS_EN_MENSAJE else "")


def buscar_excel(ubicacion):
    """Devuelve (archivo elegido, lista de otros Excel ignorados).
    'ubicacion' puede ser un archivo o una carpeta; en una carpeta se usa el Excel modificado más recientemente."""
    p = Path(ubicacion)
    if p.is_file():
        return p, []
    if not p.is_dir():
        raise FileNotFoundError(f"No existe la carpeta del Excel: {p}")
    candidatos = [f for f in p.iterdir()
                  if f.is_file() and f.suffix.lower() in EXTENSIONES and not f.name.startswith(("~$", "."))]
    if not candidatos:
        raise FileNotFoundError(f"No hay ningún archivo Excel (.xlsx) en la carpeta: {p}")
    candidatos.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return candidatos[0], candidatos[1:]


def validar(ruta):
    """Revisa el archivo. Devuelve el número de registros si todo está bien; si no, lanza ExcelInvalido."""
    ruta = Path(ruta)
    if ruta.suffix.lower() not in EXTENSIONES:
        raise ExcelInvalido(f"'{ruta.name}' no es un Excel .xlsx. Guárdalo como 'Libro de Excel (.xlsx)'.")
    if ruta.stat().st_size == 0:
        raise ExcelInvalido(f"'{ruta.name}' está vacío (0 bytes). Puede que no haya terminado de copiarse o sincronizarse.")
    if not zipfile.is_zipfile(ruta):
        raise ExcelInvalido(f"'{ruta.name}' está dañado o no es realmente un Excel (no se puede abrir).")

    try:
        libro = pd.ExcelFile(ruta)
    except Exception as e:
        raise ExcelInvalido(f"'{ruta.name}' no se pudo abrir: {str(e).splitlines()[0][:150]}")
    hoja = carga.HOJA if carga.HOJA in libro.sheet_names else libro.sheet_names[0]
    try:
        df = pd.read_excel(libro, sheet_name=hoja, dtype=object)
    except Exception as e:
        raise ExcelInvalido(f"No se pudo leer la hoja '{hoja}': {str(e).splitlines()[0][:150]}")

    faltan = [c for c in carga.COLUMNAS if c not in df.columns]
    if faltan:
        raise ExcelInvalido(f"A la hoja '{hoja}' le faltan columnas: {', '.join(faltan)}. "
                            "Los títulos deben estar en la primera fila y escritos igual que en el formato original.")

    df = df[list(carga.COLUMNAS)].rename(columns=carga.COLUMNAS)
    df["fila_excel"] = df.index + 2
    df = df.dropna(how="all", subset=list(carga.COLUMNAS.values()))
    if df.empty:
        raise ExcelInvalido(f"La hoja '{hoja}' no tiene registros.")

    problemas = []
    periodos = df["periodo"].map(carga.espacios)
    malos = df.loc[periodos.notna() & ~periodos.fillna("").str.match(PERIODO), "fila_excel"]
    if len(malos):
        problemas.append(f"periodo con formato distinto de AAAA-1 o AAAA-2 en las filas {_filas(malos)}")

    for col, nombre in (("fecha_inicio", "FECHA INICIO"), ("fecha_fin", "FECHA FINALIZACION")):
        malas = []
        for fila, valor in zip(df["fila_excel"], df[col]):
            try:
                carga.fecha(valor)
            except Exception:
                malas.append(fila)
        if malas:
            problemas.append(f"{nombre} no es una fecha válida en las filas {_filas(malas)}")

    obligatorias = carga.CATEGORICAS + ["periodo", "nombres_apellidos", "documento"]
    vacias = df[obligatorias].apply(lambda s: s.map(carga.espacios)).isna().any(axis=1)
    if vacias.any():
        problemas.append(f"faltan datos obligatorios en las filas {_filas(df.loc[vacias, 'fila_excel'])}")

    if problemas:
        raise ExcelInvalido(f"'{ruta.name}' tiene errores: " + "; ".join(problemas) + ".")
    return len(df)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Uso: python validar_excel.py "ruta del Excel.xlsx"')
    try:
        print(f"Excel válido: {validar(sys.argv[1])} registros.")
    except (ExcelInvalido, FileNotFoundError) as e:
        sys.exit(f"[EXCEL RECHAZADO] {e}")
