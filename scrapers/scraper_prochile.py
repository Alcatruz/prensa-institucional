#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper - ProChile (Dirección General de Promoción de Exportaciones)
Fuente: https://www.prochile.gob.cl/noticias

Estrategia: API OData pública que alimenta el propio sitio.
  - /api/news/newsitems filtrada por el dominio de ProChile, ordenada por
    PublicationDate descendente y paginada con $skip/$top ($top admite como
    máximo 100). ~2.190 notas desde marzo de 2016.
  - Cada registro trae el cuerpo completo: Content y ColumnaTextoDos (HTML,
    segunda columna del texto), además de Summary (bajada).
  - URL pública: /noticias/detalle-noticia + ItemDefaultUrl (/AAAA/MM/DD/slug),
    con el slug codificado. La fecha se toma de esa ruta, que coincide con la
    hora local de publicación (PublicationDate viene en UTC).

Reanudable: si el CSV existe, omite las URLs que ya contiene.
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""
from urllib.parse import quote

from bs4 import BeautifulSoup

import comun
from comun import (DATA, SalidaCSV, argumentos, fecha_de_url, fecha_iso, limpiar, obtener,
                   sesion, texto_de)

INSTITUCION = "ProChile"
BASE = "https://www.prochile.gob.cl"
API = f"{BASE}/api/news/newsitems"
DOMINIO = "dominio/any(x:x eq 54dec674-75df-4bca-841a-6e92192928c4)"
CAMPOS = "Id,PublicationDate,Title,Summary,Content,ColumnaTextoDos,UrlName,ItemDefaultUrl"
POR_PAGINA = 100

log = comun.configurar_log("prochile")


def pagina(s, salto, tope, pausa, orden="desc"):
    r = obtener(s, API, pausa, params={"$filter": DOMINIO, "$orderby": f"PublicationDate {orden}",
                                       "$skip": salto, "$top": tope, "$count": "true",
                                       "$select": CAMPOS})
    if not r:
        return [], 0
    j = r.json()
    return j.get("value", []), j.get("@odata.count", 0)


def a_fila(d):
    ruta = d.get("ItemDefaultUrl") or ""
    if not ruta:
        return None
    url = f"{BASE}/noticias/detalle-noticia" + quote(ruta, safe="/-_.~")
    fecha = fecha_de_url(ruta) or fecha_iso(d.get("PublicationDate") or "")
    bloques = []
    for campo in ("Content", "ColumnaTextoDos"):
        t = texto_de(BeautifulSoup(d.get(campo) or "", "html.parser"))
        if t:
            bloques.append(t)
    contenido = "\n".join(bloques)
    titulo = limpiar(d.get("Title"))
    resumen = limpiar(BeautifulSoup(d.get("Summary") or "", "html.parser").get_text(" "))
    if not titulo or not contenido:
        return None
    return {"fecha": fecha, "institucion": INSTITUCION, "titulo": titulo,
            "resumen": resumen or contenido[:300], "url": url, "contenido": contenido}


def main():
    ap = argumentos("Scraper de ProChile", DATA / "prochile.csv")
    ap.add_argument("--antiguas", action="store_true", help="con --muestra, toma las más antiguas")
    a = ap.parse_args()
    s = sesion()
    salida = SalidaCSV(a.salida)
    orden = "asc" if a.antiguas else "desc"
    salto, total = 0, None
    while True:
        tope = min(POR_PAGINA, a.muestra - salto) if a.muestra else POR_PAGINA
        if tope <= 0:
            break
        notas, n = pagina(s, salto, tope, a.pausa, orden)
        if total is None:
            total = n
            log.info("Notas en la API: %d", total)
        if not notas:
            break
        for d in notas:
            fila = a_fila(d)
            if fila:
                salida.agregar(fila)
        salto += len(notas)
        log.info("Procesadas %d/%d", salto, a.muestra or total)
        if salto >= (total or 0):
            break
    salida.cerrar()


if __name__ == "__main__":
    main()
