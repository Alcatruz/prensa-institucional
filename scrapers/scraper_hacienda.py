#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper del Ministerio de Hacienda (hacienda.cl): noticias y comunicados.

La paginación usa ?foco=<primera nota de la página> y sus enlaces salen del menú
`section.paginas .dropdown-menu` de la primera página de cada sección. El orden
del listado no es estrictamente cronológico, así que no se corta por novedad:
siempre se recorren todas las páginas del listado (son pocas) y solo se
descargan las notas que aún no están en el CSV. La fecha se lee de la nota
(`time`, p. ej. "Noviembre 20, 2026"); si falta, del listado (dd/mm/aaaa).

Los comunicados que se publican solo como PDF (la mayoría) no se incluyen: el
proyecto no cuenta con extractor de PDF y la regla exige el texto del cuerpo.

Uso:  python scraper_hacienda.py [--muestra N] [--salida CSV] [--hilos N] [--pausa S]
Escribe data/hacienda.csv por defecto. --completo se acepta por compatibilidad.
"""
import sys

import comun
import listados as L
from comun import fecha_iso, log

INSTITUCION = "Ministerio de Hacienda"
BASE = "https://www.hacienda.cl"
SECCIONES = [("noticias", BASE + "/noticias-y-eventos/noticias"),
             ("comunicados", BASE + "/noticias-y-eventos/comunicados")]


def paginas_de(soup, inicial: str) -> list[str]:
    """URLs de todas las páginas de la sección, en orden y sin repetir."""
    out = [inicial]
    for a in soup.select("section.paginas .dropdown-menu a[href]"):
        u = L.url_limpia(BASE, a["href"])
        if u not in out:
            out.append(u)
    return out


def parsear(soup) -> list[dict]:
    out = []
    # En comunicados, los ítems li.clase-documento enlazan a un PDF (no a una página
    # con cuerpo en HTML): se omiten; solo se toman los li.clase-articulo.
    for c in soup.select("div.card, li.clase-articulo"):
        a = c.select_one("a.stretched-link[href]") or c.select_one("h3 a[href]") or c.select_one("a[href]")
        if not a or "/noticias-y-eventos/" not in a["href"]:
            continue
        t = c.select_one("time")
        r = c.select_one(".resumen")
        out.append({"url": L.url_limpia(BASE, a["href"]),
                    "titulo": a.get_text(" ", strip=True),
                    "fecha": fecha_iso(t.get_text(" "), orden="dmy") if t else "",
                    "resumen": r.get_text(" ", strip=True) if r else ""})
    return out


def listar(s, pausa: float, limite: int = 0) -> list[dict]:
    """Recorre todas las páginas de ambas secciones. `limite` corta al juntar N notas."""
    vistas: dict[str, dict] = {}
    for nombre, inicial in SECCIONES:
        soup = L.bajar_soup(s, inicial, pausa)
        if soup is None:
            log.warning("hacienda/%s: no se pudo bajar el listado", nombre)
            continue
        paginas = paginas_de(soup, inicial)
        log.info("hacienda/%s: %d páginas", nombre, len(paginas))
        for i, url in enumerate(paginas, 1):
            if i > 1:
                soup = L.bajar_soup(s, url, pausa)
                if soup is None:
                    log.warning("hacienda/%s: falló la página %d", nombre, i)
                    continue
            for it in parsear(soup):
                vistas.setdefault(it["url"], it)
            if limite and len(vistas) >= limite:
                return list(vistas.values())[:limite]
    return list(vistas.values())


def main():
    ap = comun.argumentos("Scraper del Ministerio de Hacienda.")
    ap.add_argument("--completo", action="store_true", help="sin efecto: siempre se recorre todo el listado")
    args = ap.parse_args()
    comun.configurar_log()
    s = comun.sesion()
    salida = comun.SalidaCSV(args.salida or comun.DATA / "hacienda.csv")

    def procesar(it):
        soup = L.bajar_soup(s, it["url"], args.pausa)
        if soup is None:
            return
        t = soup.select_one("time")
        fecha = fecha_iso(t.get_text(" ")) if t else ""
        fecha = fecha or it["fecha"]
        texto = L.cuerpo_de(soup, ".cuerpo")
        if not L.valido(texto):
            log.warning("Sin cuerpo utilizable: %s", it["url"])
            return
        resumen = it["resumen"] or L.resumen_de(texto)
        salida.agregar(L.fila(fecha, INSTITUCION, it["titulo"], resumen, it["url"], texto))

    try:
        items = [it for it in listar(s, args.pausa, args.muestra) if not salida.ya_esta(it["url"])]
        if args.muestra:
            items = items[:args.muestra]
        L.ejecutar(items, procesar, salida, args.hilos, etiqueta="hacienda")
    except L.Desafio as e:
        log.error("Desafío anti-bots en %s: se detiene hacienda sin intentar saltarlo", e)
        salida.cerrar()
        sys.exit(2)


if __name__ == "__main__":
    main()
