#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper - Servicio Nacional de Aduanas
Fuente: https://www.aduana.cl (noticias y comunicados)

El sitio no tiene un listado completo de noticias; la única ruta exhaustiva es el
sitemap mensual:
  robots.txt -> /aduana/site/sitemap_pags.xml (índice de ~238 archivos
  sitemap_pags_AAAAMM.xml.gz, desde febrero de 2007) -> URLs de páginas.
Todas las páginas (~24.600: fallos, resoluciones, dictámenes, tablas, oficinas,
recortes de prensa y noticias) comparten el patrón /slug/aduana/AAAA-MM-DD/id.html,
por lo que hay que separar las noticias en dos pasos.

Filtro de noticias
  1. Por slug (sin descargar): se descartan las familias normativas y de tablas
     (fallo-, resolucion-, dictamen-, clasificacion-, valoracion-, oficio-,
     equivalencias-, compendio-, circular-, etc.) y los recortes de otros medios
     (diario-, tvn-, cooperativa-, la-tercera-, etc.).
  2. Por contenido: la página debe tener div.cuerpo-articulo con al menos 40
     palabras y algún marcador de la plantilla de noticias: .fecha (comunicados
     recientes) o .bajada (bajada de la nota). Quedan fuera los recortes de
     prensa sin texto, los formularios y las fichas de oficinas.
  En una muestra de 200 candidatos el filtro dejó ~62%; revisando a mano lo que
  pasa, ~4% no son noticias (fichas de personas o tablas) y ~10% de las noticias
  antiguas (2008-2014), que no traen .fecha ni .bajada, se pierden.

Campos
  - Fecha: .fecha ("Lunes 2 de octubre de 2017") si existe; si no, la fecha de la
    URL (que coincide con la ruta de publicación del artículo).
  - Título: h1. Resumen: .bajada o primeros ~300 caracteres.
  - Contenido: .cuerpo-articulo > .CUERPO (con la bajada al comienzo si no está dentro).

Las URL descartadas se anotan en <salida>.descartadas.txt para no volver a
descargarlas al reanudar. Reanudable también por las URLs ya presentes en el CSV.
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""
import gzip
import re
from pathlib import Path

from bs4 import BeautifulSoup

import comun
from comun import (DATA, SalidaCSV, argumentos, en_paralelo, fecha_iso, limpiar, obtener, sesion,
                   texto_de)

INSTITUCION = "Servicio Nacional de Aduanas"
INDICE = "https://www.aduana.cl/aduana/site/sitemap_pags.xml"
MIN_PALABRAS = 40

log = comun.configurar_log("aduanas")

RE_URL = re.compile(r"^https://www\.aduana\.cl/(.+)/aduana/(\d{4})-(\d{2})-(\d{2})/(\d+)\.html$")
# familias normativas, tablas y formularios (primer término del slug)
RE_NORMATIVO = re.compile(
    r"^(fallos?|resoluci[oó]n(es)?|valoracion|dictamen(es)?|clasificacion|oficios?|equivalencias|"
    r"ultima|comentarios|vigencia|actualizaciones|fax|azucar|trigo|gasolina|petroleos|indice|"
    r"compendio|publicacion|formularios?|preguntas|portal|tablas?|dolar|uf|tipo|aranceles?|"
    r"arancel|circular(es)?|ordenanza|instructivo|decreto|ley|dfl|modificaci[oó]n|anexo|lista|"
    r"listado|tarifas?)(-|$)")
# bajadas/títulos de piezas normativas con slug que no delata su tipo (dictámenes de 1999-2005, etc.)
RE_NORMA_TEXTO = re.compile(r"^(dictamen|fallo|resoluci[oó]n|oficio|circular|decreto|reclamo|vistos|certificaci[oó]n)\b",
                            re.I)
# restos de publicaciones de Instagram incrustadas al final de la nota
RE_EMBED = re.compile(r"^(Ver esta publicaci[oó]n en Instagram|Una publicaci[oó]n compartida por)")
# recortes de otros medios, sin texto propio
RE_MEDIOS = re.compile(
    r"^(diario|radio|tvn|cooperativa|chv|mega|biobio|soychile|latercera|la-tercera|la-estrella|"
    r"el-mercurio|la-prensa|las-ultimas-noticias|extra-noticias|el-pinguino|el-tipografo|"
    r"la-nacion|portalfruticola|elcalbucano|el-longino|t13|24horas)(-|$)")


def listar_sitemaps(s, pausa):
    r = obtener(s, INDICE, pausa)
    if not r:
        return []
    return sorted(re.findall(r"<loc>([^<]+)</loc>", r.text))


def urls_de(s, sitemap, pausa):
    r = obtener(s, sitemap, pausa)
    if not r:
        return []
    datos = r.content
    if datos[:2] == b"\x1f\x8b":
        datos = gzip.decompress(datos)
    return re.findall(r"<loc>([^<]+)</loc>", datos.decode("utf-8", errors="replace"))


def candidata(url):
    """Devuelve la fecha ISO de la URL si pasa el filtro por slug; si no, None."""
    m = RE_URL.match(url)
    if not m:
        return None
    slug = m.group(1)
    if RE_NORMATIVO.match(slug) or RE_MEDIOS.match(slug):
        return None
    return f"{m.group(2)}-{m.group(3)}-{m.group(4)}"


def candidatas(s, pausa, limite=0, antiguas=False):
    """Candidatas (url, fecha) ordenadas de más reciente a más antigua.

    Con límite se leen solo los sitemaps necesarios (los más recientes, o los más
    antiguos con antiguas=True) en vez de los 238.
    """
    sitemaps = listar_sitemaps(s, pausa)
    orden = sitemaps if antiguas else sitemaps[::-1]
    out, vistas = [], set()
    for sm in orden:
        for u in urls_de(s, sm, pausa):
            f = candidata(u)
            if f and u not in vistas:
                vistas.add(u)
                out.append((u, f))
        if limite and len(out) >= limite * 3:
            break
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def procesar(item, s, salida, descartadas, pausa):
    url, fecha_url = item
    if salida.ya_esta(url) or url in descartadas:
        return
    r = obtener(s, url, pausa)
    if not r:
        return
    soup = BeautifulSoup(r.content, "html.parser")
    art = soup.select_one(".cuerpo-articulo")
    fecha_el = soup.select_one(".fecha")
    bajada_el = soup.select_one(".bajada")
    h1 = soup.find("h1")
    if art is None or h1 is None or (fecha_el is None and bajada_el is None):
        descartadas.add(url)
        return
    bajada = limpiar(bajada_el.get_text(" ")) if bajada_el else ""
    if RE_NORMA_TEXTO.match(bajada) or RE_NORMA_TEXTO.match(limpiar(h1.get_text(" "))):
        descartadas.add(url)
        return
    cuerpo = art.select_one(".CUERPO") or art
    contenido = "\n".join(l for l in texto_de(cuerpo).split("\n") if not RE_EMBED.match(l))
    if bajada and bajada[:60] not in contenido:
        contenido = bajada + "\n" + contenido
    if len(contenido.split()) < MIN_PALABRAS:
        descartadas.add(url)
        return
    fecha = (fecha_iso(fecha_el.get_text(" ")) if fecha_el else "") or fecha_url
    salida.agregar({"fecha": fecha, "institucion": INSTITUCION, "titulo": limpiar(h1.get_text(" ")),
                    "resumen": bajada or contenido[:300], "url": url, "contenido": contenido})


def main():
    ap = argumentos("Scraper del Servicio Nacional de Aduanas", DATA / "aduanas.csv")
    ap.add_argument("--antiguas", action="store_true", help="con --muestra, toma las más antiguas")
    a = ap.parse_args()
    s = sesion()
    items = candidatas(s, a.pausa, a.muestra, a.antiguas)
    if a.muestra and a.antiguas:
        items = items[::-1]
    log.info("Candidatas por slug: %d", len(items))
    salida = SalidaCSV(a.salida)
    arch = Path(str(a.salida) + ".descartadas.txt")
    descartadas = set(arch.read_text(encoding="utf-8").split()) if arch.exists() else set()
    previas = len(descartadas)
    if a.muestra:
        # se avanza por tandas hasta reunir la muestra pedida (muchas candidatas se descartan)
        for i in range(0, len(items), 10):
            en_paralelo(items[i:i + 10], lambda it: procesar(it, s, salida, descartadas, a.pausa), a.hilos,
                        cada=10)
            if salida.nuevas >= a.muestra:
                break
        log.info("Muestra: %d aceptadas, %d descartadas", salida.nuevas, len(descartadas) - previas)
    else:
        en_paralelo(items, lambda it: procesar(it, s, salida, descartadas, a.pausa), a.hilos, cada=100)
    nuevas = sorted(descartadas)
    arch.write_text("\n".join(nuevas) + "\n", encoding="utf-8")
    salida.cerrar()


if __name__ == "__main__":
    main()
