#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de la Superintendencia de Casinos de Juego (scj.gob.cl).

Las noticias son un tipo de contenido propio (/noticias_scj/) listado en
noticias_scj-sitemap.xml. En la página de cada nota, el título es el h1 y la fecha
va en p.fecha-noticia ("6 de Octubre de 2024"); el cuerpo son los párrafos
restantes de div.single-noticia (sin el botón "Volver"). El feed RSS publica
enlaces con un nombre de servidor interno, por eso no se usa.

Uso:  python scraper_scj.py [--muestra N] [--salida CSV] [--hilos N] [--pausa S]
Escribe data/scj.csv por defecto. Reanudable por URL.
"""
import re

import comun
import listados as L
from bs4 import BeautifulSoup
from comun import fecha_iso, log
from scraper_sitemap import leer_xml, locs

INSTITUCION = "Superintendencia de Casinos de Juego"
SITEMAP = "https://www.scj.gob.cl/noticias_scj-sitemap.xml"


def main():
    ap = comun.argumentos("Scraper de la Superintendencia de Casinos de Juego.")
    args = ap.parse_args()
    comun.configurar_log()
    s = comun.sesion()
    salida = comun.SalidaCSV(args.salida or comun.DATA / "scj.csv")

    entradas = [(u, m) for u, m in locs(leer_xml(s, SITEMAP, args.pausa), SITEMAP)
                if re.search(r"/noticias_scj/[^/]+/?$", u)]
    entradas.sort(key=lambda x: x[1], reverse=True)   # más recientemente modificadas primero
    pendientes = [u for u, _ in entradas if not salida.ya_esta(u)]
    if args.muestra:
        pendientes = pendientes[:args.muestra]
    log.info("scj: %d URLs de notas, %d por descargar", len(entradas), len(pendientes))

    def procesar(url):
        soup = L.bajar_soup(s, url, args.pausa)
        if soup is None:
            return
        caja = soup.select_one("div.single-noticia")
        if caja is None:
            log.warning("Sin contenedor de nota: %s", url)
            return
        h1 = caja.select_one("h1")
        titulo = comun.limpiar(h1.get_text(" ")) if h1 else ""
        f = caja.select_one(".fecha-noticia")
        fecha = fecha_iso(f.get_text(" ")) if f else ""
        for x in caja.select("h1, .fecha-noticia, .btn-volver-noticia"):
            x.decompose()
        texto = comun.texto_de(caja)
        if not titulo or not L.valido(texto):
            log.warning("Sin título o cuerpo utilizable: %s", url)
            return
        salida.agregar(L.fila(fecha or comun.fecha_meta(soup), INSTITUCION, titulo,
                              L.resumen_de(texto), url, texto))

    try:
        L.ejecutar(pendientes, procesar, salida, args.hilos, etiqueta="scj")
    except L.Desafio as e:
        log.error("Desafío anti-bots en %s: se detiene scj sin intentar saltarlo", e)
        salida.cerrar()
        raise SystemExit(2)


if __name__ == "__main__":
    main()
