#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de la Contraloría General de la República (contraloria.cl).

Portal Liferay: el listado de noticias pagina con parámetros de portlet
(_cur=N, 50 notas por página) y el cuerpo viene estático en
`div.asset-content .journal-content-article`. Las notas más antiguas publican
la fecha 01/01/1970 (valor por defecto del gestor de contenidos): en ese caso la
fecha queda vacía, porque no hay otra fecha explícita en su HTML.

Uso:  python scraper_contraloria.py [--muestra N] [--completo] [--salida CSV]
                                    [--hilos N] [--pausa S]
Escribe data/contraloria.csv por defecto. Reanudable por URL; por defecto el
recorrido corta tras 3 páginas seguidas sin notas nuevas (--completo recorre todo).
"""
import sys

import comun
import listados as L
from comun import fecha_iso, log

INSTITUCION = "Contraloría General de la República"
PORTLET = "101_INSTANCE_AmxFf8h7l735"
BASE = "https://www.contraloria.cl/web/cgr/noticias"


def url_pagina(n: int) -> str:
    return (f"{BASE}?p_p_id={PORTLET}&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view"
            f"&_{PORTLET}_delta=50&_{PORTLET}_cur={n}")


def parsear(soup) -> list[dict]:
    out = []
    for it in soup.select("div.list-item"):
        a = it.select_one("div.list-item-body a[href]")
        if not a:
            continue
        # se descartan los parámetros de redirección: la URL canónica es hasta el '?'
        url = L.url_limpia(BASE, a["href"].split("?")[0])
        h = it.select_one(".list-item-heading")
        f = it.select_one("small")
        out.append({"url": url, "titulo": h.get_text(" ", strip=True) if h else "",
                    "fecha": fecha_iso(f.get_text(" ")) if f else "", "resumen": ""})
    return out


def main():
    ap = comun.argumentos("Scraper de la Contraloría General de la República.")
    ap.add_argument("--completo", action="store_true", help="recorre todo el listado (sin corte por novedad)")
    args = ap.parse_args()
    comun.configurar_log()
    s = comun.sesion()
    salida = comun.SalidaCSV(args.salida or comun.DATA / "contraloria.csv")

    def procesar(it):
        soup = L.bajar_soup(s, it["url"], args.pausa, sin_comentarios=True)
        if soup is None:
            return
        f = soup.select_one("div.asset-content .fecha-noticia")
        h = soup.select_one("div.asset-content .journal-content-article h2")
        fecha = fecha_iso(f.get_text(" ")) if f else ""  # 01/01/1970 se descarta en fecha_iso
        titulo = (h.get_text(" ", strip=True) if h else "") or it["titulo"]
        texto = L.cuerpo_de(soup, "div.asset-content .journal-content-article",
                            quitar=("h2", ".fecha-noticia"))
        if not L.valido(texto):
            log.warning("Sin cuerpo utilizable: %s", it["url"])
            return
        salida.agregar(L.fila(fecha or it["fecha"], INSTITUCION, titulo, L.resumen_de(texto),
                              it["url"], texto))

    try:
        items = L.recorrer_listado(s, salida, url_pagina, parsear, args.pausa, completo=args.completo,
                                   muestra=args.muestra, etiqueta="contraloria", sin_comentarios=True)
        L.ejecutar(items, procesar, salida, args.hilos, etiqueta="contraloria")
    except L.Desafio as e:
        log.error("Desafío anti-bots en %s: se detiene contraloria sin intentar saltarlo", e)
        salida.cerrar()
        sys.exit(2)


if __name__ == "__main__":
    main()
