"""
Scraper para noticias del Senado de la República de Chile.

Estrategia:
- Listado: API JSON Drupal en web-back.senado.cl/jsonapi/node/news
  (9 noticias por página, paginación por offset)
- Contenido completo: endpoint Next.js _next/data/BUILD_ID/comunicaciones/noticias/SLUG.json
  que entrega body HTML de cada noticia
- Paralelismo: hasta 5 workers para la descarga de contenidos individuales
- Reintentos: 3 intentos ante timeout o error de red

CSV generado: data/senado.csv
Columnas: fecha, institucion, titulo, resumen, url, contenido
"""

import asyncio
from pathlib import Path
import argparse
import csv
import logging
import re
import sys
from datetime import datetime, timezone

import aiohttp
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
INSTITUCION = "Senado de la República"
CSV_PATH = str(Path(__file__).resolve().parent.parent / "data" / "senado.csv")

API_BASE = "https://web-back.senado.cl/jsonapi/node/news"
SENADO_BASE = "https://www.senado.cl"

PAGE_SIZE = 9          # Noticias por página en la API
MAX_RETRIES = 3
RETRY_DELAY = 2        # segundos entre reintentos
CONCURRENT_WORKERS = 5  # workers paralelos para contenido individual

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_fecha(iso_str: str) -> str:
    """Convierte fecha ISO 8601 a YYYY-MM-DD."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return iso_str[:10]


def html_to_text(html: str) -> str:
    """Convierte HTML a texto plano limpio."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    # Eliminar scripts y estilos
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    # Colapsar espacios múltiples
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def get_json(session: aiohttp.ClientSession, url: str, retries: int = MAX_RETRIES) -> dict:
    """GET JSON con reintentos."""
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception as e:
            if attempt < retries:
                log.warning("Intento %d/%d fallido para %s: %s", attempt, retries, url, e)
                await asyncio.sleep(RETRY_DELAY)
            else:
                log.error("Todos los reintentos agotados para %s: %s", url, e)
                raise


# ---------------------------------------------------------------------------
# Fase 1 — Obtener el Build ID de Next.js (necesario para los endpoints de contenido)
# ---------------------------------------------------------------------------

async def get_build_id(session: aiohttp.ClientSession) -> str:
    """Extrae el BUILD_ID de Next.js del HTML del sitio."""
    url = f"{SENADO_BASE}/comunicaciones/noticias"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                html = await resp.text()
                match = re.search(r"/_next/static/([^/]+)/_buildManifest", html)
                if match:
                    build_id = match.group(1)
                    log.info("Build ID detectado: %s", build_id)
                    return build_id
                raise ValueError("Build ID no encontrado en el HTML")
        except Exception as e:
            if attempt < MAX_RETRIES:
                log.warning("Intento %d/%d fallido para Build ID: %s", attempt, MAX_RETRIES, e)
                await asyncio.sleep(RETRY_DELAY)
            else:
                raise


# ---------------------------------------------------------------------------
# Fase 2 — Listar noticias desde la API
# ---------------------------------------------------------------------------

def build_api_url(offset: int) -> str:
    params = (
        f"page%5Blimit%5D={PAGE_SIZE}"
        f"&page%5Boffset%5D={offset}"
        "&fields%5Bnode--news%5D=title,created,path,shortDescription"
        "&filter%5Bstatus%5D=1"
        "&sort=-created"
    )
    return f"{API_BASE}?{params}"


async def fetch_listing_page(session: aiohttp.ClientSession, offset: int) -> tuple[list[dict], int]:
    """
    Devuelve (lista_de_items, total_count).
    Cada item tiene: title, created, path_alias, shortDescription.
    """
    url = build_api_url(offset)
    data = await get_json(session, url)
    total = data.get("meta", {}).get("count", 0)
    items = []
    for node in data.get("data", []):
        attrs = node.get("attributes", {})
        path_alias = attrs.get("path", {}).get("alias", "")
        items.append({
            "title": attrs.get("title", ""),
            "created": attrs.get("created", ""),
            "path_alias": path_alias,
            "short_description": attrs.get("shortDescription") or "",
        })
    return items, total


async def collect_all_listings(session: aiohttp.ClientSession, max_pages: int) -> list[dict]:
    """Recopila items de todas las páginas de listado hasta max_pages."""
    # Primera página para saber el total
    items_first, total = await fetch_listing_page(session, 0)
    all_items = list(items_first)

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    pages_to_fetch = min(total_pages, max_pages) if max_pages > 0 else total_pages

    log.info("Total noticias en API: %d | Páginas totales: %d | Páginas a procesar: %d",
             total, total_pages, pages_to_fetch)

    # Páginas restantes en paralelo (en lotes para no saturar)
    remaining_offsets = [i * PAGE_SIZE for i in range(1, pages_to_fetch)]

    sem = asyncio.Semaphore(CONCURRENT_WORKERS)

    async def fetch_with_sem(offset):
        async with sem:
            items, _ = await fetch_listing_page(session, offset)
            return items

    tasks = [fetch_with_sem(off) for off in remaining_offsets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log.error("Error en página offset %d: %s", remaining_offsets[i], result)
        else:
            all_items.extend(result)

    # Respetar max_pages (en número de noticias)
    max_items = pages_to_fetch * PAGE_SIZE
    return all_items[:max_items]


# ---------------------------------------------------------------------------
# Fase 3 — Obtener contenido completo de cada noticia
# ---------------------------------------------------------------------------

async def fetch_article_content(
    session: aiohttp.ClientSession,
    build_id: str,
    path_alias: str,
) -> str:
    """
    Obtiene el body HTML de la noticia via Next.js data endpoint y lo convierte a texto.
    path_alias ejemplo: /comunicaciones/noticias/titulo-de-la-noticia
    """
    # Extraer el slug (última parte del path)
    slug = path_alias.rstrip("/").split("/")[-1]
    next_url = (
        f"{SENADO_BASE}/_next/data/{build_id}"
        f"/comunicaciones/noticias/{slug}.json"
        f"?slug=comunicaciones&slug=noticias&slug={slug}"
    )

    try:
        data = await get_json(session, next_url)
        resource = data.get("pageProps", {}).get("resource", {})

        # body puede ser None o un dict con 'value'/'processed'
        body = resource.get("body")
        if isinstance(body, dict):
            html = body.get("processed") or body.get("value") or ""
            return html_to_text(html)

        # Si no hay body, intentar extraer de components
        components = resource.get("components", [])
        texts = []
        for comp in components:
            for key in ("body", "text", "field_body", "description"):
                val = comp.get(key)
                if isinstance(val, dict):
                    html = val.get("processed") or val.get("value") or ""
                    if html:
                        texts.append(html_to_text(html))
        if texts:
            return " ".join(texts)

        # Fallback: shortDescription del resource
        return resource.get("shortDescription") or ""

    except Exception as e:
        log.warning("No se pudo obtener contenido de %s: %s", path_alias, e)
        return ""


async def process_articles(
    session: aiohttp.ClientSession,
    build_id: str,
    items: list[dict],
) -> list[dict]:
    """Enriquece cada item con su contenido completo, en paralelo."""
    sem = asyncio.Semaphore(CONCURRENT_WORKERS)
    processed = [None] * len(items)

    async def process_one(idx: int, item: dict):
        async with sem:
            await asyncio.sleep(0.5)  # cortesía mínima entre requests
            contenido = await fetch_article_content(session, build_id, item["path_alias"])
            slug = item["path_alias"].rstrip("/").split("/")[-1]
            url = f"{SENADO_BASE}/comunicaciones/noticias/{slug}"

            fecha = parse_fecha(item["created"])
            resumen = item["short_description"]

            row = {
                "fecha": fecha,
                "institucion": INSTITUCION,
                "titulo": item["title"],
                "resumen": resumen,
                "url": url,
                "contenido": contenido,
            }
            processed[idx] = row
            log.info("[%d/%d] %s — %s", idx + 1, len(items), fecha, item["title"][:70])

    tasks = [process_one(i, item) for i, item in enumerate(items)]
    await asyncio.gather(*tasks)

    return [r for r in processed if r is not None]


# ---------------------------------------------------------------------------
# Guardado CSV
# ---------------------------------------------------------------------------

def guardar_csv(rows: list[dict], ruta: str):
    columnas = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columnas)
        writer.writeheader()
        writer.writerows(rows)
    log.info("CSV guardado en %s con %d filas", ruta, len(rows))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(max_pages: int):
    connector = aiohttp.TCPConnector(limit=CONCURRENT_WORKERS + 2)
    async with aiohttp.ClientSession(connector=connector) as session:

        # Obtener Build ID de Next.js
        log.info("Obteniendo Build ID de Next.js...")
        build_id = await get_build_id(session)

        # Recopilar listado
        log.info("Recopilando listado de noticias (max_pages=%d)...", max_pages)
        items = await collect_all_listings(session, max_pages)
        log.info("Items a procesar: %d", len(items))

        # Obtener contenido completo
        log.info("Descargando contenido de cada noticia...")
        rows = await process_articles(session, build_id, items)

    # Validaciones básicas
    rows_validos = []
    for row in rows:
        if not row["url"] or not row["titulo"]:
            log.warning("Fila sin URL o título, descartada: %s", row)
            continue
        rows_validos.append(row)

    # Deduplicar por URL
    seen = set()
    deduped = []
    for row in rows_validos:
        if row["url"] not in seen:
            seen.add(row["url"])
            deduped.append(row)

    log.info("Filas válidas y deduplicadas: %d", len(deduped))
    guardar_csv(deduped, CSV_PATH)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper de noticias del Senado de Chile")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Número máximo de páginas a extraer (0 = todas). Cada página tiene 9 noticias.",
    )
    args = parser.parse_args()

    asyncio.run(main(args.max_pages))
