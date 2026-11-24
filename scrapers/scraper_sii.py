#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper - Servicio de Impuestos Internos (SII)
Fuente: https://www.sii.cl/noticias/ (sii.cl redirige a homer.sii.cl)

Estrategia: requests + BeautifulSoup.
  - Índice por año: /noticias/AAAA/index.html (2013 en adelante). Las notas están
    en un arreglo JavaScript con filas ['ddmmaanotiNNxxx', 'dd-mm-aaaa', 'título', 'imagen'].
    Se recorre cada año hasta que la página no exista.
  - Notas de 2012: /pagina/actualizada/noticias/otrasnoticias2012.htm, con
    enlaces javascript:link_cert("2012/ID.htm", ...) y la fecha entre paréntesis.
    Cada nota está en /pagina/actualizada/noticias/2012/ID.htm (HTML antiguo,
    sin clases: el cuerpo es el último div de la página).
  - Nota (2013 en adelante): /noticias/AAAA/ID.htm, cuerpo en div.contenido
    (antetítulo, título y fecha están en div.resumen-noticia / span.title-small;
    se separan del cuerpo).
  - El título y la fecha salen del índice.

Reanudable: si el CSV existe, omite las URLs que ya contiene.
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""
import html
import re
from datetime import date

from bs4 import BeautifulSoup

import comun
from comun import DATA, SalidaCSV, argumentos, en_paralelo, fecha_iso, limpiar, obtener, sesion, texto_de

INSTITUCION = "Servicio de Impuestos Internos"
BASE = "https://www.sii.cl"
URL_2012 = f"{BASE}/pagina/actualizada/noticias/otrasnoticias2012.htm"
PRIMER_ANIO = 2013

log = comun.configurar_log("sii")

RE_FILA = re.compile(r"\[\s*'([0-9]{6}noti\w+)'\s*,\s*'(\d{2}-\d{2}-\d{4})'\s*,\s*'((?:\\.|[^'\\])*)'")
RE_VIEJA = re.compile(r'link_cert\("(2012/\w+\.htm)"[^>]*>(.*?)</a>', re.S | re.I)


def decodificar(r):
    """Las páginas antiguas vienen en Windows-1252; las nuevas en UTF-8."""
    try:
        return r.content.decode("utf-8")
    except UnicodeDecodeError:
        return r.content.decode("cp1252", errors="replace")


def listar(s, pausa):
    notas = []
    for anio in range(PRIMER_ANIO, date.today().year + 1):
        r = obtener(s, f"{BASE}/noticias/{anio}/index.html", pausa)
        if not r:
            continue
        for ident, fecha, titulo in RE_FILA.findall(decodificar(r)):
            titulo = limpiar(html.unescape(titulo.replace("\\'", "'")))
            notas.append({"url": f"{BASE}/noticias/{anio}/{ident}.htm", "fecha": fecha_iso(fecha),
                          "titulo": titulo, "antigua": False})
    r = obtener(s, URL_2012, pausa)
    if r:
        for ruta, texto in RE_VIEJA.findall(decodificar(r)):
            texto = limpiar(html.unescape(re.sub(r"<[^>]+>", "", texto)))
            m = re.search(r"\((\d{2}-\d{2}-\d{4})\)\s*$", texto)
            notas.append({"url": f"{BASE}/pagina/actualizada/noticias/{ruta}",
                          "fecha": fecha_iso(m.group(1)) if m else "",
                          "titulo": texto[:m.start()].strip() if m else texto, "antigua": True})
    vistas, unicas = set(), []
    for n in sorted(notas, key=lambda n: n["fecha"], reverse=True):
        if n["url"] not in vistas:
            vistas.add(n["url"])
            unicas.append(n)
    return unicas


def cuerpo_nuevo(soup):
    c = soup.select_one("div.contenido")
    if c is None:
        return "", ""
    resumen = ""
    p = c.select_one("p.resumen")
    if p:
        resumen = limpiar(p.get_text(" "))
    for el in c.select("div.resumen-noticia, span.title-small, h2.title"):
        el.decompose()
    return texto_de(c), resumen


def cuerpo_antiguo(soup):
    divs = soup.select("#contenedor > div")
    return texto_de(divs[-1]) if divs else ""


def procesar(nota, s, salida, pausa):
    if salida.ya_esta(nota["url"]):
        return
    r = obtener(s, nota["url"], pausa)
    if not r:
        return
    soup = BeautifulSoup(decodificar(r), "html.parser")
    if nota["antigua"]:
        contenido, resumen = cuerpo_antiguo(soup), ""
    else:
        contenido, resumen = cuerpo_nuevo(soup)
    if not contenido or not nota["titulo"]:
        log.warning("Sin cuerpo o título: %s", nota["url"])
        return
    salida.agregar({"fecha": nota["fecha"], "institucion": INSTITUCION, "titulo": nota["titulo"],
                    "resumen": resumen or contenido[:300], "url": nota["url"], "contenido": contenido})


def main():
    ap = argumentos("Scraper del Servicio de Impuestos Internos", DATA / "sii.csv")
    ap.add_argument("--antiguas", action="store_true", help="con --muestra, toma las más antiguas")
    a = ap.parse_args()
    s = sesion()
    notas = listar(s, a.pausa)
    log.info("Índices: %d notas", len(notas))
    if a.muestra:
        notas = notas[::-1][:a.muestra] if a.antiguas else notas[:a.muestra]
    salida = SalidaCSV(a.salida)
    en_paralelo(notas, lambda n: procesar(n, s, salida, a.pausa), a.hilos)
    salida.cerrar()


if __name__ == "__main__":
    main()
