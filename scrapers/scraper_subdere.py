#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de la Subsecretaría de Desarrollo Regional y Administrativo (subdere.gob.cl).

Sitio Drupal 7. El sitemap solo trae páginas fijas, así que se recorre el listado
general de la sala de prensa (/sala-de-prensa?page=N, 10 notas por página, ~535
páginas). Las notas anteriores a 2014 aparecen sin fecha y con URL fuera de
/sala-de-prensa/ (formato antiguo): se omiten porque no tienen fecha de publicación.

Uso:  python scraper_subdere.py [--muestra N] [--completo] [--salida CSV]
                                [--hilos N] [--pausa S]
Escribe data/subdere.csv por defecto. Reanudable por URL; el recorrido es
incremental (se corta tras 3 páginas sin notas nuevas) salvo con --completo.
"""
import sys

import comun
import listados as L
from comun import fecha_iso, log

INSTITUCION = "Subsecretaría de Desarrollo Regional y Administrativo"
BASE = "https://www.subdere.gob.cl/"
LISTADO = BASE + "sala-de-prensa"


def parsear(soup) -> list[dict]:
    out = []
    for fila in soup.select(".view-content .views-row"):
        a = fila.select_one("h2 a[href]")
        d = fila.select_one(".date")
        if not a or not d:
            continue
        fecha = fecha_iso(d.get("content", "") or d.get_text(" ")) or fecha_iso(
            (d.select_one("[content]") or {}).get("content", ""))
        if not fecha or not a["href"].startswith("/sala-de-prensa/"):
            continue   # formato antiguo, sin fecha de publicación
        b = fila.select_one(".body")
        out.append({"url": L.url_limpia(BASE, a["href"]), "titulo": a.get_text(" ", strip=True),
                    "fecha": fecha, "resumen": comun.limpiar(b.get_text(" ")).rstrip("…") if b else ""})
    return out


def main():
    ap = comun.argumentos("Scraper de la Subsecretaría de Desarrollo Regional y Administrativo.")
    ap.add_argument("--completo", action="store_true", help="recorre todo (sin corte por novedad)")
    args = ap.parse_args()
    comun.configurar_log()
    s = comun.sesion()
    salida = comun.SalidaCSV(args.salida or comun.DATA / "subdere.csv")

    def procesar(it):
        soup = L.bajar_soup(s, it["url"], args.pausa)
        if soup is None:
            return
        texto = L.cuerpo_de(soup, "article.node .field--name-field-news-description")
        if not L.valido(texto):
            log.warning("Sin cuerpo utilizable: %s", it["url"])
            return
        h1 = soup.select_one("article.node h1.title")
        titulo = h1.get_text(" ", strip=True) if h1 else it["titulo"]
        salida.agregar(L.fila(it["fecha"], INSTITUCION, titulo, L.resumen_de(texto), it["url"], texto))

    try:
        items = L.recorrer_listado(
            s, salida, lambda n: f"{LISTADO}?page={n - 1}" if n > 1 else LISTADO, parsear, args.pausa,
            completo=args.completo, muestra=args.muestra, etiqueta="subdere")
        L.ejecutar(items, procesar, salida, args.hilos, etiqueta="subdere")
    except L.Desafio as e:
        log.error("Desafío anti-bots en %s: se detiene subdere sin intentar saltarlo", e)
        salida.cerrar()
        sys.exit(2)


if __name__ == "__main__":
    main()
