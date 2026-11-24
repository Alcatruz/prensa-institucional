#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de la Corporación de Fomento de la Producción (corfo.gob.cl).

Sala de prensa nacional y regional (6 notas por página). La paginación simple
(/page/N/) termina en la página 77 (oct-2023); lo anterior solo se alcanza con el
filtro ?archive_year=AAAA, que exige el parámetro archive_nonce que el propio
sitio publica en los enlaces de su página de prensa (se lee de ahí en cada corrida)
y se combina con /page/N/. Se recorren los años desde 2023 hacia atrás hasta
2010; los años sin notas devuelven una página vacía.

Uso:  python scraper_corfo.py [--muestra N] [--completo] [--salida CSV]
                              [--hilos N] [--pausa S]
Escribe data/corfo.csv por defecto. Reanudable por URL. Por defecto el recorrido
es incremental: si la paginación simple corta por novedad (3 páginas seguidas sin
notas nuevas) no se consultan los archivos por año; --completo recorre todo.
"""
import re
import sys

import comun
import listados as L
from comun import fecha_iso, log

INSTITUCION = "Corporación de Fomento de la Producción"
BASE = "https://www.corfo.gob.cl/sites/cpp/"
SECCIONES = [("nacional", "saladeprensanacional"), ("regional", "saladeprensaregional")]
ANIO_MAX, ANIO_MIN = 2023, 2010


def parsear(soup) -> list[dict]:
    out = []
    for c in soup.select(".little-news"):
        a = c.select_one("h4 a[href]")
        if not a:
            continue
        h5 = c.select_one("h5")
        out.append({"url": L.url_limpia(BASE, a["href"]), "titulo": a.get_text(" ", strip=True),
                    "fecha": fecha_iso(h5.get_text(" ")) if h5 else "", "resumen": ""})
    return out


def leer_nonce(s, raiz: str, pausa: float) -> str:
    soup = L.bajar_soup(s, raiz, pausa)
    if soup is None:
        return ""
    m = re.search(r"archive_nonce=(\w+)", str(soup))
    return m.group(1) if m else ""


def main():
    ap = comun.argumentos("Scraper de la Corporación de Fomento de la Producción.")
    ap.add_argument("--completo", action="store_true", help="recorre todo (sin corte por novedad)")
    args = ap.parse_args()
    comun.configurar_log()
    s = comun.sesion()
    salida = comun.SalidaCSV(args.salida or comun.DATA / "corfo.csv")

    def procesar(it):
        soup = L.bajar_soup(s, it["url"], args.pausa)
        if soup is None:
            return
        texto = L.cuerpo_de(soup, "div.post-content")
        if not L.valido(texto):
            log.warning("Sin cuerpo utilizable: %s", it["url"])
            return
        # la fecha visible del listado es la de publicación; los metadatos son el respaldo
        fecha = it["fecha"] or comun.fecha_meta(soup)
        salida.agregar(L.fila(fecha, INSTITUCION, it["titulo"], L.resumen_de(texto), it["url"], texto))

    items, vistas = [], set()
    try:
        for nombre, slug in SECCIONES:
            raiz = f"{BASE}{slug}/"
            falta = args.muestra - len(items) if args.muestra else 0
            if args.muestra and falta <= 0:
                break
            estado: dict = {}
            items += L.recorrer_listado(
                s, salida, lambda n, r=raiz: r + (f"page/{n}/" if n > 1 else ""), parsear, args.pausa,
                completo=args.completo, muestra=falta, etiqueta=f"corfo/{nombre}", seen=vistas, estado=estado)
            if estado.get("corte_por_novedad") or (args.muestra and len(items) >= args.muestra):
                continue
            nonce = leer_nonce(s, raiz, args.pausa)
            if not nonce:
                log.warning("corfo/%s: no se encontró archive_nonce; se omiten los años anteriores", nombre)
                continue
            for anio in range(ANIO_MAX, ANIO_MIN - 1, -1):
                falta = args.muestra - len(items) if args.muestra else 0
                if args.muestra and falta <= 0:
                    break
                q = f"?archive_nonce={nonce}&archive_year={anio}"
                # `seen` propio por año: el año 2023 reparte notas ya vistas en la paginación simple
                nuevas = L.recorrer_listado(
                    s, salida, lambda n, r=raiz, q=q: r + (f"page/{n}/" if n > 1 else "") + q, parsear,
                    args.pausa, completo=True, muestra=falta, etiqueta=f"corfo/{nombre}/{anio}")
                en_lista = {i["url"] for i in items}
                items += [i for i in nuevas if i["url"] not in en_lista]
        L.ejecutar(items, procesar, salida, args.hilos, etiqueta="corfo")
    except L.Desafio as e:
        log.error("Desafío anti-bots en %s: se detiene corfo sin intentar saltarlo", e)
        salida.cerrar()
        sys.exit(2)


if __name__ == "__main__":
    main()
