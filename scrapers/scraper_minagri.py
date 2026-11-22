"""
Scraper - Ministerio de Agricultura de Chile (MINAGRI)
Fuente: https://minagri.gob.cl/noticias-minagri/
Estrategia: Playwright + HTML parsing (API WP REST bloqueada con 401)
  - Listado paginado: /noticias-minagri/N/ (N de 1 a 362)
  - ~9 noticias por página, aprox. 3250 noticias en total
  - Selectores listado:
      article.elementor-post  -> tarjeta de noticia
      h3.elementor-post__title a -> titulo y URL
      div.elementor-post__excerpt p -> resumen
  - En cada noticia individual:
      JSON-LD "datePublished" -> fecha
      div.elementor-widget-theme-post-content .elementor-widget-container -> contenido completo
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""

import asyncio
from pathlib import Path
import csv
import json
import logging
import re
import sys
from datetime import datetime

from playwright.async_api import async_playwright, Page, BrowserContext

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
BASE_LIST_URL = "https://minagri.gob.cl/noticias-minagri/"
INSTITUCION = "Ministerio de Agricultura"
OUTPUT_PATH = str(Path(__file__).resolve().parent.parent / "data" / "minagri.csv")

MAX_PAGES = 362          # Total de páginas detectadas
ARTICLES_PER_PAGE = 9   # Aprox.
BATCH_SIZE = 5           # Páginas de listado procesadas en paralelo
ARTICLE_CONCURRENCY = 4  # Noticias individuales en paralelo por batch
DELAY_BETWEEN_BATCHES = 2.0  # segundos entre batches de artículos
MAX_RETRIES = 3
TIMEOUT_MS = 30_000      # 30 segundos por request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def parse_fecha(iso_str: str) -> str:
    """Convierte ISO 8601 (2026-04-10T20:38:26+00:00) a YYYY-MM-DD."""
    try:
        return datetime.fromisoformat(iso_str[:19]).strftime("%Y-%m-%d")
    except Exception:
        return ""


def clean_text(text: str) -> str:
    """Normaliza espacios en blanco en texto extraído."""
    return re.sub(r"\s+", " ", text or "").strip()


# ---------------------------------------------------------------------------
# Extracción de una página de listado
# ---------------------------------------------------------------------------
async def scrape_listing_page(context: BrowserContext, page_num: int) -> list[dict]:
    """
    Extrae los artículos (título, resumen, URL) de una página del listado.
    Retorna lista de dicts con keys: titulo, resumen, url
    """
    if page_num == 1:
        url = BASE_LIST_URL
    else:
        url = f"{BASE_LIST_URL}{page_num}/"

    for attempt in range(1, MAX_RETRIES + 1):
        page: Page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            # Esperar que aparezcan los artículos
            await page.wait_for_selector("article.elementor-post", timeout=TIMEOUT_MS)

            articles = await page.query_selector_all("article.elementor-post")
            items = []
            for art in articles:
                # Título y URL
                title_el = await art.query_selector("h3.elementor-post__title a")
                if not title_el:
                    continue
                titulo = clean_text(await title_el.inner_text())
                url_art = await title_el.get_attribute("href") or ""

                # Resumen
                excerpt_el = await art.query_selector("div.elementor-post__excerpt p")
                resumen = ""
                if excerpt_el:
                    resumen = clean_text(await excerpt_el.inner_text())

                if titulo and url_art:
                    items.append({"titulo": titulo, "resumen": resumen, "url": url_art})

            log.info(f"Página {page_num}: {len(items)} artículos encontrados")
            return items

        except Exception as e:
            log.warning(f"Página {page_num}, intento {attempt}/{MAX_RETRIES}: {e}")
            if attempt == MAX_RETRIES:
                log.error(f"Página {page_num}: fallo tras {MAX_RETRIES} intentos")
                return []
            await asyncio.sleep(2)
        finally:
            await page.close()

    return []


# ---------------------------------------------------------------------------
# Extracción de una noticia individual
# ---------------------------------------------------------------------------
async def scrape_article(context: BrowserContext, item: dict) -> dict:
    """
    Accede a la URL de una noticia, extrae fecha y contenido completo.
    Retorna dict con: fecha, institucion, titulo, resumen, url, contenido
    """
    url = item["url"]
    for attempt in range(1, MAX_RETRIES + 1):
        page: Page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)

            # --- Fecha desde JSON-LD ---
            fecha = ""
            ld_scripts = await page.query_selector_all(
                'script[type="application/ld+json"]'
            )
            for script_el in ld_scripts:
                try:
                    raw = await script_el.inner_text()
                    data = json.loads(raw)

                    # Puede ser un dict o un @graph
                    def extract_date(obj):
                        if isinstance(obj, dict):
                            dp = obj.get("datePublished", "")
                            if dp:
                                return dp
                            graph = obj.get("@graph", [])
                            for node in graph:
                                dp = node.get("datePublished", "")
                                if dp:
                                    return dp
                        return ""

                    dp_raw = extract_date(data)
                    if dp_raw:
                        fecha = parse_fecha(dp_raw)
                        break
                except Exception:
                    continue

            # --- Contenido principal ---
            contenido = ""
            content_el = await page.query_selector(
                "div.elementor-widget-theme-post-content div.elementor-widget-container"
            )
            if content_el:
                contenido = clean_text(await content_el.inner_text())

            # Fallback: buscar el post-wrap con clase similar
            if not contenido:
                fallback = await page.query_selector("article.post-wrap .entry-content")
                if fallback:
                    contenido = clean_text(await fallback.inner_text())

            return {
                "fecha": fecha,
                "institucion": INSTITUCION,
                "titulo": item["titulo"],
                "resumen": item["resumen"],
                "url": url,
                "contenido": contenido,
            }

        except Exception as e:
            log.warning(f"Artículo {url}, intento {attempt}/{MAX_RETRIES}: {e}")
            if attempt == MAX_RETRIES:
                log.error(f"Artículo {url}: fallo definitivo, guardando sin contenido")
                return {
                    "fecha": "",
                    "institucion": INSTITUCION,
                    "titulo": item["titulo"],
                    "resumen": item["resumen"],
                    "url": url,
                    "contenido": "",
                }
            await asyncio.sleep(2)
        finally:
            await page.close()

    return {
        "fecha": "",
        "institucion": INSTITUCION,
        "titulo": item["titulo"],
        "resumen": item["resumen"],
        "url": url,
        "contenido": "",
    }


# ---------------------------------------------------------------------------
# Guardado incremental CSV
# ---------------------------------------------------------------------------
def guardar_csv(rows: list[dict], ruta: str, modo: str = "w") -> None:
    """Guarda o agrega filas al CSV en UTF-8."""
    columnas = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
    with open(ruta, modo, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columnas, quoting=csv.QUOTE_ALL)
        if modo == "w":
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    log.info(f"Iniciando scraper MINAGRI | {MAX_PAGES} páginas de listado")

    # Inicializar CSV con cabecera vacía
    guardar_csv([], OUTPUT_PATH, modo="w")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )

        total_guardadas = 0

        # Procesar páginas de listado en batches
        for batch_start in range(1, MAX_PAGES + 1, BATCH_SIZE):
            batch_pages = list(range(batch_start, min(batch_start + BATCH_SIZE, MAX_PAGES + 1)))
            log.info(f"=== Procesando páginas de listado {batch_pages[0]}–{batch_pages[-1]} ===")

            # Obtener artículos de las páginas del listado (en paralelo)
            listing_tasks = [scrape_listing_page(context, pn) for pn in batch_pages]
            listing_results = await asyncio.gather(*listing_tasks)

            # Aplanar lista de items
            all_items = []
            for items in listing_results:
                all_items.extend(items)

            if not all_items:
                log.warning(f"Sin artículos en páginas {batch_pages}")
                continue

            # Deduplicar por URL (por si hay solapamientos)
            all_items = list({it["url"]: it for it in all_items}.values())

            # Procesar artículos individuales en sub-batches
            batch_rows = []
            for i in range(0, len(all_items), ARTICLE_CONCURRENCY):
                sub_batch = all_items[i : i + ARTICLE_CONCURRENCY]
                tasks = [scrape_article(context, item) for item in sub_batch]
                results = await asyncio.gather(*tasks)
                batch_rows.extend(results)
                await asyncio.sleep(1.5)

            # Filtrar filas vacías
            batch_rows = [r for r in batch_rows if r["titulo"]]

            # Guardar batch al CSV
            guardar_csv(batch_rows, OUTPUT_PATH, modo="a")
            total_guardadas += len(batch_rows)
            log.info(
                f"Guardadas {len(batch_rows)} noticias en este batch | "
                f"Total acumulado: {total_guardadas}"
            )

            # Pausa entre batches de listado
            await asyncio.sleep(DELAY_BETWEEN_BATCHES)

        await browser.close()

    log.info(f"Scraping finalizado. Total noticias guardadas: {total_guardadas}")
    log.info(f"CSV: {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
