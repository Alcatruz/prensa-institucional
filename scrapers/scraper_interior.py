"""
Scraper — Ministerio del Interior y Seguridad Pública
URL: https://www.interior.gob.cl/noticias/
Método: Playwright async (no hay WP REST API disponible)

Estructura del sitio:
  Listado:
    - Contenedor: div.post.tarjeta
    - Título:     h4.title a   (texto + href)
    - Fecha:      span.meta    (ej. "24 de marzo de 2026")
    - Paginación: a.next.page-numbers → /noticias/page/N/
                  61 páginas x 10 noticias = ~610 registros
  Detalle:
    - Título:     h3.title (dentro de .post .texto)
    - Fecha:      span.meta
    - Contenido:  .contenido (párrafos <p>)
"""

import asyncio
from pathlib import Path
import csv
import logging
import re
import argparse
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── Configuración ────────────────────────────────────────────────────────────
BASE_URL   = "https://www.interior.gob.cl"
LIST_START = "/noticias/"
INSTITUCION = "Ministerio del Interior y Seguridad Pública"
OUTPUT_CSV = str(Path(__file__).resolve().parent.parent / "data" / "interior.csv")
WORKERS    = 4
RETRY_MAX  = 3
PAGE_TIMEOUT = 30_000   # ms
WAIT_AFTER_LOAD = 2_000 # ms

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

# ── Conversión de fechas ─────────────────────────────────────────────────────
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

def parse_fecha(texto: str) -> str:
    """Convierte "24 de marzo de 2026" → "2026-03-24"."""
    texto = texto.strip()
    m = re.match(r"(\d+)\s+de\s+(\w+)\s+de\s+(\d{4})", texto, re.IGNORECASE)
    if m:
        dia, mes_str, anio = m.groups()
        mes = MESES.get(mes_str.lower())
        if mes:
            return f"{anio}-{mes:02d}-{int(dia):02d}"
    log.warning("Fecha no reconocida: %r", texto)
    return texto


# ── Funciones de scraping ────────────────────────────────────────────────────

async def get_listing_page(context, url: str) -> list[dict]:
    """
    Extrae las noticias de una página de listado.
    Retorna lista de dicts con: fecha, titulo, url
    """
    for intento in range(1, RETRY_MAX + 1):
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await page.wait_for_timeout(WAIT_AFTER_LOAD)

            posts = await page.query_selector_all("div.post.tarjeta")
            if not posts:
                log.warning("Sin posts en %s", url)
                await page.close()
                return []

            items = []
            for post in posts:
                try:
                    titulo_elem = await post.query_selector("h4.title a")
                    fecha_elem  = await post.query_selector("span.meta")

                    if not titulo_elem:
                        continue

                    titulo = (await titulo_elem.inner_text()).strip()
                    href   = await titulo_elem.get_attribute("href")
                    fecha_raw = (await fecha_elem.inner_text()).strip() if fecha_elem else ""

                    # La URL puede ser relativa o absoluta
                    full_url = href if href.startswith("http") else BASE_URL + href

                    items.append({
                        "fecha_raw": fecha_raw,
                        "titulo":    titulo,
                        "url":       full_url,
                    })
                except Exception as e:
                    log.warning("Error extrayendo post: %s", e)

            log.info("  Listado %s → %d noticias", url, len(items))
            await page.close()
            return items

        except PlaywrightTimeout:
            log.warning("Timeout en listado %s (intento %d/%d)", url, intento, RETRY_MAX)
            await page.close()
            if intento == RETRY_MAX:
                return []
            await asyncio.sleep(3)
        except Exception as e:
            log.error("Error en listado %s: %s", url, e)
            await page.close()
            return []


async def get_detail_page(context, item: dict) -> dict:
    """
    Extrae contenido completo de una noticia individual.
    Añade: resumen (primer párrafo), contenido, fecha (confirmada desde detalle).
    """
    url = item["url"]
    for intento in range(1, RETRY_MAX + 1):
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await page.wait_for_timeout(WAIT_AFTER_LOAD)

            # Título desde la página de detalle (más completo)
            titulo_elem = await page.query_selector("h3.title")
            if titulo_elem:
                titulo = (await titulo_elem.inner_text()).strip()
            else:
                titulo = item["titulo"]  # fallback al del listado

            # Fecha desde la página de detalle
            fecha_elem = await page.query_selector("span.meta")
            if fecha_elem:
                fecha_raw = (await fecha_elem.inner_text()).strip()
            else:
                fecha_raw = item.get("fecha_raw", "")
            fecha = parse_fecha(fecha_raw)

            # Contenido: todos los párrafos de .contenido
            contenido_elem = await page.query_selector(".contenido")
            if contenido_elem:
                parrafos = await contenido_elem.query_selector_all("p")
                textos = []
                for p in parrafos:
                    t = (await p.inner_text()).strip()
                    if t:
                        textos.append(t)
                contenido = "\n\n".join(textos)
            else:
                # fallback: texto completo del div
                contenido_elem = await page.query_selector(".contenido")
                contenido = (await contenido_elem.inner_text()).strip() if contenido_elem else ""

            # Resumen = primer párrafo del contenido
            if textos:
                resumen = textos[0][:500]
            else:
                resumen = contenido[:500]

            await page.close()
            return {
                "fecha":      fecha,
                "institucion": INSTITUCION,
                "titulo":     titulo,
                "resumen":    resumen,
                "url":        url,
                "contenido":  contenido,
            }

        except PlaywrightTimeout:
            log.warning("Timeout en detalle %s (intento %d/%d)", url, intento, RETRY_MAX)
            await page.close()
            if intento == RETRY_MAX:
                return {
                    "fecha":      parse_fecha(item.get("fecha_raw", "")),
                    "institucion": INSTITUCION,
                    "titulo":     item["titulo"],
                    "resumen":    "",
                    "url":        url,
                    "contenido":  "",
                }
            await asyncio.sleep(3)
        except Exception as e:
            log.error("Error en detalle %s: %s", url, e)
            await page.close()
            return {
                "fecha":      parse_fecha(item.get("fecha_raw", "")),
                "institucion": INSTITUCION,
                "titulo":     item["titulo"],
                "resumen":    "",
                "url":        url,
                "contenido":  "",
            }


# ── Worker pool para detalles ─────────────────────────────────────────────────

async def worker(context, queue: asyncio.Queue, results: list):
    """Consumidor de cola: extrae detalles de noticias."""
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break
        try:
            resultado = await get_detail_page(context, item)
            results.append(resultado)
            log.info("  OK: %s", resultado["titulo"][:70])
        except Exception as e:
            log.error("Worker error: %s", e)
        finally:
            queue.task_done()


# ── Scraping principal ────────────────────────────────────────────────────────

async def scrape(max_pages: int) -> list[dict]:
    log.info("Iniciando scraper — max_pages=%d, workers=%d", max_pages, WORKERS)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        # ── Fase 1: recolectar listados ──────────────────────────────────────
        all_items = []
        current_url = BASE_URL + LIST_START
        page_num = 0

        while current_url and (max_pages == 0 or page_num < max_pages):
            page_num += 1
            log.info("Listado página %d: %s", page_num, current_url)
            items = await get_listing_page(context, current_url)
            all_items.extend(items)

            # Determinar URL de la siguiente página
            # Patrón: /noticias/page/N/
            if max_pages == 0 or page_num < max_pages:
                next_page_url = None
                # Verificar si existe la siguiente página
                next_num = page_num + 1
                # El patrón es predecible: /noticias/page/2/, /noticias/page/3/, ...
                if page_num == 1:
                    next_page_url = BASE_URL + f"/noticias/page/2/"
                else:
                    # Extraer número actual y avanzar
                    m = re.search(r"/noticias/page/(\d+)/", current_url)
                    if m:
                        next_page_url = BASE_URL + f"/noticias/page/{int(m.group(1)) + 1}/"

                # Verificar que la siguiente página existe (si ya tenemos menos de 10 artículos,
                # probablemente estamos en la última)
                if items and len(items) < 10:
                    log.info("Última página detectada (< 10 artículos)")
                    current_url = None
                else:
                    current_url = next_page_url
            else:
                current_url = None

            # Pausa respetuosa entre páginas
            if current_url:
                await asyncio.sleep(1.5)

        log.info("Total noticias recolectadas del listado: %d", len(all_items))

        # Eliminar duplicados por URL
        seen_urls = set()
        unique_items = []
        for item in all_items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                unique_items.append(item)
        log.info("Noticias únicas: %d", len(unique_items))

        # ── Fase 2: extraer contenido de cada noticia ────────────────────────
        queue = asyncio.Queue()
        for item in unique_items:
            await queue.put(item)
        # Señales de fin para cada worker
        for _ in range(WORKERS):
            await queue.put(None)

        results = []
        workers_tasks = [
            asyncio.create_task(worker(context, queue, results))
            for _ in range(WORKERS)
        ]
        await queue.join()
        await asyncio.gather(*workers_tasks)

        await browser.close()

    return results


# ── Guardado CSV ──────────────────────────────────────────────────────────────

def guardar_csv(datos: list[dict], ruta: str):
    """Guarda los datos en CSV con las 6 columnas obligatorias."""
    columnas = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

    # Validación básica
    validos = []
    for row in datos:
        if not row.get("url") or not row.get("titulo"):
            log.warning("Fila descartada (sin URL o título): %r", row)
            continue
        validos.append(row)

    log.info("Guardando %d filas en %s", len(validos), ruta)
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(validos)
    log.info("CSV guardado correctamente.")


# ── Entrada principal ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper Interior.gob.cl")
    parser.add_argument(
        "--max-pages", type=int, default=0,
        help="Máximo de páginas del listado a procesar (0 = todas)"
    )
    args = parser.parse_args()

    datos = asyncio.run(scrape(max_pages=args.max_pages))
    guardar_csv(datos, OUTPUT_CSV)

    # Reporte final
    print(f"\nTotal registros: {len(datos)}")
    if datos:
        print(f"Columnas: {list(datos[0].keys())}")
        print(f"Ejemplo:")
        d = datos[0]
        print(f"  fecha:      {d['fecha']}")
        print(f"  titulo:     {d['titulo'][:80]}")
        print(f"  resumen:    {d['resumen'][:100]}")
        print(f"  contenido:  {d['contenido'][:100]}")
        print(f"  url:        {d['url']}")
