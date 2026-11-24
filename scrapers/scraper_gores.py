#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Gobiernos Regionales con sala de prensa propia (sin API WordPress): listado paginado
en HTML y nota individual, reutilizando el recorrido de scraper_listados_institucionales.

Uso:
    python scrapers/scraper_gores.py <sitio> [--muestra N] [--salida RUTA] [--hilos 2]
                                     [--pausa 1.0] [--completo] [--pagina-inicial N]

Sitios y fuentes (cada uno escribe data/<sitio>.csv):
    gore_arica        Arica y Parinacota  CMS Newtenberg, .../site/tax/port/all/taxport_10___N.html (desde 1; 404 al terminar)
    gore_antofagasta  Antofagasta         CMS Newtenberg, .../site/tax/port/all/taxport_1___N.html (desde 1)
    gore_coquimbo     Coquimbo            CMS Newtenberg, taxport_24___N (administración actual) y taxport_21___N (anterior)
    gore_valparaiso   Valparaíso          PHP propio, listarNoticias.php?idPag=N (desde 1); la fecha está solo en la nota
    gore_ohiggins     O'Higgins           Joomla, /noticias/noticias-gore?start=K (de 5 en 5)
    gore_araucania    La Araucanía        Laravel, /noticias?page=N (desde 1)
    gore_aysen        Aysén               WordPress sin API ni sitemap al día, /category/noticias/page/N/ (404 al terminar)

Los demás gobiernos regionales se cubren con otras familias:
    gore_maule, gore_nuble  -> scraper_wp_api.py
    gore_loslagos           -> sin cobertura (el sitio exige un desafío anti-bots)

El modo incremental, la reanudación por URL y el marcador "<csv>.completo" son los de
scraper_listados_institucionales.py. Los listados de Newtenberg devuelven la última página
una y otra vez cuando se pide una inexistente; el recorrido lo detecta y termina.
"""
import re
import sys

import scraper_listados_institucionales as LI
from comun import fecha_iso, fecha_meta, limpiar, obtener
from scraper_listados_institucionales import normalizar, txt


# ---------------------------------------------------------------------------
# Lectores de listado: devuelven [{url, titulo, fecha, resumen}]
# ---------------------------------------------------------------------------
def listado_newtenberg(soup, base):
    """Antofagasta y Coquimbo: h2.titular con fecha (span.fecha) y bajada (p.bajada) como hermanos."""
    out = []
    for h in soup.select("h2.titular"):
        a = h.select_one("a[href]")
        if not a:
            continue
        cont = h.parent
        out.append({"url": normalizar(base, a["href"]), "titulo": txt(a),
                    "fecha": fecha_iso(txt(cont.select_one("span.fecha"))),
                    "resumen": txt(cont.select_one("p.bajada"))})
    return out


def listado_arica(soup, base):
    out = []
    for c in soup.select("article.taxport-item"):
        a = c.select_one(".taxport-title a[href]")
        if not a:
            continue
        out.append({"url": normalizar(base, a["href"]), "titulo": txt(a),
                    "fecha": fecha_iso(txt(c.select_one(".taxport-date"))), "resumen": ""})
    return out


def _fecha_de_imagen(src: str) -> str:
    """Fecha en el nombre de la foto de portada: portadaAAAAMMDDhhmmss.jpg o DDMMAAAA_1200.jpg."""
    m = re.search(r"portada(\d{4})(\d{2})(\d{2})", src or "")
    if m:
        return fecha_iso("-".join(m.groups()))
    m = re.search(r"/(\d{2})(\d{2})(\d{4})_", src or "")
    return fecha_iso(f"{m.group(3)}-{m.group(2)}-{m.group(1)}") if m else ""


def listado_valparaiso(soup, base):
    """Cada nota aparece varias veces (título, tarjeta, "Ver noticia completa"): se queda con el mejor título.

    Las notas antiguas muestran "00 de  de 0000" como fecha; la fecha de respaldo sale de la foto."""
    por_url = {}
    for a in soup.select("a[href*='detalleNoticia.php?idNot=']"):
        url = normalizar(base, a["href"])
        t = txt(a)
        if t.lower().startswith("ver noticia") or "compartir" in t.lower():
            t = ""
        img = a.select_one("img[src]")
        antes = por_url.get(url, {"titulo": "", "respaldo": ""})
        por_url[url] = {"titulo": t if len(t) > len(antes["titulo"]) else antes["titulo"],
                        "respaldo": antes["respaldo"] or _fecha_de_imagen(img["src"] if img else "")}
    return [{"url": u, "titulo": d["titulo"], "fecha": "", "resumen": "", "fecha_respaldo": d["respaldo"]}
            for u, d in por_url.items()]


def listado_aysen(soup, base):
    """WordPress (tema Kadence) con la API desactivada y un sitemap desactualizado: se usa la categoría Noticias.

    La fecha del listado puede ser la de modificación; se deja de respaldo y se prefiere la de la nota."""
    out = []
    for c in soup.select("article.loop-entry"):
        a = c.select_one("h2.entry-title a[href]")
        if not a:
            continue
        t = c.select_one("time[datetime]")
        out.append({"url": normalizar(base, a["href"]), "titulo": txt(a), "fecha": "",
                    "resumen": txt(c.select_one(".entry-summary")),
                    "fecha_respaldo": fecha_iso(t["datetime"]) if t else ""})
    return out


def listado_ohiggins(soup, base):
    out = []
    for h in soup.select("h2[itemprop=name] a[href*='/noticias/noticias-gore/']"):
        cont = h.find_parent(attrs={"itemprop": "blogPost"}) or h.parent
        t = cont.select_one("time[datetime]")
        out.append({"url": normalizar(base, h["href"]), "titulo": txt(h),
                    "fecha": fecha_iso(t["datetime"]) if t else "", "resumen": ""})
    return out


def listado_araucania(soup, base):
    out = []
    for c in soup.select("div[id^=item_]"):
        a = c.select_one("a[href*='/noticias/20']")
        if not a:
            continue
        titulo = ""
        for x in c.select("a[href*='/noticias/20']"):
            titulo = titulo or txt(x)
        img = c.select_one("img[alt]")
        titulo = titulo or (limpiar(img["alt"]) if img else "")
        f = c.select_one("div[title]")
        out.append({"url": normalizar(base, a["href"]), "titulo": titulo,
                    "fecha": fecha_iso(f["title"] if f else ""), "resumen": ""})
    return out


class PaginasPorPaso:
    """Plantilla de URL para listados que se recorren por desplazamiento (start=0, 5, 10...)."""

    def __init__(self, url: str, paso: int):
        self.url, self.paso = url, paso

    def format(self, n: int) -> str:
        return self.url.format(n=(n - 1) * self.paso)


# ---------------------------------------------------------------------------
# Configuración por sitio
# ---------------------------------------------------------------------------
# secciones: (plantilla de URL de listado con {n}, número de la primera página)
# cuerpo / quitar / resumen: como en scraper_listados_institucionales
# fecha_nota, titulo_nota: selectores en la nota cuando el listado no trae fecha o título
SITIOS = {
    "gore_arica": dict(
        institucion="Gobierno Regional de Arica y Parinacota",
        secciones=[("https://www.gorearicayparinacota.cl/goreayp/site/tax/port/all/taxport_10___{n}.html", 1)],
        listado=listado_arica,
        cuerpo=["article.news-article div.news-body"],
        resumen="article.news-article p.lead-text",
        quitar=[]),
    "gore_antofagasta": dict(
        institucion="Gobierno Regional de Antofagasta",
        secciones=[("https://goreantofagasta.cl/goreantofagasta/site/tax/port/all/taxport_1___{n}.html", 1)],
        listado=listado_newtenberg,
        cuerpo=["div.CUERPO"],
        resumen="h2.bajada",
        quitar=[]),
    "gore_coquimbo": dict(
        institucion="Gobierno Regional de Coquimbo",
        secciones=[("https://www.gorecoquimbo.cl/gore/site/tax/port/all/taxport_24___{n}.html", 1),
                   ("https://www.gorecoquimbo.cl/gore/site/tax/port/all/taxport_21___{n}.html", 1)],
        listado=listado_newtenberg,
        cuerpo=["div.CUERPO"],
        resumen="h2.bajada",
        quitar=[]),
    "gore_aysen": dict(
        institucion="Gobierno Regional de Aysén del General Carlos Ibáñez del Campo",
        secciones=[("https://www.goreaysen.cl/category/noticias/page/{n}/", 1)],
        listado=listado_aysen,
        cuerpo=[".entry-content"],
        quitar=[]),
    "gore_valparaiso": dict(
        institucion="Gobierno Regional de Valparaíso",
        secciones=[("https://www.gobiernovalparaiso.cl/listarNoticias.php?idPag={n}", 1)],
        listado=listado_valparaiso,
        cuerpo=["div.editor-zone"],
        quitar=[],
        fecha_nota="article.page-news small.d-block",
        titulo_nota="article.page-news h1.news-title"),
    "gore_ohiggins": dict(
        institucion="Gobierno Regional del Libertador General Bernardo O'Higgins",
        secciones=[(PaginasPorPaso("https://www.goreohiggins.cl/noticias/noticias-gore?start={n}", 5), 1)],
        listado=listado_ohiggins,
        cuerpo=["[itemprop=articleBody]"],
        resumen="meta:og:description",
        quitar=[]),
    "gore_araucania": dict(
        institucion="Gobierno Regional de La Araucanía",
        secciones=[("https://gorearaucania.cl/noticias?page={n}", 1)],
        listado=listado_araucania,
        cuerpo=["article.ck-content"],
        quitar=[]),
}


# ---------------------------------------------------------------------------
# Nota individual
# ---------------------------------------------------------------------------
def _meta(soup, nombre: str) -> str:
    el = soup.select_one(f'meta[property="{nombre}"], meta[name="{nombre}"]')
    return limpiar(el["content"]) if el and el.get("content") else ""


def procesar_gore(item, s, cfg, salida, pausa):
    """Como LI.procesar, con fecha y título tomados de la nota cuando el listado no los trae."""
    url = item["url"]
    r = obtener(s, url, pausa)
    if r is None:
        LI.notas_fallidas.append(url)
        return
    soup = LI.sopa(r)
    fecha = item["fecha"]
    if not fecha and cfg.get("fecha_nota"):
        el = soup.select_one(cfg["fecha_nota"])
        fecha = fecha_iso(el.get("datetime") or txt(el)) if el else ""
    fecha = fecha or fecha_meta(soup) or item.get("fecha_respaldo", "")
    titulo = item["titulo"]
    if not titulo and cfg.get("titulo_nota"):
        titulo = txt(soup.select_one(cfg["titulo_nota"]))
    titulo = titulo or _meta(soup, "og:title")
    sel = cfg.get("resumen", "")
    if sel.startswith("meta:"):
        bajada = _meta(soup, sel[5:])
    else:
        bajada = txt(soup.select_one(sel)) if sel else ""
    cuerpo = LI.cuerpo_de(soup, cfg)
    if not cuerpo:
        LI.log.warning("Sin cuerpo: %s", url)
        LI.notas_fallidas.append(url)
        return
    if not fecha:
        LI.log.warning("Sin fecha válida: %s", url)
        LI.notas_fallidas.append(url)
        return
    if not titulo:
        LI.log.warning("Sin título: %s", url)
        LI.notas_fallidas.append(url)
        return
    resumen = bajada or item["resumen"] or limpiar(cuerpo)[:300]
    salida.agregar({"fecha": fecha, "institucion": cfg["institucion"], "titulo": titulo,
                    "resumen": resumen, "url": url, "contenido": cuerpo})


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in SITIOS:
        sys.exit("Uso: scraper_gores.py {" + ",".join(SITIOS) + "} [opciones]  (ver el docstring)")
    LI.SITIOS.update(SITIOS)
    LI.procesar = procesar_gore
    LI.main()


if __name__ == "__main__":
    main()
