#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper genérico para salas de prensa que publican un sitemap XML.

Sirve para sitios WordPress con la API REST bloqueada o con las notas en un
tipo de contenido propio: el sitemap lista todas las URLs de notas y luego se
entra a cada una para tomar título, fecha y cuerpo.

Cada sitio se define en SITIOS:
    sitemaps     lista de sitemaps de notas, o un índice + patrón para elegirlos
    filtro_url   expresión que deben cumplir las URLs de notas (opcional)
    excluir_url  expresión para descartar URLs (portadas de sección, etc.)
    cuerpo       selectores CSS del cuerpo; se usa el primero con 40+ palabras
                 (con mayor=True, el que tenga más texto entre los que calzan)
    fecha        selectores cuyo texto trae la fecha (si no, metadatos y URL)
    rss          feed RSS paginado (?paged=N), o lista de feeds, de donde tomar la
                 fecha cuando la página de la nota no la publica; con rss_urls=True
                 sus enlaces se suman a los del sitemap (sitemaps incompletos) y con
                 solo_rss=True solo se toman las notas que aparecen en los feeds
                 (cuando el sitemap mezcla noticias con otro contenido)
    titulo       selectores del título ("og" = meta og:title); sufijo_titulo
                 es una expresión a quitar del final (p. ej. " - nombre del sitio")
    desde        fecha mínima AAAA-MM-DD (para descartar contenido que no es prensa)
    ruido        líneas exactas a eliminar del cuerpo (botones, etiquetas)

Escribe data/<sitio>.csv y es reanudable (omite las URLs ya guardadas).

Uso:
    python scrapers/scraper_sitemap.py culturas
    python scrapers/scraper_sitemap.py culturas --muestra 20 --salida prueba.csv
    python scrapers/scraper_sitemap.py --lista
"""
import gzip
import html
import re
import sys
import unicodedata
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from comun import (DATA, SalidaCSV, argumentos, configurar_log, cuerpo_por_densidad, en_paralelo,
                   fecha_de_url, fecha_iso, fecha_meta, limpiar, obtener, sesion, texto_de)
from fuentes import SUJETOS

log = configurar_log("sitemap")

GOBCL = ["div.post div.texto div.contenido"]   # plantilla WordPress común de sitios gob.cl
ELEMENTOR = [".elementor-widget-theme-post-content"]

SITIOS = {
    "culturas": dict(
        institucion="Ministerio de las Culturas, las Artes y el Patrimonio",
        indice="https://www.cultura.gob.cl/wp-sitemap.xml", patron=r"posts-post-\d+\.xml",
        # notas de prensa; se excluyen agenda cultural, eventos, convocatorias y programas
        filtro_url=r"cultura\.gob\.cl/(actualidad|institucional|ministra|ministro)/",
        cuerpo=[".cont-text"], fecha=[".cont-text"], titulo=["og"]),
    "economia": dict(
        institucion="Ministerio de Economía, Fomento y Turismo",
        indice="https://www.economia.gob.cl/sitemap_index.xml", patron=r"post-sitemap\d*\.xml",
        filtro_url=r"/\d{4}/\d{2}/\d{2}/", desde="2014-03-11",   # antes: fichas de proyectos de ley
        cuerpo=["div.col-lg-12.pb-4"], mayor=True,
        # el sitemap mezcla noticias con transparencia, resoluciones e informes
        rss=[f"https://www.economia.gob.cl/category/{c}/feed/" for c in (
            "noticias", "prensa", "noticias-ministerio", "noticias-subsecretaria",
            "noticias-servicios", "noticias-pesca", "noticias-turismo")], solo_rss=True),
    "segegob": dict(
        institucion="Ministerio Secretaría General de Gobierno",
        indice="https://msgg.gob.cl/wp/wp-sitemap.xml", patron=r"posts-post-\d+\.xml",
        filtro_url=r"/wp/\d{4}/\d{2}/\d{2}/", excluir_url=r"/test/?$", desde="2014-03-11",
        cuerpo=GOBCL),
    "minmineria": dict(
        institucion="Ministerio de Minería",
        sitemaps=["https://www.minmineria.gob.cl/?sitemap=posts&sitemap-subtype=noticia&paged=1"],
        cuerpo=[".componente-contenido", ".contenido"], fecha=["#contenido .fecha", ".fecha"],
        resumen=[".bajada"]),
    "minvu": dict(
        institucion="Ministerio de Vivienda y Urbanismo",
        indice="https://www.minvu.gob.cl/sitemap_index.xml", patron=r"noticia-sitemap\d*\.xml",
        excluir_url=r"/noticia/?$", cuerpo=[".componente-contenido", ".contenido"]),
    "bienes_nacionales": dict(
        institucion="Ministerio de Bienes Nacionales",
        indice="https://www.bienesnacionales.cl/wp-sitemap.xml", patron=r"posts-post-\d+\.xml",
        cuerpo=[".mbn-single__content"], rss="https://www.bienesnacionales.cl/feed/"),
    "sernapesca": dict(
        institucion="Servicio Nacional de Pesca y Acuicultura",
        indice="https://www.sernapesca.cl/sitemap_index.xml", patron=r"noticia-sitemap\d*\.xml",
        excluir_url=r"/noticias/?$", cuerpo=["main#main div.col-lg-8"], fecha=["div.date"],
        titulo=["og"], sufijo_titulo=r"\s+-\s+sernapesca$", ruido=["Compartir"]),
    "tgr": dict(
        institucion="Tesorería General de la República",
        indice="https://tgr.gob.cl/sitemap_index.xml", patron=r"post-sitemap\d*\.xml",
        # fuera de la actualización: tras unas 40 solicitudes el sitio exige un desafío anti-bots
        excluir_url=r"/municipalidades/?$",   # avisos de pago a municipios, sin texto de prensa
        cuerpo=ELEMENTOR),
    "conaf": dict(
        institucion="Corporación Nacional Forestal",
        indice="https://www.conaf.cl/sitemap_index.xml", patron=r"post-sitemap\d*\.xml",
        excluir_url=r"/noticias/?$", cuerpo=ELEMENTOR),
    "cde": dict(
        institucion="Consejo de Defensa del Estado",
        indice="https://www.cde.cl/sitemap_index.xml", patron=r"post-sitemap\d*\.xml",
        cuerpo=ELEMENTOR),
    "cenabast": dict(
        institucion="Central de Abastecimiento del Sistema Nacional de Servicios de Salud",
        indice="https://www.cenabast.cl/wp-sitemap.xml", patron=r"posts-post-\d+\.xml",
        excluir_url=r"/diapositiva/?$", cuerpo=GOBCL + [".contenido"], fecha=["div.post", "article"]),
    "supereduc": dict(
        institucion="Superintendencia de Educación",
        indice="https://www.supereduc.cl/wp-sitemap.xml", patron=r"posts-prensa-\d+\.xml",
        # el segundo sitemap de prensa responde 404: las notas recientes salen del RSS
        rss="https://www.supereduc.cl/feed/?post_type=prensa", rss_urls=True,
        cuerpo=["#detalle-general", "section.col-md-9"], fecha=["section.col-md-9 small.date", ".rrss-date"]),
    "isp": dict(
        institucion="Instituto de Salud Pública",
        indice="https://www.ispch.cl/wp-sitemap.xml", patron=r"posts-noticia-\d+\.xml",
        cuerpo=[".page-content"], fecha=["time"], titulo=["h1.title"]),
    "senapred": dict(
        institucion="Servicio Nacional de Prevención y Respuesta ante Desastres",
        sitemaps=["https://www.senapred.cl/post-sitemap.xml"], excluir_url=r"/noticias/?$",
        cuerpo=ELEMENTOR),
    "odepa": dict(
        institucion="Oficina de Estudios y Políticas Agrarias",
        indice="https://www.odepa.gob.cl/sitemap_index.xml", patron=r"post-sitemap\d*\.xml",
        # solo notas institucionales; "agro en la prensa" son recortes de otros medios
        filtro_url=r"/publicaciones/noticias/noticias-institucionales/",
        cuerpo=["div.entry-body .entry-meta div.col-md-8", "div.entry-body", "article"]),
    "uantof": dict(
        institucion="Universidad de Antofagasta",
        # las noticias son un tipo propio ("noticia") que la API REST no expone
        indice="https://www.uantof.cl/wp-sitemap.xml", patron=r"wp-sitemap-posts-noticia-\d+\.xml",
        excluir_url=r"/noticia-de-prueba/?$",
        cuerpo=["div.col-md-9 > section"], fecha=[".ua_media-description"],
        titulo=["h3.ua_badge-title", "og"]),
    "umag": dict(
        institucion="Universidad de Magallanes",
        sitemaps=["https://umag.cl/post-sitemap.xml"],   # la API REST responde 500
        filtro_url=r"umag\.cl/\d{4}/\d{2}/\d{2}/", cuerpo=[".entry-content"]),
    "senda": dict(
        institucion="Servicio Nacional para la Prevención y Rehabilitación del Consumo de Drogas y Alcohol",
        # las notas son un tipo propio (/noticia/); la API de posts está vacía y la de `noticia`
        # solo entrega la bajada, así que el cuerpo sale de la página
        indice="https://www.senda.gob.cl/sitemap_index.xml", patron=r"/noticia-sitemap\d*\.xml",
        filtro_url=r"/noticia/[^/]+/?$", cuerpo=[".ntc-content"], fecha=[".ntc-date"],
        titulo=[".ntc-title", "og"], resumen=[".ntc-content-bajada"]),
    "migraciones": dict(
        institucion="Servicio Nacional de Migraciones",
        sitemaps=["https://serviciomigraciones.cl/post-sitemap.xml"], cuerpo=ELEMENTOR),
}

# Sujetos obligados del catálogo (auditoria_salas_prensa.py --catalogo) que son WordPress
# sin API abierta: se toman las entradas del sitemap y el cuerpo con selectores comunes
# de los temas de WordPress. Las notas quedan en data/sujetos/<clave>.csv.
# Si ningún selector calza, el cuerpo es el bloque con más texto en párrafos, y la
# fecha sale del feed RSS del sitio (WordPress lo publica en /feed/).
WORDPRESS = [".entry-content", ".elementor-widget-theme-post-content", ".post-content",
             ".et_pb_post_content", ".single-content", ".td-post-content"]
for _s in SUJETOS:
    if _s["metodo"] == "wp_sitemap" and _s["clave"] not in SITIOS:
        SITIOS[_s["clave"]] = dict(institucion=_s["institucion"], indice=_s["sitemap"],
                                   patron=r"(posts-post-\d+|post-sitemap\d*)\.xml", cuerpo=WORDPRESS,
                                   densidad=True, rss=_s["base"].rstrip("/") + "/feed/",
                                   archivo=f"sujetos/{_s['clave']}")
    elif _s["metodo"] == "wp_rss" and _s["clave"] not in SITIOS:  # sin API ni sitemap: las notas del feed
        SITIOS[_s["clave"]] = dict(institucion=_s["institucion"], rss=_s["base"].rstrip("/") + "/feed/",
                                   rss_urls=True, cuerpo=WORDPRESS, densidad=True,
                                   archivo=f"sujetos/{_s['clave']}")

SELECTORES_TITULO = ["h1.entry-title", ".entry-title", "article h1", "main h1", "h1"]


def leer_xml(s, url: str, pausa: float) -> str:
    r = obtener(s, url, pausa)
    if r is None:
        return ""
    if url.endswith(".gz") or r.content[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(r.content).decode("utf-8", "replace")
        except OSError:
            pass
    return r.text


def locs(xml: str, base: str = "") -> list[tuple[str, str]]:
    """(loc, lastmod) de cada entrada de un sitemap o índice (admite CDATA y rutas relativas)."""
    out = []
    for bloque in re.findall(r"<(?:url|sitemap)>(.*?)</(?:url|sitemap)>", xml, re.S):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", bloque, re.S)
        mod = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", bloque, re.S)
        if loc:
            u = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", loc.group(1).strip()).replace("&amp;", "&")
            out.append((urljoin(base, u), mod.group(1)[:10] if mod else ""))
    return out


def _cdata(xml: str, etiqueta: str) -> str:
    m = re.search(rf"<{etiqueta}>(.*?)</{etiqueta}>", xml, re.S)
    return re.sub(r"^\s*<!\[CDATA\[(.*)\]\]>\s*$", r"\1", m.group(1), flags=re.S) if m else ""


def fechas_rss(s, feed: str, pausa: float, textos: dict | None = None) -> dict[str, str]:
    """URL -> fecha de publicación, recorriendo el feed RSS paginado.

    Si se pasa `textos`, guarda ahí el título y el cuerpo (content:encoded) de cada
    nota, para usarlos cuando la página de la nota no se puede leer."""
    fechas, pagina = {}, 1
    while True:
        r = obtener(s, feed, pausa, params={"paged": pagina})
        items = re.findall(r"<item>(.*?)</item>", r.text, re.S) if r else []
        items = [it for it in items if "<link>" in it and "<pubDate>" in it]
        if not items:
            break
        antes = len(fechas)
        for it in items:
            link = re.sub(r"[?&]utm_[^#]*", "", html.unescape(_cdata(it, "link")).strip()).rstrip("/")
            fechas[link] = fecha_iso(_cdata(it, "pubDate"))
            if textos is not None:
                textos[link] = {
                    "titulo": limpiar(html.unescape(_cdata(it, "title"))),
                    "contenido": texto_de(BeautifulSoup(_cdata(it, "content:encoded"), "html.parser"))}
        if len(fechas) == antes:  # el sitio ignora ?paged= y repite la misma página
            break
        pagina += 1
    log.info("RSS: %d fechas en %d páginas", len(fechas), pagina - 1)
    return fechas


def _plano(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower())


def quitar_sitio(titulo: str, url: str, institucion: str) -> str:
    """Quita del final del título el nombre del sitio ("Nota | InvestChile"), solo si
    coincide con el dominio, la sigla o el nombre de la institución."""
    m = re.search(r"\s+[-–|]\s+([^-–|]+)$", titulo)
    if not m:
        return titulo
    final = _plano(m.group(1))
    propios = {_plano(urlparse(url).netloc.removeprefix("www.").split(".")[0]), _plano(institucion)}
    propios |= {_plano(x) for x in re.findall(r"\(([^)]+)\)", institucion)}
    return titulo[:m.start()].strip() if final and final in propios else titulo


def urls_de_notas(s, cfg: dict, pausa: float) -> list[tuple[str, str]]:
    sitemaps = list(cfg.get("sitemaps", []))
    if cfg.get("indice"):
        patron = re.compile(cfg["patron"])
        sitemaps += [u for u, _ in locs(leer_xml(s, cfg["indice"], pausa), cfg["indice"]) if patron.search(u)]
    filtro = re.compile(cfg["filtro_url"]) if cfg.get("filtro_url") else None
    excluir = re.compile(cfg["excluir_url"]) if cfg.get("excluir_url") else None
    vistas, out = set(), []
    for sm in sitemaps:
        for u, mod in locs(leer_xml(s, sm, pausa), sm):
            if u in vistas or (filtro and not filtro.search(u)) or (excluir and excluir.search(u)):
                continue
            vistas.add(u)
            out.append((u, mod))
    log.info("%d sitemaps, %d URLs de notas", len(sitemaps), len(out))
    return out


def extraer(soup, cfg: dict) -> dict:
    # cuerpo
    candidatos = []
    for sel in cfg["cuerpo"]:
        for el in soup.select(sel):
            t = texto_de(BeautifulSoup(str(el), "html.parser"))
            if len(t.split()) >= 40:
                candidatos.append(t)
        if candidatos and not cfg.get("mayor"):
            break
    if not candidatos and cfg.get("densidad"):
        t = cuerpo_por_densidad(soup)
        if len(t.split()) >= 40:
            candidatos.append(t)
    cuerpo = max(candidatos, key=lambda t: len(t.split())) if candidatos else ""
    if cfg.get("ruido") and cuerpo:
        cuerpo = "\n".join(l for l in cuerpo.split("\n") if l.strip() not in cfg["ruido"])
    # título: se descartan encabezados ocultos o que solo repiten el nombre del organismo
    og = soup.select_one('meta[property="og:title"]')
    og_titulo = limpiar(og["content"]) if og and og.get("content") else ""
    nombre = limpiar(cfg["institucion"]).lower()
    titulo = ""
    for sel in cfg.get("titulo", SELECTORES_TITULO):
        if sel == "og":
            titulo = og_titulo
        else:
            for el in soup.select(sel):
                t = limpiar(el.get_text(" "))
                oculto = {"visually-hidden", "sr-only", "screen-reader-text"} & set(el.get("class", []))
                if len(t.split()) >= 3 and not oculto and t.lower() != nombre:
                    titulo = t
                    break
        if titulo:
            break
    titulo = titulo or og_titulo
    if not titulo and soup.title:
        # <title> sin el nombre del sitio al final ("Nota - Ministerio ...")
        partes = re.split(r"\s+[-–|]\s+", limpiar(soup.title.get_text(" ")))
        if len(partes) > 1 and partes[-1].lower() == nombre:
            partes = partes[:-1]
        titulo = " - ".join(partes)
    sitio = soup.select_one('meta[property="og:site_name"]')
    if sitio and sitio.get("content"):  # "Título - Nombre del sitio" -> "Título"
        sufijo = re.escape(limpiar(sitio["content"]))
        titulo = re.sub(rf"\s+[-–|]\s+{sufijo}$", "", titulo, flags=re.I).strip() or titulo
    if cfg.get("sufijo_titulo"):
        titulo = re.sub(cfg["sufijo_titulo"], "", titulo, flags=re.I).strip()
    # fecha
    fecha = ""
    for sel in cfg.get("fecha", []):
        el = soup.select_one(sel)
        if el:
            fecha = fecha_iso(el.get_text(" ", strip=True)[:600])
            if fecha:
                break
    # resumen
    resumen = ""
    for sel in cfg.get("resumen", []):
        el = soup.select_one(sel)
        if el:
            resumen = limpiar(el.get_text(" "))
            break
    if not resumen:
        og = soup.select_one('meta[property="og:description"], meta[name="description"]')
        resumen = limpiar(og["content"]) if og and og.get("content") else ""
    return {"titulo": titulo, "contenido": cuerpo, "fecha": fecha, "resumen": resumen}


def main():
    ap = argumentos("Salas de prensa a partir de su sitemap XML")
    ap.add_argument("sitio", nargs="?", help="clave del sitio (ver --lista)")
    ap.add_argument("--lista", action="store_true", help="muestra los sitios configurados")
    args = ap.parse_args()

    if args.lista or not args.sitio:
        for k, v in SITIOS.items():
            print(f"  {k:20} {v['institucion']}")
        return
    if args.sitio not in SITIOS:
        sys.exit(f"Sitio desconocido: {args.sitio}. Usa --lista.")
    cfg = SITIOS[args.sitio]
    salida = SalidaCSV(args.salida or DATA / f"{cfg.get('archivo', args.sitio)}.csv")
    s = sesion()
    log.info("%s: %s (%d notas ya guardadas)", args.sitio, cfg["institucion"], salida.previas)

    urls = urls_de_notas(s, cfg, args.pausa)
    por_rss, textos_rss = {}, {}
    feeds = cfg.get("rss") or []
    for feed in [feeds] if isinstance(feeds, str) else feeds:
        por_rss.update(fechas_rss(s, feed, args.pausa, textos_rss))
    if cfg.get("solo_rss"):
        antes = len(urls)
        urls = [(u, f) for u, f in urls if u.rstrip("/") in por_rss]
        log.info("Solo notas de los feeds: %d de %d URLs del sitemap", len(urls), antes)
    if cfg.get("rss_urls") or cfg.get("solo_rss"):
        conocidas = {u.rstrip("/") for u, _ in urls}
        extra = [(u if re.search(r"\.html?$", u) else u + "/", f)
                 for u, f in por_rss.items() if u not in conocidas]
        urls += extra
        log.info("RSS: %d URLs que no estaban en el sitemap", len(extra))
    urls.sort(key=lambda x: (fecha_de_url(x[0]) or por_rss.get(x[0].rstrip("/")) or x[1]), reverse=True)
    pendientes = [u for u, _ in urls if not salida.ya_esta(u)]
    if args.muestra:
        pendientes = pendientes[:args.muestra]
    log.info("%d notas por descargar", len(pendientes))
    descartadas = []

    def procesar(url):
        r = obtener(s, url, args.pausa)
        if r is None:
            return
        soup = BeautifulSoup(r.text, "html.parser")
        d = extraer(soup, cfg)
        d["titulo"] = quitar_sitio(d["titulo"], url, cfg["institucion"])
        # página rota o vacía: título y cuerpo desde el feed, si los trae
        del_feed = textos_rss.get(url.rstrip("/"), {})
        if len(d["contenido"].split()) < 40 and len(del_feed.get("contenido", "").split()) > len(d["contenido"].split()):
            d["contenido"] = del_feed["contenido"]
        d["titulo"] = d["titulo"] or del_feed.get("titulo", "")
        d["fecha"] = (d["fecha"] or por_rss.get(url.rstrip("/")) or fecha_meta(soup)
                      or fecha_de_url(url))
        if cfg.get("desde") and d["fecha"] and d["fecha"] < cfg["desde"]:
            descartadas.append(url)
            return
        if not d["resumen"] and d["contenido"]:
            d["resumen"] = d["contenido"][:300].rsplit(" ", 1)[0] + "…"
        salida.agregar({**d, "institucion": cfg["institucion"], "url": url})

    en_paralelo(pendientes, procesar, args.hilos)
    if descartadas:
        log.info("%d notas anteriores a %s descartadas", len(descartadas), cfg["desde"])
    salida.cerrar()


if __name__ == "__main__":
    main()
