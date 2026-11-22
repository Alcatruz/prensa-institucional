"""
Scraper para noticias del Ministerio de Educación de Chile
Sitio: https://www.mineduc.cl/noticias/
Estructura: paginación /noticias/page/N/, 10 noticias por página, ~280 páginas
Selectores: .box para cards en listado, .content para cuerpo de noticia individual
"""

import asyncio
import csv
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# --- Configuración ---
BASE_URL = "https://www.mineduc.cl/noticias/"
INSTITUCION = "Ministerio de Educación"
OUTPUT_CSV = Path(__file__).parent.parent / "data" / "mineduc.csv"
HEADLESS = True
DELAY_BETWEEN_PAGES = 1.5      # segundos entre páginas de listado
DELAY_BETWEEN_ARTICLES = 1.2   # segundos entre artículos individuales
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0            # factor exponencial entre reintentos
BATCH_SIZE = 5                 # noticias antes de hacer flush al CSV

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)

# Mapa de nombres de mes en español a número
MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12
}


def parse_fecha(texto: str) -> str:
    """
    Convierte 'Lunes 30 de Marzo, 2026' → '2026-03-30'.
    Devuelve '' si no se puede parsear.
    """
    texto = texto.strip().lower()
    # Eliminar nombre del día si viene al principio (ej. 'lunes ')
    texto = re.sub(r'^(lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)\s+', '', texto)
    # Formato esperado: "30 de marzo, 2026" o "30 de marzo 2026"
    m = re.search(r'(\d{1,2})\s+de\s+([a-záéíóúüñ]+)[,\s]+(\d{4})', texto)
    if m:
        dia = int(m.group(1))
        mes_str = m.group(2).strip().rstrip(',')
        anio = int(m.group(3))
        mes = MESES.get(mes_str)
        if mes:
            return f"{anio:04d}-{mes:02d}-{dia:02d}"
    return ""


def cargar_urls_existentes() -> set:
    """Carga las URLs ya presentes en el CSV para modo resume."""
    if not OUTPUT_CSV.exists():
        return set()
    urls = set()
    try:
        with open(OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("url"):
                    urls.add(row["url"].strip())
    except Exception as e:
        log.warning(f"No se pudo leer CSV existente para resume: {e}")
    return urls


def inicializar_csv(append: bool) -> tuple:
    """
    Abre el CSV y devuelve (file_handle, writer).
    Si append=True, abre en modo 'a' (sin escribir header).
    Si append=False, abre en modo 'w' y escribe el header.
    """
    modo = "a" if append else "w"
    fh = open(OUTPUT_CSV, modo, newline="", encoding="utf-8")
    writer = csv.writer(fh)
    if not append:
        writer.writerow(["fecha", "institucion", "titulo", "resumen", "url", "contenido"])
    return fh, writer


async def fetch_with_retry(page, url: str, max_retries: int = MAX_RETRIES) -> bool:
    """
    Navega a url con reintentos y backoff exponencial.
    Devuelve True si tuvo éxito, False si agotó los intentos.
    """
    for intento in range(1, max_retries + 1):
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            return True
        except PlaywrightTimeoutError:
            log.warning(f"Timeout en {url} (intento {intento}/{max_retries})")
        except Exception as e:
            log.warning(f"Error en {url} (intento {intento}/{max_retries}): {e}")
        if intento < max_retries:
            await asyncio.sleep(RETRY_BACKOFF ** intento)
    return False


async def extraer_contenido(page, url: str) -> str:
    """Navega a la URL de la noticia y extrae el texto completo del cuerpo."""
    ok = await fetch_with_retry(page, url)
    if not ok:
        log.error(f"No se pudo cargar artículo: {url}")
        return ""
    try:
        # Selector principal: div.content (el cuerpo de la noticia)
        el = await page.query_selector(".content")
        if el:
            texto = await el.inner_text()
            return texto.strip()
        # Fallback: contentBody
        el = await page.query_selector(".contentBody")
        if el:
            texto = await el.inner_text()
            return texto.strip()
    except Exception as e:
        log.warning(f"Error extrayendo contenido de {url}: {e}")
    return ""


async def extraer_pagina_listado(page, url_pagina: str) -> list:
    """
    Extrae los ítems de una página del listado.
    Devuelve lista de dicts: {url, titulo, resumen, fecha_raw}
    """
    ok = await fetch_with_retry(page, url_pagina)
    if not ok:
        log.error(f"No se pudo cargar página de listado: {url_pagina}")
        return []

    items = []
    try:
        boxes = await page.query_selector_all(".content .box")
        if not boxes:
            # Intento alternativo con selector más amplio
            boxes = await page.query_selector_all(".box")

        for box in boxes:
            try:
                # URL y título: el segundo enlace dentro del box (el primero es la imagen)
                links = await box.query_selector_all("a")
                url_noticia = ""
                titulo = ""
                for link in links:
                    href = await link.get_attribute("href") or ""
                    cls = await link.get_attribute("class") or ""
                    if "thumb" in cls or "vermas" in cls:
                        if not url_noticia:
                            url_noticia = href
                        continue
                    texto_link = (await link.inner_text()).strip()
                    if texto_link:
                        titulo = texto_link
                        if not url_noticia:
                            url_noticia = href

                # Si no encontramos por clases, usamos primer href
                if not url_noticia and links:
                    url_noticia = await links[0].get_attribute("href") or ""

                # Fecha
                fecha_el = await box.query_selector(".fecha")
                fecha_raw = (await fecha_el.inner_text()).strip() if fecha_el else ""

                # Resumen
                extracto_el = await box.query_selector(".extracto")
                resumen = (await extracto_el.inner_text()).strip() if extracto_el else ""
                # Limpiar saltos de línea extra del resumen
                resumen = re.sub(r'\s+', ' ', resumen).strip()

                if url_noticia and titulo:
                    items.append({
                        "url": url_noticia,
                        "titulo": titulo,
                        "resumen": resumen,
                        "fecha_raw": fecha_raw,
                    })
            except Exception as e:
                log.warning(f"Error procesando un box: {e}")

    except Exception as e:
        log.error(f"Error extrayendo listado de {url_pagina}: {e}")

    return items


async def obtener_total_paginas(page) -> int:
    """Obtiene el número total de páginas desde la paginación."""
    try:
        ok = await fetch_with_retry(page, BASE_URL)
        if not ok:
            return 1
        # Busca el mayor número en los links de paginación con /noticias/page/N
        links = await page.query_selector_all("a[href*='/noticias/page/']")
        max_pag = 1
        for link in links:
            href = await link.get_attribute("href") or ""
            m = re.search(r'/noticias/page/(\d+)', href)
            if m:
                n = int(m.group(1))
                if n > max_pag:
                    max_pag = n
        return max_pag
    except Exception as e:
        log.warning(f"Error obteniendo total de páginas: {e}")
        return 280  # fallback conocido


async def scrape():
    urls_existentes = cargar_urls_existentes()
    append_mode = len(urls_existentes) > 0
    if append_mode:
        log.info(f"Modo resume: {len(urls_existentes)} URLs ya procesadas, continuando...")
    else:
        log.info("Iniciando extracción desde cero.")

    fh, writer = inicializar_csv(append=append_mode)
    total_guardadas = 0
    buffer = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=HEADLESS)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            # Página para listados
            page_list = await context.new_page()
            # Página para artículos individuales
            page_art = await context.new_page()

            total_pags = await obtener_total_paginas(page_list)
            log.info(f"Total de páginas detectadas: {total_pags}")

            for num_pag in range(1, total_pags + 1):
                if num_pag == 1:
                    url_pag = BASE_URL
                else:
                    url_pag = f"https://www.mineduc.cl/noticias/page/{num_pag}/"

                log.info(f"Página {num_pag}/{total_pags}: {url_pag}")
                items = await extraer_pagina_listado(page_list, url_pag)

                if not items:
                    log.warning(f"Sin ítems en página {num_pag}, saltando.")
                    await asyncio.sleep(DELAY_BETWEEN_PAGES)
                    continue

                for item in items:
                    url_noticia = item["url"]

                    # Modo resume: saltar si ya está procesada
                    if url_noticia in urls_existentes:
                        log.debug(f"  Ya procesada: {url_noticia}")
                        continue

                    # Extraer contenido completo
                    await asyncio.sleep(DELAY_BETWEEN_ARTICLES)
                    contenido = await extraer_contenido(page_art, url_noticia)

                    fecha = parse_fecha(item["fecha_raw"])
                    if not fecha:
                        log.warning(f"  Fecha no parseada: '{item['fecha_raw']}' en {url_noticia}")

                    fila = [
                        fecha,
                        INSTITUCION,
                        item["titulo"],
                        item["resumen"],
                        url_noticia,
                        contenido,
                    ]
                    buffer.append(fila)
                    urls_existentes.add(url_noticia)

                    # Flush por batch
                    if len(buffer) >= BATCH_SIZE:
                        writer.writerows(buffer)
                        fh.flush()
                        total_guardadas += len(buffer)
                        log.info(f"  Flush: {total_guardadas} noticias guardadas en total.")
                        buffer = []

                await asyncio.sleep(DELAY_BETWEEN_PAGES)

            # Flush final
            if buffer:
                writer.writerows(buffer)
                fh.flush()
                total_guardadas += len(buffer)

            await browser.close()

    finally:
        fh.close()

    log.info(f"Extracción completada. Total noticias guardadas esta ejecución: {total_guardadas}")
    log.info(f"CSV guardado en: {OUTPUT_CSV}")
    return total_guardadas


if __name__ == "__main__":
    t0 = time.time()
    total = asyncio.run(scrape())
    elapsed = time.time() - t0
    log.info(f"Tiempo total: {elapsed:.1f}s | Noticias: {total}")
