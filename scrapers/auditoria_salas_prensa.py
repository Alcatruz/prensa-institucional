#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Auditoría de salas de prensa a partir de los sujetos obligados por la Ley de Transparencia.

1. Descarga el listado de organismos del Portal de Transparencia (Consejo para la
   Transparencia, datos abiertos) y deja los activos que tienen sitio web.
2. Entra a la portada de cada organismo y anota los enlaces de menú que llevan a una
   sección de noticias, prensa, comunicados, actualidad o novedades. Un mismo sitio
   puede tener más de una (p. ej. "Noticias" y "Sala de prensa").
3. Entra a cada sección encontrada, junta los enlaces que parecen notas y cuenta
   cuántos ya están en el corpus (data/<fuente>.csv). Una sección con varias notas y
   pocas en el corpus es una sección que los scrapers no están recorriendo.

Salida: data/auditoria_salas_prensa.csv, una fila por organismo, con las secciones
encontradas y, para cada una, notas vistas / notas en el corpus. Registra además la
plataforma del sitio y si es un WordPress con la API REST abierta.

Con --catalogo arma scrapers/catalogo_sujetos.csv a partir de la última auditoría:
los sitios WordPress con API abierta que aún no están en el corpus, clasificados en
los grupos del corpus. Ese catálogo alimenta a scraper_wp_api.py y a fuentes.py.

Uso:  python scrapers/auditoria_salas_prensa.py [--hilos 12] [--solo-grupo Salud]
      python scrapers/auditoria_salas_prensa.py --catalogo
"""
import argparse
import csv
import io
import re
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urldefrag, urljoin, urlparse

import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comun  # noqa: E402
from fuentes import FUENTES  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ / "data"
URL_ORGANISMOS = "https://consejotransparencia.cl/transparencia_activa/datoabierto/archivos/Organismos_360.csv"
COLUMNAS = ["Codigo_org", "Organismo", "Codigo_padre", "Padre_org", "Region", "url_organismo", "activado"]

PALABRAS = r"noticias|prensa|sala de prensa|comunicados?|comunicados de prensa|notas de prensa|actualidad|novedades|news"
RE_TEXTO = re.compile(rf"^(ultimas |todas las |mas |ver (todas las )?)?({PALABRAS})( y ({PALABRAS}))?( institucionales| regionales)?$")
RE_RUTA = re.compile(r"/(noticias?|prensa|sala-de-prensa|saladeprensa|comunicados?|comunicados-de-prensa|actualidad|novedades|news)(\.(html?|php|aspx))?$", re.I)
RE_SUBDOMINIO = re.compile(r"^(noticias|prensa|comunicaciones|news)\.", re.I)
EXCLUIR = re.compile(r"/(tag|tags|etiqueta|category|categoria|categorias|author|autor|page|pagina|wp-content|wp-json|feed|search|buscar)/", re.I)
EXTENSIONES = re.compile(r"\.(pdf|jpe?g|png|gif|docx?|xlsx?|pptx?|zip|rar|mp[34])$", re.I)


def normalizar_texto(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip(" :»›>")


def base_dominio(host: str) -> str:
    """Dominio registrable aproximado: ministerio.gob.cl, universidad.cl, empresa.com."""
    partes = host.lower().removeprefix("www.").split(".")
    if len(partes) >= 3 and partes[-2] in ("gob", "gov", "mil", "co", "com", "org", "net", "edu"):
        return ".".join(partes[-3:])
    return ".".join(partes[-2:])


def nucleo(host: str) -> str:
    """Nombre del dominio sin terminación: dipres.cl y dipres.gob.cl -> 'dipres'."""
    return "nucleo:" + base_dominio(host).split(".")[0]


def host_principal(host: str) -> bool:
    """El host es el sitio principal del dominio (sin un subdominio propio)."""
    h = host.lower().removeprefix("www.")
    return h == base_dominio(h) or h.split(".")[0] in ("home", "web", "portal", "inicio", "www2")


def clave_url(url: str) -> str:
    """URL comparable: sin esquema, sin www, sin fragmento ni barra final."""
    u = urlparse(urldefrag(url.strip())[0])
    q = f"?{unquote(u.query)}" if u.query else ""
    host = re.sub(r":(443|80)$", "", u.netloc.lower()).removeprefix("www.")
    return f"{host}{unquote(u.path).rstrip('/')}{q}"


def cargar_organismos() -> pd.DataFrame:
    destino = DATA / "cplt_organismos.csv"
    s = comun.sesion()
    r = comun.obtener(s, URL_ORGANISMOS, pausa=0, timeout=90)
    if r is not None and r.status_code == 200 and r.content[:20].startswith(b"Codigo_org"):
        destino.write_bytes(r.content)
    elif not destino.exists():
        sys.exit("No se pudo descargar el listado de organismos del Portal de Transparencia")
    d = pd.read_csv(io.BytesIO(destino.read_bytes()), sep=";", encoding="latin-1", dtype=str,
                    keep_default_na=False, usecols=COLUMNAS)
    d = d[(d.activado.str.upper().str.startswith("S")) & (d.url_organismo.str.strip() != "")].copy()
    d["url_organismo"] = d.url_organismo.str.strip().apply(
        lambda u: u if u.lower().startswith("http") else "https://" + u.lstrip("/"))
    return d


def cargar_corpus() -> tuple[dict, set]:
    """Dominio -> fuentes del corpus, y el conjunto de URLs (normalizadas) ya descargadas."""
    csv.field_size_limit(2**31 - 1)
    dominios: dict[str, set] = {}
    urls: set[str] = set()
    for archivo, etiqueta, _grupo in FUENTES:
        ruta = DATA / f"{archivo}.csv"
        if not ruta.exists():
            continue
        try:
            serie = pd.read_csv(ruta, usecols=["url"], dtype=str, keep_default_na=False)["url"]
        except (ValueError, pd.errors.EmptyDataError):
            continue
        for u in serie:
            urls.add(clave_url(u))
        for host in serie.map(lambda u: urlparse(u).netloc.lower().removeprefix("www.")).value_counts().index[:3]:
            dominios.setdefault(host, set()).add(archivo)
            dominios.setdefault(base_dominio(host), set()).add(archivo)
            if host_principal(host):
                dominios.setdefault(nucleo(host), set()).add(archivo)
    return dominios, urls


def secciones_de(soup, url_final: str) -> list[tuple[str, str]]:
    """Enlaces de la portada que llevan a una sección de prensa del mismo sitio."""
    base = base_dominio(urlparse(url_final).netloc)
    vistas, salida = set(), []
    for a in soup.select("a[href]"):
        href = urldefrag(urljoin(url_final, a["href"].strip()))[0]
        u = urlparse(href)
        if u.scheme not in ("http", "https") or base_dominio(u.netloc) != base:
            continue
        texto = normalizar_texto(a.get_text(" "))
        ruta = u.path.rstrip("/")
        es_seccion = (len(texto) <= 40 and RE_TEXTO.match(texto)) or RE_RUTA.search(ruta) \
            or (RE_SUBDOMINIO.match(u.netloc) and ruta == "")
        if not es_seccion or EXTENSIONES.search(ruta):
            continue
        # una sección es una ruta corta: se descartan enlaces a notas que terminan en /noticias/
        segmentos = [x for x in ruta.split("/") if x]
        if len(segmentos) > 4 or any(len(re.findall(r"[-_]", x)) >= 3 for x in segmentos[:-1]) \
                or re.search(r"[?&](id|p|nid)=\d+", u.query or "", re.I):
            continue
        k = clave_url(href)
        if k in vistas or k == clave_url(url_final):
            continue
        vistas.add(k)
        salida.append((texto or ruta.rsplit("/", 1)[-1], href))
    return salida[:6]


def notas_de(soup, url_seccion: str) -> set[str]:
    """Enlaces de una sección que parecen notas individuales (en el mismo host)."""
    host = urlparse(url_seccion).netloc.lower().removeprefix("www.")
    propia = clave_url(url_seccion)
    notas = set()
    for a in soup.select("a[href]"):
        href = urldefrag(urljoin(url_seccion, a["href"].strip()))[0]
        href = re.sub(r"/(print|amp)/?$", "/", href)
        u = urlparse(href)
        if u.scheme not in ("http", "https") or u.netloc.lower().removeprefix("www.") != host:
            continue
        ruta = u.path.rstrip("/")
        if not ruta or EXCLUIR.search(ruta + "/") or EXTENSIONES.search(ruta) or clave_url(href) == propia:
            continue
        ultimo = ruta.rsplit("/", 1)[-1]
        parece_nota = len(re.findall(r"[-_]", ultimo)) >= 3 \
            or (ruta.count("/") >= 2 and re.search(r"\d{3,}", ruta)) \
            or re.search(r"[?&](id|idnoticia|noticia|nid|p)=\d+", href, re.I)
        if parece_nota:
            notas.add(clave_url(href))
    return notas


RE_MAPA_POSTS = re.compile(r"(posts-post-\d+|post-sitemap\d*)\.xml")


def sitemap_wp(s, base: str, pausa: float) -> tuple[str, int]:
    """Índice de sitemaps de WordPress (núcleo o Yoast/Rank Math) con entradas, y cuántas hay (aprox.)."""
    for ruta in ("/wp-sitemap.xml", "/sitemap_index.xml"):
        r = comun.obtener(s, base + ruta, pausa=pausa, timeout=25)
        if r is None or "<loc>" not in r.text:
            continue
        hijos = [u for u in re.findall(r"<loc>\s*([^<\s]+)", r.text) if RE_MAPA_POSTS.search(u)]
        if not hijos:
            continue
        rh = comun.obtener(s, hijos[0], pausa=pausa, timeout=25)
        por_mapa = rh.text.count("<loc>") if rh is not None else 0
        return base + ruta, por_mapa * len(hijos)
    return "", 0


def portada(s, url: str):
    """Respuesta de la portada; si no responde, prueba con https y con o sin www."""
    u = urlparse(url)
    host = u.netloc.removeprefix("www.")
    if not host or "." not in host:
        return None
    for esquema in dict.fromkeys(("https", u.scheme or "https")):
        for h in dict.fromkeys((u.netloc, "www." + host, host)):
            r = comun.obtener(s, f"{esquema}://{h}{u.path or '/'}", pausa=0, timeout=25)
            if r is not None:
                return r
    return None


def revisar(s, fila: dict, dominios: dict, corpus: set, pausa: float) -> dict:
    url = fila["url_organismo"]
    salida = {"codigo": fila["Codigo_org"], "organismo": comun.limpiar(fila["Organismo"]),
              "grupo_cplt": fila["Padre_org"], "region": fila["Region"], "sitio": url,
              "fuente_corpus": "", "cobertura": "", "estado": "", "base": "", "plataforma": "",
              "api_wp": "", "posts_wp": "", "sitemap": "", "n_secciones": 0, "secciones": "",
              "seccion_sin_cubrir": ""}
    hosts = {urlparse(url).netloc.lower().removeprefix("www.")}
    r = portada(s, url)
    if r is None:
        salida["estado"] = "sin respuesta"
    else:
        hosts.add(urlparse(r.url).netloc.lower().removeprefix("www."))
        salida["estado"] = "ok"
    fuentes = set()
    for h in hosts:
        fuentes |= dominios.get(h, set())
        if host_principal(h):  # mismo nombre con otra terminación (.cl / .gob.cl)
            fuentes |= dominios.get(nucleo(h), set())
    salida["cobertura"] = "sitio" if fuentes else ""
    if not fuentes:  # mismo dominio, otro subdominio: puede ser otro organismo
        for h in hosts:
            fuentes |= dominios.get(base_dominio(h), set())
        salida["cobertura"] = "dominio" if fuentes else ""
    salida["fuente_corpus"] = ", ".join(sorted(fuentes))
    if salida["estado"] != "ok":
        return salida
    html = r.text
    p = urlparse(r.url)
    salida["base"] = f"{p.scheme}://{p.netloc}"
    gen = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if "wp-content" in html or "wp-json" in html:
        salida["plataforma"] = "WordPress"
        ra = comun.obtener(s, f"{salida['base']}/wp-json/wp/v2/posts?per_page=1&_fields=id,date",
                           pausa=pausa, timeout=25)
        salida["api_wp"] = "si" if ra is not None and ra.text.lstrip().startswith("[{") else "no"
        if salida["api_wp"] == "no":  # algunos sitios cierran /wp-json/ pero no /?rest_route=
            ra = comun.obtener(s, f"{salida['base']}/", pausa=pausa, timeout=25,
                               params={"rest_route": "/wp/v2/posts", "per_page": 1, "_fields": "id,date"})
            if ra is not None and ra.text.lstrip().startswith("[{"):
                salida["api_wp"] = "rest_route"
        if salida["api_wp"] != "no":
            salida["posts_wp"] = ra.headers.get("X-WP-Total", "")
        else:  # sin API: el sitemap de entradas, si lo publica
            mapa, n = sitemap_wp(s, salida["base"], pausa)
            if mapa:
                salida.update(api_wp="sitemap", sitemap=mapa, posts_wp=str(n))
            else:  # ni API ni sitemap de entradas: el feed RSS, que se puede paginar
                rf = comun.obtener(s, salida["base"] + "/feed/", pausa=pausa, timeout=25)
                if rf is not None and "<item>" in rf.text:
                    salida.update(api_wp="rss", posts_wp=str(rf.text.count("<item>")))
    elif gen:
        salida["plataforma"] = gen.group(1)[:40]
    elif "/sites/default/files" in html or "Drupal" in html:
        salida["plataforma"] = "Drupal"
    soup = BeautifulSoup(r.content, "html.parser")
    secciones = secciones_de(soup, r.url)
    salida["n_secciones"] = len(secciones)
    partes, sin_cubrir = [], []
    for texto, href in secciones:
        time.sleep(pausa)
        rs = comun.obtener(s, href, pausa=0, timeout=25)
        if rs is None:
            partes.append(f"{texto} -> {href} (sin respuesta)")
            continue
        notas = notas_de(BeautifulSoup(rs.content, "html.parser"), rs.url)
        en_corpus = len(notas & corpus)
        partes.append(f"{texto} -> {href} ({en_corpus}/{len(notas)} en corpus)")
        if salida["cobertura"] == "sitio" and len(notas) >= 5 and en_corpus < 0.5 * len(notas):
            sin_cubrir.append(href)
    salida["secciones"] = " | ".join(partes)
    salida["seccion_sin_cubrir"] = " | ".join(sin_cubrir)
    return salida


# --- Catálogo de sujetos obligados que se suman al corpus -------------------------

CATALOGO = Path(__file__).with_name("catalogo_sujetos.csv")
COLUMNAS_CATALOGO = ["clave", "codigo_cplt", "institucion", "grupo_cplt", "grupo", "fila", "base", "metodo",
                     "sitemap"]
METODOS = {"si": "wp_api", "rest_route": "wp_api_rest_route", "sitemap": "wp_sitemap", "rss": "wp_rss"}
# Alcance: los partidos políticos no son organismos del Estado, y las Subsecretarías de
# Seguridad Pública y de Prevención del Delito se dejaron fuera por decisión del proyecto.
EXCLUIR_GRUPOS = {"Partidos Políticos"}
EXCLUIR_NOMBRES = re.compile(r"Subsecretar[ií]a de (?:Seguridad P[uú]blica|Prevenci[oó]n del Delito)", re.I)
POSTS_MIN = 10  # menos publicaciones suele ser un sitio de prueba o sin sala de prensa
# Sitios que se dejan fuera: desafíos anti-bots (no se intentan sortear) y entradas que no son prensa.
EXCLUIR_DOMINIOS = re.compile(r"(^|\.)(tgr\.gob\.cl|bcentral\.cl|registrocivil\.cl|ejercito\.cl|fach\.mil\.cl|"
                              r"bancoestado\.cl|correos\.cl|anid\.cl|goreloslagos\.cl|subrei\.gob\.cl|"
                              # entradas que no son notas de prensa: estados diarios del TDPI y
                              # avisos de cortes y de descargas de SASIPA
                              r"tdpi\.cl|sasipa\.cl)$")
REGIONES = [("Arica", "de Arica y Parinacota"), ("Tarapac", "de Tarapacá"), ("Antofagasta", "de Antofagasta"),
            ("Atacama", "de Atacama"), ("Coquimbo", "de Coquimbo"), ("Valpara", "de Valparaíso"),
            ("Metropolitana", "de la R. Metropolitana"), ("Higgins", "de O'Higgins"), ("Maule", "del Maule"),
            ("Ñuble", "de Ñuble"), ("B[ií]o ?b[ií]o", "del Biobío"), ("Araucan", "de La Araucanía"),
            ("Los R[ií]os", "de Los Ríos"), ("Los Lagos", "de Los Lagos"), ("Ays[eé]n", "de Aysén"),
            ("Magallanes", "de Magallanes")]


def clasificar(nombre: str, grupo_cplt: str) -> tuple[str, str]:
    """Grupo del corpus y fila agregada ('' = fila propia) para un sujeto obligado."""
    n = nombre
    if grupo_cplt.startswith("Municipios de"):
        region = next((r for patron, r in REGIONES if re.search(patron, grupo_cplt, re.I)), grupo_cplt[14:])
        return "Municipios", f"Municipios {region}"
    municipales = {"Corporaciones Municipales": "Corporaciones municipales",
                   "Asociación Municipal": "Asociaciones de municipios",
                   "Fundaciones y Otros Organismos Municipales": "Fundaciones municipales"}
    if grupo_cplt in municipales:
        return "Municipios", municipales[grupo_cplt]
    if grupo_cplt == "Salud":
        if re.match(r"Servicio de Salud", n):
            return "Salud", "Servicios de salud"
        if re.search(r"SEREMI", n):
            return "Salud", "SEREMI de Salud"
        if re.search(r"Hospital|Instituto|Complejo|Centro de Referencia", n):
            return "Salud", "Hospitales e institutos"
        return "Salud", "Otros organismos de salud"
    if re.search(r"Servicio Local ?de Educaci|Direcci[oó]n de Educaci[oó]n P[uú]blica", n):
        return "Educación pública", "Servicios Locales de Educación Pública"
    if grupo_cplt == "Centro de Formación Técnica":
        return "Educación superior estatal", "Centros de formación técnica estatales"
    if re.search(r"SERVIU|SEREMI de Vivienda", n):
        return "Servicios públicos", "SERVIU y SEREMI de Vivienda"
    if re.search(r"Empresa Portuaria", n):
        return "Empresas públicas", "Empresas portuarias"
    if re.search(r"Corporaci[oó]n de Asistencia Judicial", n):
        return "Servicios públicos", "Corporaciones de Asistencia Judicial"
    if grupo_cplt == "Fund. Corp. Asoc. NO municipales":
        return "Organismos autónomos y otros", "Fundaciones y corporaciones públicas"
    if n.startswith("Subsecretar"):
        return "Ministerios y subsecretarías", ""
    if grupo_cplt == "Empresas y Sociedades":
        return "Empresas públicas", ""
    if grupo_cplt == "Consorcio de Universidades":
        return "Educación superior estatal", ""
    if re.search(r"Consejo de Rectores", n):
        return "Organismos autónomos y otros", ""
    return "Servicios públicos", ""


def clave_de(base: str) -> str:
    h = urlparse(base).netloc.lower().removeprefix("www.")
    h = re.sub(r"\.(gob|gov|mil)\.cl$|\.(cl|com|org|net)$", "", h)
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_")


def escribir_catalogo(auditoria: Path) -> None:
    """Catálogo de sitios WordPress (API, sitemap o feed) que aún no estaban en el corpus."""
    from fuentes import GRUPOS
    d = pd.read_csv(auditoria, dtype=str, keep_default_na=False)
    d = d[(d.cobertura != "sitio") & d.api_wp.isin(list(METODOS)) & ~d.grupo_cplt.isin(EXCLUIR_GRUPOS)
          & ~d.organismo.str.contains(EXCLUIR_NOMBRES)]
    # con el feed solo se conoce la primera página (10 entradas por defecto): basta con 5
    n = pd.to_numeric(d.posts_wp, errors="coerce").fillna(0)
    d = d[(n >= POSTS_MIN) | ((d.api_wp == "rss") & (n >= 5))]
    d = d[d.base != ""].drop_duplicates("base")
    d = d[~d.base.map(lambda u: bool(EXCLUIR_DOMINIOS.search(urlparse(u).netloc.lower())))]
    # un sitio que ya estaba en el catálogo conserva su clave (y con ella su CSV)
    previas = {}
    if CATALOGO.exists():
        with open(CATALOGO, encoding="utf-8", newline="") as fh:
            previas = {f["codigo_cplt"]: f["clave"] for f in csv.DictReader(fh)}
    filas, claves = [], set(previas.values())
    for r in d.itertuples():
        grupo, fila = clasificar(r.organismo, r.grupo_cplt)
        clave = previas.get(r.codigo)
        if clave is None:
            clave = clave_de(r.base)
            if clave in claves:
                clave = f"{clave}_{r.codigo.lower()}"
        claves.add(clave)
        filas.append({"clave": clave, "codigo_cplt": r.codigo, "institucion": r.organismo,
                      "grupo_cplt": r.grupo_cplt, "grupo": grupo, "fila": fila, "base": r.base,
                      "metodo": METODOS[r.api_wp], "sitemap": r.sitemap})
    orden_region = {f"Municipios {r}": i for i, (_, r) in enumerate(REGIONES)}
    filas.sort(key=lambda f: (GRUPOS.index(f["grupo"]), f["fila"] == "", orden_region.get(f["fila"], 99),
                              f["fila"], f["institucion"]))
    with open(CATALOGO, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS_CATALOGO)
        w.writeheader()
        w.writerows(filas)
    print(f"{len(filas)} sitios -> {CATALOGO}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--catalogo", action="store_true",
                    help="no revisa sitios: arma scrapers/catalogo_sujetos.csv desde la última auditoría")
    ap.add_argument("--hilos", type=int, default=12, help="sitios revisados a la vez (cada sitio, en serie)")
    ap.add_argument("--pausa", type=float, default=1.0, help="segundos entre solicitudes a un mismo sitio")
    ap.add_argument("--solo-grupo", help="revisa solo un grupo del listado (columna Padre_org)")
    ap.add_argument("--omitir", help="expresión regular: no visita los sitios cuya URL calce (p. ej. un servidor que está limitando las conexiones)")
    ap.add_argument("--salida", default=str(DATA / "auditoria_salas_prensa.csv"))
    args = ap.parse_args()
    if args.catalogo:
        escribir_catalogo(Path(args.salida))
        return
    log = comun.configurar_log()

    organismos = cargar_organismos()
    if args.solo_grupo:
        organismos = organismos[organismos.Padre_org == args.solo_grupo]
    if args.omitir:
        organismos = organismos[~organismos.url_organismo.str.contains(args.omitir, case=False, regex=True)]
    log.info("%d organismos activos con sitio web", len(organismos))
    dominios, corpus = cargar_corpus()
    log.info("corpus: %d URLs de %d dominios", len(corpus), len(dominios))

    s = comun.sesion(reintentos=1)
    filas = []
    with ThreadPoolExecutor(max_workers=args.hilos) as ex:
        futuros = [ex.submit(revisar, s, f, dominios, corpus, args.pausa)
                   for f in organismos.to_dict("records")]
        for i, fut in enumerate(as_completed(futuros), 1):
            try:
                filas.append(fut.result())
            except Exception as e:  # un sitio con HTML inesperado no detiene la auditoría
                log.warning("error: %s", e)
            if i % 100 == 0:
                log.info("%d/%d organismos revisados", i, len(futuros))

    d = pd.DataFrame(filas).sort_values(["grupo_cplt", "organismo"])
    d.to_csv(args.salida, index=False, encoding="utf-8")
    cubiertos = d.cobertura == "sitio"
    log.info("Listo: %d organismos; %d en el corpus; %d sin cubrir con sección de prensa "
             "(%d con la API de WordPress abierta); %d cubiertos con alguna sección que no "
             "aparece en el corpus -> %s",
             len(d), cubiertos.sum(), ((~cubiertos) & (d.n_secciones > 0)).sum(),
             ((~cubiertos) & (d.api_wp == "si")).sum(), (d.seccion_sin_cubrir != "").sum(), args.salida)


if __name__ == "__main__":
    main()
