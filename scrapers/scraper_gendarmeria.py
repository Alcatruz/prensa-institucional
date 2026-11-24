#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper - Gendarmería de Chile
Fuente: https://www.gendarmeria.gob.cl (sección de noticias)

Estrategia: API JSON pública que usa el propio sitio.
  - Índice completo: .../Api/SitioGendarmeria/mas_noti?cant_noti=1000
    (~490 notas desde diciembre de 2022; no viene ordenado, se ordena por fecha).
  - Detalle: .../Api/SitioGendarmeria/noticia_id?id_noti=ID
    El cuerpo está en cuerpo_noticia.cuerpo_cuerpo (HTML).
  - Las respuestas son JSON aunque declaren text/html; las fechas vienen MM/DD/AAAA.
  - Las notas no tienen URL propia: como `url` se usa la URL de la API de detalle
    con el ID, que es estable y única.

Reanudable: si el CSV existe, omite las URLs que ya contiene.
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""
import json

from bs4 import BeautifulSoup

import comun
from comun import DATA, SalidaCSV, argumentos, en_paralelo, fecha_iso, limpiar, obtener, sesion, texto_de

INSTITUCION = "Gendarmería de Chile"
API = "https://www.gendarmeria.gob.cl/apisitio.gendarmeria.gob.cl/Api/SitioGendarmeria"
URL_INDICE = f"{API}/mas_noti?cant_noti=1000"
URL_DETALLE = f"{API}/noticia_id?id_noti="

log = comun.configurar_log("gendarmeria")


def _json(r):
    """El servidor declara text/html; se decodifica el cuerpo como JSON."""
    try:
        return json.loads(r.content.decode("utf-8-sig"))
    except ValueError:
        return None


def listar(s, pausa):
    r = obtener(s, URL_INDICE, pausa)
    datos = _json(r) if r else None
    if not isinstance(datos, list):
        return []
    notas = []
    for d in datos:
        if d.get("estado_noticia") not in (None, "A"):
            continue
        fecha = fecha_iso(d.get("fecha_inicio_noticia") or d.get("fecha_creacion_noticia") or "", "mdy")
        notas.append({"id": d["id_noticia_noticia"], "fecha": fecha,
                      "titulo": limpiar(d.get("titulo_noticia")),
                      "resumen": limpiar(d.get("bajada_noticia"))})
    notas.sort(key=lambda n: (n["fecha"], n["id"]), reverse=True)
    return notas


def procesar(nota, s, salida, pausa):
    url = f"{URL_DETALLE}{nota['id']}"
    if salida.ya_esta(url):
        return
    r = obtener(s, url, pausa)
    datos = _json(r) if r else None
    cuerpo = ((datos or {}).get("cuerpo_noticia") or {}).get("cuerpo_cuerpo") or ""
    contenido = texto_de(BeautifulSoup(cuerpo, "html.parser"))
    if not contenido or not nota["titulo"]:
        log.warning("Sin cuerpo o título: id %s", nota["id"])
        return
    salida.agregar({"fecha": nota["fecha"], "institucion": INSTITUCION, "titulo": nota["titulo"],
                    "resumen": nota["resumen"] or contenido[:300], "url": url, "contenido": contenido})


def main():
    ap = argumentos("Scraper de Gendarmería de Chile", DATA / "gendarmeria.csv")
    ap.add_argument("--antiguas", action="store_true", help="con --muestra, toma las más antiguas")
    a = ap.parse_args()
    s = sesion()
    notas = listar(s, a.pausa)
    log.info("Índice: %d notas", len(notas))
    if a.muestra:
        notas = notas[::-1][:a.muestra] if a.antiguas else notas[:a.muestra]
    salida = SalidaCSV(a.salida)
    en_paralelo(notas, lambda n: procesar(n, s, salida, a.pausa), a.hilos)
    salida.cerrar()


if __name__ == "__main__":
    main()
