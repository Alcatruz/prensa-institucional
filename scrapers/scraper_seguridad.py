"""
Scraper — Ministerio de Seguridad Pública
URL: https://minsegpublica.cl/category/noticias/
Método: WP REST API (requests + BeautifulSoup). No se necesita navegador.

Estructura del sitio:
  - Sitio oficial: minsegpublica.cl (WordPress). El dominio minsegpublica.gob.cl
    no resuelve; seguridadpublica.gob.cl es el sitio de la Subsecretaría de
    Seguridad Pública, no del ministerio.
  - Listado: /wp-json/wp/v2/posts?per_page=100&page=N
      · Cabecera X-WP-TotalPages indica cuántas páginas hay (~2).
      · Cada post trae date, link, title, excerpt y content (HTML completo).
  - El cuerpo completo viene en content.rendered. Si por algún motivo viene
    vacío o muy corto, se entra a la URL de la nota y se lee
    .elementor-widget-theme-post-content (plantilla Elementor del detalle).
  - Hay 7 notas anteriores a 2025 (julio 2023 a julio 2024, categoría
    "Trata de personas", anteriores al ministerio). Se excluyen con --desde.

Reanudable: si el CSV ya existe, se saltan las URLs que ya contiene.
"""

import argparse
import csv
import html
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Configuración ────────────────────────────────────────────────────────────
BASE_URL    = "https://minsegpublica.cl"
API_POSTS   = BASE_URL + "/wp-json/wp/v2/posts"
INSTITUCION = "Ministerio de Seguridad Pública"
OUTPUT_CSV  = str(Path(__file__).resolve().parent.parent / "data" / "seguridad.csv")
COLUMNAS    = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
WORKERS     = 4
RETRY_MAX   = 3
TIMEOUT     = 30
PAUSA       = 1.0     # segundos entre requests de cada worker
MIN_PALABRAS = 40     # bajo este umbral se intenta leer la página de la nota
DESDE_DEFECTO = "2025-01-01"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-CL,es;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ── Utilidades ───────────────────────────────────────────────────────────────

def get(session: requests.Session, url: str, **kw) -> requests.Response | None:
    """GET con reintentos y espera creciente. Retorna None si agota los intentos."""
    for intento in range(1, RETRY_MAX + 1):
        try:
            r = session.get(url, timeout=TIMEOUT, **kw)
            if r.status_code == 200:
                return r
            log.warning("HTTP %s en %s (intento %d/%d)", r.status_code, url, intento, RETRY_MAX)
        except requests.RequestException as e:
            log.warning("Error de red en %s: %s (intento %d/%d)", url, e, intento, RETRY_MAX)
        time.sleep(2 * intento)
    return None


def html_a_texto(fragmento: str) -> str:
    """Convierte HTML a texto plano con párrafos separados por línea en blanco."""
    soup = BeautifulSoup(fragmento, "html.parser")
    for tag in soup(["script", "style", "iframe", "noscript"]):
        tag.decompose()
    texto = soup.get_text("\n").replace("\xa0", " ")
    lineas = [re.sub(r"[ \t]+", " ", l).strip() for l in texto.split("\n")]
    return "\n\n".join(l for l in lineas if l)


def contar_palabras(texto: str) -> int:
    return len(texto.split())


def limpiar_resumen(excerpt_html: str, contenido: str) -> str:
    """Extracto de WP sin HTML; si viene vacío, primer párrafo del contenido."""
    resumen = html_a_texto(excerpt_html)
    resumen = re.sub(r"\s*(\[…\]|\[&hellip;\]|Leer más.*)$", "", resumen).strip()
    resumen = re.sub(r"\s+", " ", resumen)
    if not resumen and contenido:
        resumen = contenido.split("\n\n")[0]
    return resumen[:500]


# ── Fase 1: listado vía API ──────────────────────────────────────────────────

def listar_posts(session: requests.Session) -> list[dict]:
    """Descarga todos los posts (con contenido) de la API, de más nuevo a más antiguo."""
    posts, pagina, total_paginas = [], 1, 1
    while pagina <= total_paginas:
        r = get(session, API_POSTS, params={
            "per_page": 100,
            "page": pagina,
            "orderby": "date",
            "order": "desc",
            "_fields": "id,date,link,title,excerpt,content",
        })
        if r is None:
            log.error("No se pudo leer la página %d de la API", pagina)
            break
        total_paginas = int(r.headers.get("X-WP-TotalPages", 1))
        lote = r.json()
        posts.extend(lote)
        log.info("API página %d/%d → %d posts", pagina, total_paginas, len(lote))
        pagina += 1
        time.sleep(PAUSA)
    return posts


# ── Fase 2: contenido de cada nota ───────────────────────────────────────────

def leer_pagina_nota(session: requests.Session, url: str) -> str:
    """Respaldo: entra a la nota y extrae el cuerpo desde el HTML."""
    r = get(session, url)
    if r is None:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    cuerpo = soup.select_one(".elementor-widget-theme-post-content") or soup.select_one(".entry-content")
    return html_a_texto(str(cuerpo)) if cuerpo else ""


def procesar_post(session: requests.Session, post: dict) -> dict:
    url = post["link"]
    titulo = html.unescape(post["title"]["rendered"]).strip()
    contenido = html_a_texto(post["content"]["rendered"])

    if contar_palabras(contenido) < MIN_PALABRAS:
        time.sleep(PAUSA)
        desde_pagina = leer_pagina_nota(session, url)
        if contar_palabras(desde_pagina) > contar_palabras(contenido):
            contenido = desde_pagina

    return {
        "fecha":       post["date"][:10],
        "institucion": INSTITUCION,
        "titulo":      titulo,
        "resumen":     limpiar_resumen(post["excerpt"]["rendered"], contenido),
        "url":         url,
        "contenido":   contenido,
    }


# ── CSV ──────────────────────────────────────────────────────────────────────

def leer_csv_existente(ruta: str) -> list[dict]:
    if not Path(ruta).exists():
        return []
    with open(ruta, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def guardar_csv(datos: list[dict], ruta: str):
    """Valida, elimina duplicados por URL y guarda con las 6 columnas obligatorias."""
    vistos, validos = set(), []
    for row in datos:
        if not row.get("url") or not row.get("titulo") or not row.get("fecha"):
            log.warning("Fila descartada (sin URL, título o fecha): %r", row.get("url"))
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["fecha"]):
            log.warning("Fecha con formato inválido: %r", row["fecha"])
            continue
        if row["url"] in vistos:
            continue
        vistos.add(row["url"])
        validos.append(row)

    validos.sort(key=lambda r: r["fecha"], reverse=True)
    Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(validos)
    log.info("Guardadas %d filas en %s", len(validos), ruta)
    return validos


# ── Scraping principal ───────────────────────────────────────────────────────

def scrape(desde: str, limite: int = 0) -> list[dict]:
    existentes = leer_csv_existente(OUTPUT_CSV)
    urls_hechas = {r["url"] for r in existentes}
    log.info("CSV existente: %d filas", len(existentes))

    session = requests.Session()
    session.headers.update(HEADERS)

    posts = listar_posts(session)
    pendientes = [p for p in posts if p["date"][:10] >= desde and p["link"] not in urls_hechas]
    log.info("Posts en API: %d | pendientes desde %s: %d", len(posts), desde, len(pendientes))
    if limite:
        pendientes = pendientes[:limite]

    nuevos = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, fila in enumerate(pool.map(lambda p: procesar_post(session, p), pendientes), 1):
            nuevos.append(fila)
            if i % 25 == 0 or i == len(pendientes):
                log.info("  Procesadas %d/%d", i, len(pendientes))

    return existentes + nuevos


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper Ministerio de Seguridad Pública")
    parser.add_argument("--desde", default=DESDE_DEFECTO,
                        help="Fecha mínima YYYY-MM-DD (por defecto 2025-01-01)")
    parser.add_argument("--limite", type=int, default=0,
                        help="Máximo de notas nuevas a procesar (0 = todas)")
    args = parser.parse_args()

    datos = scrape(desde=args.desde, limite=args.limite)
    validos = guardar_csv(datos, OUTPUT_CSV)

    print(f"\nTotal registros: {len(validos)}")
    if validos:
        print(f"Rango de fechas: {validos[-1]['fecha']} a {validos[0]['fecha']}")
        largas = sum(1 for r in validos if contar_palabras(r["contenido"]) >= MIN_PALABRAS)
        print(f"Con contenido >= {MIN_PALABRAS} palabras: {largas} ({100 * largas / len(validos):.1f}%)")
