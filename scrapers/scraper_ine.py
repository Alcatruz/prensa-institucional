#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper - Instituto Nacional de Estadísticas (INE)
Fuente: https://www.ine.gob.cl/sala-de-prensa/prensa/general

Estrategia: requests + BeautifulSoup.
  - Listado principal: /sala-de-prensa/prensa/general/page/N (5 notas por página,
    ~306 páginas, continuo desde octubre de 2019; termina cuando la página
    no trae tarjetas). Cada li.sfnewsListItem trae título, fecha dd-mm-aaaa y
    un enlace relativo (../noticia/AAAA/MM/DD/slug) que se resuelve contra la
    página del listado.
  - Listado complementario: /sala-de-prensa/prensa-actualidad/actualidad[/N]
    (9 páginas). Enlaza a notas del mismo espacio de URL pero ~la mitad no
    aparece en el listado principal (~20 notas más), así que se suman.
  - Las URL se normalizan a https con la ruta codificada en porcentaje y se
    deduplican entre ambos listados.
  - Nota: cuerpo en div.NoticiaSingleContent; fecha en el listado o, si falta,
    en la URL o en .fechaNoticia.

Reanudable: si el CSV existe, omite las URLs que ya contiene.
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""
import re
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

import comun
from comun import (DATA, SalidaCSV, argumentos, en_paralelo, fecha_de_url, fecha_iso, limpiar,
                   obtener, sesion, texto_de)

INSTITUCION = "Instituto Nacional de Estadísticas"
BASE = "https://www.ine.gob.cl"
URL_GENERAL = f"{BASE}/sala-de-prensa/prensa/general/page/"
URL_ACTUALIDAD = f"{BASE}/sala-de-prensa/prensa-actualidad/actualidad"
MAX_PAGINAS = 500  # tope de seguridad; el listado real termina antes

log = comun.configurar_log("ine")


def normalizar(url: str) -> str:
    p = urlsplit(url)
    ruta = quote(unquote(p.path), safe="/%-._~!$&'()*+,;=:@")
    return urlunsplit(("https", p.netloc.replace(":80", ""), ruta, "", ""))


def tarjetas_general(s, pagina, pausa):
    url = f"{URL_GENERAL}{pagina}"
    r = obtener(s, url, pausa)
    if not r:
        return None
    soup = BeautifulSoup(r.content, "html.parser")
    notas = []
    for li in soup.select("li.sfnewsListItem"):
        a = li.find("a", href=True)
        if a is None:
            continue
        sm = li.find("small")
        notas.append({"url": normalizar(urljoin(url, a["href"])), "titulo": limpiar(a.get_text(" ")),
                      "fecha": fecha_iso(sm.get_text()) if sm else ""})
    return notas


def tarjetas_actualidad(s, pausa):
    notas = []
    for pagina in range(1, 40):
        url = URL_ACTUALIDAD + (f"/{pagina}" if pagina > 1 else "")
        r = obtener(s, url, pausa)
        if not r:
            break
        lote = []
        for a in BeautifulSoup(r.content, "html.parser").find_all("a", href=re.compile(r"/noticia/")):
            tit = a.select_one(".noticiaHomeTitular")
            f = a.select_one(".fechaNoticia")
            if tit is None:
                continue
            lote.append({"url": normalizar(urljoin(url, a["href"])), "titulo": limpiar(tit.get_text(" ")),
                         "fecha": fecha_iso(f.get_text()) if f else ""})
        if not lote:
            break
        notas += lote
    return notas


def listar(s, pausa, limite=0, desde_el_final=False):
    notas, vistas = [], set()

    def sumar(lote):
        for n in lote:
            if n["url"] not in vistas:
                vistas.add(n["url"])
                n["fecha"] = n["fecha"] or fecha_de_url(n["url"])
                notas.append(n)

    for pagina in range(1, MAX_PAGINAS + 1):
        lote = tarjetas_general(s, pagina, pausa)
        if not lote:
            break
        sumar(lote)
        if limite and not desde_el_final and len(notas) >= limite:
            break
        if pagina % 50 == 0:
            log.info("Listado: página %d (%d notas)", pagina, len(notas))
    if not (limite and not desde_el_final):
        sumar(tarjetas_actualidad(s, pausa))
    return sorted(notas, key=lambda n: n["fecha"], reverse=True)


def procesar(nota, s, salida, pausa):
    if salida.ya_esta(nota["url"]):
        return
    r = obtener(s, nota["url"], pausa)
    if not r:
        return
    soup = BeautifulSoup(r.content, "html.parser")
    cuerpo = soup.select_one("div.NoticiaSingleContent")
    fecha = nota["fecha"]
    if not fecha:
        f = soup.select_one(".fechaNoticiaSingleDetalle, .fechaNoticia")
        fecha = fecha_iso(f.get_text()) if f else ""
    contenido = texto_de(cuerpo)
    if not contenido or not nota["titulo"]:
        log.warning("Sin cuerpo o título: %s", nota["url"])
        return
    resumen = next((l for l in contenido.split("\n") if len(l) >= 60), contenido)
    salida.agregar({"fecha": fecha, "institucion": INSTITUCION, "titulo": nota["titulo"],
                    "resumen": resumen[:300], "url": nota["url"], "contenido": contenido})


def main():
    ap = argumentos("Scraper del Instituto Nacional de Estadísticas", DATA / "ine.csv")
    ap.add_argument("--antiguas", action="store_true", help="con --muestra, toma las más antiguas")
    a = ap.parse_args()
    s = sesion()
    notas = listar(s, a.pausa, a.muestra, a.antiguas)
    log.info("Listado: %d notas", len(notas))
    if a.muestra:
        notas = notas[::-1][:a.muestra] if a.antiguas else notas[:a.muestra]
    salida = SalidaCSV(a.salida)
    en_paralelo(notas, lambda n: procesar(n, s, salida, a.pausa), a.hilos)
    salida.cerrar()


if __name__ == "__main__":
    main()
