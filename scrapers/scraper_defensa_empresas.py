#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de salas de prensa de Defensa, Armada y empresas públicas.

Cada sitio tiene un listado propio, así que cada uno se descubre con su función y
luego se entra a la página de cada nota para obtener el texto íntegro.

Sitios (cada uno escribe data/<sitio>.csv por defecto):
    defensa     Ministerio de Defensa Nacional (WordPress estático; /category/noticias/page/N/)
    armada      Armada de Chile (Prontus; /noticias-navales/p/N, unas 2.400 páginas de 8 notas)
    directemar  Dirección General del Territorio Marítimo y de Marina Mercante (Prontus; listado
                tagport_2452_N desde 2024 + sitemaps mensuales para las notas anteriores)
    dgac        Dirección General de Aeronáutica Civil (Angular; API JSON pública del propio sitio)
    codelco     Corporación Nacional del Cobre (Prontus; sitemaps mensuales, /prensa y /noticias/AAAA;
                no se incluyen las noticias de cada división bajo /operaciones/)
    enap        Empresa Nacional del Petróleo (/sala-de-prensa?page=N)
    metro       Metro de Santiago (el listado completo viene de /ajax/gSalaPrensa.php)
    zofri       Zona Franca de Iquique (API JSON del propio sitio; solo noticias, sin columnas ni videos)
    enami       Empresa Nacional de Minería (SharePoint; el listado se pagina con Playwright porque
                lo arma el navegador; las notas se leen con requests)

Método: requests + BeautifulSoup (Playwright solo en ENAMI). Pausa de cortesía entre solicitudes y
2 hilos por defecto. No se esquiva ninguna protección: los sitios con desafío anti-bots
(Ejército, FACh, BancoEstado) no están aquí.

Reanudable: las URLs que ya están en el CSV no se vuelven a descargar. En los listados paginados se
detiene tras 3 páginas seguidas sin notas nuevas, salvo con --completo.

Uso:
    python scrapers/scraper_defensa_empresas.py metro
    python scrapers/scraper_defensa_empresas.py armada --muestra 10 --salida prueba.csv
    python scrapers/scraper_defensa_empresas.py armada --completo
    python scrapers/scraper_defensa_empresas.py --lista
"""
import asyncio
import gzip
import re
import sys
import threading
from itertools import count
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import comun
from comun import (DATA, SalidaCSV, argumentos, configurar_log, en_paralelo, fecha_iso,
                   fecha_meta, limpiar, obtener, sesion, texto_de)

log = configurar_log("defensa_empresas")

LARGO_RESUMEN = 300
SIN_NOVEDAD = 3                 # páginas seguidas sin notas nuevas que cortan el listado
_hilo = threading.local()


def _sesion():
    """Una sesión HTTP por hilo."""
    if not hasattr(_hilo, "s"):
        _hilo.s = sesion()
    return _hilo.s


def sopa(r) -> BeautifulSoup:
    return BeautifulSoup(r.text, "html.parser")


# ---------------------------------------------------------------------------
# Piezas comunes
# ---------------------------------------------------------------------------
def paginar(s, pausa, url_de, extraer, salida, completo, muestra) -> list[dict]:
    """Recorre un listado paginado (página 1, 2, ...) y devuelve los ítems encontrados.

    `url_de(n)` da la URL de la página n; `extraer(soup, url)` devuelve los ítems de la página
    (dicts con url, fecha, titulo, resumen). Corta si la página no trae ítems, si repite la
    anterior, tras SIN_NOVEDAD páginas sin notas nuevas (salvo `completo`) o al juntar `muestra`.
    """
    items, vistas, sin_novedad = [], set(), 0
    for n in count(1):
        r = obtener(s, url_de(n), pausa)
        if r is None:
            break
        pag = [i for i in extraer(sopa(r), r.url) if i["url"] not in vistas]
        if not pag:
            break
        vistas.update(i["url"] for i in pag)
        items += pag
        if any(not salida.ya_esta(i["url"]) for i in pag):
            sin_novedad = 0
        else:
            sin_novedad += 1
            if sin_novedad >= SIN_NOVEDAD and not completo:
                log.info("Página %d: %d páginas sin novedades, se detiene el listado", n, sin_novedad)
                break
        if n % 25 == 0:
            log.info("Listado: %d páginas, %d notas", n, len(items))
        if muestra and len(items) >= muestra:
            break
    log.info("Listado: %d notas", len(items))
    return items


def cuerpo_de(soup, selectores: list[str], ruido=(), minimo: int = 20) -> str:
    """Texto del primer selector con `minimo`+ palabras (el de más texto si hay varios del mismo).

    `ruido` son líneas exactas (str) o expresiones (re.Pattern) a quitar del cuerpo."""
    mejor = ""
    for sel in selectores:
        for el in soup.select(sel):
            t = texto_de(BeautifulSoup(str(el), "html.parser"))
            if len(t.split()) > len(mejor.split()):
                mejor = t
        if len(mejor.split()) >= minimo:
            break
    lineas = []
    for l in mejor.replace("\u200b", "").split("\n"):
        if not l.strip():
            continue
        if any((r.search(l) if hasattr(r, "search") else l.strip() == r) for r in ruido):
            continue
        lineas.append(l)
    return "\n".join(lineas)


def titulo_de(soup, selectores=("h1",)) -> str:
    for sel in selectores:
        el = soup.select_one(sel)
        if el and limpiar(el.get_text(" ")):
            return limpiar(el.get_text(" "))
    og = soup.select_one('meta[property="og:title"]')
    return limpiar(og["content"]) if og and og.get("content") else ""


def resumen_og(soup) -> str:
    og = soup.select_one('meta[property="og:description"], meta[name="description"]')
    return limpiar(og["content"]) if og and og.get("content") else ""


def sitemap_urls(s, pausa, indice: str, patron: str) -> list[tuple[str, str]]:
    """(url, lastmod) de todos los sitemaps del índice cuyo nombre calza con `patron`."""
    def leer(u):
        r = obtener(s, u, pausa)
        if r is None:
            return ""
        return gzip.decompress(r.content).decode("utf-8", "replace") if r.content[:2] == b"\x1f\x8b" else r.text

    def locs(xml):
        out = []
        for b in re.findall(r"<(?:url|sitemap)>(.*?)</(?:url|sitemap)>", xml, re.S):
            loc = re.search(r"<loc>\s*(.*?)\s*</loc>", b, re.S)
            mod = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", b, re.S)
            if loc:
                out.append((loc.group(1).replace("&amp;", "&"), mod.group(1)[:10] if mod else ""))
        return out

    res = []
    for sm, _ in locs(leer(indice)):
        if re.search(patron, sm):
            res += locs(leer(sm))
    return res


# ---------------------------------------------------------------------------
# Listados por sitio
# ---------------------------------------------------------------------------
def listar_defensa(s, a):
    def extraer(soup, url):
        out = []
        for caja in soup.select("#grid div.mg-blog-post-box"):
            enlace = caja.select_one("h4 a, h3 a, h2 a")
            if not enlace or not enlace.get("href"):
                continue
            u = re.sub(r"/index\.html$", "/", urljoin(url, enlace["href"]))
            fecha = caja.select_one("span.mg-blog-date")
            out.append({"url": u, "titulo": limpiar(enlace.get_text(" ")),
                        "fecha": fecha_iso(fecha.get_text(" ", strip=True)) if fecha else ""})
        return out
    return paginar(s, a.pausa, lambda n: ("https://www.defensa.cl/category/noticias/" if n == 1
                              else f"https://www.defensa.cl/category/noticias/page/{n}/"),
                   extraer, a.salida_csv, a.completo, a.muestra)


def listar_armada(s, a):
    def extraer(soup, url):
        out = []
        for art in soup.select("article.card-tax"):
            enlace = art.select_one("a.card-tax__link")
            if not enlace or not enlace.get("href"):
                continue
            fecha = art.select_one(".card-tax__fecha")
            out.append({"url": urljoin("https://www.armada.cl", enlace["href"]),
                        "titulo": limpiar(enlace.get_text(" ")),
                        # algunas fechas antiguas traen el año mal escrito: fecha_iso las descarta
                        "fecha": fecha_iso(fecha.get_text(" ", strip=True)) if fecha else ""})
        return out
    return paginar(s, a.pausa, lambda n: f"https://www.armada.cl/noticias-navales/p/{n}",
                   extraer, a.salida_csv, a.completo, a.muestra)


def _clave(titulo: str) -> str:
    return re.sub(r"[^a-z0-9]", "", titulo.lower())


def listar_directemar(s, a):
    def extraer(soup, url):
        out = []
        for art in soup.select("article.file"):
            enlace = art.select_one("h2.titular-relacionados a")
            if not enlace or not enlace.get("href"):
                continue
            fecha = art.select_one("span.fecha-relacionado")
            resumen = art.select_one("p")
            out.append({"url": urljoin("https://www.directemar.cl", enlace["href"]),
                        "titulo": limpiar(enlace.get_text(" ")),
                        "resumen": limpiar(resumen.get_text(" ")) if resumen else "",
                        "fecha": fecha_iso(fecha.get_text(" ", strip=True)) if fecha else ""})
        return out
    items = paginar(s, a.pausa,
                    lambda n: f"https://www.directemar.cl/directemar/site/tag/port/all/tagport_2452_{n}.html",
                    extraer, a.salida_csv, a.completo, a.muestra)
    # el listado cubre desde marzo de 2024; las notas anteriores salen de los sitemaps mensuales
    if a.muestra or not (a.completo or a.salida_csv.previas == 0):
        return items
    titulos = {_clave(i["titulo"]) for i in items}     # para no repetir notas ya listadas
    conocidas = {i["url"] for i in items}
    viejas = [{"url": u, "fecha": "", "titulos": titulos} for u, _ in sitemap_urls(
        s, a.pausa, "https://www.directemar.cl/directemar/site/sitemap_pags.xml", r"sitemap_pags_\d+\.xml")
        if re.search(r"/noticias-y-comunicaciones/noticias/\d{4}/", u) and u not in conocidas]
    log.info("Sitemaps: %d notas anteriores o no listadas", len(viejas))
    return items + viejas


def listar_dgac(s, a):
    base = "https://servicios.dgac.gob.cl/dgac_backend/api/v1/noticias"
    items = []
    for pagina in count(0):
        r = obtener(s, base, a.pausa, params={"pagina": pagina, "tamano": 50, "tipo": "NOTICIA"})
        if r is None:
            break
        d = r.json()
        for n in d.get("contenido", []):
            items.append({"url": f"https://www.dgac.gob.cl/sala-de-prensa/noticias/{n['slug']}",
                          "slug": n["slug"], "titulo": limpiar(n.get("titulo")),
                          "fecha": fecha_iso(n.get("fechaPublicacion") or ""),
                          "resumen": limpiar(n.get("resumen"))})
        if pagina + 1 >= d.get("totalPaginas", 0) or (a.muestra and len(items) >= a.muestra):
            break
    return items


def listar_codelco(s, a):
    urls = sitemap_urls(s, a.pausa, "https://www.codelco.com/prontus_codelco/sitemap_pags.xml",
                        r"sitemap_pags_\d+\.xml")
    items, vistas = [], set()
    for u, mod in sorted(urls, key=lambda x: x[1], reverse=True):
        # solo la sala de prensa corporativa: /prensa/... y /noticias/AAAA/... (no las divisiones)
        if u not in vistas and re.match(r"https://www\.codelco\.com/(prensa/|noticias/\d{4}/)", u):
            vistas.add(u)
            items.append({"url": u, "fecha": "", "modificada": mod})
    log.info("Sitemaps: %d notas", len(items))
    return items


def listar_enap(s, a):
    def extraer(soup, url):
        out = []
        for card in soup.select("div.card"):
            enlace = card.select_one("a[href*='/sala-de-prensa/']")
            titulo = card.select_one("h4")
            if not enlace or not titulo:
                continue
            resumen = card.select_one("p.destacamos--texto")
            out.append({"url": urljoin("https://www.enap.cl", enlace["href"]),
                        "titulo": limpiar(titulo.get_text(" ")), "fecha": "",
                        "resumen": limpiar(resumen.get_text(" "))[:LARGO_RESUMEN] if resumen else ""})
        return out
    return paginar(s, a.pausa, lambda n: f"https://www.enap.cl/sala-de-prensa?page={n}",
                   extraer, a.salida_csv, a.completo, a.muestra)


def listar_metro(s, a):
    r = s.post("https://www.metro.cl/ajax/gSalaPrensa.php", data={"draw": 1, "start": 0, "length": 12},
               headers={"X-Requested-With": "XMLHttpRequest", "Referer": "https://www.metro.cl/noticias/"},
               timeout=60)
    r.raise_for_status()
    items = []
    for (celda,) in r.json().get("data", []):
        c = BeautifulSoup(celda, "html.parser")
        enlace = c.select_one("a[href]")
        titulo = c.select_one(".card-title")
        fecha = c.select_one("i")
        if enlace and titulo:
            items.append({"url": urljoin("https://www.metro.cl", enlace["href"]),
                          "titulo": limpiar(titulo.get_text(" ")),
                          "fecha": fecha_iso(fecha.get_text().replace(",", ", ")) if fecha else ""})
    log.info("Listado: %d notas", len(items))
    return items


def listar_zofri(s, a):
    r = s.post("https://www.zofri.cl/api/newsPress/newsPressForType/noticia", json={"language": "es"},
               timeout=60)
    r.raise_for_status()
    items = []
    for n in r.json().get("content", []):
        items.append({"url": f"https://www.zofri.cl/es/press?NewsId={n['id']}", "id": n["id"],
                      "titulo": limpiar(n.get("titulo")), "fecha": fecha_iso(n.get("fecha") or "")})
    log.info("Listado: %d notas", len(items))
    return items


def listar_enami(s, a):
    """Pagina /Noticias con Playwright (el listado lo arma el navegador) y lee título, fecha y enlace."""
    from playwright.async_api import async_playwright
    url = "https://www.enami.cl/Noticias/Pages/default.aspx"

    items = []

    async def recorrer():
        vistas, sin_novedad = set(), 0
        async with async_playwright() as p:
            nav = await p.chromium.launch(headless=True)
            ctx = await nav.new_context(user_agent=comun.USER_AGENT, ignore_https_errors=True)
            pg = await ctx.new_page()
            await pg.goto(url, wait_until="networkidle", timeout=60000)
            for n in count(1):
                await pg.wait_for_selector(".contNoticias a", timeout=20000)
                cajas = await pg.eval_on_selector_all(
                    ".contNoticias",
                    "els => els.map(e => ({href: e.querySelector('a').href, texto: e.innerText}))")
                nuevas = []
                for c in cajas:
                    if c["href"] in vistas:
                        continue
                    vistas.add(c["href"])
                    lineas = [limpiar(x) for x in c["texto"].split("\n") if limpiar(x)]
                    fecha = next((fecha_iso(x) for x in lineas if re.match(r"\d{2}/\d{2}/\d{4}", x)), "")
                    titulo = next((x for x in lineas if not re.match(r"\d{2}/\d{2}/\d{4}$", x)), "")
                    nuevas.append({"url": c["href"], "titulo": titulo, "fecha": fecha})
                if not nuevas:
                    break
                items.extend(nuevas)
                if any(not a.salida_csv.ya_esta(i["url"]) for i in nuevas):
                    sin_novedad = 0
                else:
                    sin_novedad += 1
                    if sin_novedad >= SIN_NOVEDAD and not a.completo:
                        break
                if a.muestra and len(items) >= a.muestra:
                    break
                primero = cajas[0]["href"]
                if not await pg.is_visible("[ng-click='next()']"):
                    break                       # última página
                await pg.click("[ng-click='next()']", timeout=10000)
                await pg.wait_for_function(
                    "p => document.querySelector('.contNoticias a') && document.querySelector('.contNoticias a').href !== p",
                    arg=primero, timeout=15000)
                await asyncio.sleep(a.pausa)
            await nav.close()

    try:
        asyncio.run(recorrer())
    except Exception as e:      # se conserva lo recorrido hasta el error
        log.warning("Paginación de ENAMI interrumpida: %s", str(e).splitlines()[0])
    log.info("Listado: %d notas", len(items))
    return items


# ---------------------------------------------------------------------------
# Extracción de cada nota
# ---------------------------------------------------------------------------
RE_FECHA_LINEA = re.compile(r"^\d{2}/\d{2}/\d{4}\s*\|\s*\d{2}:\d{2}$")

SITIOS = {
    "defensa": dict(
        institucion="Ministerio de Defensa Nacional", listar=listar_defensa,
        cuerpo=["article.single div.elementor", "article.single"], ruido=["Previous", "Next"],
        titulo=["h1"], fecha=["span.mg-blog-date"]),
    "armada": dict(
        institucion="Armada de Chile", listar=listar_armada,
        cuerpo=["div.CUERPO"], titulo=["h1.titular", "h1"], resumen=["p.bajada"]),
    "directemar": dict(
        institucion="Dirección General del Territorio Marítimo y de Marina Mercante",
        listar=listar_directemar, cuerpo=["div.CUERPO"], titulo=["h1.titular", "h1"],
        resumen=["p.bajada"]),
    "dgac": dict(
        institucion="Dirección General de Aeronáutica Civil", listar=listar_dgac, api=True),
    "codelco": dict(
        institucion="Corporación Nacional del Cobre de Chile", listar=listar_codelco,
        cuerpo=[".articulo-body-text", "div.CUERPO"], titulo=["h1.articulo-header-title", "h1"],
        fecha=[".articulo-date"], fecha_en_cuerpo=True),
    "enap": dict(
        institucion="Empresa Nacional del Petróleo", listar=listar_enap,
        cuerpo=["article.contenido div.container", "article.contenido"],
        titulo=["article.contenido h1", "h1"], fecha=["p.seccion__header--fecha"],
        resumen=["h3.contenido__noticia--bajada"], ruido=["COMUNICADO DE PRENSA", "NOTICIA"]),
    "metro": dict(
        institucion="Metro de Santiago", listar=listar_metro,
        cuerpo=["main div.container.pb-5 div.col-md-12"], titulo=["main h1.titulo-noticia", "h1"],
        ruido=[RE_FECHA_LINEA, "Volver"]),
    "zofri": dict(
        institucion="Zona Franca de Iquique S.A.", listar=listar_zofri, api=True),
    "enami": dict(
        institucion="Empresa Nacional de Minería", listar=listar_enami,
        cuerpo=["div[id$=RichHtmlField]"], titulo=["h2", "h1"], sin_og=True),
}


def nota_api(item: dict, cfg: dict, s) -> dict | None:
    """Sitios cuyo texto viene de una API JSON del propio sitio (DGAC, ZOFRI)."""
    if "slug" in item:           # DGAC
        r = obtener(s, f"https://servicios.dgac.gob.cl/dgac_backend/api/v1/noticias/{item['slug']}", 0)
        if r is None:
            return None
        d = r.json()
        html = d.get("cuerpo") or ""
        titulo = limpiar(d.get("titulo")) or item["titulo"]
        resumen = limpiar(d.get("resumen"))
    else:                        # ZOFRI
        r = s.post(f"https://www.zofri.cl/api/newsPress/{item['id']}", timeout=40)
        if r.status_code != 200:
            return None
        d = r.json().get("content") or {}
        html = d.get("resumen") or ""      # en esta API "resumen" trae el cuerpo completo en HTML
        titulo, resumen = limpiar(d.get("titulo")) or item["titulo"], ""
    return {"titulo": titulo, "resumen": resumen,
            "contenido": texto_de(BeautifulSoup(html, "html.parser")).replace("\u200b", "").strip()}


def nota_html(item: dict, cfg: dict, s, pausa: float) -> dict | None:
    r = obtener(s, item["url"], pausa)
    if r is None:
        return None
    soup = sopa(r)
    contenido = cuerpo_de(soup, cfg["cuerpo"], cfg.get("ruido", ()))
    titulo = item.get("titulo") or titulo_de(soup, cfg.get("titulo", ["h1"]))
    resumen = item.get("resumen", "")
    for sel in cfg.get("resumen", []):
        el = soup.select_one(sel)
        if el and not resumen:
            resumen = limpiar(el.get_text(" "))
    fecha = item.get("fecha", "")
    for sel in cfg.get("fecha", []):
        el = soup.select_one(sel)
        if el and not fecha:
            fecha = fecha_iso(el.get_text(" ", strip=True)[:200])
    if cfg.get("fecha_en_cuerpo") and not fecha:
        # "Santiago, 29 de octubre 2026.- ..." al comienzo del cuerpo
        fecha = fecha_iso(contenido[:160])
        if not fecha:
            img = re.search(r"/artic/(\d{4})(\d{2})(\d{2})/", str(soup.select_one(".articulo-card") or ""))
            fecha = fecha_iso("-".join(img.groups())) if img else ""
    fecha = fecha or fecha_meta(soup) or item.get("modificada", "")
    return {"titulo": titulo, "resumen": resumen or ("" if cfg.get("sin_og") else resumen_og(soup)),
            "contenido": contenido,
            "fecha": fecha}


def main():
    ap = argumentos("Salas de prensa de Defensa, Armada y empresas públicas")
    ap.add_argument("sitio", nargs="?", help="clave del sitio (ver --lista)")
    ap.add_argument("--lista", action="store_true", help="muestra los sitios configurados")
    ap.add_argument("--completo", action="store_true",
                    help="recorre todo el listado aunque haya páginas sin novedades")
    args = ap.parse_args()

    if args.lista or not args.sitio:
        for k, v in SITIOS.items():
            print(f"  {k:12} {v['institucion']}")
        return 0
    if args.sitio not in SITIOS:
        sys.exit(f"Sitio desconocido: {args.sitio}. Usa --lista.")
    cfg = SITIOS[args.sitio]
    args.salida_csv = salida = SalidaCSV(args.salida or DATA / f"{args.sitio}.csv")
    log.info("%s: %s (%d notas ya guardadas)", args.sitio, cfg["institucion"], salida.previas)

    items = cfg["listar"](sesion(), args)
    pendientes = [i for i in items if not salida.ya_esta(i["url"])]
    if args.muestra:
        pendientes = pendientes[:args.muestra]
    log.info("%d notas por descargar", len(pendientes))
    corte_sitemap = {"descartadas": 0}

    def procesar(item):
        s = _sesion()
        d = nota_api(item, cfg, s) if cfg.get("api") else nota_html(item, cfg, s, args.pausa)
        if d is None:
            return
        d["fecha"] = d.get("fecha") or item.get("fecha", "")
        d["titulo"] = d.get("titulo") or item.get("titulo", "")
        # DIRECTEMAR: una nota del sitemap que ya figura en el listado (otra URL) se omite
        if item.get("titulos") is not None and _clave(d["titulo"]) in item["titulos"]:
            corte_sitemap["descartadas"] += 1
            return
        if not d["contenido"]:
            log.warning("Sin cuerpo: %s", item["url"])
            return
        resumen = d.get("resumen") or item.get("resumen") or ""
        if not resumen:
            resumen = limpiar(d["contenido"][:LARGO_RESUMEN]).rsplit(" ", 1)[0] + "…"
        salida.agregar({**d, "resumen": resumen, "institucion": cfg["institucion"], "url": item["url"]})

    en_paralelo(pendientes, procesar, args.hilos)
    if corte_sitemap["descartadas"]:
        log.info("%d notas del sitemap repetidas respecto del listado", corte_sitemap["descartadas"])
    salida.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
