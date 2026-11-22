#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Consolida los 11 CSV de PRENSA INSTITUCIONAL en un único CSV + XLSX.

Salida:
    data/prensa_institucional_consolidado.csv
    data/prensa_institucional_consolidado.xlsx  (hoja 'notas' + hoja 'resumen')

- Respeta el orden de columnas canónico del proyecto.
- UTF-8, coma como separador, campos con comas/saltos entrecomillados.
- Ordena por fecha descendente (timeline unificado).
- Para el XLSX sanea caracteres ilegales y recorta celdas al límite de Excel (32.767).
"""
import csv
import re
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

COLUMNAS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

FUENTES = [
    "interior", "minrel", "mop", "mma", "minagri", "mineduc",
    "midesof", "energia", "subtel", "dpp", "consejo_transparencia",
]

OUT_CSV = DATA / "prensa_institucional_consolidado.csv"
OUT_XLSX = DATA / "prensa_institucional_consolidado.xlsx"

EXCEL_MAX = 32767

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def leer_csv(nombre: str) -> list[dict]:
    ruta = DATA / f"{nombre}.csv"
    if not ruta.exists():
        print(f"  ! falta {ruta.name}, se omite")
        return []
    filas = []
    with open(ruta, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Normalizar al orden canónico (ignora columnas extra, rellena faltantes)
            filas.append({c: (row.get(c) or "").strip() for c in COLUMNAS})
    print(f"  {nombre:22} {len(filas):>7} filas")
    return filas


def sanear_celda(valor: str) -> str:
    """Quita caracteres de control ilegales para XLSX y recorta al máximo de Excel."""
    if valor is None:
        return ""
    v = ILLEGAL_CHARACTERS_RE.sub("", valor)
    if len(v) > EXCEL_MAX:
        v = v[:EXCEL_MAX - 20] + " …[texto recortado]"
    return v


def main():
    print("== Consolidando prensa institucional ==")
    todas: list[dict] = []
    for nombre in FUENTES:
        todas.extend(leer_csv(nombre))

    # Ordenar por fecha desc (ISO ordena bien; vacías/inválidas al final)
    def clave(r):
        f = r["fecha"]
        return (0, f) if re.match(r"\d{4}-\d{2}-\d{2}$", f) else (1, "")
    todas.sort(key=clave, reverse=True)

    print(f"\nTotal consolidado: {len(todas)} filas")

    # ---- CSV ----
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(todas)
    print(f"CSV  -> {OUT_CSV}")

    # ---- XLSX ----
    wb = Workbook()
    ws = wb.active
    ws.title = "notas"

    ws.append(COLUMNAS)
    recortadas = 0
    for r in todas:
        fila = []
        for c in COLUMNAS:
            val = sanear_celda(r[c])
            if c == "contenido" and r[c] and len(r[c]) > EXCEL_MAX:
                recortadas += 1
            fila.append(val)
        ws.append(fila)

    # Estilo cabecera + freeze + anchos
    bold = Font(bold=True)
    for col_idx, _ in enumerate(COLUMNAS, 1):
        ws.cell(row=1, column=col_idx).font = bold
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNAS))}1"
    anchos = {"fecha": 12, "institucion": 34, "titulo": 55, "resumen": 55, "url": 45, "contenido": 70}
    for col_idx, c in enumerate(COLUMNAS, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = anchos.get(c, 20)

    # ---- Hoja resumen ----
    ws2 = wb.create_sheet("resumen")
    ws2.append(["institucion", "n_notas", "fecha_min", "fecha_max"])
    for c in ws2[1]:
        c.font = bold
    # Agrupar por institucion
    resumen = {}
    for r in todas:
        inst = r["institucion"] or "(sin institución)"
        d = resumen.setdefault(inst, {"n": 0, "fmin": "9999", "fmax": "0000"})
        d["n"] += 1
        f = r["fecha"]
        if re.match(r"\d{4}-\d{2}-\d{2}$", f):
            d["fmin"] = min(d["fmin"], f)
            d["fmax"] = max(d["fmax"], f)
    for inst in sorted(resumen, key=lambda k: -resumen[k]["n"]):
        d = resumen[inst]
        ws2.append([inst, d["n"],
                    d["fmin"] if d["fmin"] != "9999" else "",
                    d["fmax"] if d["fmax"] != "0000" else ""])
    ws2.append([])
    ws2.append(["TOTAL", len(todas), "", ""])
    ws2[ws2.max_row][0].font = bold
    ws2[ws2.max_row][1].font = bold
    for col_idx, w in enumerate([34, 10, 12, 12], 1):
        ws2.column_dimensions[get_column_letter(col_idx)].width = w

    wb.save(OUT_XLSX)
    print(f"XLSX -> {OUT_XLSX}  ({recortadas} contenidos recortados al límite de Excel)")
    print("\nListo.")


if __name__ == "__main__":
    main()
