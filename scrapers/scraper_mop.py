"""
Scraper MOP — Ministerio de Obras Públicas (Chile)
URL listado: https://www.mop.gob.cl/noticias/

Estrategia híbrida:
  Fase 1 — Listado via WP REST API (requests)
  Fase 2 — Contenido completo via Playwright async (workers en paralelo)

Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""

import asyncio
from pathlib import Path
import csv
import html
import logging
import re
import sys
import time
import argparse
from datetime import datetime

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

API_BASE = "https://www.mop.gob.cl/wp-json/wp/v2/posts"
API_FIELDS = "id,date,title,excerpt,link"
INSTITUCION = "Ministerio de Obras Públicas"
DATA_PATH = str(Path(__file__).resolve().parent.parent / "data" / "mop.csv")

WORKERS = 4          # paralelismo Playwright
RETRY_LIMIT = 3      # reintentos por página de detalle
DETAIL_TIMEOUT = 30_000  # ms
DETAIL_WAIT_MS = 2_500   # ms tras domcontentloaded

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers de limpieza
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")


def strip_html(texto: str) -> str:
    """Elimina tags HTML y limpia entidades."""
    texto = TAG_RE.sub(" ", texto)
    texto = html.unescape(texto)
    return " ".join(texto.split()).strip()


def clean_title(rendered: str) -> str:
    return html.unescape(rendered).strip()


# ---------------------------------------------------------------------------
# Fase 1 — WP REST API
# ---------------------------------------------------------------------------

def fetch_api_page(page: int, session: requests.Session) -> tuple[list[dict], int]:
    """
    Devuelve (lista de posts, total_pages).
    Cada post: {fecha, titulo, resumen, url}
    """
    params = {
        "per_page": 100,
        "page": page,
        "_fields": API_FIELDS,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    for intento in range(1, 4):
        try:
            resp = session.get(API_BASE, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
            total_posts = int(resp.headers.get("X-WP-Total", 0))
            items = []
            for post in resp.json():
                fecha = post.get("date", "")[:10]
                titulo = clean_title(post.get("title", {}).get("rendered", ""))
                resumen = strip_html(post.get("excerpt", {}).get("rendered", ""))
                url = post.get("link", "")
                items.append({
                    "fecha": fecha,
                    "titulo": titulo,
                    "resumen": resumen,
                    "url": url,
                })
            log.info(
                "API página %d/%d — %d posts (total: %d)",
                page, total_pages, len(items), total_posts,
            )
            return items, total_pages
        except Exception as exc:
            log.warning("API página %d — intento %d fallido: %s", page, intento, exc)
            if intento < 3:
                time.sleep(3)
    log.error("API página %d — se agotaron los reintentos", page)
    return [], 1


def fetch_all_api(max_pages: int | None = None) -> list[dict]:
    """Itera todas las páginas de la API y devuelve lista de posts."""
    session = requests.Session()
    posts: list[dict] = []

    # Primera página para saber cuántas hay
    primera, total_pages = fetch_api_page(1, session)
    posts.extend(primera)

    if max_pages:
        total_pages = min(total_pages, max_pages)

    for page in range(2, total_pages + 1):
        time.sleep(1)  # pausa educada
        pagina, _ = fetch_api_page(page, session)
        posts.extend(pagina)

    log.info("API completa — %d posts obtenidos", len(posts))
    return posts


# ---------------------------------------------------------------------------
# Fase 2 — Playwright: contenido de cada noticia
# ---------------------------------------------------------------------------

async def fetch_detail(url: str, sem: asyncio.Semaphore, browser) -> str:
    """Abre la URL de detalle y extrae .et_pb_post_content como texto."""
    async with sem:
        for intento in range(1, RETRY_LIMIT + 1):
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()
            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=DETAIL_TIMEOUT,
                )
                await page.wait_for_timeout(DETAIL_WAIT_MS)

                # Intentar selector principal
                elem = page.locator(".et_pb_post_content").first
                if await elem.count() > 0:
                    contenido = await elem.inner_text()
                    contenido = " ".join(contenido.split()).strip()
                else:
                    # Fallback: cuerpo completo del article
                    elem2 = page.locator("article").first
                    if await elem2.count() > 0:
                        contenido = await elem2.inner_text()
                        contenido = " ".join(contenido.split()).strip()
                    else:
                        contenido = ""
                        log.warning("Sin contenido en %s", url)
                return contenido

            except PlaywrightTimeout:
                log.warning("Timeout en %s (intento %d/%d)", url, intento, RETRY_LIMIT)
                if intento == RETRY_LIMIT:
                    return ""
                await asyncio.sleep(3)
            except Exception as exc:
                log.warning("Error en %s (intento %d): %s", url, intento, exc)
                if intento == RETRY_LIMIT:
                    return ""
                await asyncio.sleep(2)
            finally:
                await page.close()
                await context.close()
    return ""


async def fetch_all_details(posts: list[dict]) -> list[dict]:
    """
    Para cada post añade el campo 'contenido' extrayendo la página de detalle.
    Usa WORKERS en paralelo.
    """
    sem = asyncio.Semaphore(WORKERS)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        tasks = [
            fetch_detail(post["url"], sem, browser)
            for post in posts
        ]

        total = len(tasks)
        completados = 0
        resultados = []

        # Procesar en lotes para dar feedback de progreso
        BATCH = 20
        for i in range(0, total, BATCH):
            lote = tasks[i:i + BATCH]
            res = await asyncio.gather(*lote)
            resultados.extend(res)
            completados += len(lote)
            log.info("Contenido: %d/%d extraídos", completados, total)

        await browser.close()

    for post, contenido in zip(posts, resultados):
        post["contenido"] = contenido

    return posts


# ---------------------------------------------------------------------------
# Guardado CSV
# ---------------------------------------------------------------------------

def guardar_csv(posts: list[dict], ruta: str) -> None:
    columnas = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columnas)
        writer.writeheader()
        for post in posts:
            writer.writerow({
                "fecha": post.get("fecha", ""),
                "institucion": INSTITUCION,
                "titulo": post.get("titulo", ""),
                "resumen": post.get("resumen", ""),
                "url": post.get("url", ""),
                "contenido": post.get("contenido", ""),
            })
    log.info("CSV guardado en %s (%d filas)", ruta, len(posts))


# ---------------------------------------------------------------------------
# Validación básica
# ---------------------------------------------------------------------------

def validar_csv(ruta: str) -> None:
    with open(ruta, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columnas_esperadas = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
        if reader.fieldnames != columnas_esperadas:
            log.error("Columnas incorrectas: %s", reader.fieldnames)
            return
        rows = list(reader)

    fecha_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    errores = 0
    for i, row in enumerate(rows, start=2):
        if not fecha_re.match(row.get("fecha", "")):
            log.warning("Fila %d: fecha inválida '%s'", i, row.get("fecha"))
            errores += 1
        if not row.get("url", "").startswith("http"):
            log.warning("Fila %d: URL inválida '%s'", i, row.get("url"))
            errores += 1

    log.info(
        "Validacion: %d filas, %d errores detectados",
        len(rows), errores,
    )

    # Muestra de las primeras 3 filas
    log.info("--- Muestra (3 primeras filas) ---")
    for row in rows[:3]:
        log.info(
            "[%s] %s | resumen: %.60s... | contenido: %d chars",
            row["fecha"],
            row["titulo"][:60],
            row["resumen"],
            len(row["contenido"]),
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Scraper MOP — Ministerio de Obras Públicas"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="Limitar a N páginas de la API (100 noticias por página)",
    )
    parser.add_argument(
        "--skip-detail",
        action="store_true",
        help="No extraer contenido completo (solo datos de la API)",
    )
    parser.add_argument(
        "--output",
        default=DATA_PATH,
        help="Ruta del CSV de salida",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    log.info("=== Scraper MOP iniciado ===")
    log.info("max_pages=%s, skip_detail=%s, output=%s",
             args.max_pages, args.skip_detail, args.output)

    # Fase 1: API
    posts = fetch_all_api(max_pages=args.max_pages)

    if not posts:
        log.error("No se obtuvieron posts. Abortando.")
        sys.exit(1)

    # Fase 2: Playwright
    if not args.skip_detail:
        log.info("=== Fase 2: extrayendo contenido de %d noticias ===", len(posts))
        posts = asyncio.run(fetch_all_details(posts))
    else:
        for post in posts:
            post["contenido"] = ""
        log.info("Fase 2 omitida (--skip-detail)")

    # Guardar
    guardar_csv(posts, args.output)

    # Validar
    validar_csv(args.output)

    log.info("=== Scraper MOP finalizado ===")


if __name__ == "__main__":
    main()
