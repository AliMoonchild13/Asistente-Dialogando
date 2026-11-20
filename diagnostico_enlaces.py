"""
Diagnóstico de los informes que NO quedaron enlazados con su registro de movilidad.

Para cada informe sin enlazar indica el motivo y sugiere los registros del Excel que más se parecen
(por el nombre de la persona), para saber qué corregir: el nombre del PDF o el Excel.

Uso (dentro de Docker):
    docker compose exec app python diagnostico_enlaces.py
Resultado: datos/salida/diagnostico_informes_sin_enlazar.xlsx   (contiene nombres: no compartir)

Después de corregir el Excel o renombrar los PDF, el enlace se rehace solo en la siguiente revisión.
"""
from pathlib import Path

import pandas as pd
import psycopg2

import config
from extraer import datos_del_nombre
from ingesta import buscar_registro, sin_tildes

SALIDA = Path(config.CARPETA_SALIDA) / "diagnostico_informes_sin_enlazar.xlsx"


def _palabras(texto):
    return set(sin_tildes(texto).split())


def diagnosticar():
    with psycopg2.connect(**config.PG) as con, con.cursor() as cur:
        cur.execute("SELECT archivo FROM movilidad.informe WHERE id_registro_movilidad IS NULL ORDER BY archivo")
        sin_enlace = [a for (a,) in cur.fetchall()]
        cur.execute("""
            SELECT p.nombres_apellidos, r.periodo, pm.nombre, tm.sentido
            FROM movilidad.registro_movilidad r
            JOIN movilidad.persona p             ON p.id  = r.id_persona
            JOIN movilidad.programa_movilidad pm ON pm.id = r.id_programa_movilidad
            JOIN movilidad.tipo_movilidad tm     ON tm.id = r.id_tipo_movilidad""")
        registros = cur.fetchall()

        filas = []
        for archivo in sin_enlace:
            d = datos_del_nombre(archivo)
            persona = d["persona"] or ""
            buscadas = _palabras(persona)

            # Registros cuyo nombre se parece (sin importar periodo ni programa)
            parecidos = []
            for nombre, periodo, programa, sentido in registros:
                if buscadas:
                    coincide = len(buscadas & _palabras(nombre)) / len(buscadas)
                    if coincide >= 0.5:
                        parecidos.append((coincide, nombre, periodo, programa, sentido))
            parecidos.sort(key=lambda x: (-x[0], x[2] != d["periodo"]))

            if not d["periodo"]:
                motivo = "El nombre del archivo no tiene el periodo (ej. 2024-1)"
            elif not persona:
                motivo = "El nombre del archivo no tiene el nombre de la persona"
            elif not d["programa_movilidad"] or not d["sentido"]:
                motivo = "Al nombre del archivo le falta el programa o el sentido (SALIENTE/ENTRANTE)"
            elif not parecidos:
                motivo = "No hay en el Excel ninguna persona con ese nombre"
            else:
                exactos = [p for p in parecidos if p[0] == 1]
                mismo = [p for p in exactos if (p[2], p[3], p[4]) ==
                         (d["periodo"], d["programa_movilidad"], d["sentido"])]
                if len(mismo) > 1:
                    motivo = "Hay varias personas que coinciden (nombre incompleto en el archivo)"
                elif exactos and not mismo:
                    motivo = "La persona está en el Excel, pero con otro periodo, programa o sentido"
                else:
                    motivo = "El nombre está escrito distinto en el archivo y en el Excel"

            sugerencias = [f"{n} | {pe} | {pr} | {se}" for _, n, pe, pr, se in parecidos[:3]]
            # Qué dato no coincide con el registro más parecido (para decidir qué corregir)
            diferencias = ""
            if parecidos:
                _, _, pe, pr, se = parecidos[0]
                diferencias = ", ".join(f"{campo}: archivo {a} / Excel {e}" for campo, a, e in (
                    ("periodo", d["periodo"], pe), ("programa", d["programa_movilidad"], pr),
                    ("sentido", d["sentido"], se)) if a != e)
            filas.append({
                "archivo": archivo,
                "persona (archivo)": persona,
                "programa (archivo)": d["programa_movilidad"],
                "periodo (archivo)": d["periodo"],
                "sentido (archivo)": d["sentido"],
                "motivo": motivo,
                "qué no coincide (con la sugerencia 1)": diferencias,
                "sugerencia 1 (nombre | periodo | programa | sentido)": sugerencias[0] if sugerencias else "",
                "sugerencia 2": sugerencias[1] if len(sugerencias) > 1 else "",
                "sugerencia 3": sugerencias[2] if len(sugerencias) > 2 else "",
            })
    return pd.DataFrame(filas)


if __name__ == "__main__":
    df = diagnosticar()
    if df.empty:
        print("Todos los informes están enlazados.")
    else:
        SALIDA.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(SALIDA, index=False, sheet_name="sin_enlazar")
        print(f"{len(df)} informes sin enlazar. Resumen por motivo:\n")
        print(df["motivo"].value_counts().to_string())
        campos = df["qué no coincide (con la sugerencia 1)"].str.findall(r"(periodo|programa|sentido):").explode()
        if campos.notna().any():
            print("\nDatos que no coinciden (con el registro más parecido):")
            print(campos.value_counts().to_string())
        print(f"\nDetalle con sugerencias: {SALIDA}")
