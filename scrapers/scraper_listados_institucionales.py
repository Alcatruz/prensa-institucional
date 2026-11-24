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
    senadis    Servicio Nacional de la Discapac.  senadis.gob.cl/sala_prensa/noticias/page/N (desde 1)

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
from comun import (DATA, SalidaCSV, argumentos, configurar_log, en_paralelo, fecha_iso, fecha_meta,
                   limpiar, obtener, sesion, texto_de)

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
        secciones=[("https://www.senadis.gob.cl/sala_prensa/noticias/page/{n}", 1)],
        listado=listado_senadis,
        cuerpo=["div.cont_contenidos #contenido"],
        resumen="#contenido > ul",
        quitar=[".cont_img", "p.fecha", "h2.modifica", ".clear"]),
}


# ---------------------------------------------------------------------------
# Nota individual
# ---------------------------------------------------------------------------
def cuerpo_de(soup, cfg) -> str:
    """Texto íntegro: entre los elementos que calzan con los selectores, el de más texto."""
    mejor = ""
    for sel in cfg["cuerpo"]:
        for el in soup.select(sel):
            for q in cfg["quitar"]:
                for x in el.select(q):
                    x.decompose()
            t = texto_de(el)
            if len(t) > len(mejor):
                mejor = t
    return mejor


def procesar(item, s, cfg, salida, pausa):
    url = item["url"]
    r = obtener(s, url, pausa)
    if r is None:
        notas_fallidas.append(url)
        return
    soup = sopa(r)
    fecha = item["fecha"] or fecha_meta(soup)
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
    salida.agregar({"fecha": fecha, "institucion": cfg["institucion"], "titulo": item["titulo"],
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
    while True:
        url_lista = plantilla.format(n=n)
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
        nuevos = [i for i in items if not salida.ya_esta(i["url"])]
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
        for i, (plantilla, primera) in enumerate(cfg["secciones"]):
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
    if notas_fallidas:
        log.warning("Notas que no se pudieron bajar: %d (se reintentan en la próxima corrida)", len(notas_fallidas))
    if "migracion" in cfg:
        informar_migracion(ruta, cfg["migracion"])
    if not a.muestra and a.pagina_inicial is None and a.seccion is None and terminado \
            and len(notas_fallidas) <= max(2, salida.nuevas // 200):
        marcador.write_text("recorrido completo\n", encoding="utf-8")
    log.info("Listo: %d notas nuevas, %d en total", salida.nuevas, filas)


if __name__ == "__main__":
    main()
