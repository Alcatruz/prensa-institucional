"""
Scraper para el sitio de noticias de la Subsecretaría de Telecomunicaciones
(SUBTEL) de Chile.

URL base : https://www.subtel.gob.cl/sala-de-prensa/noticias/
Salida   : ../data/subtel.csv

Columnas : fecha, institucion, titulo, resumen, url, contenido

Uso
----
  # Todas las páginas (puede tomar horas):
  python scraper_subtel.py

  # Solo las primeras N páginas del listado:
  python scraper_subtel.py --max-pages 10
"""

import argparse
from pathlib import Path
import asyncio
import csv
import logging
import re
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BASE_URL    = "https://www.subtel.gob.cl/sala-de-prensa/noticias/"
INSTITUCION = "Subsecretaría de Telecomunicaciones (SUBTEL)"
OUTPUT_PATH = str(Path(__file__).resolve().parent.parent / "data" / "subtel.csv")
COLUMNAS    = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

CONCURRENCIA = 5        # páginas de artículo procesadas en paralelo
PAUSA_LISTA  = 1.5      # segundos entre páginas del listado
PAUSA_ART    = 0.8      # segundos entre artículos dentro de cada worker

MESES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def parsear_fecha(texto: str) -> str:
    """Convierte 'DD de Mes de AAAA' → 'AAAA-MM-DD'. Devuelve '' si falla."""
    if not texto:
        return ""
    texto = texto.strip().lower()
    m = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+(?:de\s+)?(\d{4})", texto)
    if m:
        dia, mes_str, anio = m.group(1), m.group(2), m.group(3)
        mes = MESES.get(mes_str)
        if mes:
            return f"{anio}-{mes}-{dia.zfill(2)}"
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    log.debug("No se pudo parsear la fecha: %r", texto)
    return ""


def limpiar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


# ---------------------------------------------------------------------------
# Extracción de un artículo individual
# ---------------------------------------------------------------------------
async def extraer_articulo(page, url: str) -> dict:
    resultado = {"titulo": "", "fecha": "", "resumen": "", "contenido": ""}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        log.warning("Timeout al cargar artículo: %s", url)
        return resultado

    # Título
    for sel in ["h1.entry-title", "h1.post-title", "h1", "h2.entry-title"]:
        el = page.locator(sel).first
        if await el.count() > 0:
            resultado["titulo"] = limpiar(await el.inner_text())
            break

    # Fecha: el sitio SUBTEL usa div.texto que contiene la fecha al inicio.
    # También buscamos en otros selectores comunes como fallback.
    fecha_raw = ""

    # Primero intentar leer la fecha desde div.texto (patrón "DD de Mes de AAAA")
    texto_el = page.locator("div.texto").first
    if await texto_el.count() > 0:
        texto_completo = await texto_el.inner_text()
        m = re.search(r"\d{1,2}\s+de\s+\w+\s+(?:de\s+)?\d{4}", texto_completo, re.IGNORECASE)
        if m:
            fecha_raw = m.group(0)

    # Fallback a selectores semánticos
    if not fecha_raw:
        for sel in ["time.entry-date", "span.date", "span.entry-date",
                    ".post-date", ".fecha", "time"]:
            el = page.locator(sel).first
            if await el.count() > 0:
                dt = await el.get_attribute("datetime")
                if dt:
                    fecha_raw = dt[:10]
                    break
                fecha_raw = limpiar(await el.inner_text())
                if fecha_raw:
                    break

    # Último recurso: regex sobre todo el body
    if not fecha_raw:
        body = await page.locator("body").inner_text()
        m = re.search(r"\d{1,2}\s+de\s+\w+\s+(?:de\s+)?\d{4}", body, re.IGNORECASE)
        if m:
            fecha_raw = m.group(0)

    resultado["fecha"] = parsear_fecha(fecha_raw)

    # Contenido: el sitio SUBTEL usa div.contenido o div.contenido-pagina
    contenido = ""
    for sel in ["div.contenido", "div.contenido-pagina", "div.texto",
                "div.entry-content", "div.post-content",
                "article .entry-content", "div.td-post-content",
                "div.content-inner", "article"]:
        el = page.locator(sel).first
        if await el.count() > 0:
            contenido = limpiar(await el.inner_text())
            if len(contenido) > 100:
                break
    resultado["contenido"] = contenido

    # Resumen: primer fragmento de hasta 300 caracteres del contenido
    if contenido:
        primer = contenido.split(". ")[0] if ". " in contenido[:400] else contenido[:300]
        resultado["resumen"] = primer[:300]

    return resultado


# ---------------------------------------------------------------------------
# Worker concurrente de artículos
# ---------------------------------------------------------------------------
async def worker_articulos(worker_id: int, queue: asyncio.Queue,
                            resultados: list, context) -> None:
    page = await context.new_page()
    while True:
        try:
            idx, total, noticia = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        url = noticia["url"]
        log.info("[worker %d | %d/%d] %s", worker_id, idx, total, url)

        art = await extraer_articulo(page, url)

        titulo   = art["titulo"]   or noticia["titulo_lista"]
        fecha    = art["fecha"]    or parsear_fecha(noticia["fecha_lista"])
        resumen  = art["resumen"]  or noticia["resumen_lista"]
        contenido = art["contenido"]

        if titulo:
            resultados.append([fecha, INSTITUCION, titulo, resumen, url, contenido])
        else:
            log.warning("Sin título para %s — fila omitida", url)

        queue.task_done()
        await asyncio.sleep(PAUSA_ART)

    await page.close()


# ---------------------------------------------------------------------------
# Extracción del listado (paginado)
# ---------------------------------------------------------------------------
async def extraer_listado(page, max_pages: int | None) -> list[dict]:
    noticias = []
    url_pagina = BASE_URL
    pagina_num = 1

    while url_pagina:
        if max_pages and pagina_num > max_pages:
            log.info("Límite de páginas alcanzado (%d).", max_pages)
            break

        log.info("Listado — página %d: %s", pagina_num, url_pagina)
        try:
            await page.goto(url_pagina, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeout:
            log.error("Timeout en listado página %d — abortando paginación", pagina_num)
            break

        await page.wait_for_selector("article, .post, .entry", timeout=15_000)

        items = page.locator("article, .td-block-span6, .post")
        count = await items.count()
        log.info("  %d ítems encontrados", count)

        for i in range(count):
            item = items.nth(i)

            enlace_el = item.locator("a").first
            url_noticia = ""
            if await enlace_el.count() > 0:
                url_noticia = await enlace_el.get_attribute("href") or ""

            titulo = ""
            for sel in ["h2", "h3", "h4", ".entry-title", ".td-module-title"]:
                t_el = item.locator(sel).first
                if await t_el.count() > 0:
                    titulo = limpiar(await t_el.inner_text())
                    break
            if not titulo and await enlace_el.count() > 0:
                titulo = limpiar(await enlace_el.inner_text())

            fecha_raw = ""
            for sel in ["time", ".td-post-date", ".date", "span.date", ".post-date"]:
                f_el = item.locator(sel).first
                if await f_el.count() > 0:
                    dt = await f_el.get_attribute("datetime")
                    fecha_raw = dt[:10] if dt else limpiar(await f_el.inner_text())
                    if fecha_raw:
                        break

            resumen = ""
            for sel in [".td-excerpt", ".entry-summary", "p"]:
                r_el = item.locator(sel).first
                if await r_el.count() > 0:
                    resumen = limpiar(await r_el.inner_text())[:300]
                    if resumen:
                        break

            if url_noticia and url_noticia.startswith("http"):
                noticias.append({
                    "url": url_noticia,
                    "titulo_lista": titulo,
                    "fecha_lista": fecha_raw,
                    "resumen_lista": resumen,
                })

        # Siguiente página
        siguiente = page.locator(
            "a.next.page-numbers, a:has-text('SIGUIENTE'), .next a"
        ).first
        if await siguiente.count() > 0:
            url_pagina = await siguiente.get_attribute("href")
            pagina_num += 1
            await asyncio.sleep(PAUSA_LISTA)
        else:
            url_pagina = None

    log.info("Listado completo: %d noticias.", len(noticias))
    return noticias


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------
async def scrape(max_pages: int | None = None) -> list[list]:
    resultados: list[list] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="es-CL",
        )

        # Fase 1: recorrer paginación del listado
        page_lista = await context.new_page()
        noticias = await extraer_listado(page_lista, max_pages)
        await page_lista.close()

        if not noticias:
            log.error("No se encontraron noticias en el listado.")
            await browser.close()
            return resultados

        # Fase 2: extraer artículos en paralelo
        total = len(noticias)
        queue: asyncio.Queue = asyncio.Queue()
        for idx, n in enumerate(noticias, 1):
            await queue.put((idx, total, n))

        workers = [
            asyncio.create_task(
                worker_articulos(w_id, queue, resultados, context)
            )
            for w_id in range(1, CONCURRENCIA + 1)
        ]
        await asyncio.gather(*workers)
        await browser.close()

    return resultados


# ---------------------------------------------------------------------------
# Guardado CSV
# ---------------------------------------------------------------------------
def guardar_csv(filas: list[list], ruta: str) -> None:
    # Deduplicar por URL
    vistas: set[str] = set()
    unicas = []
    for fila in filas:
        if fila[4] not in vistas:
            vistas.add(fila[4])
            unicas.append(fila)

    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(COLUMNAS)
        writer.writerows(unicas)

    log.info("CSV guardado: %s  (%d registros)", ruta, len(unicas))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper SUBTEL noticias")
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="Número máximo de páginas del listado a procesar (default: todas)"
    )
    args = parser.parse_args()

    datos = asyncio.run(scrape(max_pages=args.max_pages))
    if datos:
        guardar_csv(datos, OUTPUT_PATH)
        log.info("Proceso completado. Registros totales: %d", len(datos))
    else:
        log.error("No se extrajeron datos.")
