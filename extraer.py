"""
Extrae el texto de los informes (formato MDMr015 "Informe de actividades académicas conjuntas")
y lo devuelve separado por campo: "Impacto Social", "Fortalezas", "Observaciones", etc.

- Quita encabezado y pie de página repetidos.
- Lee las tablas con PyMuPDF para no mezclar la etiqueta con la respuesta.
- Descarta la zona de firmas (nombres, correo, coordinador) y borra correos/teléfonos.
- Si el PDF no sigue el formato, devuelve el texto completo en un solo campo.
"""
import re
import unicodedata

import pymupdf

MARGEN_SUP = 112      # encabezado institucional (pt)
MARGEN_INF = 692      # pie de página (dirección, NIT, SGC)
LARGO_VALOR = 60      # una celda con más caracteres se considera respuesta, no etiqueta

INICIO_FIRMAS = re.compile(r"^(Firma:|Nombres y apellidos de quien presenta)", re.I)
SECCION = re.compile(r"^\s*\d+\s*\.\s*-")
CORREO = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
TELEFONO = re.compile(r"\+?\d[\d\s()-]{7,}\d")

PROGRAMAS = {
    "EMBAJADOR": "Estudiante Embajador",   # "DOCENTE" se revisa primero
    "CASA": "Casa Cundinamarca",
    "VOLUNTARIADO": "Voluntariado Internacional",
}


def limpiar(texto):
    texto = CORREO.sub("[correo]", texto)
    texto = TELEFONO.sub("[teléfono]", texto)
    return re.sub(r"\s+", " ", texto).strip()


def datos_del_nombre(nombre_archivo):
    """EMBAJADOR_2023-2_SALIENTE_YINETH_RUBIO.pdf -> programa, periodo, sentido, persona."""
    base = re.sub(r"\.pdf$", "", nombre_archivo, flags=re.I)
    base_up = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode().upper()
    datos = {"programa_movilidad": None, "periodo": None, "sentido": None, "persona": None}

    if "DOCENTE" in base_up:
        datos["programa_movilidad"] = "Docente Embajador"
    else:
        for clave, nombre in PROGRAMAS.items():
            if clave in base_up:
                datos["programa_movilidad"] = nombre
                break

    m = re.search(r"(20\d\d)\s*[-_ ]\s*([12])(?!\d)", base_up)
    if m:
        datos["periodo"] = f"{m.group(1)}-{m.group(2)}"
    if "SALIENTE" in base_up:
        datos["sentido"] = "Saliente"
    elif "ENTRANTE" in base_up:
        datos["sentido"] = "Entrante"

    # La persona es lo que queda después del sentido (o del periodo)
    resto = re.split(r"SALIENTE|ENTRANTE|20\d\d\s*[-_ ]\s*[12]", base_up)[-1]
    persona = " ".join(p for p in re.split(r"[_\s-]+", resto) if p.isalpha())
    datos["persona"] = persona.title() or None
    return datos


def _elementos_pagina(pagina):
    """Devuelve el texto del cuerpo de la página en orden de lectura:
    lista de (y, tipo, texto) con tipo 'L' (etiqueta) o 'V' (respuesta)."""
    cuerpo = pymupdf.Rect(0, MARGEN_SUP, pagina.rect.width, MARGEN_INF)
    elementos, zonas_tabla = [], []

    for tabla in pagina.find_tables(clip=cuerpo).tables:
        zonas_tabla.append(pymupdf.Rect(tabla.bbox))
        for fila, celdas in zip(tabla.rows, tabla.extract()):
            y = fila.bbox[1]
            for celda in celdas:
                t = limpiar(celda or "")
                if not t or t == "X":
                    continue
                elementos.append((y, "V" if len(t) > LARGO_VALOR else "L", t))

    # Texto fuera de tablas (títulos de sección, "Observaciones", etc.)
    for x0, y0, x1, y1, t, *_ in pagina.get_text("blocks", clip=cuerpo):
        caja = pymupdf.Rect(x0, y0, x1, y1)
        if any(z.intersects(caja) and (z & caja).get_area() > 0.5 * caja.get_area() for z in zonas_tabla):
            continue
        t = limpiar(t)
        if not t:
            continue
        if SECCION.match(t):                         # "5.- Anexos: ..." inicia un campo nuevo
            elementos.append((y0, "S", t))
            continue
        etiqueta, dos_puntos, valor = t.partition(":")
        if dos_puntos and len(etiqueta) < LARGO_VALOR and len(valor) > LARGO_VALOR:
            elementos.append((y0, "L", etiqueta + ":"))   # "Observaciones: texto largo..."
            elementos.append((y0 + 0.1, "V", valor.strip()))
        else:
            elementos.append((y0, "V" if len(t) > LARGO_VALOR else "L", t))

    return sorted(elementos, key=lambda e: e[0])


def extraer_campos(ruta_pdf):
    """Devuelve una lista de dicts {"campo": ..., "texto": ...}."""
    doc = pymupdf.open(ruta_pdf)
    elementos = []
    for pagina in doc:
        elementos.extend((tipo, t) for _, tipo, t in _elementos_pagina(pagina))

    # Cortar en la zona de firmas
    for i, (_, t) in enumerate(elementos):
        if INICIO_FIRMAS.match(t):
            elementos = elementos[:i]
            break

    campos, etiquetas = [], []
    datos_generales = []          # todo lo que va antes de la primera respuesta larga
    for tipo, t in elementos:
        if not campos and (tipo != "V" or len(t) < 150):   # secciones 1 a 3 (casillas y datos cortos)
            etiquetas.append(t)
            continue
        if tipo == "S":
            titulo, _, cuerpo = t.partition(":")
            campos.append({"campo": titulo.strip(), "texto": cuerpo.strip()})
            etiquetas = []
        elif tipo == "L":
            etiquetas.append(t)
        else:  # respuesta
            # La etiqueta del campo nuevo empieza en el último fragmento con mayúscula inicial;
            # lo anterior es la cola de la etiqueta del campo previo (celdas partidas en filas).
            inicio = max((i for i, e in enumerate(etiquetas) if e[:1].isupper() and not SECCION.match(e)),
                         default=None)
            if inicio is None and campos:                # continuación en la página siguiente
                campos[-1]["texto"] += " " + t
            else:
                previas = etiquetas[:inicio] if inicio is not None else []
                if campos:
                    campos[-1]["campo"] += " " + " ".join(previas)
                else:
                    datos_generales.extend(previas)
                campos.append({"campo": " ".join(etiquetas[inicio or 0:]), "texto": t})
            etiquetas = []
    if campos and etiquetas:
        campos[-1]["campo"] += " " + " ".join(etiquetas)

    for c in campos:
        c["campo"] = re.sub(r"\s+", " ", c["campo"]).strip(" :") or "Texto"
        c["texto"] = c["texto"].strip()
    campos = [c for c in campos if len(c["texto"]) > 20]

    # De las secciones 1 a 3 solo se guardan unos datos clave (el resto son casillas y nombres)
    previo = " | ".join(datos_generales)
    patrones = {
        "Universidad": r"desarrolla la actividad: \|(?: Universidad con la que se desarrolla la actividad: \|)? ([^|]+)",
        "País": r"\bPaís \| ([^|]+)",
        "Fecha de realización": r"Fecha de realización del evento \| ([^|]+)",
        "Campo de aprendizaje": r"Campo de \| ([^|]+)",
        "Total de participantes": r"Total, de participantes: \| ([^|]+)",
        "Duración": r"trabajo \| ([^|]+)",
    }
    resumen = []
    for nombre, patron in patrones.items():
        m = re.search(patron, previo)
        if m:
            resumen.append(f"{nombre}: {m.group(1).strip()}")
    if resumen:
        campos.insert(0, {"campo": "Datos generales", "texto": ". ".join(resumen) + "."})

    if len(campos) <= 1:          # formato distinto: se guarda el texto completo
        texto = " ".join(limpiar(p.get_text(clip=pymupdf.Rect(0, MARGEN_SUP, p.rect.width, MARGEN_INF)))
                         for p in doc)
        campos = [{"campo": "Texto completo", "texto": texto}] if texto else []
    return campos


def dividir(texto, max_chars=1500, solape=200):
    """Parte un texto largo en trozos, cortando en final de oración cuando se puede."""
    if len(texto) <= max_chars:
        return [texto]
    trozos, inicio = [], 0
    while inicio < len(texto):
        fin = min(inicio + max_chars, len(texto))
        if fin < len(texto):
            punto = texto.rfind(". ", inicio + max_chars // 2, fin)
            fin = punto + 1 if punto != -1 else fin
        trozos.append(texto[inicio:fin].strip())
        if fin >= len(texto):
            break
        inicio = max(fin - solape, inicio + 1)
    return trozos


if __name__ == "__main__":
    import sys
    print(datos_del_nombre(sys.argv[1].split("/")[-1]))
    for c in extraer_campos(sys.argv[1]):
        print(f"\n### {c['campo']}  ({len(c['texto'])} caracteres)\n{c['texto'][:300]}")
