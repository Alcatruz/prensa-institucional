#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de la Agencia de Calidad de la Educación (agenciaeducacion.cl).

El listado del sitio carga más notas por admin-ajax; en cambio el RSS paginado
(/feed/?paged=N, 10 notas por página) entrega el texto íntegro en content:encoded,
que coincide con el cuerpo de la nota (article .post-content). Por eso se lee el
cuerpo desde el RSS y no se vuelve a bajar cada nota.

Uso:  python scraper_agencia_educacion.py [--muestra N] [--completo] [--salida CSV]
Escribe data/agencia_educacion.csv por defecto. Reanudable por URL; por defecto
corta tras 3 páginas seguidas sin notas nuevas (--completo recorre todo el RSS).
"""
import re
import time
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

import comun
import listados as L
from comun import log

INSTITUCION = "Agencia de Calidad de la Educación"
FEED = "https://www.agenciaeducacion.cl/feed/?paged={n}"
NS = {"c": "http://purl.org/rss/1.0/modules/content/"}

# el RSS agrega al final un pie automático ("The post ... first appeared on ...")
_PIE = re.compile(r"\n?The post .{0,400}? first appeared on [^\n]*\s*$", re.S)

try:
    from zoneinfo import ZoneInfo
    _CHILE = ZoneInfo("America/Santiago")
except Exception:  # sin base de zonas horarias: se usa la fecha en UTC
    _CHILE = None


def fecha_pub(texto: str) -> str:
    """pubDate (RFC 822, en UTC) como AAAA-MM-DD en hora de Chile."""
    try:
        dt = parsedate_to_datetime(texto)
    except (TypeError, ValueError):
        return comun.fecha_iso(texto or "")
    if _CHILE is not None:
        dt = dt.astimezone(_CHILE)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.date().isoformat()


def leer_pagina(s, n: int, pausa: float):
    """Notas de una página del RSS ([] si ya no hay) o None si falló la descarga."""
    url = FEED.format(n=n)
    r = comun.obtener(s, url, pausa)
    if r is None:
        time.sleep(5)
        r = comun.obtener(s, url, pausa)
    if r is None:
        return None
    try:
        items = ET.fromstring(r.content).findall("./channel/item")
    except ET.ParseError as e:
        log.warning("RSS ilegible en %s: %s", url, e)
        return None
    out = []
    for it in items:
        html = it.findtext("c:encoded", default="", namespaces=NS)
        texto = _PIE.sub("", comun.texto_de(BeautifulSoup(html, "html.parser"))).strip()
        out.append({"url": (it.findtext("link") or "").strip(),
                    "titulo": it.findtext("title") or "",
                    "fecha": fecha_pub(it.findtext("pubDate") or ""),
                    "contenido": texto})
    return out


def main():
    ap = comun.argumentos("Scraper de la Agencia de Calidad de la Educación (RSS).")
    ap.add_argument("--completo", action="store_true", help="recorre todo el RSS (sin corte por novedad)")
    args = ap.parse_args()
    comun.configurar_log()
    s = comun.sesion()
    salida = comun.SalidaCSV(args.salida or comun.DATA / "agencia_educacion.csv")
    agregadas, sin_nuevas, descartadas = 0, 0, 0
    try:
        for n in range(1, 500):
            items = leer_pagina(s, n, args.pausa)
            if not items:
                log.info("RSS: fin en la página %d", n)
                break
            nuevas = 0
            for it in items:
                if args.muestra and agregadas + nuevas >= args.muestra:
                    break
                if not it["url"] or salida.ya_esta(it["url"]):
                    continue
                if not L.valido(it["contenido"]):
                    descartadas += 1
                    log.warning("Sin cuerpo utilizable: %s", it["url"])
                    continue
                nuevas += 1
                salida.agregar(L.fila(it["fecha"], INSTITUCION, it["titulo"],
                                      L.resumen_de(it["contenido"]), it["url"], it["contenido"]))
            agregadas += nuevas
            if args.muestra and agregadas >= args.muestra:
                break
            if not args.completo:
                sin_nuevas = 0 if nuevas else sin_nuevas + 1
                if sin_nuevas >= 3:
                    log.info("RSS: 3 páginas seguidas sin notas nuevas, se corta en la %d "
                             "(use --completo para recorrer todo)", n)
                    break
    finally:
        salida.cerrar()
    if descartadas:
        log.info("Notas descartadas por falta de cuerpo: %d", descartadas)


if __name__ == "__main__":
    main()
