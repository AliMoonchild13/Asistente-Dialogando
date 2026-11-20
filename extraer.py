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
from collections import Counter

import pymupdf

MARGEN_SUP = 112      # encabezado institucional (pt)
MARGEN_INF = 692      # pie de página (dirección, NIT, SGC)
LARGO_VALOR = 60      # una celda con más caracteres se considera respuesta, no etiqueta

INICIO_FIRMAS = re.compile(r"(?:^|\s)(Firma:|Nombres y apellidos de quien presenta)", re.I)
SECCION = re.compile(r"^\s*\d+\s*\.\s*-")
CORREO = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
TELEFONO = re.compile(r"(?<![\d/])\+?\d[\d ()-]{6,}\d(?![\d/])")   # no toma fechas como 2023/12/15

PROGRAMAS = {
    "EMBAJADOR": "Estudiante Embajador",   # "DOCENTE" se revisa primero
    "CASA": "Casa Cundinamarca",
    "VOLUNTARIADO": "Voluntariado Internacional",
}


def limpiar(texto):
    texto = CORREO.sub("[correo]", texto)
    texto = TELEFONO.sub(lambda m: m.group() if sum(c.isdigit() for c in m.group()) < 7
                         or re.fullmatch(r"\d{4}-\d\d-\d\d", m.group()) else "[teléfono]", texto)
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


def _separador(pagina, cuerpo):
    """Busca la línea vertical que separa, en las tablas del formato, la columna de etiquetas
    de la columna de respuestas. Devuelve su posición x y los tramos (y0, y1) donde aparece."""
    ancho, segmentos = pagina.rect.width, []
    for dibujo in pagina.get_drawings():
        for item in dibujo["items"]:
            if item[0] == "l":
                x0, x1, y0, y1 = item[1].x, item[2].x, min(item[1].y, item[2].y), max(item[1].y, item[2].y)
            elif item[0] == "re":
                x0, x1, y0, y1 = item[1].x0, item[1].x1, item[1].y0, item[1].y1
            else:
                continue
            if abs(x1 - x0) < 2 and y1 - y0 > 5 and 0.42 * ancho <= x0 <= 0.53 * ancho \
                    and y1 > cuerpo.y0 and y0 < cuerpo.y1:
                segmentos.append((round(x0), max(y0, cuerpo.y0), min(y1, cuerpo.y1)))
    if not segmentos:
        return None, []
    x = Counter(s[0] for s in segmentos).most_common(1)[0][0]
    return x, sorted((y0, y1) for sx, y0, y1 in segmentos if abs(sx - x) <= 2)


def _unir(palabras):
    return " ".join(p[4] for p in sorted(palabras, key=lambda p: (round(p[1]), p[0])))


def _elementos_pagina(pagina):
    """Devuelve el texto del cuerpo de la página en orden de lectura:
    lista de (y, tipo, texto) con tipo 'L' (etiqueta), 'V' (respuesta) o 'S' (sección)."""
    cuerpo = pymupdf.Rect(0, MARGEN_SUP, pagina.rect.width, MARGEN_INF)
    elementos = []
    x_sep, tramos = _separador(pagina, cuerpo)
    en_tramo = lambda y: next((t for t in tramos if t[0] <= y < t[1]), None)

    # Zona de tabla: en cada tramo, lo de la izquierda es etiqueta y lo de la derecha respuesta.
    # Se arma palabra por palabra para conservar los espacios.
    por_tramo = {}
    for p in pagina.get_text("words", clip=cuerpo):
        tramo = en_tramo((p[1] + p[3]) / 2)
        if tramo:
            por_tramo.setdefault(tramo, []).append(p)
    for (y0, _), palabras in por_tramo.items():
        for tipo, lado, dy in (("L", [p for p in palabras if p[0] < x_sep], 0),
                               ("V", [p for p in palabras if p[0] >= x_sep], 0.1)):
            t = limpiar(_unir(lado))
            if t and t.upper() != "X":
                elementos.append((y0 + dy, tipo, t))

    # Texto fuera de las tablas (títulos de sección, "Observaciones", párrafos de ancho completo)
    for bloque in pagina.get_text("dict", clip=cuerpo)["blocks"]:
        lineas = [l for l in bloque.get("lines", []) if not en_tramo((l["bbox"][1] + l["bbox"][3]) / 2)]
        if not lineas:
            continue
        y0 = lineas[0]["bbox"][1]
        t = limpiar("\n".join("".join(s["text"] for s in l["spans"]) for l in lineas))
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

    # Cortar en la zona de firmas (aunque esté pegada al final de otro texto)
    for i, (tipo, t) in enumerate(elementos):
        m = INICIO_FIRMAS.search(t)
        if m:
            antes = t[:m.start()].strip()
            elementos = elementos[:i] + ([(tipo, antes)] if antes else [])
            break

    campos, etiquetas = [], []
    datos_generales = []          # secciones 1 a 3: casillas y datos cortos
    # En el formato MDMr015 las respuestas narrativas empiezan en "Impacto Social"
    inicio_narrativa = next((i for i, (_, t) in enumerate(elementos) if "impacto social" in t.lower()), None)
    if inicio_narrativa is not None:
        tipo, t = elementos[inicio_narrativa]
        pos = t.lower().find("impacto social")
        datos_generales = [x for _, x in elementos[:inicio_narrativa]] + ([t[:pos]] if pos else [])
        elementos = [(tipo, t[pos:])] + elementos[inicio_narrativa + 1:]
    for tipo, t in elementos:
        if inicio_narrativa is None and not campos and (tipo != "V" or len(t) < 150):
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
            if inicio is None and campos:                # continuación del mismo campo
                campos[-1]["texto"] += " " + t
                campos[-1]["campo"] += " " + " ".join(etiquetas)   # cola de la etiqueta (línea siguiente)
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
        "Universidad": r"desarrolla la actividad: \| ([^|]+)",
        "País": r"País (?:[xX] )?([^|]+?) Denominación",
        "Fecha de realización": r"Fecha de realización del evento \| ([^|]+)",
        "Campo de aprendizaje": r"Núcleo temático: \| ([^|]+)",
        "Duración": r"\| (\d+ \w+) No\. Total, de hora",
        "Total de participantes": r"Total, de participantes: (\d+)",
    }
    resumen = []
    for nombre, patron in patrones.items():
        m = re.search(patron, previo)
        if m:
            resumen.append(f"{nombre}: {re.sub(r'^[xX] ', '', m.group(1).strip())}")
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
        espacio = texto.find(" ", inicio, fin)          # que el trozo no empiece a mitad de palabra
        if espacio != -1:
            inicio = espacio + 1
    return trozos


if __name__ == "__main__":
    import sys
    print(datos_del_nombre(sys.argv[1].split("/")[-1]))
    for c in extraer_campos(sys.argv[1]):
        print(f"\n### {c['campo']}  ({len(c['texto'])} caracteres)\n{c['texto'][:300]}")
