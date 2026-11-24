#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de la Subsecretaría de Turismo (subturismo.gob.cl).

WordPress con plantilla propia: las notas son los posts de wp-sitemap-posts-post-*.xml
y la fecha va en la URL (/AAAA/MM/DD/). En la página, el título es el h1 y el cuerpo
son los bloques div.text-justify de la misma fila (cuando existe, el primero es la
bajada, que se incluye al inicio del texto).

Uso:  python scraper_subturismo.py [--muestra N] [--salida CSV] [--hilos N] [--pausa S]
Escribe data/subturismo.csv por defecto. Reanudable por URL.
"""
import re

import comun
import listados as L
from comun import fecha_de_url, log
from scraper_sitemap import leer_xml, locs

INSTITUCION = "Subsecretaría de Turismo"
INDICE = "https://www.subturismo.gob.cl/wp-sitemap.xml"


def main():
    ap = comun.argumentos("Scraper de la Subsecretaría de Turismo.")
    args = ap.parse_args()
    comun.configurar_log()
    s = comun.sesion()
    salida = comun.SalidaCSV(args.salida or comun.DATA / "subturismo.csv")

    sitemaps = [u for u, _ in locs(leer_xml(s, INDICE, args.pausa), INDICE)
                if re.search(r"wp-sitemap-posts-post-\d+\.xml", u)]
    urls = []
    for sm in sitemaps:
        urls += [u for u, _ in locs(leer_xml(s, sm, args.pausa), sm) if fecha_de_url(u)]
    urls.sort(key=fecha_de_url, reverse=True)
    pendientes = [u for u in urls if not salida.ya_esta(u)]
    if args.muestra:
        pendientes = pendientes[:args.muestra]
    log.info("subturismo: %d URLs de notas, %d por descargar", len(urls), len(pendientes))

    def procesar(url):
        soup = L.bajar_soup(s, url, args.pausa)
        if soup is None:
            return
        h1 = soup.select_one("h1")
        if h1 is None:
            log.warning("Sin título: %s", url)
            return
        fila = h1.find_parent("div", class_="row")   # fila con fecha, título y cuerpo
        bloques = [comun.texto_de(b) for b in (fila.select("div.text-justify") if fila else [])
                   if not b.find_parent("div", class_="text-justify")]
        texto = "\n".join(b for b in bloques if b)
        if not L.valido(texto):
            log.warning("Sin cuerpo utilizable: %s", url)
            return
        salida.agregar(L.fila(fecha_de_url(url), INSTITUCION, h1.get_text(" "),
                              L.resumen_de(texto), url, texto))

    try:
        L.ejecutar(pendientes, procesar, salida, args.hilos, etiqueta="subturismo")
    except L.Desafio as e:
        log.error("Desafío anti-bots en %s: se detiene subturismo sin intentar saltarlo", e)
        salida.cerrar()
        raise SystemExit(2)


if __name__ == "__main__":
    main()
