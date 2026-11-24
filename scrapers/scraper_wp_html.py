#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de familia para sitios con listado paginado en HTML y nota individual.

Uso:  python scraper_wp_html.py <sitio> [--muestra N] [--completo] [--salida CSV]
                                         [--hilos N] [--pausa S]

Sitios (ver SITIOS): minjusticia, sml, defensoria_ninez, minsal, minciencia.
Escribe data/<sitio>.csv por defecto. Es reanudable: las URLs ya presentes en el
CSV no se vuelven a bajar. Por defecto el recorrido es incremental (se corta tras
3 páginas seguidas sin notas nuevas); --completo recorre todas las páginas.
"""
import sys
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse

import comun
import listados as L
from comun import fecha_iso, fecha_meta, log


# ---------------------------------------------------------------------------
# Listados: cada función devuelve [{url, titulo, fecha, resumen}] de una página
# ---------------------------------------------------------------------------
def _item(base, a, titulo="", fecha="", resumen=""):
    return {"url": L.url_limpia(base, a["href"]), "titulo": titulo or a.get_text(" ", strip=True),
            "fecha": fecha, "resumen": resumen}


def listado_gobcl(base):
    """Plantilla gob.cl (Ministerio de Justicia, SML): tarjetas div.post.tarjeta."""
    def parsear(soup):
        out = []
        for d in soup.select("div.post.tarjeta"):
            a = d.select_one("h4.title a[href]")
            if a:
                meta = d.select_one("span.meta")
                out.append(_item(base, a, fecha=fecha_iso(meta.get_text(" ")) if meta else ""))
        return out
    return parsear


def listado_defensoria(base):
    """Defensoría de la Niñez: tarjetas div.hov-noticia dentro de un enlace."""
    def parsear(soup):
        out = []
        for d in soup.select("div.hov-noticia"):
            a = d.find_parent("a", href=True)
            if not a:
                continue
            url = L.url_limpia(base, a["href"])
            # las notas de medios externos viven en /prensa/ y no son comunicados propios
            if not L.mismo_sitio(url, base) or urlparse(url).path.startswith("/prensa"):
                continue
            t = d.select_one("time")
            fecha = fecha_iso((t.get("datatime") or t.get("datetime") or t.get_text()) if t else "")
            h = d.select_one("h3")
            out.append({"url": url, "titulo": h.get_text(" ", strip=True) if h else "",
                        "fecha": fecha, "resumen": ""})
        return out
    return parsear


def listado_minsal(base):
    """Ministerio de Salud: artículos de Elementor (article.elementor-post)."""
    def parsear(soup):
        out = []
        for art in soup.select("article.elementor-post"):
            a = art.select_one(".elementor-post__title a[href]")
            if a:
                f = art.select_one(".elementor-post-date")
                out.append(_item(base, a, fecha=fecha_iso(f.get_text(" ")) if f else ""))
        return out
    return parsear


def listado_minciencia(base):
    """MinCTCI: tarjetas .nt-card-body más la nota destacada fija (article.nt-dest)."""
    def parsear(soup):
        out = []
        for c in soup.select("article.nt-dest, .nt-card-body"):
            a = c.select_one("h2 a[href], h3 a[href]") or c.select_one("a.nt-dest-img[href]")
            if not a and c.name == "article":
                a = c.select_one("a[href]")
            if not a:
                continue
            h = c.select_one("h2, h3")
            t = c.select_one("time")
            p = c.select_one("p.nt-dest-txt") or c.select_one("p:not(.eyebrow)")
            out.append({"url": L.url_limpia(base, a["href"]),
                        "titulo": h.get_text(" ", strip=True) if h else "",
                        "fecha": fecha_iso((t.get("datetime") or t.get_text()) if t else ""),
                        "resumen": p.get_text(" ", strip=True) if p else ""})
        return out
    return parsear


# ---------------------------------------------------------------------------
# Notas: fecha y cuerpo
# ---------------------------------------------------------------------------
def fecha_gobcl(soup):
    m = soup.select_one("div.post div.texto span.meta")
    return fecha_iso(m.get_text(" ")) if m else ""


def fecha_defensoria(soup):
    t = soup.select_one("div.col-md-9 > section time")
    f = fecha_iso((t.get("datatime") or t.get("datetime") or t.get_text()) if t else "")
    return f or fecha_meta(soup)


def limpiar_defensoria(soup):
    """Quita de la sección el encabezado (título, imagen, fecha y botones de compartir)."""
    sec = soup.select_one("div.col-md-9 > section")
    if sec is None:
        return
    for hijo in list(sec.find_all(recursive=False)):
        if hijo.name in ("p", "ul", "ol", "h3", "h4", "table", "blockquote"):
            break
        hijo.decompose()


@dataclass
class Sitio:
    slug: str
    institucion: str
    paginas: list  # [(etiqueta, función n -> URL)]
    listado: Callable
    cuerpo: str
    fecha: Callable = fecha_meta
    titulo: str = "h1"
    quitar: tuple = ()
    previo: Callable | None = None
    bajada_listado: bool = False
    hilos: int = 2
    pausa_min: float = 0.0
    base: str = ""


SITIOS = {s.slug: s for s in [
    Sitio("minjusticia", "Ministerio de Justicia y Derechos Humanos",
          [("noticias", lambda n: "https://www.minjusticia.gob.cl/category/noticias/" + (f"page/{n}/" if n > 1 else ""))],
          listado_gobcl, "div.post div.texto div.contenido", fecha=fecha_gobcl,
          titulo="div.post div.texto .title", base="https://www.minjusticia.gob.cl/"),
    Sitio("sml", "Servicio Médico Legal",
          [("noticias", lambda n: "https://www.sml.gob.cl/index.php/category/noticias/" + (f"page/{n}/" if n > 1 else ""))],
          listado_gobcl, "div.post div.texto div.contenido", fecha=fecha_gobcl,
          titulo="div.post div.texto .title", base="https://www.sml.gob.cl/"),
    Sitio("defensoria_ninez", "Defensoría de la Niñez",
          [("noticias", lambda n: "https://www.defensorianinez.cl/noticias/" + (f"page/{n}/" if n > 1 else ""))],
          listado_defensoria, "div.col-md-9 > section", fecha=fecha_defensoria,
          titulo="div.col-md-9 > section h2", previo=limpiar_defensoria, hilos=1,
          base="https://www.defensorianinez.cl/"),
    Sitio("minsal", "Ministerio de Salud",
          [("noticias", lambda n: "https://www.minsal.cl/category/noticias/" + (f"page/{n}/" if n > 1 else "")),
           ("noticias regionales", lambda n: "https://www.minsal.cl/category/noticiasregionales/" + (f"page/{n}/" if n > 1 else ""))],
          listado_minsal, ".elementor-widget-theme-post-content", hilos=1,
          base="https://www.minsal.cl/"),
    Sitio("minciencia", "Ministerio de Ciencia, Tecnología, Conocimiento e Innovación",
          [("noticias", lambda n: "https://minctci.gob.cl/noticias/" + (f"?pagina={n}" if n > 1 else ""))],
          listado_minciencia, "article.nt-art .nt-body", titulo="article.nt-art h1",
          bajada_listado=True, base="https://minctci.gob.cl/"),
]}


def main():
    ap = comun.argumentos("Scraper de prensa para sitios con listado HTML paginado.")
    ap.add_argument("sitio", choices=sorted(SITIOS))
    ap.add_argument("--completo", action="store_true", help="recorre todas las páginas (sin corte por novedad)")
    ap.set_defaults(hilos=None)
    args = ap.parse_args()
    cfg = SITIOS[args.sitio]
    hilos = args.hilos or cfg.hilos
    comun.configurar_log()
    s = comun.sesion()
    salida = comun.SalidaCSV(args.salida or comun.DATA / f"{cfg.slug}.csv")

    def procesar(it):
        soup = L.bajar_soup(s, it["url"], args.pausa)
        if soup is None:
            return
        # título y fecha se leen antes de limpiar el encabezado de la nota
        h = soup.select_one(cfg.titulo)
        titulo = (h.get_text(" ", strip=True) if h else "") or it["titulo"]
        fecha = cfg.fecha(soup) or it["fecha"]
        if cfg.previo:
            cfg.previo(soup)
        texto = L.cuerpo_de(soup, cfg.cuerpo, cfg.quitar)
        if not L.valido(texto):
            log.warning("Sin cuerpo utilizable: %s", it["url"])
            return
        resumen = it["resumen"] if cfg.bajada_listado and it["resumen"] else L.resumen_de(texto)
        salida.agregar(L.fila(fecha, cfg.institucion, titulo, resumen, it["url"], texto))

    items, vistas = [], set()
    try:
        for etiqueta, url_pagina in cfg.paginas:
            falta = args.muestra - len(items) if args.muestra else 0
            if args.muestra and falta <= 0:
                break
            items += L.recorrer_listado(
                s, salida, url_pagina, cfg.listado(cfg.base), args.pausa, completo=args.completo,
                muestra=falta, etiqueta=f"{cfg.slug}/{etiqueta}", seen=vistas)
        L.ejecutar(items, procesar, salida, hilos, etiqueta=cfg.slug)
    except L.Desafio as e:
        log.error("Desafío anti-bots en %s: se detiene %s sin intentar saltarlo", e, cfg.slug)
        salida.cerrar()
        sys.exit(2)


if __name__ == "__main__":
    main()
