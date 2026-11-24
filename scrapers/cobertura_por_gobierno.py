#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tabla de cobertura del corpus: notas por organismo y gobierno.

Cuenta las notas de cada CSV de data/ según el gobierno en que se publicaron
(cambios de mando el 11 de marzo) e informa fechas extremas y el porcentaje de
notas con cuerpo de al menos 40 palabras.

Salida:
    data/cobertura_por_gobierno.csv
    tabla en Markdown por consola con --markdown (para el README)

Uso:
    python scrapers/cobertura_por_gobierno.py
    python scrapers/cobertura_por_gobierno.py --markdown
"""
import argparse
import csv
import sys
from pathlib import Path

import pandas as pd

from fuentes import FUENTES

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SALIDA = DATA / "cobertura_por_gobierno.csv"

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

CAMBIOS_DE_MANDO = [
    ("2006-03-11", "Bachelet I"), ("2010-03-11", "Piñera I"), ("2014-03-11", "Bachelet II"),
    ("2018-03-11", "Piñera II"), ("2022-03-11", "Boric"), ("2026-03-11", "Kast"),
]
GOBIERNOS = ["Lagos"] + [g for _, g in CAMBIOS_DE_MANDO]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def gobierno(fecha: str):
    if len(fecha) != 10 or fecha < "2000":
        return None
    g = "Lagos"
    for corte, nombre in CAMBIOS_DE_MANDO:
        if fecha >= corte:
            g = nombre
    return g


def cobertura() -> pd.DataFrame:
    filas = []
    for archivo, etiqueta, grupo in FUENTES:
        ruta = DATA / f"{archivo}.csv"
        if not ruta.exists():
            print(f"  ! falta {ruta.name}, se omite")
            continue
        try:
            d = pd.read_csv(ruta, dtype=str, keep_default_na=False, usecols=["fecha", "contenido"])
        except (pd.errors.EmptyDataError, ValueError):
            print(f"  ! {ruta.name} vacío o en escritura, se omite")
            continue
        if d.empty:
            continue
        validas = d.fecha[d.fecha.str.match(r"^20\d{2}-\d{2}-\d{2}$")]
        por_gob = d.fecha.map(gobierno).value_counts()
        filas.append({
            "organismo": etiqueta, "grupo": grupo, "archivo": f"{archivo}.csv",
            **{g: int(por_gob.get(g, 0)) for g in GOBIERNOS},
            "sin_fecha": int(len(d) - por_gob.sum()),
            "total": len(d), "desde": validas.min(), "hasta": validas.max(),
            "pct_cuerpo_40": round(100 * (d.contenido.str.split().str.len() >= 40).mean(), 1),
        })
    return pd.DataFrame(filas)


def mes_anio(fecha: str) -> str:
    return f"{MESES[int(fecha[5:7]) - 1]} {fecha[:4]}" if fecha else ""


def markdown(t: pd.DataFrame) -> str:
    gobs = [g for g in GOBIERNOS if t[g].sum() > 0]

    def num(n):
        return f"{n:,}".replace(",", ".") if n else "–"

    lineas = ["| Organismo | " + " | ".join(gobs) + " | Total | Desde | Hasta |",
              "|---|" + "---:|" * (len(gobs) + 1) + "---|---|"]
    for grupo in t.grupo.unique():
        lineas.append(f"| **{grupo}** |" + " |" * (len(gobs) + 3))
        for _, r in t[t.grupo == grupo].iterrows():
            celdas = [num(r[g]) for g in gobs] + [num(r["total"]), mes_anio(r["desde"]), mes_anio(r["hasta"])]
            lineas.append(f"| {r['organismo']} | " + " | ".join(celdas) + " |")
    return "\n".join(lineas)


def main():
    ap = argparse.ArgumentParser(description="Notas por organismo y gobierno")
    ap.add_argument("--markdown", action="store_true", help="imprime la tabla en Markdown")
    args = ap.parse_args()

    t = cobertura()
    t.to_csv(SALIDA, index=False, encoding="utf-8")
    if args.markdown:
        print(markdown(t))
    else:
        pd.set_option("display.width", 250)
        print(t.drop(columns=["grupo", "archivo"]).to_string(index=False))
    print(f"\nCSV -> {SALIDA}")


if __name__ == "__main__":
    main()
