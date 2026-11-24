#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de la Superintendencia de Servicios Sanitarios (siss.gob.cl).

Sitio Newtenberg. El "Archivo histórico" de prensa publica una página por año
(w3-multipropertyvalues-6348-<id>.html) que lista todas las notas del año con su
fecha; los años salen de los enlaces de esa misma página. Cada nota
(w3-article-N.html) trae la bajada en h4.abstract y el cuerpo en div.cuerpo-simple.
El buscador del sitio no se usa: depende de un índice que hoy responde vacío.

Uso:  python scraper_siss.py [--muestra N] [--completo] [--salida CSV]
                             [--hilos N] [--pausa S]
Escribe data/siss.csv por defecto. Reanudable por URL. Por defecto, el recorrido
de años (del más reciente al más antiguo) se corta al llegar a un año sin notas
nuevas; --completo recorre todos.
"""
import re
import sys
from urllib.parse import urljoin

import comun
import listados as L
from comun import fecha_iso, log

INSTITUCION = "Superintendencia de Servicios Sanitarios"
BASE = "https://www.siss.gob.cl/589/"
ARCHIVO = BASE + "w3-propertyvalue-6348.html"   # redirige al último año con noticias


def parsear(soup) -> list[dict]:
    caja = soup.select_one("#SISS23_pa_archivo_noticias")
    out = []
    for r in (caja.select(".recuadro") if caja else []):
        a = r.select_one("h1 a[href]")
        f = r.select_one(".fecha")
        if not a:
            continue
        out.append({"url": L.url_limpia(BASE, a["href"]), "titulo": a.get_text(" ", strip=True),
                    "fecha": fecha_iso(f.get_text(" ")) if f else ""})
    return out


def enlaces_de_anios(soup) -> list[tuple[int, str]]:
    anios = {}
    for a in soup.select('a[href*="w3-multipropertyvalues-"]'):
        t = a.get_text(strip=True)
        if re.fullmatch(r"(19|20)\d\d", t):
            anios[int(t)] = urljoin(BASE, a["href"])
    return sorted(anios.items(), reverse=True)


def main():
    ap = comun.argumentos("Scraper de la Superintendencia de Servicios Sanitarios.")
    ap.add_argument("--completo", action="store_true", help="recorre todos los años")
    args = ap.parse_args()
    comun.configurar_log()
    s = comun.sesion()
    salida = comun.SalidaCSV(args.salida or comun.DATA / "siss.csv")

    def procesar(it):
        soup = L.bajar_soup(s, it["url"], args.pausa)
        if soup is None:
            return
        caja = soup.select_one("#SISS23_ar_articuloCcompleto .recuadro")
        cuerpo = caja.select_one(".cuerpo-simple") if caja else None
        texto = comun.texto_de(cuerpo)
        if not L.valido(texto):
            log.warning("Sin cuerpo utilizable: %s", it["url"])
            return
        ab = caja.select_one(".abstract")
        resumen = comun.limpiar(ab.get_text(" ")) if ab else L.resumen_de(texto)
        h1 = caja.select_one("h1")
        titulo = h1.get_text(" ") if h1 else it["titulo"]
        fecha = it["fecha"] or comun.fecha_meta(soup)
        salida.agregar(L.fila(fecha, INSTITUCION, titulo, resumen, it["url"], texto))

    items = []
    try:
        raiz = L.bajar_soup(s, ARCHIVO, args.pausa)
        # el redirect es un meta refresh: se sigue a mano
        m = re.search(r'URL=([^"]+)', str(raiz)) if raiz is not None else None
        if m and raiz.select_one("#SISS23_pa_archivo_noticias") is None:
            raiz = L.bajar_soup(s, urljoin(BASE, m.group(1)), args.pausa)
        if raiz is None:
            sys.exit("No se pudo leer el archivo histórico de prensa")
        for anio, url in enlaces_de_anios(raiz):
            falta = args.muestra - len(items) if args.muestra else 0
            if args.muestra and falta <= 0:
                break
            soup = L.bajar_soup(s, url, args.pausa)
            notas = parsear(soup) if soup is not None else []
            pend = [n for n in notas if not salida.ya_esta(n["url"])]
            log.info("siss %d: %d notas, %d por descargar", anio, len(notas), len(pend))
            # el listado ya viene ordenado de la más nueva a la más antigua
            items += pend[:falta] if args.muestra else pend
            if not args.completo and notas and not pend and salida.previas:
                log.info("siss: año sin notas nuevas, se corta (use --completo para recorrer todo)")
                break
        L.ejecutar(items, procesar, salida, args.hilos, etiqueta="siss")
    except L.Desafio as e:
        log.error("Desafío anti-bots en %s: se detiene siss sin intentar saltarlo", e)
        salida.cerrar()
        sys.exit(2)


if __name__ == "__main__":
    main()
