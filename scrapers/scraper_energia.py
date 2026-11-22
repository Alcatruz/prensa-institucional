"""
Scraper: Ministerio de Energía de Chile
URL: https://energia.gob.cl/noticias
CMS: Drupal 7 (minenergia theme)
Paginación: ?page=N (ul.pagination, sin enlace "siguiente" = última página)
Artículo: article.noticia-detalle-contenido
"""

import asyncio
import csv
import logging
import os
import random
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import (
    async_playwright,
    TimeoutError as PWTimeoutError,
    Error as PWError,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL = "https://energia.gob.cl"
LIST_URL = "https://energia.gob.cl/noticias"
INSTITUCION = "Ministerio de Energía"
OUTPUT_CSV = Path(__file__).parent.parent / "data" / "energia.csv"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

# Concurrencia para artículos individuales
# El sitio energía.gob.cl tiene WAF que devuelve 403 con múltiples requests simultáneos.
# Procesar artículos de forma secuencial (ARTICLE_CONCURRENCY=1) para evitar el bloqueo.
# NOTA: se comparte un único contexto de navegador (cookies persistentes) para no
# re-disparar el challenge del WAF en cada artículo — mantener concurrencia en 1.
ARTICLE_CONCURRENCY = 1
DELAY_BETWEEN_PAGES = 4.0     # segundos base entre páginas del listado
DELAY_BETWEEN_ARTICLES = 3.0  # segundos base entre artículos (secuencial)
DELAY_JITTER = 2.0            # 0..N segundos aleatorios extra sobre cada delay
MAX_RETRIES = 4
COOLDOWN_403 = 90.0          # pausa larga tras 403 persistente (WAF enfriándose)
COOLDOWN_403_UMBRAL = 5      # nº de 403 seguidos que dispara el enfriamiento

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Cabeceras extra para parecer un navegador real (además del UA del contexto).
EXTRA_HEADERS = {
    "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
}


class WAFState:
    """Contador global de 403 consecutivos para enfriamiento adaptativo."""
    consecutive_403 = 0

    @classmethod
    def hit_403(cls) -> int:
        cls.consecutive_403 += 1
        return cls.consecutive_403

    @classmethod
    def reset(cls) -> None:
        cls.consecutive_403 = 0


async def enfriar_si_hace_falta() -> None:
    """Si se acumulan demasiados 403 seguidos, pausa larga para que el WAF se calme."""
    if WAFState.consecutive_403 >= COOLDOWN_403_UMBRAL:
        log.warning("WAF: %d respuestas 403 seguidas. Enfriando %.0fs...",
                    WAFState.consecutive_403, COOLDOWN_403)
        await asyncio.sleep(COOLDOWN_403)


async def dormir(base: float) -> None:
    """Pausa con jitter aleatorio para parecer menos robótico."""
    await asyncio.sleep(base + random.uniform(0, DELAY_JITTER))

# ---------------------------------------------------------------------------
# Utilidades de fecha
# ---------------------------------------------------------------------------
MESES = {
    "ene": "01", "feb": "02", "mar": "03", "abr": "04",
    "may": "05", "jun": "06", "jul": "07", "ago": "08",
    "sep": "09", "oct": "10", "nov": "11", "dic": "12",
}

def parse_fecha(texto: str) -> str:
    """Convierte '10 Abr 2026' → '2026-04-10'."""
    texto = texto.strip().lower()
    m = re.match(r"(\d{1,2})\s+([a-záéíóú]+)\.?\s+(\d{4})", texto)
    if m:
        dia, mes_txt, anio = m.group(1), m.group(2)[:3], m.group(3)
        mes = MESES.get(mes_txt, "01")
        return f"{anio}-{mes}-{dia.zfill(2)}"
    # Intentar ISO directo
    try:
        return datetime.fromisoformat(texto).strftime("%Y-%m-%d")
    except Exception:
        pass
    return texto  # fallback

# ---------------------------------------------------------------------------
# Modo resume: cargar URLs ya procesadas
# ---------------------------------------------------------------------------
def cargar_urls_procesadas() -> set:
    procesadas = set()
    if OUTPUT_CSV.exists():
        with open(OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames and "url" in reader.fieldnames:
                for row in reader:
                    if row.get("url"):
                        procesadas.add(row["url"].strip())
        log.info("Resume: %d URLs ya procesadas encontradas en CSV.", len(procesadas))
    return procesadas

# ---------------------------------------------------------------------------
# Inicialización del CSV
# ---------------------------------------------------------------------------
COLUMNAS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

def inicializar_csv() -> bool:
    """Crea el CSV con cabeceras si no existe. Devuelve True si es nuevo."""
    if not OUTPUT_CSV.exists():
        OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(COLUMNAS)
        log.info("CSV creado: %s", OUTPUT_CSV)
        return True
    return False

def escribir_filas(filas: list[dict]) -> None:
    """Escribe filas al CSV de forma incremental."""
    if not filas:
        return
    with open(OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS)
        writer.writerows(filas)
    log.info("  → %d filas escritas al CSV.", len(filas))

# ---------------------------------------------------------------------------
# Extracción del listado (una página)
# ---------------------------------------------------------------------------
async def extraer_listado_pagina(page, page_num: int) -> tuple[list[dict], bool]:
    """
    Navega a ?page=N y extrae los artículos del listado.
    Devuelve (lista_items, hay_siguiente_pagina).
    Cada item: {titulo, resumen, url, fecha_raw}
    """
    url = f"{LIST_URL}?page={page_num}"
    html = ""
    for intento in range(1, MAX_RETRIES + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000)
            html = await page.content()
            # Detectar 403 WAF
            if len(html) < 1000 or '403 Forbidden' in html:
                WAFState.hit_403()
                wait_secs = min(60, 15 * intento)
                log.warning("403 en listado página %d (intento %d). Esperando %ds.",
                             page_num, intento, wait_secs)
                await enfriar_si_hace_falta()
                if intento == MAX_RETRIES:
                    return [], False
                await asyncio.sleep(wait_secs)
                continue
            WAFState.reset()
            break
        except PWError as e:
            # Cualquier error de navegación (timeout, ERR_NETWORK_CHANGED,
            # ERR_CONNECTION_RESET, DNS, etc.): reintentar con backoff en vez de crashear.
            log.warning("Error de red en página %d (intento %d/%d): %s",
                        page_num, intento, MAX_RETRIES, str(e).splitlines()[0][:120])
            if intento == MAX_RETRIES:
                return [], False
            await asyncio.sleep(min(30, 5 * intento))

    if not html or len(html) < 1000:
        return [], False

    items = []

    # ---- Artículo destacado (view-id-noticias_page) ----
    # Estructura: <p class="date-text">DD Mes YYYY</p> <h2>...</h2> <p>...</p>
    # <a href="/noticias/...">
    destacado_block = re.search(
        r'view-id-noticias_page.*?(?=view-id-noticias_secundarias|view-id-noticias_mas_noticias|</section>)',
        html, re.DOTALL
    )
    if destacado_block:
        blk = destacado_block.group()
        fecha_m = re.search(r'<p class="date-text">\s*(.*?)\s*</p>', blk, re.DOTALL)
        titulo_m = re.search(r'<h2>(.*?)</h2>', blk, re.DOTALL)
        resumen_m = re.search(r'<p>\s*(?!<)[^<]{30,}', blk)
        href_m = re.search(r'<a href="(/noticias/[^"]+)"[^>]*>\s*Continuar', blk)
        if not href_m:
            href_m = re.search(r'<a href="(/noticias/[^"]+)"', blk)
        if href_m and titulo_m:
            fecha_raw = fecha_m.group(1).strip() if fecha_m else ""
            titulo_txt = re.sub(r'<[^>]+>', '', titulo_m.group(1)).strip()
            resumen_txt = re.sub(r'<[^>]+>', '', resumen_m.group(0)).strip() if resumen_m else ""
            items.append({
                "titulo": titulo_txt,
                "resumen": resumen_txt,
                "url": BASE_URL + href_m.group(1),
                "fecha_raw": fecha_raw,
            })

    # ---- Artículos secundarios (cards con h3) ----
    # Estructura: div.thumbnail.card > div.caption > p.date-text + a[href] > h3 + p
    # Usamos búsqueda por índice para evitar problemas con divs anidados
    search_start = 0
    while True:
        card_start = html.find('<div class="thumbnail card">', search_start)
        if card_start < 0:
            break
        # Buscar el cierre de la caption div (dos </div> después del caption)
        # La estructura es: thumbnail card > img-card + caption > ...
        # Encontrar el final: buscamos </div> que cierre thumbnail card
        # Estrategia: avanzar hasta encontrar la siguiente thumbnail card o la sección de paginación
        next_card = html.find('<div class="thumbnail card">', card_start + 10)
        next_pager = html.find('class="pagination"', card_start)
        if next_card < 0:
            card_end = (next_pager if next_pager > 0 else card_start + 3000)
        else:
            card_end = next_card

        card = html[card_start:card_end]
        search_start = card_start + 10

        fecha_m = re.search(r'<p class="date-text">\s*(.*?)\s*</p>', card, re.DOTALL)
        href_m = re.search(r'<a href="(/noticias/[^"]+)"[^>]*>\s*\n?\s*<h3>', card)
        if not href_m:
            href_m = re.search(r'<a href="(/noticias/[^"]+)"', card)
        titulo_m = re.search(r'<h3>(.*?)</h3>', card, re.DOTALL)
        # Párrafo de resumen: buscar <p> que no tenga class (cuerpo de texto)
        parrafos = re.findall(r'<p(?:\s+[^>]*)?>(?!<)(.*?)</p>', card, re.DOTALL)
        # Tomar el último párrafo como resumen
        resumen_raw = parrafos[-1] if parrafos else ""
        resumen_txt = re.sub(r'<[^>]+>', '', resumen_raw).strip()
        resumen_txt = re.sub(r'[\u00a0\u200b]+', ' ', resumen_txt)
        resumen_txt = re.sub(r'\s+', ' ', resumen_txt).strip()

        if href_m and titulo_m:
            titulo_txt = re.sub(r'<[^>]+>', '', titulo_m.group(1)).strip()
            fecha_raw = fecha_m.group(1).strip() if fecha_m else ""
            full_url = BASE_URL + href_m.group(1)
            # Evitar duplicado con el destacado
            if not any(i["url"] == full_url for i in items):
                items.append({
                    "titulo": titulo_txt,
                    "resumen": resumen_txt,
                    "url": full_url,
                    "fecha_raw": fecha_raw,
                })

    # ---- ¿Hay página siguiente? ----
    hay_siguiente = bool(re.search(r'class="next".*?siguiente', html, re.DOTALL))

    log.info("Página %d: %d artículos encontrados, siguiente=%s", page_num, len(items), hay_siguiente)
    return items, hay_siguiente

# ---------------------------------------------------------------------------
# Extracción del contenido de un artículo individual
# ---------------------------------------------------------------------------
async def extraer_contenido_articulo(page, url: str) -> tuple[str, str, str]:
    """
    Navega al artículo y extrae (titulo_completo, fecha_iso, contenido).
    """
    tag = 'noticia-detalle-contenido'
    html = ""
    for intento in range(1, MAX_RETRIES + 1):
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000)
            html = await page.content()
            # Detectar 403 WAF (respuesta truncada)
            if len(html) < 1000 or '403 Forbidden' in html:
                WAFState.hit_403()
                wait_secs = min(60, 15 * intento)
                log.warning("403/respuesta truncada en %s (intento %d). Esperando %ds.",
                             url, intento, wait_secs)
                await enfriar_si_hace_falta()
                if intento == MAX_RETRIES:
                    log.warning("No se pudo cargar (403 persistente): %s", url)
                    return "", "", ""
                await asyncio.sleep(wait_secs)
                continue
            WAFState.reset()
            break
        except PWError as e:
            log.warning("Error de red en %s (intento %d/%d): %s",
                        url, intento, MAX_RETRIES, str(e).splitlines()[0][:120])
            if intento == MAX_RETRIES:
                return "", "", ""
            await asyncio.sleep(min(30, 5 * intento))

    if not html:
        return "", "", ""

    # Bloque principal: article.noticia-detalle-contenido
    # Búsqueda por índice para evitar problemas con </article> anidados
    idx_tag = html.find(tag)
    blk = ""
    if idx_tag > 0:
        # Encontrar el </article> que cierra ESTE artículo (el siguiente después del tag)
        art_close = html.find('</article>', idx_tag)
        if art_close > 0:
            # Retroceder al '<' del <article que contiene noticia-detalle-contenido
            art_open = html.rfind('<article', 0, idx_tag + 1)
            if art_open >= 0:
                blk = html[art_open:art_close]

    if not blk:
        log.warning("No se encontró contenido en: %s", url)
        return "", "", ""

    # Título completo
    titulo_m = re.search(r'<h2>(.*?)</h2>', blk, re.DOTALL)
    titulo = re.sub(r'<[^>]+>', '', titulo_m.group(1)).strip() if titulo_m else ""

    # Fecha
    fecha_m = re.search(r'<p class="date-text">(.*?)</p>', blk, re.DOTALL)
    fecha_raw = fecha_m.group(1).strip() if fecha_m else ""
    fecha_iso = parse_fecha(fecha_raw) if fecha_raw else ""

    # Contenido: todos los párrafos después de la fecha
    # Remover imagen, h2, p.date-text, quedan los párrafos del cuerpo
    blk_clean = re.sub(r'<img[^>]+>', '', blk)
    blk_clean = re.sub(r'<h2>.*?</h2>', '', blk_clean, flags=re.DOTALL)
    blk_clean = re.sub(r'<p class="date-text">.*?</p>', '', blk_clean, flags=re.DOTALL)
    parrafos = re.findall(r'<p[^>]*>(.*?)</p>', blk_clean, re.DOTALL)
    parrafos_txt = []
    for p in parrafos:
        txt = re.sub(r'<[^>]+>', '', p).strip()
        # Normalizar espacios/NBSP
        txt = re.sub(r'[\u00a0\u200b]+', ' ', txt)
        txt = re.sub(r'\s+', ' ', txt).strip()
        if txt and len(txt) > 5:
            parrafos_txt.append(txt)

    contenido = "\n\n".join(parrafos_txt)

    return titulo, fecha_iso, contenido

# ---------------------------------------------------------------------------
# Worker de artículos (semáforo para concurrencia controlada)
# ---------------------------------------------------------------------------
async def worker_articulo(sem, page, item: dict, procesadas: set) -> dict | None:
    """Procesa un artículo usando la PÁGINA COMPARTIDA (cookies del WAF persistentes).

    Requiere ARTICLE_CONCURRENCY=1: el semáforo garantiza un único artículo a la vez
    sobre la misma pestaña, evitando re-disparar el challenge del WAF en cada request.
    """
    url = item["url"]
    if url in procesadas:
        return None  # ya procesado, saltar

    async with sem:
        titulo, fecha_iso, contenido = await extraer_contenido_articulo(page, url)
        await dormir(DELAY_BETWEEN_ARTICLES)

    if not titulo:
        titulo = item.get("titulo", "")
    if not fecha_iso:
        fecha_iso = parse_fecha(item.get("fecha_raw", ""))

    resumen = item.get("resumen", "")

    # Validación mínima
    if not fecha_iso or not titulo:
        log.warning("Datos incompletos para: %s", url)
        return None

    return {
        "fecha": fecha_iso,
        "institucion": INSTITUCION,
        "titulo": titulo,
        "resumen": resumen,
        "url": url,
        "contenido": contenido,
    }

# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------
async def scrape(max_pages: int = 0, start_page: int = 0):
    """
    max_pages: si > 0, limita el scraping a ese número de páginas (para tests).
    start_page: página del listado desde la cual empezar. Sirve para reanudar
        corridas largas sin re-recorrer todas las páginas ya procesadas. Es seguro
        porque el listado va de más nuevo a más viejo y el modo resume deduplica
        por URL, así que un pequeño solape no genera duplicados ni huecos.
    """
    inicializar_csv()
    procesadas = cargar_urls_procesadas()

    async with async_playwright() as pw:
        # Un solo browser con contextos separados para listado y artículos.
        # Los workers de artículos se ejecutan en serie (ARTICLE_CONCURRENCY=1)
        # para evitar saturar el sitio y que el contexto de listado reciba respuestas truncadas.
        browser = await pw.chromium.launch(headless=True)

        context_list = await browser.new_context(
            user_agent=UA, locale="es-CL", extra_http_headers=EXTRA_HEADERS,
        )
        page_list = await context_list.new_page()
        # Segunda pestaña en el MISMO contexto: comparte cookies del WAF con el listado,
        # así el "clearance" obtenido al navegar el listado sirve para los artículos.
        page_art = await context_list.new_page()

        sem = asyncio.Semaphore(ARTICLE_CONCURRENCY)
        page_num = max(0, start_page)
        if page_num > 0:
            log.info("Reanudando desde la página %d (se omite el re-recorrido de %d páginas ya hechas).",
                     page_num, page_num)
        total_escritos = 0

        while True:
            if max_pages > 0 and page_num >= max_pages:
                log.info("Límite de %d páginas alcanzado. Deteniendo.", max_pages)
                break

            items_pagina, hay_siguiente = await extraer_listado_pagina(page_list, page_num)

            if not items_pagina:
                # Reintento: el sitio pudo haber devuelto una respuesta truncada
                log.warning("Página %d vacía, reintentando en 5s...", page_num)
                await asyncio.sleep(5)
                items_pagina, hay_siguiente = await extraer_listado_pagina(page_list, page_num)
                if not items_pagina:
                    log.info("Página %d vacía tras reintento. Terminando.", page_num)
                    break

            # Filtrar los ya procesados
            nuevos = [i for i in items_pagina if i["url"] not in procesadas]
            log.info("Página %d: %d nuevos de %d totales.", page_num, len(nuevos), len(items_pagina))

            if nuevos:
                # Procesar artículos con concurrencia controlada
                tareas = [
                    worker_articulo(sem, page_art, item, procesadas)
                    for item in nuevos
                ]
                resultados = await asyncio.gather(*tareas)
                filas = [r for r in resultados if r is not None]

                if filas:
                    escribir_filas(filas)
                    for f in filas:
                        procesadas.add(f["url"])
                    total_escritos += len(filas)

            if not hay_siguiente:
                log.info("No hay página siguiente. Scraping completo.")
                break

            page_num += 1
            await dormir(DELAY_BETWEEN_PAGES)

        await context_list.close()
        await browser.close()

    log.info("Total de noticias escritas: %d", total_escritos)
    return total_escritos

# ---------------------------------------------------------------------------
# Validación del CSV resultante
# ---------------------------------------------------------------------------
def validar_csv():
    if not OUTPUT_CSV.exists():
        log.error("CSV no encontrado: %s", OUTPUT_CSV)
        return

    errores = 0
    with open(OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        # Verificar columnas
        if list(reader.fieldnames) != COLUMNAS:
            log.error("Columnas incorrectas: %s", reader.fieldnames)
            errores += 1
        total = 0
        urls_vistas = set()
        for i, row in enumerate(reader, 2):
            total += 1
            # Fecha YYYY-MM-DD
            fecha = row.get("fecha", "")
            if not re.match(r"\d{4}-\d{2}-\d{2}$", fecha):
                log.warning("Fila %d: fecha inválida '%s'", i, fecha)
                errores += 1
            # URL
            url = row.get("url", "")
            if not url.startswith("http"):
                log.warning("Fila %d: URL inválida '%s'", i, url)
                errores += 1
            # Duplicados
            if url in urls_vistas:
                log.warning("Fila %d: URL duplicada '%s'", i, url)
                errores += 1
            urls_vistas.add(url)
            # Campos vacíos críticos
            for col in ["fecha", "titulo", "url"]:
                if not row.get(col, "").strip():
                    log.warning("Fila %d: columna '%s' vacía", i, col)
                    errores += 1

    log.info("Validación: %d filas, %d errores.", total, errores)
    if errores == 0:
        log.info("CSV válido.")
    else:
        log.warning("CSV con %d problemas detectados.", errores)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scraper Ministerio de Energía")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="Limitar a N páginas (0 = sin límite, scraping completo)")
    parser.add_argument("--start-page", type=int, default=0,
                        help="Página del listado desde la cual reanudar (0 = desde el inicio). "
                             "Evita re-recorrer páginas ya procesadas en corridas largas.")
    args = parser.parse_args()

    n = asyncio.run(scrape(max_pages=args.max_pages, start_page=args.start_page))
    print(f"\nScraping finalizado. Total noticias: {n}")
    print("Validando CSV...")
    validar_csv()
