#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper - Ministerio del Deporte (Mindep)
Fuente: https://www.mindep.cl/actividades/noticias

Estrategia: requests + BeautifulSoup.
  - El listado es UNA sola página (~2,7 MB) con todas las notas (~1.180, ids
    645 a 2571, desde marzo de 2018). Cada nota es un div.itemBox-newsSection
    con la fecha ("Publicado el 18 de nov, 2026") y el enlace /actividades/noticias/ID.
  - Nota: /actividades/noticias/ID. El cuerpo está en div.col-md-9, que además
    trae la fecha, el título (primer h3) y los botones de compartir; se
    separan del texto. La bajada, cuando existe, queda como primer párrafo.

Reanudable: si el CSV existe, omite las URLs que ya contiene.
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""
import re

from bs4 import BeautifulSoup

import comun
from comun import DATA, SalidaCSV, argumentos, en_paralelo, fecha_iso, limpiar, obtener, sesion, texto_de

INSTITUCION = "Ministerio del Deporte"
URL_LISTADO = "https://www.mindep.cl/actividades/noticias"
RE_NOTA = re.compile(r"/actividades/noticias/(\d+)/?$")

log = comun.configurar_log("mindep")


def listar(s, pausa):
    r = obtener(s, URL_LISTADO, pausa, timeout=120)
    if not r:
        return []
    soup = BeautifulSoup(r.content, "html.parser")
    notas = {}
    for caja in soup.select("div.itemBox-newsSection"):
        a = caja.find("a", href=RE_NOTA)
        if a is None:
            continue
        ident = int(RE_NOTA.search(a["href"]).group(1))
        small = caja.find("small")
        notas[ident] = {"id": ident, "url": f"{URL_LISTADO}/{ident}",
                        "fecha": fecha_iso(small.get_text(" ")) if small else "",
                        "titulo": limpiar(a.get_text(" "))}
    return sorted(notas.values(), key=lambda n: n["id"], reverse=True)


def procesar(nota, s, salida, pausa):
    if salida.ya_esta(nota["url"]):
        return
    r = obtener(s, nota["url"], pausa)
    if not r:
        return
    soup = BeautifulSoup(r.content, "html.parser")
    col = soup.select_one("div.col-md-9")
    if col is None:
        log.warning("Sin cuerpo: %s", nota["url"])
        return
    fecha = nota["fecha"]
    marca = col.find(string=re.compile(r"^\s*Publicado el"))
    if marca is not None:
        fecha = fecha or fecha_iso(marca)
        marca.parent.decompose()
    titulo = nota["titulo"]
    h3 = col.find("h3")
    if h3 is not None:
        titulo = titulo or limpiar(h3.get_text(" "))
        h3.decompose()
    for el in col.select("a.itemBox-imgContainer, [class*=social], a[href*='sharer'], a[href*='intent/tweet']"):
        el.decompose()
    contenido = texto_de(col)
    if not contenido or not titulo:
        log.warning("Sin texto o título: %s", nota["url"])
        return
    primero = contenido.split("\n", 1)[0]
    salida.agregar({"fecha": fecha, "institucion": INSTITUCION, "titulo": titulo,
                    "resumen": primero[:300], "url": nota["url"], "contenido": contenido})


def main():
    ap = argumentos("Scraper del Ministerio del Deporte", DATA / "mindep.csv")
    ap.add_argument("--antiguas", action="store_true", help="con --muestra, toma las más antiguas")
    a = ap.parse_args()
    s = sesion()
    notas = listar(s, a.pausa)
    log.info("Listado: %d notas", len(notas))
    if a.muestra:
        notas = notas[::-1][:a.muestra] if a.antiguas else notas[:a.muestra]
    salida = SalidaCSV(a.salida)
    en_paralelo(notas, lambda n: procesar(n, s, salida, a.pausa), a.hilos)
    salida.cerrar()


if __name__ == "__main__":
    main()
