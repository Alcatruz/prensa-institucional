"""
Scraper para noticias del Ministerio del Medio Ambiente (MMA) de Chile.
Fuente: https://mma.gob.cl/category/noticias/

Columnas del CSV de salida:
    fecha, institucion, titulo, resumen, url, contenido

Uso:
    python scraper_mma.py                  # todas las páginas
    python scraper_mma.py --max-pages 5    # limitar a 5 páginas del listado
"""

import asyncio
import argparse
import csv
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

BASE_URL = "https://mma.gob.cl/category/noticias/"
INSTITUCION = "Ministerio del Medio Ambiente"
OUTPUT_CSV = Path(__file__).parent.parent / "data" / "mma.csv"

# Workers en paralelo para la visita individual de artículos
ARTICLE_WORKERS = 4

# Pausas (segundos) para ser amables con el servidor
DELAY_BETWEEN_PAGES = 1.5      # entre páginas del listado
DELAY_BETWEEN_ARTICLES = 0.5   # entre artículos dentro de un worker

# Reintentos ante errores de red
MAX_RETRIES = 3
RETRY_DELAY = 3.0

# User-agent realista
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper_mma")

# ---------------------------------------------------------------------------
# Helpers de fecha
# ---------------------------------------------------------------------------

MESES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}


def parse_fecha(texto: str) -> str:
    """
    Convierte 'DD mes, YYYY' o 'DD mes YYYY' a 'YYYY-MM-DD'.
    Devuelve '' si no puede parsear.
    """
    texto = texto.strip().lower()
    # Patron: "19 marzo, 2026" o "19 marzo 2026"
    m = re.match(r"(\d{1,2})\s+([a-záéíóúñ]+),?\s+(\d{4})", texto)
    if m:
        dia, mes_txt, anio = m.group(1), m.group(2), m.group(3)
        mes = MESES.get(mes_txt)
        if mes:
            return f"{anio}-{mes}-{dia.zfill(2)}"
    # Intentar con datetime como fallback
    for fmt in ("%d de %B de %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, fmt).strftime("%Y-%m-%D")
        except ValueError:
            pass
    log.warning("No se pudo parsear fecha: %r", texto)
    return ""


# ---------------------------------------------------------------------------
# Extracción de artículo individual
# ---------------------------------------------------------------------------

async def extraer_articulo(page, url: str) -> dict:
    """
    Visita una URL de artículo y extrae titulo, fecha, resumen y contenido.
    Devuelve un dict con las claves esperadas.
    """
    for intento in range(1, MAX_RETRIES + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_load_state("domcontentloaded")

            # Título
            titulo = ""
            h1 = page.locator("h1.entry-title")
            if await h1.count() > 0:
                titulo = (await h1.first.text_content() or "").strip()
            if not titulo:
                titulo = (await page.title()).strip()

            # Fecha: <span class="published"> o <time class="entry-date">
            fecha_raw = ""
            for sel in ["span.published", "time.entry-date", ".entry-meta .published"]:
                el = page.locator(sel)
                if await el.count() > 0:
                    fecha_raw = (await el.first.text_content() or "").strip()
                    if fecha_raw:
                        break
            fecha = parse_fecha(fecha_raw) if fecha_raw else ""

            # Contenido completo: div.entry-content
            contenido = ""
            content_div = page.locator("div.entry-content")
            if await content_div.count() > 0:
                # Extraer sólo texto, sin scripts ni estilos
                contenido = (await content_div.first.inner_text() or "").strip()
                # Normalizar espacios en blanco excesivos
                contenido = re.sub(r"\n{3,}", "\n\n", contenido)
                contenido = re.sub(r"[ \t]+", " ", contenido)

            # Resumen: primer párrafo no vacío del contenido
            resumen = ""
            if contenido:
                parrafos = [p.strip() for p in contenido.split("\n") if p.strip()]
                resumen = parrafos[0][:500] if parrafos else ""

            return {
                "titulo": titulo,
                "fecha": fecha,
                "resumen": resumen,
                "contenido": contenido,
                "url": url,
            }

        except PWTimeout:
            log.warning("Timeout en %s (intento %d/%d)", url, intento, MAX_RETRIES)
        except Exception as exc:
            log.warning("Error en %s (intento %d/%d): %s", url, intento, MAX_RETRIES, exc)

        if intento < MAX_RETRIES:
            await asyncio.sleep(RETRY_DELAY)

    log.error("Fallaron todos los intentos para %s", url)
    return {"titulo": "", "fecha": "", "resumen": "", "contenido": "", "url": url}


# ---------------------------------------------------------------------------
# Worker: consume la cola de URLs y extrae artículos
# ---------------------------------------------------------------------------

async def worker(worker_id: int, browser, queue: asyncio.Queue, results: list):
    context = await browser.new_context(user_agent=USER_AGENT)
    page = await context.new_page()
    log.info("Worker %d iniciado.", worker_id)

    while True:
        try:
            url = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        log.info("Worker %d → %s", worker_id, url)
        datos = await extraer_articulo(page, url)
        results.append(datos)
        queue.task_done()
        await asyncio.sleep(DELAY_BETWEEN_ARTICLES)

    await context.close()
    log.info("Worker %d finalizado.", worker_id)


# ---------------------------------------------------------------------------
# Scraping del listado de noticias
# ---------------------------------------------------------------------------

async def obtener_urls_listado(page, max_pages: int | None) -> list[str]:
    """
    Recorre las páginas del listado y recolecta las URLs de cada artículo.
    """
    urls = []
    pagina = 1

    while True:
        if pagina == 1:
            url_pagina = BASE_URL
        else:
            url_pagina = f"https://mma.gob.cl/category/noticias/page/{pagina}/"

        log.info("Listado página %d → %s", pagina, url_pagina)

        for intento in range(1, MAX_RETRIES + 1):
            try:
                await page.goto(url_pagina, wait_until="domcontentloaded", timeout=30_000)
                break
            except PWTimeout:
                log.warning("Timeout cargando listado pág %d (intento %d)", pagina, intento)
                if intento == MAX_RETRIES:
                    log.error("No se pudo cargar pág %d, abortando.", pagina)
                    return urls
                await asyncio.sleep(RETRY_DELAY)

        # Recopilar enlaces de artículos
        # Los artículos están en <article> o en contenedores con clase "entry"
        # Buscamos todos los <a> con href que apunten a posts del MMA
        # El patrón de URL es: https://mma.gob.cl/YYYY/MM/DD/slug/ o https://mma.gob.cl/slug/
        enlaces = await page.locator("article a[href*='mma.gob.cl']").all()
        if not enlaces:
            # Fallback: cualquier enlace de artículo en el main content
            enlaces = await page.locator(".entry-title a, h2 a[href*='mma.gob.cl'], h3 a[href*='mma.gob.cl']").all()

        hrefs_pagina = set()
        for el in enlaces:
            href = await el.get_attribute("href")
            if href and "mma.gob.cl" in href and "/category/" not in href and "/tag/" not in href:
                # Excluir la propia página de categoría, tags y recursos
                if href.endswith("/") or re.search(r"/\d{4}/\d{2}/", href):
                    hrefs_pagina.add(href.rstrip("/") + "/")

        nuevas = [u for u in hrefs_pagina if u not in urls]
        log.info("  Encontradas %d URLs nuevas en pág %d.", len(nuevas), pagina)
        urls.extend(nuevas)

        if not nuevas:
            log.info("Sin nuevas URLs en pág %d, deteniendo.", pagina)
            break

        # Verificar si hay página siguiente
        sig = page.locator("a:has-text('Siguiente'), a.next")
        if await sig.count() == 0:
            log.info("No hay página siguiente. Fin del listado.")
            break

        if max_pages and pagina >= max_pages:
            log.info("Límite de %d páginas alcanzado.", max_pages)
            break

        pagina += 1
        await asyncio.sleep(DELAY_BETWEEN_PAGES)

    log.info("Total URLs recolectadas: %d", len(urls))
    return urls


# ---------------------------------------------------------------------------
# Guardado del CSV
# ---------------------------------------------------------------------------

def guardar_csv(datos: list[dict], ruta: Path):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    columnas = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

    # Deduplicar por URL
    vistos = set()
    unicos = []
    for fila in datos:
        if fila["url"] not in vistos:
            vistos.add(fila["url"])
            unicos.append(fila)

    # Ordenar por fecha descendente (más reciente primero)
    unicos.sort(key=lambda x: x.get("fecha", ""), reverse=True)

    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
        writer.writeheader()
        for fila in unicos:
            fila["institucion"] = INSTITUCION
            writer.writerow(fila)

    log.info("CSV guardado: %s (%d filas)", ruta, len(unicos))


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

async def main(max_pages: int | None):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # 1. Recolectar URLs del listado con un contexto dedicado
        list_context = await browser.new_context(user_agent=USER_AGENT)
        list_page = await list_context.new_page()
        urls = await obtener_urls_listado(list_page, max_pages)
        await list_context.close()

        if not urls:
            log.error("No se recolectó ninguna URL. Abortando.")
            await browser.close()
            return []

        # 2. Distribuir URLs en una cola y procesarlas con workers en paralelo
        queue: asyncio.Queue = asyncio.Queue()
        for url in urls:
            await queue.put(url)

        results: list[dict] = []
        tasks = [
            asyncio.create_task(worker(i + 1, browser, queue, results))
            for i in range(min(ARTICLE_WORKERS, len(urls)))
        ]
        await asyncio.gather(*tasks)
        await browser.close()

    return results


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper de noticias del MMA de Chile")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="Limitar a N páginas del listado (por defecto: todas)",
    )
    args = parser.parse_args()

    log.info("Iniciando scraper MMA (max_pages=%s)...", args.max_pages)
    datos = asyncio.run(main(args.max_pages))

    if datos:
        guardar_csv(datos, OUTPUT_CSV)
        log.info("Proceso completado. %d noticias extraídas.", len(datos))
    else:
        log.error("No se obtuvieron datos.")
        sys.exit(1)
