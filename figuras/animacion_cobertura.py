#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Animación de la cobertura temporal del corpus de prensa institucional.

Lee la columna `fecha` de cada CSV en data/, cuenta notas por mes y dibuja un
panel por organismo (misma escala en todos) con la serie avanzando en el tiempo.

Salida:
    figuras/cobertura.gif
    figuras/cobertura.png   (último cuadro, estático)

Uso:
    python figuras/animacion_cobertura.py
"""
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT_GIF = Path(__file__).resolve().parent / "cobertura.gif"
OUT_PNG = Path(__file__).resolve().parent / "cobertura.png"

# (archivo, etiqueta, grupo)
FUENTES = [
    ("dpp", "Defensoría Penal Pública", "otros"),
    ("subtel", "SUBTEL", "ejecutivo"),
    ("consejo_transparencia", "Consejo para la Transparencia", "otros"),
    ("senado", "Senado", "congreso"),
    ("mma", "Medio Ambiente", "ejecutivo"),
    ("energia", "Energía", "ejecutivo"),
    ("mineduc", "Educación", "ejecutivo"),
    ("mop", "Obras Públicas", "ejecutivo"),
    ("camara_diputados", "Cámara de Diputadas y Diputados", "congreso"),
    ("minrel", "Relaciones Exteriores", "ejecutivo"),
    ("interior", "Interior", "ejecutivo"),
    ("minagri", "Agricultura", "ejecutivo"),
    ("midesof", "Desarrollo Social y Familia", "ejecutivo"),
    ("seguridad", "Seguridad Pública", "ejecutivo"),
    ("presidencia", "Presidencia", "ejecutivo"),
    ("sag", "SAG", "ejecutivo"),
    ("indap", "INDAP", "ejecutivo"),
    ("sec", "SEC", "ejecutivo"),
    ("junaeb", "Junaeb", "ejecutivo"),
]

GRUPOS = {
    "ejecutivo": ("Presidencia, ministerios y servicios", "#2a78d6"),
    "otros": ("Otros organismos públicos", "#eb6834"),
    "congreso": ("Congreso Nacional", "#1baf7a"),
}

SUPERFICIE = "#fcfcfb"
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
TINTA_MUTED = "#898781"
GRILLA = "#e1e0d9"
BASE = "#c3c2b7"

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
INICIO = pd.Period("2006-01", "M")
VENTANA = 3          # media móvil (meses)
PASO = 2             # meses por cuadro
PAUSA_FINAL = 30     # cuadros congelados al final
FPS = 12

COLS, FILAS = 5, 4
ANCHO, ALTO = 12, 9.4    # pulgadas; ~2 pulgadas por fila de paneles


def arriba(pulg: float) -> float:
    """Posición vertical (fracción de la figura) a `pulg` pulgadas del borde superior."""
    return 1 - pulg / ALTO


def abajo(pulg: float) -> float:
    return pulg / ALTO


def miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def cargar_mensual(nombre: str) -> tuple[pd.Series, pd.Series]:
    """Devuelve (conteo mensual, media móvil para dibujar)."""
    fechas = pd.read_csv(DATA / f"{nombre}.csv", usecols=["fecha"], dtype=str)["fecha"].str.strip()
    fechas = fechas[fechas.str.match(r"^(19|20)\d{2}-\d{2}-\d{2}$", na=False)]
    fechas = pd.to_datetime(fechas, errors="coerce").dropna()
    meses = fechas.dt.to_period("M")
    meses = meses[meses >= INICIO]

    conteo = meses.value_counts().sort_index()
    rango = pd.period_range(conteo.index.min(), conteo.index.max(), freq="M")
    conteo = conteo.reindex(rango, fill_value=0)

    # El último mes extraído suele estar incompleto: se corta la línea ahí
    ultimo = fechas.max()
    fin_linea = conteo.index.max() if ultimo.day >= 28 else conteo.index.max() - 1
    suave = conteo.rolling(VENTANA, min_periods=1).mean()
    suave = suave[suave.index <= fin_linea]
    return conteo, suave


def main():
    for fam in ("Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"):
        if any(fam == f.name for f in matplotlib.font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = fam
            break

    series = []
    for nombre, etiqueta, grupo in FUENTES:
        conteo, suave = cargar_mensual(nombre)
        series.append(dict(etiqueta=etiqueta, grupo=grupo, conteo=conteo, suave=suave))
        print(f"  {nombre:22} {int(conteo.sum()):>7} notas  {conteo.index.min()} a {conteo.index.max()}")
    series.sort(key=lambda s: s["conteo"].index.min())

    fin = max(s["conteo"].index.max() for s in series)
    meses = pd.period_range(INICIO, fin, freq="M")
    x_de = {p: i for i, p in enumerate(meses)}
    ymax = max(s["suave"].max() for s in series)
    ytope = int(np.ceil(ymax / 50.0) * 50)

    # Arrays alineados a la línea de tiempo común
    for s in series:
        y = np.full(len(meses), np.nan)
        for p, v in s["suave"].items():
            y[x_de[p]] = v
        s["y"] = y
        c = np.zeros(len(meses))
        for p, v in s["conteo"].items():
            c[x_de[p]] = v
        s["acum"] = np.cumsum(c)

    fig = plt.figure(figsize=(ANCHO, ALTO), dpi=100, facecolor=SUPERFICIE)
    gs = fig.add_gridspec(FILAS, COLS, left=0.045, right=0.985, top=arriba(1.48), bottom=abajo(0.52),
                          hspace=0.55, wspace=0.12)

    fig.text(0.045, arriba(0.41), "Prensa institucional del Estado de Chile", fontsize=17,
             fontweight="semibold", color=TINTA, ha="left", va="baseline")
    fig.text(0.045, arriba(0.70), f"Notas publicadas por mes y organismo (media móvil de {VENTANA} meses, "
             "misma escala en todos los paneles)", fontsize=10.5, color=TINTA_2, ha="left", va="baseline")
    txt_fecha = fig.text(0.985, arriba(0.44), "", fontsize=22, fontweight="semibold", color=TINTA,
                         ha="right", va="baseline")
    txt_total = fig.text(0.985, arriba(0.70), "", fontsize=10.5, color=TINTA_2, ha="right", va="baseline")

    claves = [Line2D([0], [0], color=col, lw=2.2, solid_capstyle="round") for _, col in GRUPOS.values()]
    fig.legend(claves, [nom for nom, _ in GRUPOS.values()], loc="upper left", ncol=3,
               bbox_to_anchor=(0.04, arriba(0.81)), frameon=False, fontsize=9.5, labelcolor=TINTA_2,
               handlelength=1.6, columnspacing=1.8)

    xt = [x_de[pd.Period(f"{a}-01", "M")] for a in range(2006, 2027, 4)]
    artistas = []
    for i, s in enumerate(series):
        ax = fig.add_subplot(gs[i // COLS, i % COLS])
        ax.set_facecolor(SUPERFICIE)
        ax.set_xlim(-2, len(meses) + 1)
        ax.set_ylim(0, ytope)
        for lado in ("top", "right", "left"):
            ax.spines[lado].set_visible(False)
        ax.spines["bottom"].set_color(BASE)
        ax.spines["bottom"].set_linewidth(0.8)
        ax.grid(axis="y", color=GRILLA, linewidth=0.7)
        ax.set_axisbelow(True)
        ax.set_yticks(range(0, ytope + 1, 50))
        ax.tick_params(colors=TINTA_MUTED, labelsize=8, length=0, pad=3)
        if i % COLS != 0:
            ax.set_yticklabels([])
        ax.set_xticks(xt)
        if i + COLS < len(series):
            ax.set_xticklabels([])
        else:
            ax.set_xticklabels([str(a) for a in range(2006, 2027, 4)])

        col = GRUPOS[s["grupo"]][1]
        ax.set_title(s["etiqueta"], loc="left", fontsize=9.5, color=TINTA, pad=4)
        txt_n = ax.text(1.0, 0.95, "", transform=ax.transAxes, fontsize=8.5, color=TINTA_2,
                        ha="right", va="top")
        linea, = ax.plot([], [], color=col, lw=1.6, solid_capstyle="round", solid_joinstyle="round")
        punto, = ax.plot([], [], "o", color=col, ms=6, mec=SUPERFICIE, mew=1.5, zorder=4)
        artistas.append(dict(ax=ax, linea=linea, punto=punto, txt_n=txt_n, col=col, area=None, s=s))

    fig.text(0.045, abajo(0.09), "La cifra de cada panel es el total acumulado de notas. "
             "El último mes extraído, si está incompleto, no se dibuja.",
             fontsize=8, color=TINTA_MUTED, ha="left", va="bottom")
    fig.text(0.985, abajo(0.09), "Fuente: salas de prensa de cada organismo. Elaboración propia.",
             fontsize=8, color=TINTA_MUTED, ha="right", va="bottom")

    idx = list(range(0, len(meses), PASO))
    if idx[-1] != len(meses) - 1:
        idx.append(len(meses) - 1)
    cuadros = idx + [idx[-1]] * PAUSA_FINAL

    def dibujar(k):
        x = np.arange(k + 1)
        total = 0
        for a in artistas:
            y = a["s"]["y"][: k + 1]
            a["linea"].set_data(x, y)
            if a["area"] is not None:
                a["area"].remove()
            a["area"] = a["ax"].fill_between(x, 0, np.nan_to_num(y), where=~np.isnan(y),
                                             color=a["col"], alpha=0.10, linewidth=0)
            validos = np.where(~np.isnan(y))[0]
            if len(validos):
                j = validos[-1]
                a["punto"].set_data([j], [y[j]])
            else:
                a["punto"].set_data([], [])
            n = int(a["s"]["acum"][k])
            total += n
            a["txt_n"].set_text(miles(n) if n else "")
        p = meses[k]
        txt_fecha.set_text(f"{MESES[p.month - 1]} {p.year}")
        txt_total.set_text(f"{miles(total)} notas acumuladas")
        return []

    anim = FuncAnimation(fig, dibujar, frames=cuadros, blit=False)
    anim.save(OUT_GIF, writer=PillowWriter(fps=FPS), savefig_kwargs={"facecolor": SUPERFICIE})
    dibujar(len(meses) - 1)
    fig.savefig(OUT_PNG, facecolor=SUPERFICIE, dpi=100)
    print(f"GIF -> {OUT_GIF}")
    print(f"PNG -> {OUT_PNG}")


if __name__ == "__main__":
    main()
