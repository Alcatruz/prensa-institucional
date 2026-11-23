#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Une cada CSV recién actualizado con su respaldo, usando la URL como clave.

Algunos scrapers reescriben el CSV completo con lo que el sitio muestra hoy. Si el
sitio dejó de mostrar notas antiguas, esas notas se perderían. Este script conserva
la unión: todas las notas del respaldo más las nuevas. Cuando una URL está en ambos,
se queda con la versión actual salvo que venga sin contenido.

Uso:
    python scrapers/fusionar_respaldo.py data/_respaldo_2026-11-23
    python scrapers/fusionar_respaldo.py data/_respaldo_2026-11-23 --solo interior dpp
"""
import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COLUMNAS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def leer(ruta: Path) -> dict[str, dict]:
    filas = {}
    if not ruta.exists():
        return filas
    with open(ruta, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            url = (row.get("url") or "").strip()
            if url:
                filas[url] = {c: (row.get(c) or "").strip() for c in COLUMNAS}
    return filas


def fusionar(nombre: str, respaldo_dir: Path) -> None:
    viejo = leer(respaldo_dir / f"{nombre}.csv")
    nuevo = leer(DATA / f"{nombre}.csv")
    if not viejo:
        print(f"  {nombre:22} sin respaldo, se deja igual")
        return

    union = dict(viejo)
    for url, fila in nuevo.items():
        if url not in union or fila["contenido"]:
            union[url] = fila

    def clave(r):
        return r["fecha"] if re.match(r"\d{4}-\d{2}-\d{2}$", r["fecha"]) else ""

    filas = sorted(union.values(), key=clave, reverse=True)
    with open(DATA / f"{nombre}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(filas)

    agregadas = len(set(nuevo) - set(viejo))
    solo_respaldo = len(set(viejo) - set(nuevo))
    fechas = [clave(r) for r in filas if clave(r)]
    print(f"  {nombre:22} respaldo={len(viejo):>6}  actual={len(nuevo):>6}  "
          f"final={len(filas):>6}  nuevas={agregadas:>5}  solo_en_respaldo={solo_respaldo:>5}  "
          f"hasta={max(fechas) if fechas else '-'}")


def main():
    ap = argparse.ArgumentParser(description="Une CSV actualizados con su respaldo (por URL)")
    ap.add_argument("respaldo", help="carpeta con los CSV respaldados")
    ap.add_argument("--solo", nargs="*", help="nombres a fusionar (por defecto, todos los del respaldo)")
    args = ap.parse_args()

    respaldo_dir = Path(args.respaldo)
    if not respaldo_dir.is_absolute():
        respaldo_dir = ROOT / respaldo_dir
    nombres = args.solo or sorted(p.stem for p in respaldo_dir.glob("*.csv"))
    print(f"== Fusionando {len(nombres)} CSV con {respaldo_dir.name} ==")
    for nombre in nombres:
        fusionar(nombre, respaldo_dir)


if __name__ == "__main__":
    main()
