#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de familia: organismos con listado paginado de noticias (requests + BeautifulSoup).

Uso:
    python scraper_listados_institucionales.py <sitio> [--muestra N] [--salida RUTA]
                                               [--hilos 2] [--pausa 1.0]
                                               [--completo] [--pagina-inicial N]

Sitios y fuentes:
    fiscalia   Ministerio Público                 fiscaliadechile.cl/actualidad/noticias/{nacionales,regionales}?page=N (desde 0)
    sea        Servicio de Evaluación Ambiental   sea.gob.cl/noticias?page=N (desde 0)
    patrimonio Servicio Nacional del Patrimonio   patrimoniocultural.gob.cl/noticias?page=N (desde 0; termina con página vacía)
    injuv      Instituto Nacional de la Juventud  injuv.gob.cl/personas/noticias?page=N (desde 0)
    sence      Servicio Nac. de Capacitación      sence.gob.cl/personas/noticias?page=N (desde 0)
    fosis      Fondo de Solidaridad e Inversión   fosis.gob.cl/es/noticias/lista_noticias/?page=N (desde 1)
    senama     Servicio Nacional del Adulto Mayor senama.gob.cl/noticias?page=N (desde 1)
    conadi     Corp. Nac. de Desarrollo Indígena  conadi.gob.cl/noticias?page=N (desde 1)
    senadis    Servicio Nacional de la Discapac.  senadis.gob.cl/{sala_prensa,participacion,endisc}/noticias/page/N (desde 1;
                                                  el mismo ID de nota puede repetirse entre secciones y se guarda una vez)

    Con el lector genérico por_enlaces (enlaces que calzan con la forma de URL de las notas; título,
    fecha y cuerpo desde la nota): Gobierno Digital, Laboratorio de Gobierno, CNEP, Subsecretarías
    de Defensa y de las FF.AA., CFA, SBAP, CAPREDENA, INN, CIREN, INFOR, INAPI, UAF, AGCID, Consejo
    de Auditoría Interna, ECONSSA y las empresas portuarias de Valparaíso, Arica, Chacabuco y Puerto
    Montt. Opciones: paso (listados por desplazamiento, ?start=N) y paginas (páginas enumeradas en
    el menú de la primera, sin número).

Estrategia: se recorre el listado página a página (la más reciente primero); de cada página
se bajan en paralelo las notas que aún no están en el CSV, entrando al enlace de cada una para
extraer el texto íntegro. Datos del listado: título, fecha y, si existe, bajada (resumen);
cuando el listado no trae bajada, el resumen son los primeros ~300 caracteres del cuerpo.

Reanudación y modo incremental:
  - Las URLs ya presentes en el CSV no se vuelven a bajar.
  - Al terminar un recorrido completo se deja un archivo marcador "<csv>.completo". Si existe,
    las corridas siguientes se detienen tras 3 páginas seguidas sin notas nuevas (actualización
    incremental). Sin marcador, o con --completo, se recorre todo el listado; así una corrida
    interrumpida se retoma sin cortarse antes de tiempo.
  - Con --muestra N se toman solo las N notas nuevas más recientes y no se escribe el marcador.
  - --pagina-inicial y --seccion permiten probar páginas antiguas o una sola sección (sin marcador).

Fechas: se toman del listado. En SENAMA, CONADI y SENCE las notas más antiguas llevan la fecha de
migración del sitio; se conserva la del listado (no se inventa otra) y al cerrar se informa cuántas
notas caen en esas fechas.
"""
import re
import sys
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

import comun
from comun import (DATA, SalidaCSV, argumentos, configurar_log, cuerpo_por_densidad, en_paralelo,
                   fecha_iso, fecha_meta, limpiar, obtener, sesion, texto_de)

log = configurar_log("listados")

PAGINAS_SIN_NUEVAS = 3      # corte del modo incremental
FALLOS_SEGUIDOS = 3         # páginas de listado seguidas que fallan antes de abandonar la sección
notas_fallidas: list[str] = []   # notas que no se pudieron bajar (se reintentan en la próxima corrida)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def normalizar(base: str, href: str) -> str:
    """URL absoluta, siempre https y con la ruta codificada (hay slugs con '$' y comas)."""
    href = href.strip()
    if re.match(r"^/https?%3A", href, re.I):      # enlaces mal formados del sitio: "/http%3A//host/ruta"
        href = unquote(href[1:])
    u = urlsplit(urljoin(base, href))
    ruta = quote(u.path, safe="/%$,;=@&+:~!*'()-._")
    return urlunsplit(("https", u.netloc, ruta, u.query, ""))


def sopa(r) -> BeautifulSoup:
    return BeautifulSoup(r.content, "html.parser")


def txt(el) -> str:
    return limpiar(el.get_text(" ")) if el else ""


# ---------------------------------------------------------------------------
# Lectores de listado: devuelven [{url, titulo, fecha, resumen}]
# ---------------------------------------------------------------------------
def listado_fiscalia(soup, base):
    out = []
    for c in soup.select(".card-new"):
        a = c.select_one("h4.title a[href]")
        if not a:
            continue
        out.append({"url": normalizar(base, a["href"]), "titulo": txt(a),
                    "fecha": fecha_iso(txt(c.select_one("p.fecha"))),
                    "resumen": txt(c.select_one(".field--name-body"))})
    return out


def listado_sea(soup, base):
    out = []
    for c in soup.select(".views-row"):
        a = c.select_one(".views-field-title a[href]")
        if not a:
            continue
        t = c.select_one("time")
        fecha = fecha_iso(txt(t)) or fecha_iso(t.get("datetime", "") if t else "")
        out.append({"url": normalizar(base, a["href"]), "titulo": txt(a), "fecha": fecha, "resumen": ""})
    return out


def listado_patrimonio(soup, base):
    out = []
    for c in soup.select("article.node--noticia"):
        a = c.select_one("h2 a[href]")
        if not a or "/noticias/" not in a["href"]:
            continue
        out.append({"url": normalizar(base, a["href"]), "titulo": txt(a),
                    "fecha": fecha_iso(txt(c.select_one(".date_custom_published"))), "resumen": ""})
    return out


def listado_drupal7(soup, base):
    """INJUV y SENCE: tarjetas .cuerpoListaNoticiasHome con título, bajada y fecha."""
    out = []
    for c in soup.select(".cuerpoListaNoticiasHome"):
        a = c.select_one("h1 a[href]")
        if not a:
            continue
        baj = txt(c.select_one("h2")).rstrip()
        baj = re.sub(r"\s*(\.{3}|…)$", "", baj)     # la bajada del listado viene cortada con "..."
        out.append({"url": normalizar(base, a["href"]), "titulo": txt(a),
                    "fecha": fecha_iso(txt(c.select_one(".fechaListaNoticiasHome"))),
                    "resumen": baj})
    return out


def listado_fosis(soup, base):
    out = []
    for c in soup.select("article.news-card"):
        a = c.select_one("a.news-card-link[href]")
        if not a:
            continue
        out.append({"url": normalizar(base, a["href"]), "titulo": txt(c.select_one(".news-card-title")),
                    "fecha": fecha_iso(txt(c.select_one(".news-card-date"))),
                    "resumen": txt(c.select_one(".news-card-summary"))})
    return out


def listado_senama(soup, base):
    """SENAMA y CONADI: tarjetas .post.tarjeta con fecha en span.meta."""
    out = []
    for c in soup.select(".post.tarjeta"):
        a = c.select_one("h4.title a[href]")
        if not a:
            continue
        out.append({"url": normalizar(base, a["href"]), "titulo": txt(a),
                    "fecha": fecha_iso(txt(c.select_one("span.meta"))), "resumen": ""})
    return out


def listado_senadis(soup, base):
    out = []
    for c in soup.select(".noticias .nota"):
        a = c.select_one("h2 a[href]")
        if not a:
            continue
        out.append({"url": normalizar(base, a["href"]), "titulo": txt(a),
                    "fecha": fecha_iso(txt(c.select_one("p.fecha"))), "resumen": ""})
    return out


RE_FECHA_INICIAL = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{4}\s*")


def por_enlaces(patron, contenedor=None, excluir=None, fecha_url=None, fecha_en_enlace=False):
    """Lector genérico: los enlaces del listado que calzan con `patron` (dentro de `contenedor`,
    si se indica). El título es el texto más largo del enlace; la fecha sale de la URL
    (`fecha_url`, con grupos a, m y d), del texto del enlace o, si no, de la nota."""
    rx, rx_ex = re.compile(patron), re.compile(excluir) if excluir else None
    rx_f = re.compile(fecha_url) if fecha_url else None

    def leer(soup, base):
        raices = soup.select(contenedor) if contenedor else [soup]
        vistos: dict[str, str] = {}
        for raiz in raices:
            for a in raiz.find_all("a", href=True):
                u = normalizar(base, a["href"])
                if not rx.search(u) or (rx_ex and rx_ex.search(u)):
                    continue
                t = txt(a)
                if len(t) > len(vistos.get(u, "")):
                    vistos[u] = t
                else:
                    vistos.setdefault(u, t)
        out = []
        for u, t in vistos.items():
            fecha = ""
            if rx_f and (m := rx_f.search(u)):
                fecha = f"{m['a']}-{m['m']}-{m['d']}"
            elif fecha_en_enlace:
                fecha = fecha_iso(t)
            out.append({"url": u, "titulo": RE_FECHA_INICIAL.sub("", t), "fecha": fecha, "resumen": ""})
        return out
    return leer


# ---------------------------------------------------------------------------
# Configuración por sitio
# ---------------------------------------------------------------------------
# secciones: (plantilla de URL de listado con {n}, número de la primera página)
SITIOS = {
    "fiscalia": dict(
        institucion="Ministerio Público",
        secciones=[("https://www.fiscaliadechile.cl/actualidad/noticias/nacionales?page={n}", 0),
                   ("https://www.fiscaliadechile.cl/actualidad/noticias/regionales?page={n}", 0)],
        listado=listado_fiscalia,
        cuerpo=["div.field--name-body"],
        quitar=[".field--name-field-compartir", ".share", ".addtoany", ".social-share"]),
    "sea": dict(
        institucion="Servicio de Evaluación Ambiental",
        secciones=[("https://www.sea.gob.cl/noticias?page={n}", 0)],
        listado=listado_sea,
        cuerpo=["article.node--type-noticia .field-name-field-shared-body"],
        quitar=[]),
    "patrimonio": dict(
        institucion="Servicio Nacional del Patrimonio Cultural",
        secciones=[("https://www.patrimoniocultural.gob.cl/noticias?page={n}", 0)],
        listado=listado_patrimonio,
        cuerpo=["article.node--noticia .text-long"],
        quitar=[]),
    "injuv": dict(
        institucion="Instituto Nacional de la Juventud",
        secciones=[("https://www.injuv.gob.cl/personas/noticias?page={n}", 0)],
        listado=listado_drupal7,
        cuerpo=["div.contenido-noticia"],
        resumen=".noticias-detalle-interna",
        quitar=["h1", ".table-responsive", ".addthis_toolbox", ".compartir", ".redes-sociales"]),
    "sence": dict(
        institucion="Servicio Nacional de Capacitación y Empleo",
        secciones=[("https://sence.gob.cl/personas/noticias?page={n}", 0)],
        listado=listado_drupal7,
        cuerpo=["div.contenido-noticia"],
        resumen=".noticias-detalle-interna",
        quitar=["h1", ".table-responsive", ".addthis_toolbox", ".compartir", ".redes-sociales"],
        migracion=("2019-03-10", "2019-03-10")),
    "fosis": dict(
        institucion="Fondo de Solidaridad e Inversión Social",
        secciones=[("https://www.fosis.gob.cl/es/noticias/lista_noticias/?page={n}", 1)],
        listado=listado_fosis,
        cuerpo=["div.banners_fosis"],
        quitar=[]),
    "senama": dict(
        institucion="Servicio Nacional del Adulto Mayor",
        secciones=[("https://www.senama.gob.cl/noticias?page={n}", 1)],
        listado=listado_senama,
        cuerpo=["div.post div.contenido"],
        quitar=[],
        migracion=("2017-10-24", "2017-10-24")),
    "conadi": dict(
        institucion="Corporación Nacional de Desarrollo Indígena",
        secciones=[("https://www.conadi.gob.cl/noticias?page={n}", 1)],
        listado=listado_senama,
        cuerpo=["div.post div.contenido"],
        quitar=[],
        migracion=("2017-10-01", "2017-10-31")),
    "senadis": dict(
        institucion="Servicio Nacional de la Discapacidad",
        secciones=[("https://www.senadis.gob.cl/sala_prensa/noticias/page/{n}", 1),
                   ("https://www.senadis.gob.cl/participacion/noticias/page/{n}", 1),
                   ("https://www.senadis.gob.cl/endisc/noticias/page/{n}", 1)],
        id_nota=r"/noticias/(\d+)/",
        listado=listado_senadis,
        cuerpo=["div.cont_contenidos #contenido"],
        resumen="#contenido > ul",
        quitar=[".cont_img", "p.fecha", "h2.modifica", ".clear"]),
    # agenda digital y modernización del Estado
    "gobierno_digital": dict(
        institucion="Secretaría de Gobierno Digital",
        secciones=[("https://digital.gob.cl/media/noticias/?p={n}", 1)],
        listado=por_enlaces(r"digital\.gob\.cl/media/noticias/[^/?]+/?$"),
        cuerpo=["article.section-article div.section-body div.col-lg-8"], titulo=["h1"], quitar=[]),
    "laboratorio_gobierno": dict(
        institucion="Laboratorio de Gobierno",
        # Webflow: la paginación usa un parámetro propio; la fecha (d/m/a) viene en el enlace
        secciones=[("https://www.lab.gob.cl/noticias?ed09641b_page={n}", 1)],
        listado=por_enlaces(r"lab\.gob\.cl/noticias/[^/?]+$", fecha_en_enlace=True),
        cuerpo=["section.section-27 .rich_txt"], densidad=True, quitar=[]),
    "cnep": dict(
        institucion="Comisión Nacional de Evaluación y Productividad",
        # una página por período: la actual y tres de archivo (?foco=)
        secciones=[("https://cnep.cl/prensa/comunicados-de-prensa", 1)] + [
            ("https://cnep.cl/prensa/comunicados-de-prensa?foco=/prensa/comunicados-de-prensa/" + s, 1) for s in (
                "economista-de-cnep-jose-ignacio-donoso-acudira-becado-a-programa-de-magister-en-politicas-"
                "publicas-y-nuevas-tecnologias-en-sciencespo-paris",
                "comision-de-productividad-se-reunio-con-ministro-de-economia-juan-andres-fontaine",
                "comision-de-productividad-estuvo-presente-en-4o-summit-pais-digital")],
        listado=por_enlaces(r"cnep\.cl/prensa/comunicados-de-prensa/[^/?]+$"),
        cuerpo=["div.body-main-center div.cuerpo"], fecha=["article.nodo header time"],
        titulo=["og"], quitar=[]),
    "subsecretaria_defensa": dict(
        institucion="Subsecretaría de Defensa",
        # copia estática del sitio: la primera página no lleva número; la fecha va en la URL
        secciones=[("https://www.ssdefensa.cl/cat-noticias.html", 1),
                   ("https://www.ssdefensa.cl/cat-noticias-page-{n}.html", 2)],
        listado=por_enlaces(r"ssdefensa\.cl/n\d+_\d{2}-\d{2}-\d{4}\.html$",
                            fecha_url=r"_(?P<d>\d{2})-(?P<m>\d{2})-(?P<a>\d{4})\.html$"),
        cuerpo=["article.post section.body"], titulo=["article.post header h2.title"], quitar=[]),
    "subsecretaria_ffaa": dict(
        institucion="Subsecretaría para las Fuerzas Armadas",
        secciones=[("https://www.ssffaa.gob.cl/category/noticias/index.html", 1),
                   ("https://www.ssffaa.gob.cl/category/noticias/page/{n}/index.html", 2)],
        listado=por_enlaces(r"ssffaa\.gob\.cl/[^/]+/index\.html$", contenedor="article.mg-posts-sec-post"),
        cuerpo=[], densidad=True, fecha=[".mg-blog-post-box .mg-blog-date"], titulo=["h1"], quitar=[]),
    # servicios públicos
    "cfa": dict(
        institucion="Consejo Fiscal Autónomo",
        # sin número de página: el menú de la primera enumera las demás (?foco=<nota>)
        secciones=[("https://cfachile.cl/noticias", 1)], paginas="a.dropdown-item[href*='foco=']",
        listado=por_enlaces(r"cfachile\.cl/noticias/[^/?]+$", fecha_en_enlace=True),
        cuerpo=["article div.cuerpo"], titulo=["h1"], quitar=[]),
    "sbap": dict(
        institucion="Servicio de Biodiversidad y Áreas Protegidas",
        secciones=[("https://sbap.gob.cl/sala-de-prensa/noticias-y-comunicados", 1)],   # todo en una página
        listado=por_enlaces(r"/noticias-y-comunicados/detalle/\d{4}/\d{2}/\d{2}/",
                            fecha_url=r"/detalle/(?P<a>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})/"),
        cuerpo=[".content-com__txt"], titulo=["h1"], quitar=[]),
    "capredena": dict(
        institucion="Caja de Previsión de la Defensa Nacional",
        secciones=[("https://www.capredena.gob.cl/noticias/page/{n}", 1)],
        listado=por_enlaces(r"capredena\.gob\.cl/noticias/\d{4}/\d{2}/\d{2}/",
                            fecha_url=r"/noticias/(?P<a>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})/"),
        cuerpo=[".tarjeta .sfnewsContent"], quitar=[]),
    "inn": dict(
        institucion="Instituto Nacional de Normalización",
        secciones=[("https://www.inn.cl/listado-noticias/?page={n}", 1)],
        listado=por_enlaces(r"inn\.cl/contenedor-de-noticias/[^/?]+/?$"),
        cuerpo=["article.news-content"], fecha=["header.news-detail-header"], titulo=["h1"], quitar=[]),
    "ciren": dict(
        institucion="Centro de Información de Recursos Naturales",
        secciones=[("https://www.ciren.cl/noticias?pagina={n}", 1)],
        listado=por_enlaces(r"ciren\.cl/noticia\.php\?id=\d+$"),
        cuerpo=[".news-detail-body"], titulo=["h1"], quitar=[]),
    "infor": dict(
        institucion="Instituto Forestal",
        secciones=[("https://www.infor.cl/index.php/noticias?start={n}", 0)], paso=12,   # Joomla
        listado=por_enlaces(r"infor\.cl/index\.php/noticias/\d+-"),
        cuerpo=[], densidad=True, quitar=[]),
    "inapi": dict(
        institucion="Instituto Nacional de Propiedad Industrial",
        secciones=[("https://www.inapi.cl/sala-de-prensa/noticias/todas-las-noticias/{n}", 1)],
        listado=por_enlaces(r"inapi\.cl/sala-de-prensa/detalle-noticia/[^/?]+$"),
        cuerpo=[".texto-noticia"], fecha=["section.noticia-detalle"], quitar=[]),   # el h1 dice "Detalle"
    "uaf": dict(
        institucion="Unidad de Análisis Financiero",
        secciones=[("https://www.uaf.cl/es-cl/noticias-lista?page={n}", 1)],
        listado=por_enlaces(r"uaf\.cl/es-cl/noticia-detalle\?id=\d+$"),
        cuerpo=[".texto_detalle_noticia"], fecha=[".noticia-detalle .meta-info .fecha"], titulo=["h1"],
        quitar=[]),
    "agcid": dict(
        institucion="Agencia Chilena de Cooperación Internacional para el Desarrollo",
        secciones=[("https://www.agcid.gob.cl/sala-de-prensa?start={n}", 0)], paso=12,   # Joomla
        listado=por_enlaces(r"agcid\.gob\.cl/noticias/\d+-"),
        cuerpo=[], densidad=True, titulo=["h1"], quitar=[]),
    "auditoria_interna": dict(
        institucion="Consejo de Auditoría Interna General de Gobierno",
        # las notas cuelgan de la raíz del sitio: se toman las tarjetas del listado
        secciones=[("https://www.auditoriainternadegobierno.gob.cl/noticias/page/{n}/", 1)],
        listado=por_enlaces(r"auditoriainternadegobierno\.gob\.cl/[^/?#]+/?$", contenedor="div.box-notice",
                            excluir=r"\.cl/noticias/?$"),
        cuerpo=[], densidad=True, fecha=["p.date"], titulo=["h1"], quitar=[]),
    # empresas públicas
    "econssa": dict(
        institucion="Empresa Concesionaria de Servicios Sanitarios",
        secciones=[("https://www.econssachile.cl/comunicaciones/comunicados?start={n}", 0)], paso=10,
        listado=por_enlaces(r"econssachile\.cl/comunicaciones/comunicados/\d+-"),
        cuerpo=[".com-content-article__body"], quitar=[]),   # el h1 dice "Comunicados"
    "puerto_valparaiso": dict(
        institucion="Empresa Portuaria Valparaíso",
        secciones=[("https://www.puertovalparaiso.cl/noticias/p/{n}", 1)],
        listado=por_enlaces(r"puertovalparaiso\.cl/[^/?#]+$", contenedor=".img-card-item",
                            excluir=r"\.cl/noticias$"),
        cuerpo=[".articulo-body-text-inner"], fecha=[".articulo-header"], titulo=["h1", "og"], quitar=[]),
    "puerto_arica": dict(
        institucion="Empresa Portuaria Arica",
        # la paginación es por desplazamiento en la ruta: /novedades, /novedades/10, /novedades/20...
        secciones=[("https://puertoarica.cl/novedades", 1), ("https://puertoarica.cl/novedades/{n}", 1)],
        paso=10, listado=por_enlaces(r"puertoarica\.cl/novedad/[^/?]+$"),
        cuerpo=["article p.clearfix"], fecha=["article h5.text-info"], titulo=["article h2", "og"],
        quitar=[]),
    "puerto_chacabuco": dict(
        institucion="Empresa Portuaria Chacabuco",
        secciones=[("https://chacabucoport.cl/index.php/noticias/page/{n}/", 1)],
        listado=por_enlaces(r"chacabucoport\.cl/index\.php/\d{4}/\d{2}/\d{2}/[^/]+/?$",
                            fecha_url=r"/(?P<a>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})/"),
        cuerpo=["article .entry-content"], titulo=["h1"], quitar=[]),
    "puerto_montt": dict(
        institucion="Empresa Portuaria Puerto Montt",
        secciones=[("https://www.empormontt.cl/noticias?page={n}", 1)],   # page=0 repite la primera
        listado=por_enlaces(r"empormontt\.cl/noticias/[^/?]+$", excluir=r"licitacion"),   # avisos de licitación
        cuerpo=["div.single-excerpt"], fecha=[".entry-header .entry-meta"], titulo=["h1"], quitar=[]),
}


# ---------------------------------------------------------------------------
# Nota individual
# ---------------------------------------------------------------------------
def cuerpo_de(soup, cfg) -> str:
    """Texto íntegro: entre los elementos que calzan con los selectores, el de más texto.
    Con densidad=True y sin selector que calce, el bloque con más texto en párrafos."""
    mejor = ""
    for sel in cfg.get("cuerpo", []):
        for el in soup.select(sel):
            for q in cfg.get("quitar", []):
                for x in el.select(q):
                    x.decompose()
            t = texto_de(el)
            if len(t) > len(mejor):
                mejor = t
    if not mejor and cfg.get("densidad"):
        mejor = cuerpo_por_densidad(soup)
    return mejor


def fecha_de_nota(soup, cfg) -> str:
    """Fecha en la página de la nota: selectores del sitio (texto o atributo datetime) y metadatos."""
    for sel in cfg.get("fecha", []):
        for el in soup.select(sel):
            f = fecha_iso(el.get("datetime", "")) or fecha_iso(txt(el))
            if f:
                return f
    return fecha_meta(soup)


def titulo_de_nota(soup, cfg) -> str:
    for sel in cfg.get("titulo", []):
        if sel == "og":
            m = soup.select_one('meta[property="og:title"]')
            t = limpiar(m.get("content", "")) if m else ""
        else:
            t = txt(soup.select_one(sel))
        if t:
            return t
    return ""


def procesar(item, s, cfg, salida, pausa):
    url = item["url"]
    r = obtener(s, url, pausa)
    if r is None:
        notas_fallidas.append(url)
        return
    soup = sopa(r)
    fecha = item["fecha"] or fecha_de_nota(soup, cfg)
    titulo = titulo_de_nota(soup, cfg) or item["titulo"]
    bajada = txt(soup.select_one(cfg["resumen"])) if cfg.get("resumen") else ""
    cuerpo = cuerpo_de(soup, cfg)
    if not cuerpo:
        log.warning("Sin cuerpo: %s", url)
        notas_fallidas.append(url)
        return
    if not fecha:
        log.warning("Sin fecha válida: %s", url)
        notas_fallidas.append(url)
        return
    resumen = bajada or item["resumen"] or limpiar(cuerpo)[:300]
    salida.agregar({"fecha": fecha, "institucion": cfg["institucion"], "titulo": titulo,
                    "resumen": resumen, "url": url, "contenido": cuerpo})


# ---------------------------------------------------------------------------
# Recorrido
# ---------------------------------------------------------------------------
def recorrer_seccion(plantilla, primera, cfg, salida, s, a, incremental, tope):
    """Recorre un listado. Devuelve (notas procesadas, terminó de forma natural)."""
    n = a.pagina_inicial if a.pagina_inicial is not None else primera
    procesadas = 0
    sin_nuevas = 0
    fallos = 0
    vistas_ant: set[str] = set()
    # sitios cuyas secciones comparten numeración: una nota ya guardada bajo otra sección no se repite
    re_id = re.compile(cfg["id_nota"]) if cfg.get("id_nota") else None
    ids_vistos: set[str] = set()
    if re_id:
        ids_vistos = {m.group(1) for u in salida.urls if (m := re_id.search(u))}
    while True:
        url_lista = plantilla.format(n=n * cfg.get("paso", 1))   # paso: listados por desplazamiento (?start=)
        r = obtener(s, url_lista, a.pausa)
        if r is None:
            fallos += 1
            if fallos >= FALLOS_SEGUIDOS:
                log.error("Listado inaccesible en %s: se abandona la sección", url_lista)
                return procesadas, False
            n += 1
            continue
        fallos = 0
        items = cfg["listado"](sopa(r), url_lista)
        urls = {i["url"] for i in items}
        if not items or urls <= vistas_ant:
            log.info("Fin del listado en la página %d", n)
            return procesadas, True
        vistas_ant = urls
        # una nota que ya falló en esta corrida no se reintenta (p. ej. destacadas repetidas en cada página)
        nuevos = [i for i in items if not salida.ya_esta(i["url"]) and i["url"] not in notas_fallidas]
        if re_id:
            filtrados = []
            for i in nuevos:
                m = re_id.search(i["url"])
                if m and m.group(1) in ids_vistos:
                    continue
                if m:
                    ids_vistos.add(m.group(1))
                filtrados.append(i)
            nuevos = filtrados
        if tope:
            nuevos = nuevos[:max(0, tope - procesadas)]
        if nuevos:
            sin_nuevas = 0
            en_paralelo(nuevos, lambda it: procesar(it, s, cfg, salida, a.pausa), a.hilos,
                        cada=10 ** 9, etiqueta=f"página {n}")
            procesadas += len(nuevos)
            if n % 10 == 0:
                log.info("Página %d: %d notas nuevas hasta ahora (CSV: %d)", n, salida.nuevas,
                         len(salida.urls))
        else:
            sin_nuevas += 1
            if incremental and sin_nuevas >= PAGINAS_SIN_NUEVAS:
                log.info("Modo incremental: %d páginas seguidas sin notas nuevas, se detiene en la %d",
                         PAGINAS_SIN_NUEVAS, n)
                return procesadas, False
        if tope and procesadas >= tope:
            return procesadas, False
        n += 1


def secciones_de(cfg, s, pausa):
    """Las secciones fijas del sitio o, con paginas=<selector>, cada página que enumera el menú
    de paginación de la primera (listados sin número de página, p. ej. ?foco=<nota>)."""
    if not cfg.get("paginas"):
        return cfg["secciones"]
    primera = cfg["secciones"][0][0]
    urls = [primera]
    r = obtener(s, primera, pausa)
    if r is not None:
        for el in sopa(r).select(cfg["paginas"]):
            u = normalizar(primera, el["href"])
            if u not in urls:
                urls.append(u)
    return [(u.replace("{", "{{").replace("}", "}}"), 1) for u in urls]


def informar_migracion(ruta, rango):
    import csv
    with open(ruta, encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    k = sum(1 for r in filas if rango[0] <= r["fecha"] <= rango[1])
    log.info("Notas con fecha de migración del sitio (%s a %s): %d de %d", rango[0], rango[1], k, len(filas))


def main():
    ap = argumentos("Listados paginados de noticias de organismos públicos")
    ap.add_argument("sitio", choices=sorted(SITIOS))
    ap.add_argument("--completo", action="store_true", help="recorre todo el listado aunque ya esté completo")
    ap.add_argument("--pagina-inicial", type=int, default=None, help="primera página a recorrer (prueba)")
    ap.add_argument("--seccion", type=int, default=None,
                    help="solo la sección N del sitio, desde 0 (Ministerio Público: 0 nacionales, 1 regionales)")
    a = ap.parse_args()
    cfg = SITIOS[a.sitio]
    ruta = a.salida or DATA / f"{a.sitio}.csv"
    marcador = ruta.with_name(ruta.name + ".completo")
    incremental = (marcador.exists() and not a.completo and not a.muestra and a.pagina_inicial is None
                   and a.seccion is None)
    log.info("%s -> %s (%s)", cfg["institucion"], ruta,
             "incremental" if incremental else "recorrido completo")

    s = sesion()
    salida = SalidaCSV(ruta)
    log.info("URLs ya presentes: %d", salida.previas)
    terminado = True
    try:
        total = 0
        for i, (plantilla, primera) in enumerate(secciones_de(cfg, s, a.pausa)):
            if a.seccion is not None and i != a.seccion:
                continue
            tope = (a.muestra - total) if a.muestra else 0
            if a.muestra and tope <= 0:
                break
            n, natural = recorrer_seccion(plantilla, primera, cfg, salida, s, a, incremental, tope)
            total += n
            terminado = terminado and natural
    finally:
        filas = salida.cerrar()
    fallidas = len(set(notas_fallidas))
    if fallidas:
        log.warning("Notas que no se pudieron bajar: %d (se reintentan en la próxima corrida)", fallidas)
    if "migracion" in cfg:
        informar_migracion(ruta, cfg["migracion"])
    if not a.muestra and a.pagina_inicial is None and a.seccion is None and terminado \
            and fallidas <= max(2, salida.nuevas // 200):
        marcador.write_text("recorrido completo\n", encoding="utf-8")
    log.info("Listo: %d notas nuevas, %d en total", salida.nuevas, filas)


if __name__ == "__main__":
    main()
