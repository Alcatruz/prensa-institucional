#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de prensa para organismos con listados por año (HTML renderizado en servidor).

Uso:
    python scrapers/scraper_listados_web.py <sitio> [--muestra N] [--antiguas N]
                                            [--salida RUTA] [--hilos 2] [--pausa 1.0]

Sitios (cada uno escribe data/<sitio>.csv por defecto):
    dt                   Dirección del Trabajo (CMS Newtenberg; una página por año, 2004 en adelante)
    sernac               Servicio Nacional del Consumidor (Newtenberg; 2002 en adelante)
    suseso               Superintendencia de Seguridad Social (Newtenberg; año en curso + archivo 2020-2025)
    spensiones           Superintendencia de Pensiones (Newtenberg; "Ver todas", 2002 en adelante;
                         la nota no trae fecha: se toma del listado)
    subpesca             Subsecretaría de Pesca y Acuicultura (dos páginas: vigentes e históricas)
    servicio_proteccion  Servicio Nacional de Protección Especializada a la Niñez y Adolescencia
                         (Archivo;Periodo/AAAA, 2021 en adelante, más la sección Prensa)
    dipres               Dirección de Presupuestos (noticias + comunicados que traen texto HTML)
    cmf                  Comisión para el Mercado Financiero (una sola página con todos los comunicados)
    superdesalud         Superintendencia de Salud (WordPress; una página por año, 2005 en adelante)

Método: requests + BeautifulSoup. En cada sitio se descubren las páginas del listado, se extraen
fecha, título y resumen del listado y luego se entra a cada nota para obtener el texto íntegro.

Reanudable: las URLs que ya están en el CSV no se vuelven a descargar. Las notas cuyo cuerpo es
solo un PDF adjunto (comunicados antiguos de SP y CMF) se guardan con el contenido vacío o muy
corto; en DIPRES los comunicados sin texto HTML no se incluyen (su ficha solo enlaza el PDF) y,
como no quedan en el CSV, se vuelven a revisar en cada corrida.

--muestra N toma las N notas más recientes del listado; --antiguas N, las N más antiguas
(ambas son para pruebas).

Robots.txt: ninguna ruta usada está prohibida (DIPRES veta /dp2017/ y /w4; no se entra ahí).
"""
import re
import sys
import threading
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import comun
from comun import (DATA, SalidaCSV, argumentos, configurar_log, en_paralelo, fecha_iso,
                   fecha_meta, limpiar, obtener, sesion, texto_de)

log = configurar_log("listados_web")

PALABRAS_MIN_OMITIR = 40          # umbral de los comunicados de DIPRES sin texto HTML
LARGO_RESUMEN = 300               # resumen de respaldo: primeros N caracteres del cuerpo

RE_ISO_CLASE = re.compile(r"iso8601-(\d{4})(\d{2})(\d{2})T")
RE_ID_W3 = re.compile(r"w[34]-article-(\d+)")
RE_ARCHIVO = re.compile(r"^[^\n]{1,80}\.(?:jpe?g|png|gif|bmp|webp|pdf)$", re.I)
RE_SOLO_FECHA = re.compile(r"^\[?\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}\]?$")

RE_PESO_ARCHIVO = re.compile(r"^(?:jpe?g|png|gif|pdf|docx?|xlsx?|pptx?|zip)\s*\d+(?:[.,]\d+)?\s*[km]b$", re.I)

_hilo = threading.local()


def _sesion():
    """Una sesión por hilo."""
    if not hasattr(_hilo, "s"):
        _hilo.s = sesion()
    return _hilo.s


def sopa(r) -> BeautifulSoup:
    """HTML a partir de los bytes (los sitios declaran UTF-8 solo en el meta)."""
    return BeautifulSoup(r.content, "html.parser")


# ---------------------------------------------------------------------------
# Piezas de listado
# ---------------------------------------------------------------------------
def fecha_de_clase(el) -> str:
    """Fecha de un elemento Newtenberg con clase iso8601-AAAAMMDDT... ('' si no hay)."""
    if el is None:
        return ""
    for c in el.get("class") or []:
        m = RE_ISO_CLASE.match(c)
        if m:
            return fecha_iso("-".join(m.groups()))
    return ""


def anios(soup, base: str, patron_href: str) -> list[str]:
    """URLs de las páginas por año: enlaces cuyo texto es solo un año de 4 dígitos."""
    vistos, urls = set(), []
    for a in soup.select("a[href]"):
        if re.fullmatch(r"(19|20)\d{2}", a.get_text(strip=True)) and re.search(patron_href, a["href"]):
            u = urljoin(base, a["href"].split("#")[0])
            if u not in vistos:
                vistos.add(u)
                urls.append(u)
    return urls


def items_newtenberg(soup, pagina: str, enlace: str,
                     cuerpo=None, omitir_menor: int = 0, filtro_href=None, unir: bool = False) -> list[dict]:
    """Notas de una página de listado Newtenberg: `enlace` selecciona el enlace del título."""
    items, vistos = [], set()
    for a in soup.select(enlace):
        href = a.get("href", "")
        m = RE_ID_W3.search(href)
        if not m and not re.search(r"/\d+:", href):
            continue
        if filtro_href and not filtro_href(href):
            continue
        url = urljoin(pagina, href)
        if url in vistos:
            continue
        vistos.add(url)
        cont = a.find_parent(class_="recuadro") or a.find_parent("tr")
        fecha, resumen = "", ""
        if cont is not None:
            fecha = fecha_de_clase(cont.select_one(".fecha"))
            r = cont.select_one("p.abstract, .pn-xattr, p.card-text")
            resumen = limpiar(texto_de(r)) if r else ""
        items.append({"url": url, "titulo": limpiar(a.get_text(" ")), "fecha": fecha,
                      "resumen": resumen, "cuerpo": cuerpo, "omitir_menor": omitir_menor,
                      "unir": unir})
    return items


# ---------------------------------------------------------------------------
# Descubrimiento por sitio (devuelve notas con fecha/título/resumen del listado)
# ---------------------------------------------------------------------------
def _paginas(s, pausa, urls, extraer) -> list[dict]:
    """Descarga cada página de listado y junta las notas que devuelve `extraer(soup, url)`."""
    items = []
    for u in urls:
        r = obtener(s, u, pausa)
        if r is None:
            log.warning("No se pudo bajar el listado %s", u)
            continue
        nuevos = extraer(sopa(r), u)
        log.info("Listado %s: %d notas", u.rsplit("/", 1)[-1] or u, len(nuevos))
        items += nuevos
    return items


def desc_dt(s, pausa):
    canal = "http://www.dt.gob.cl/portal/1627/w3-channel.html"
    r = obtener(s, canal, pausa)
    urls = anios(sopa(r), canal, r"w3-propertyvalue-\d+") if r else []
    cuerpo = "div#article_i__dt_ar_articulo_cuerpo_1"
    return _paginas(s, pausa, urls, lambda sp, u: items_newtenberg(
        sp, u, ".titulo a[href*=w3-article]", cuerpo=cuerpo))


def desc_sernac(s, pausa):
    canal = "https://www.sernac.cl/portal/604/w3-channel.html"
    r = obtener(s, canal, pausa)
    urls = anios(sopa(r), canal, r"w3-propertyvalue-\d+") if r else []
    cuerpo = "#article_i__SNC_ar_cuerpo_1"
    return _paginas(s, pausa, urls, lambda sp, u: items_newtenberg(
        sp, u, ".titulo a[href*=w3-article]", cuerpo=cuerpo))


def desc_suseso(s, pausa):
    actual = "https://www.suseso.gob.cl/605/w3-propertyname-536.html"
    archivo = "https://www.suseso.gob.cl/605/w3-propertyvalue-10359.html"
    r = obtener(s, archivo, pausa)
    urls = [actual] + (anios(sopa(r), archivo, r"w3-propertyvalue-\d+") if r else [])
    cuerpo = "#ssArCuerpo"
    return _paginas(s, pausa, urls, lambda sp, u: items_newtenberg(
        sp, u, ".titulo a[href*=w3-article]", cuerpo=cuerpo))


def desc_spensiones(s, pausa):
    canal = "https://www.spensiones.cl/portal/institucional/594/w3-propertyvalue-9971.html"
    r = obtener(s, canal, pausa)
    if r is None:
        return []
    todas = next((a for a in sopa(r).select("a[href]") if a.get_text(strip=True) == "Ver todas"), None)
    if todas is None:
        log.error("No se encontró el enlace 'Ver todas' en %s", canal)
        return []
    pagina_todas = urljoin(canal, todas["href"])
    r = obtener(s, pagina_todas, pausa)
    urls = anios(sopa(r), pagina_todas, r"w3-multipropertyvalues-\d+-\d+") if r else []
    cuerpo = "#ar_noticias_resumen_y_cuerpo"
    return _paginas(s, pausa, urls, lambda sp, u: items_newtenberg(
        sp, u, ".titulo a[href*=w3-article]", cuerpo=cuerpo))


def desc_subpesca(s, pausa):
    urls = ["https://www.subpesca.cl/portal/difusion/Noticias/",
            "https://www.subpesca.cl/portal/difusion/Noticias/Noticias-historicas/"]
    cuerpo = "#article_i__sp_ar_articulo_cuerpo_1"
    vistos: set[str] = set()                      # la misma nota no está en ambas páginas; por si acaso

    def extraer(sp, u):
        nuevos = []
        for it in items_newtenberg(sp, u, "td.titulo a", cuerpo=cuerpo):
            id_ = re.search(r"/(\d+):", it["url"])
            clave = id_.group(1) if id_ else it["url"]
            if clave not in vistos:
                vistos.add(clave)
                nuevos.append(it)
        return nuevos
    return _paginas(s, pausa, urls, extraer)


def desc_servicio_proteccion(s, pausa):
    # Archivo por año (Noticias del Servicio y Prensa) y la sección Prensa, que lista aparte las
    # notas de medios. Desde 2026 las notas nuevas usan la URL corta /portal/<id>: (sin sección).
    urls = [f"https://www.servicioproteccion.gob.cl/portal/Noticias/Archivo;Periodo/{a}/"
            for a in range(2021, date.today().year + 1)]
    urls += ["https://www.servicioproteccion.gob.cl/portal/Noticias/Noticias-del-Servicio/",
             "https://www.servicioproteccion.gob.cl/portal/Noticias/Prensa/"]
    cuerpo = "div.ar-noticia-cuerpo div.cuerpo"
    return _paginas(s, pausa, urls, lambda sp, u: items_newtenberg(
        sp, u, ".titulo a", cuerpo=cuerpo,
        filtro_href=lambda h: re.search(r"/portal/(?:Noticias/[^/]+/)?\d+:", h)))


def desc_dipres(s, pausa):
    base = "https://www.dipres.gob.cl/598/"
    noticias = base + "w3-propertyvalue-21025.html"
    comunicados = base + "w3-propertyvalue-2135.html"
    items = []
    r = obtener(s, noticias, pausa)
    if r:
        items += items_newtenberg(sopa(r), noticias, ".recuadro .titulo a",
                                  cuerpo='div[id^="article_i__dp_ar_noticia_"]', unir=True)
        log.info("Dipres noticias: %d", len(items))
    r = obtener(s, comunicados, pausa)
    if r:
        com = items_newtenberg(sopa(r), comunicados, ".recuadro p.epigrafe a",
                               cuerpo='div[id^="article_i__dp_ar_articulo_prensa"]',
                               omitir_menor=PALABRAS_MIN_OMITIR)
        log.info("Dipres comunicados en el listado: %d (solo se guardan los que traen texto HTML)", len(com))
        items += com
    return items


def desc_cmf(s, pausa):
    pagina = "https://www.cmfchile.cl/portal/prensa/625/w4-propertyvalue-43349.html"
    r = obtener(s, pagina, pausa, timeout=120)
    if r is None:
        return []
    sp = sopa(r)
    items, vistos = [], set()
    for card in sp.select(".ntg-fecha-destacada"):
        a = card.select_one("h3 a[href*=w4-article]")
        if a is None:
            continue
        url = urljoin(pagina, a["href"])
        if url in vistos:
            continue
        vistos.add(url)
        t = card.select_one("time[datetime]")
        p = card.select_one("p.card-text")
        items.append({"url": url, "titulo": limpiar(a.get_text(" ")),
                      "fecha": fecha_iso(t["datetime"]) if t else "",
                      "resumen": limpiar(p.get_text(" ")) if p else "",
                      # el primer bloque viene vacío en algunas notas: se unen todos salvo los adjuntos
                      "cuerpo": ".ntg-articulo-cuerpo:not([id*=descarga])", "unir": True,
                      "omitir_menor": 0})
    log.info("CMF: %d comunicados en el listado", len(items))
    return items


def desc_superdesalud(s, pausa):
    inicio = "https://www.superdesalud.gob.cl/noticias/2025/"
    r = obtener(s, inicio, pausa)
    urls = []
    if r:
        urls = anios(sopa(r), inicio, r"/noticias/(19|20)\d{2}/?$")
    if not urls:
        urls = [f"https://www.superdesalud.gob.cl/noticias/{a}/" for a in range(date.today().year, 2004, -1)]

    def extraer(sp, u):
        items, vistos = [], set()
        for a in sp.select("main article a.news-card[href]"):
            url = urljoin(u, a["href"])
            if url in vistos:
                continue
            vistos.add(url)
            f = a.select_one(".news-card__date")
            t = a.select_one(".news-card__title")
            items.append({"url": url, "titulo": limpiar(t.get_text(" ")) if t else "",
                          "fecha": fecha_iso(f.get_text()) if f else "", "resumen": "",
                          "cuerpo": "div.main-content-post", "omitir_menor": 0})
        return items
    return _paginas(s, pausa, urls, extraer)


SITIOS = {
    "dt": ("Dirección del Trabajo", desc_dt),
    "sernac": ("Servicio Nacional del Consumidor", desc_sernac),
    "suseso": ("Superintendencia de Seguridad Social", desc_suseso),
    "spensiones": ("Superintendencia de Pensiones", desc_spensiones),
    "subpesca": ("Subsecretaría de Pesca y Acuicultura", desc_subpesca),
    "servicio_proteccion": ("Servicio Nacional de Protección Especializada a la Niñez y Adolescencia",
                            desc_servicio_proteccion),
    "dipres": ("Dirección de Presupuestos", desc_dipres),
    "cmf": ("Comisión para el Mercado Financiero", desc_cmf),
    "superdesalud": ("Superintendencia de Salud", desc_superdesalud),
}


# ---------------------------------------------------------------------------
# Nota individual
# ---------------------------------------------------------------------------
def limpiar_cuerpo(texto: str, titulo: str) -> str:
    """Quita líneas que no son cuerpo: título repetido, fecha suelta, nombres de archivo de imagen."""
    t = titulo.lower()
    lineas = []
    for ln in texto.replace("﻿", "").replace("​", "").split("\n"):
        ln = ln.strip()
        if re.match(r"revise\s+(el|la)\s+\w+\s+en\s+(formato\s+)?pdf\b", ln, re.I):
            continue                                    # enlace al PDF adjunto
        if (not ln or ln.lower() == t or RE_ARCHIVO.match(ln) or RE_SOLO_FECHA.match(ln)
                or RE_PESO_ARCHIVO.match(ln)):
            continue
        if re.fullmatch(r"(ver )?(el )?(comunicado|documento.*)", ln, re.I):
            continue
        if ln.lower().startswith("comunicado de prensa:") and ln.lower().endswith(t):
            continue                                    # etiqueta del PDF adjunto
        lineas.append(ln)
    return "\n".join(lineas)


def resumen_de(contenido: str) -> str:
    plano = re.sub(r"\s+", " ", contenido).strip()
    if len(plano) <= LARGO_RESUMEN:
        return plano
    corte = plano[:LARGO_RESUMEN].rsplit(" ", 1)[0]
    return corte + "..."


def procesar(item: dict, institucion: str, salida: SalidaCSV, pausa: float, contadores: dict) -> None:
    url = item["url"]
    if salida.ya_esta(url):
        return
    r = obtener(_sesion(), url, pausa)
    if r is None:
        contadores["fallidas"] += 1
        return
    sp = sopa(r)
    fecha = item["fecha"] or fecha_meta(sp)
    selectores = item["cuerpo"] if isinstance(item["cuerpo"], (list, tuple)) else [item["cuerpo"]]
    elementos = next((e for e in (sp.select(c) for c in selectores) if e), [])
    if item.get("unir"):
        # varios bloques forman el cuerpo (se unen en orden, salvo los anidados en otro ya elegido)
        elementos = [e for e in elementos if not any(p is o for o in elementos for p in e.parents)]
    else:
        elementos = elementos[:1]
    cuerpo = elementos[0] if elementos else None
    if not fecha:
        el = sp.select_one(".fecha, time, [id*=fecha]")
        fecha = fecha_iso(el.get_text(" ")) if el else ""
    titulo = item["titulo"]
    if not titulo:
        h = sp.select_one("h1.titulo, h2.titulo, h1.page-header, h2, h1")
        titulo = limpiar(h.get_text(" ")) if h else ""
    for el in elementos:
        for oculto in el.select("[style], [hidden]"):          # etiquetas ocultas (palabras clave)
            if oculto.decomposed:
                continue
            if oculto.has_attr("hidden") or re.search(r"display\s*:\s*none", oculto.get("style", ""), re.I):
                oculto.decompose()
    contenido = limpiar_cuerpo("\n".join(texto_de(el) for el in elementos), titulo)
    if not fecha and contenido:
        fecha = fecha_iso(contenido[:300])
    if item["omitir_menor"] and len(contenido.split()) < item["omitir_menor"]:
        contadores["omitidas"] += 1
        return
    if cuerpo is None:
        log.warning("Sin cuerpo en %s", url)
    if not contenido:
        contadores["sin_texto"] += 1
    if not fecha:
        contadores["sin_fecha"] += 1
    salida.agregar({"fecha": fecha, "institucion": institucion, "titulo": titulo,
                    "resumen": item["resumen"] or resumen_de(contenido), "url": url,
                    "contenido": contenido})


def main() -> int:
    ap = argumentos(__doc__.split("\n")[1] if __doc__ else "Scraper de prensa")
    ap.add_argument("sitio", choices=sorted(SITIOS), help="organismo a extraer")
    ap.add_argument("--antiguas", type=int, default=0, help="solo las N notas más antiguas (prueba)")
    args = ap.parse_args()
    institucion, descubrir = SITIOS[args.sitio]
    ruta = args.salida or DATA / f"{args.sitio}.csv"
    salida = SalidaCSV(ruta)
    log.info("%s -> %s (%d filas previas)", institucion, ruta, salida.previas)

    items = descubrir(sesion(), args.pausa)
    unicos = {it["url"]: it for it in items}
    items = sorted(unicos.values(), key=lambda it: it["fecha"], reverse=True)
    log.info("Notas descubiertas: %d", len(items))
    if args.muestra:
        items = items[:args.muestra]
    elif args.antiguas:
        con_fecha = [it for it in items if it["fecha"]] or items
        items = con_fecha[-args.antiguas:]
    pendientes = [it for it in items if not salida.ya_esta(it["url"])]
    log.info("Por descargar: %d", len(pendientes))

    contadores = {"fallidas": 0, "omitidas": 0, "sin_texto": 0, "sin_fecha": 0}
    en_paralelo(pendientes, lambda it: procesar(it, institucion, salida, args.pausa, contadores),
                hilos=args.hilos, cada=50)
    total = salida.cerrar()
    log.info("Fin: %d filas en el CSV; fallidas (se reintentan al reanudar): %d; omitidas por no traer "
             "texto HTML: %d; con contenido vacío: %d; sin fecha: %d",
             total, contadores["fallidas"], contadores["omitidas"], contadores["sin_texto"],
             contadores["sin_fecha"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
