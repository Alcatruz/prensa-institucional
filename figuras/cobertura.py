#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cobertura del corpus: notas por año y organismo (mapa de calor).

Una fila por organismo, agrupadas como en scrapers/fuentes.py, y una columna por
año. El color indica cuántas notas hay ese año (escala azul por tramos); una celda
vacía significa que no hay notas. Las líneas verticales marcan los cambios de
gobierno (el mando cambia el 11 de marzo, así que la línea es aproximada).

Salida:
    figuras/cobertura.png

Uso:
    python figuras/cobertura.py
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scrapers"))
from fuentes import FILA_AGREGADA, FUENTES, GRUPOS  # noqa: E402

DATA = ROOT / "data"
SALIDA = Path(__file__).resolve().parent / "cobertura.png"

ANIOS = list(range(2006, 2027))
GOBIERNOS = [(2006, 2009, "Bachelet I"), (2010, 2013, "Piñera I"), (2014, 2017, "Bachelet II"),
             (2018, 2021, "Piñera II"), (2022, 2025, "Boric"), (2026, 2026, "Kast")]

# Tramos de notas por año -> escala secuencial azul (claro = pocas, oscuro = muchas)
TRAMOS = [(1, 9, "#cde2fb"), (10, 24, "#9ec5f4"), (25, 49, "#6da7ec"), (50, 99, "#3987e5"),
          (100, 199, "#256abf"), (200, 399, "#184f95"), (400, None, "#0d366b")]

SUPERFICIE = "#fcfcfb"
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
TINTA_MUTED = "#898781"
LINEA = "#c3c2b7"


def color(n: int) -> str | None:
    for lo, hi, c in TRAMOS:
        if n >= lo and (hi is None or n <= hi):
            return c
    return None


def miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def conteos() -> list[dict]:
    filas = []
    for archivo, etiqueta, grupo in FUENTES:
        ruta = DATA / f"{archivo}.csv"
        if not ruta.exists() or ruta.stat().st_size == 0:
            continue
        try:
            f = pd.read_csv(ruta, usecols=["fecha"], dtype=str, keep_default_na=False)["fecha"]
        except (pd.errors.EmptyDataError, ValueError):
            continue
        anios = pd.to_numeric(f.str[:4], errors="coerce").dropna().astype(int)
        c = anios[anios.isin(ANIOS)].value_counts()
        filas.append({"etiqueta": etiqueta, "grupo": grupo, "total": len(f), "sitios": 1,
                      "anual": {a: int(c.get(a, 0)) for a in ANIOS}, "fila": FILA_AGREGADA.get(archivo, "")})
    return agregar(filas)


def agregar(filas: list[dict]) -> list[dict]:
    """Suma en una fila los sitios del mismo tipo (municipios de una región, hospitales...)."""
    salida, juntas = [], {}
    for f in filas:
        if not f["fila"]:
            salida.append(f)
            continue
        k = (f["grupo"], f["fila"])
        if k not in juntas:
            juntas[k] = {"etiqueta": f["fila"], "grupo": f["grupo"], "total": 0, "sitios": 0,
                         "anual": {a: 0 for a in ANIOS}, "fila": f["fila"]}
            salida.append(juntas[k])
        j = juntas[k]
        j["total"] += f["total"]
        j["sitios"] += 1
        for a in ANIOS:
            j["anual"][a] += f["anual"][a]
    for j in juntas.values():
        j["etiqueta"] = f"{j['etiqueta']} ({j['sitios']} {'sitio' if j['sitios'] == 1 else 'sitios'})"
    return salida


def main():
    for fam in ("Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"):
        if any(fam == f.name for f in matplotlib.font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = fam
            break

    filas = conteos()
    # filas de dibujo: encabezado de grupo + organismos
    items = []
    for g in GRUPOS:
        del_grupo = [f for f in filas if f["grupo"] == g]
        if del_grupo:
            items.append(("grupo", g))
            items += [("fila", f) for f in del_grupo]

    alto_fila = 0.17
    alto = 1.55 + alto_fila * len(items) + 0.75
    ancho = 11.5
    fig = plt.figure(figsize=(ancho, alto), dpi=110, facecolor=SUPERFICIE)
    # área de celdas en coordenadas de figura (pulgadas -> fracción)
    x0, x1 = 3.45 / ancho, 1 - 0.95 / ancho
    y_top = 1 - 1.55 / alto
    ax = fig.add_axes([x0, 0.75 / alto, x1 - x0, y_top - 0.75 / alto])
    ax.set_xlim(0, len(ANIOS))
    ax.set_ylim(len(items), 0)
    ax.axis("off")
    ax.set_facecolor(SUPERFICIE)

    total_notas = 0
    for i, (tipo, v) in enumerate(items):
        if tipo == "grupo":
            ax.text(-0.15, i + 0.62, v, ha="right", va="center", fontsize=8.6,
                    fontweight="semibold", color=TINTA, clip_on=False)
            continue
        ax.text(-0.15, i + 0.55, v["etiqueta"], ha="right", va="center", fontsize=7.4,
                color=TINTA_2, clip_on=False)
        ax.text(len(ANIOS) + 0.2, i + 0.55, miles(v["total"]), ha="left", va="center",
                fontsize=7.2, color=TINTA_MUTED, clip_on=False)
        total_notas += v["total"]
        for j, a in enumerate(ANIOS):
            c = color(v["anual"][a])
            if c:
                # 2 px de separación entre celdas (borde del color de la superficie)
                ax.add_patch(Rectangle((j, i + 0.08), 1, 0.84, facecolor=c, edgecolor=SUPERFICIE,
                                       linewidth=1.2))

    # cambios de gobierno
    for ini, fin, nombre in GOBIERNOS:
        xi = ANIOS.index(ini)
        if ini != ANIOS[0]:
            ax.plot([xi, xi], [-0.9, len(items)], color=LINEA, linewidth=0.8, clip_on=False, zorder=3)
        ax.text((xi + ANIOS.index(fin) + 1) / 2, -1.25, nombre, ha="center", va="bottom", fontsize=8.2,
                color=TINTA_2, clip_on=False)
    for j, a in enumerate(ANIOS):
        if a % 2 == 0:
            ax.text(j + 0.5, -0.35, str(a), ha="center", va="bottom", fontsize=7, color=TINTA_MUTED,
                    clip_on=False)
    ax.text(len(ANIOS) + 0.2, -0.35, "Total", ha="left", va="bottom", fontsize=7, color=TINTA_MUTED,
            clip_on=False)

    # título y leyenda
    fig.text(0.02, 1 - 0.38 / alto, "Prensa institucional del Estado de Chile", fontsize=16,
             fontweight="semibold", color=TINTA, ha="left", va="baseline")
    fig.text(0.02, 1 - 0.66 / alto, f"Notas publicadas por año y organismo · {miles(total_notas)} notas "
             f"de {sum(f['sitios'] for f in filas)} fuentes", fontsize=10, color=TINTA_2, ha="left", va="baseline")
    lx = 0.02
    fig.text(lx, 1 - 0.95 / alto, "Notas por año:", fontsize=8.2, color=TINTA_2, ha="left", va="center")
    lx += 0.085
    for lo, hi, c in TRAMOS:
        fig.patches.append(Rectangle((lx, 1 - 1.01 / alto), 0.012, 0.11 / alto, transform=fig.transFigure,
                                     facecolor=c, edgecolor="none"))
        etiqueta = f"{lo}–{hi}" if hi else f"{lo} o más"
        fig.text(lx + 0.016, 1 - 0.95 / alto, etiqueta, fontsize=8, color=TINTA_2, ha="left", va="center")
        lx += 0.065
    fig.text(0.02, 0.28 / alto, "Celda vacía: sin notas ese año. El año 2026 llega hasta noviembre. "
             "Las líneas verticales marcan los cambios de gobierno (11 de marzo).",
             fontsize=7.6, color=TINTA_MUTED, ha="left", va="baseline")
    fig.text(0.98, 0.28 / alto, "Fuente: salas de prensa de cada organismo. Elaboración propia.",
             fontsize=7.6, color=TINTA_MUTED, ha="right", va="baseline")

    fig.savefig(SALIDA, facecolor=SUPERFICIE, dpi=110)
    print(f"{sum(f['sitios'] for f in filas)} fuentes, {miles(total_notas)} notas -> {SALIDA}")


if __name__ == "__main__":
    main()
