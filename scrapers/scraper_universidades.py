#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de familia: noticias institucionales de universidades estatales cuyo portal
no tiene una API WordPress abierta (requests + BeautifulSoup).

Uso:
    python scrapers/scraper_universidades.py <sitio> [--muestra N] [--completo]
                                             [--pagina-inicial N] [--salida RUTA]
                                             [--hilos 2] [--pausa 1.0]
    python scrapers/scraper_universidades.py --lista

Sitios y fuentes (cada uno escribe data/<sitio>.csv):
    uchile    Universidad de Chile            uchile.cl/noticias/archivo/organismo/3065/N (15 por página;
                                              incluye las columnas de opinión que publica el portal)
    usach     Universidad de Santiago         usach.cl/lista-noticias?page=N-1 (12 por página; el listado
                                              llega hasta mayo de 2024)
    uv        Universidad de Valparaíso       uv.cl/archivo-noticias-uv?start=(N-1)*12 (Joomla)
    unap      Universidad Arturo Prat         actualidad.unap.cl (Prontus): ocho listados por
                                              categoría (taxport_87_<cat>__N), unidos y deduplicados
    userena   Universidad de La Serena        userena.cl/actualidad.html?start=(N-1)*6 (Joomla; el
                                              dominio con www no responde)
    utalca    Universidad de Talca            API REST del tipo propio "noticias" para el listado
                                              (el cuerpo viene vacío) y la página de cada nota

Estrategia: se recorre el listado desde lo más reciente; de cada nota se entra a su página para
tomar el texto íntegro. Reanudable (las URLs ya presentes en el CSV no se vuelven a bajar) y, salvo
--completo, el recorrido se corta tras 3 páginas seguidas sin notas nuevas. --pagina-inicial permite
probar páginas antiguas del listado.
"""
import html
import re
import sys
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import comun
import listados as L
from comun import fecha_iso, fecha_meta, limpiar, log, texto_de


def txt(el) -> str:
    return limpiar(el.get_text(" ")) if el else ""


def sin(el, *selectores):
    """Quita de `el` los bloques que calzan con los selectores (menús, fechas, botones)."""
    for sel in selectores:
        for x in el.select(sel):
            x.decompose()
    return el


# ---------------------------------------------------------------------------
# Listados: cada función devuelve [{url, titulo, fecha, resumen}] de una página
# ---------------------------------------------------------------------------
def listado_uchile(base):
    def parsear(soup):
        out = []
        for a in soup.select("a.mod__list-item[href]"):
            h = a.select_one("h3")
            p = a.select_one("p.mod_last-news__item-teaser")
            out.append({"url": L.url_limpia(base, a["href"]), "titulo": txt(h), "fecha": "",
                        "resumen": txt(p)})
        return out
    return parsear


def listado_usach(base):
    def parsear(soup):
        out = []
        for li in soup.select(".noticias-list li.col-lg-4"):
            a = li.select_one("a[href]")
            if not a:
                continue
            t = li.select_one("time[datetime]")
            out.append({"url": L.url_limpia(base, a["href"]), "titulo": txt(li.select_one(".caja_t a")),
                        "fecha": fecha_iso(t["datetime"]) if t else "", "resumen": ""})
        return out
    return parsear


def listado_uv(base):
    def parsear(soup):
        out = []
        for a in soup.select("a.uk-link-toggle[href*='/archivo-noticias-uv/']"):
            out.append({"url": L.url_limpia(base, a["href"]), "titulo": txt(a.select_one(".el-title")),
                        "fecha": fecha_iso(txt(a.select_one(".el-meta"))), "resumen": ""})
        return out
    return parsear


def listado_unap(base):
    def parsear(soup):
        out = []
        for a in soup.select(".uap-port-secc-art-tit a[href*='/artic/']"):
            tarjeta = a.find_parent("div", class_="col-md-4") or a.parent
            f = tarjeta.select_one(".uap-port-nota-fecha span")
            out.append({"url": L.url_limpia(base, a["href"]), "titulo": txt(a),
                        "fecha": fecha_iso(txt(f)), "resumen": ""})
        return out
    return parsear


def listado_userena(base):
    def parsear(soup):
        out = []
        for art in soup.select("article.item"):
            a = art.select_one("h2 a[href]")
            if not a:
                continue
            t = art.select_one("time[datetime]")
            out.append({"url": L.url_limpia(base, a["href"]), "titulo": txt(a),
                        "fecha": fecha_iso(t["datetime"]) if t else "", "resumen": ""})
        return out
    return parsear


# ---------------------------------------------------------------------------
# Notas: devuelven {titulo, fecha, resumen, contenido} (vacío lo que la página no trae)
# ---------------------------------------------------------------------------
def nota_uchile(soup):
    t = soup.select_one("time[itemprop=datePublished]")
    return {"titulo": txt(soup.select_one("h1.content__title")),
            "fecha": fecha_iso((t.get("datetime") or t.get_text()) if t else "") or fecha_meta(soup),
            "resumen": txt(soup.select_one(".content__teaser")),
            "contenido": texto_de(soup.select_one(".content__description"))}


def nota_usach(soup):
    cuerpo = soup.select_one(".block-field-blocknodearticlebody .field--name-body")
    return {"titulo": txt(soup.select_one("article.node h1, h1")), "fecha": fecha_meta(soup),
            "resumen": txt(soup.select_one(".field--name-field-bajada")),
            "contenido": texto_de(cuerpo)}


def nota_uv(soup):
    col = soup.select_one("div.uk-width-3-4\\@m")
    if col is not None:
        sin(col, ".uk-text-muted")          # línea con la fecha
    return {"titulo": txt(soup.select_one("main h1, h1")), "fecha": "", "resumen": "",
            "contenido": texto_de(col)}


def nota_unap(soup):
    f = soup.select_one(".uap-port-nota-fecha")
    return {"titulo": txt(soup.select_one("h1.titular")), "fecha": fecha_iso(txt(f)),
            "resumen": txt(soup.select_one(".actualidad-artic-bajada")),
            "contenido": texto_de(soup.select_one("div.CUERPO"))}


def nota_userena(soup):
    return {"titulo": txt(soup.select_one("article.item-page h2[itemprop=name]")),
            "fecha": fecha_meta(soup), "resumen": "",
            "contenido": texto_de(soup.select_one("[itemprop=articleBody]"))}


def nota_utalca(soup):
    col = soup.select_one(".blog-details")
    if col is not None:
        # encabezado (fecha, autor, categoría, título, bajada) y botones de compartir; la imagen
        # no se toca porque el HTML de la página deja el cuerpo anidado dentro de ella
        sin(col, ".entry-meta", "h5", ".the-excerpt", ".rrss_post")
    return {"titulo": "", "fecha": "", "resumen": "", "contenido": texto_de(col)}


# ---------------------------------------------------------------------------
# Listado por API (Universidad de Talca): el tipo propio "noticias" entrega fecha,
# título y bajada; el cuerpo hay que leerlo de la página de cada nota.
# ---------------------------------------------------------------------------
def listar_utalca(s, salida, args):
    base = "https://www.utalca.cl/wp-json/wp/v2/noticias"
    por_pagina = min(100, args.muestra) if args.muestra else 100
    items, pagina, total = [], 1, None
    while True:
        params = {"per_page": por_pagina, "page": pagina, "orderby": "date", "order": "desc",
                  "_fields": "date,link,title,excerpt"}
        r = comun.obtener(s, base, args.pausa, params=params)
        if r is None:
            break
        total = total or int(r.headers.get("X-WP-TotalPages", "1"))
        datos = r.json()
        if not datos:
            break
        nuevas = 0
        for d in datos:
            url = d["link"]
            if salida.ya_esta(url):
                continue
            nuevas += 1
            items.append({
                "url": url, "fecha": fecha_iso(d.get("date", "")),
                "titulo": limpiar(html.unescape(BeautifulSoup(d["title"]["rendered"], "html.parser").get_text())),
                "resumen": limpiar(BeautifulSoup(d["excerpt"]["rendered"], "html.parser").get_text())})
        log.info("utalca: página %d/%s, %d notas nuevas", pagina, total, nuevas)
        if args.muestra and len(items) >= args.muestra:
            break
        if not args.completo and not nuevas and salida.previas:
            break
        if pagina >= total:
            break
        pagina += 1
    return items[:args.muestra] if args.muestra else items


# ---------------------------------------------------------------------------
@dataclass
class Sitio:
    slug: str
    institucion: str
    base: str
    nota: Callable
    paginas: list = field(default_factory=list)   # [(etiqueta, función n -> URL)]
    listado: Callable | None = None
    listar: Callable | None = None                # listado propio (reemplaza a paginas)
    hilos: int = 2


TAX_UNAP = {"universidad": 126, "estudiantes": 127, "docencia": 128, "vinculacion": 129,
            "investigacion": 130, "internacional": 133, "cultura": 132, "comunidad": 135}

SITIOS = {s.slug: s for s in [
    Sitio("uchile", "Universidad de Chile", "https://uchile.cl/", nota_uchile,
          [("noticias", lambda n: f"https://uchile.cl/noticias/archivo/organismo/3065/{n}")],
          listado_uchile),
    Sitio("usach", "Universidad de Santiago de Chile", "https://www.usach.cl/", nota_usach,
          [("noticias", lambda n: f"https://www.usach.cl/lista-noticias?page={n - 1}")],
          listado_usach),
    Sitio("uv", "Universidad de Valparaíso", "https://www.uv.cl/", nota_uv,
          [("noticias", lambda n: f"https://www.uv.cl/archivo-noticias-uv?start={(n - 1) * 12}")],
          listado_uv),
    Sitio("unap", "Universidad Arturo Prat", "https://www.unap.cl/", nota_unap,
          [(k, (lambda c: lambda n: f"https://www.unap.cl/prontus_unap/site/tax/port/all/taxport_87_{c}__{n}.html")(c))
           for k, c in TAX_UNAP.items()],
          listado_unap),
    Sitio("userena", "Universidad de La Serena", "https://userena.cl/", nota_userena,
          [("actualidad", lambda n: f"https://userena.cl/actualidad.html?start={(n - 1) * 6}")],
          listado_userena),
    Sitio("utalca", "Universidad de Talca", "https://www.utalca.cl/", nota_utalca,
          listar=listar_utalca),
]}


def main():
    ap = comun.argumentos("Noticias de universidades estatales sin API WordPress.")
    ap.add_argument("sitio", nargs="?", choices=sorted(SITIOS))
    ap.add_argument("--lista", action="store_true", help="muestra los sitios configurados")
    ap.add_argument("--completo", action="store_true", help="recorre todas las páginas (sin corte por novedad)")
    ap.add_argument("--pagina-inicial", type=int, default=1, help="primera página del listado (pruebas)")
    args = ap.parse_args()
    if args.lista or not args.sitio:
        for k, v in SITIOS.items():
            print(f"  {k:10} {v.institucion}")
        return
    cfg = SITIOS[args.sitio]
    comun.configurar_log()
    s = comun.sesion()
    salida = comun.SalidaCSV(args.salida or comun.DATA / f"{cfg.slug}.csv")
    log.info("%s: %s (%d notas ya guardadas)", cfg.slug, cfg.institucion, salida.previas)

    def procesar(it):
        soup = L.bajar_soup(s, it["url"], args.pausa)
        if soup is None:
            return
        d = cfg.nota(soup)
        texto = d["contenido"]
        if not L.valido(texto):
            log.warning("Sin cuerpo utilizable: %s", it["url"])
            return
        titulo = d["titulo"] or it["titulo"]
        # la fecha de la página manda; el listado y la URL son respaldo
        fecha = (d["fecha"] or it["fecha"] or fecha_meta(soup)
                 or (comun.fecha_de_url(it["url"]) or ""))
        if not fecha:
            m = re.search(r"/artic/(\d{4})(\d{2})(\d{2})/", it["url"])
            fecha = "-".join(m.groups()) if m else ""
        resumen = d["resumen"] or it["resumen"] or L.resumen_de(texto)
        salida.agregar(L.fila(fecha, cfg.institucion, titulo, resumen, it["url"], texto))

    items, vistas = [], set()
    try:
        if cfg.listar:
            items = cfg.listar(s, salida, args)
        else:
            for etiqueta, url_pagina in cfg.paginas:
                falta = args.muestra - len(items) if args.muestra else 0
                if args.muestra and falta <= 0:
                    break
                inicial = args.pagina_inicial
                items += L.recorrer_listado(
                    s, salida, lambda n: url_pagina(n + inicial - 1), cfg.listado(cfg.base), args.pausa,
                    completo=args.completo, muestra=falta, etiqueta=f"{cfg.slug}/{etiqueta}", seen=vistas)
        L.ejecutar(items, procesar, salida, args.hilos or cfg.hilos, etiqueta=cfg.slug)
    except L.Desafio as e:
        log.error("Desafío anti-bots en %s: se detiene %s sin intentar saltarlo", e, cfg.slug)
        salida.cerrar()
        sys.exit(2)


if __name__ == "__main__":
    main()
