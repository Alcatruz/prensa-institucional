"""
Scraper de noticias de la Defensoría Penal Pública (DPP)
Sitio: https://www.dpp.cl/sala_prensa/noticias
Paginación: https://www.dpp.cl/sala_prensa/noticias/2/page/N (hasta ~1513 páginas)
Estructura listado: div.documento > p.fecha, h5 > a (titulo+url), p (resumen)
Estructura artículo: #contenido > p.fecha, h3, p.breve, párrafos <p> del cuerpo

Soporta reanudación: si el CSV ya existe, omite las URLs ya procesadas.
Uso:
    python scraper_dpp.py            # Todas las páginas
    python scraper_dpp.py 10         # Primeras 10 páginas (prueba)
    python scraper_dpp.py --validate # Solo valida el CSV existente
"""

import asyncio
from pathlib import Path
import csv
import logging
import os
import sys
import time
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BASE_URL = "https://www.dpp.cl"
LIST_URL_TEMPLATE = "https://www.dpp.cl/sala_prensa/noticias/2/page/{page}"
FIRST_PAGE_URL = "https://www.dpp.cl/sala_prensa/noticias"
OUTPUT_CSV = str(Path(__file__).resolve().parent.parent / "data" / "dpp.csv")
INSTITUCION = "Defensoría Penal Pública"
COLUMNS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

DELAY_BETWEEN_PAGES = 1.5    # segundos entre páginas del listado
DELAY_BETWEEN_ARTICLES = 1.0  # segundos entre artículos individuales
REQUEST_TIMEOUT = 30          # segundos timeout HTTP
MAX_RETRIES = 3

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


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def parse_fecha(raw: str) -> str:
    """Convierte DD/MM/YYYY → YYYY-MM-DD. Retorna original si falla."""
    raw = raw.strip()
    try:
        return datetime.strptime(raw, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        log.warning("No se pudo parsear fecha: %r", raw)
        return raw


def get_page(url: str, retries: int = MAX_RETRIES) -> BeautifulSoup | None:
    """GET con reintentos. Retorna BeautifulSoup o None si falla tras todos los intentos."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            log.warning("Intento %d/%d falló para %s: %s", attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(2 * attempt)
    return None


# ---------------------------------------------------------------------------
# Reanudación: cargar URLs ya procesadas
# ---------------------------------------------------------------------------

def cargar_urls_procesadas(ruta: str) -> set[str]:
    """Lee el CSV existente y retorna el conjunto de URLs ya procesadas."""
    if not os.path.exists(ruta):
        return set()
    urls = set()
    try:
        with open(ruta, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = row.get("url", "").strip()
                if url:
                    urls.add(url)
        log.info("Reanudando: %d URLs ya procesadas en CSV existente", len(urls))
    except Exception as exc:
        log.warning("No se pudo leer CSV existente para reanudación: %s", exc)
    return urls


def abrir_csv_writer(ruta: str, append: bool) -> tuple:
    """Abre el CSV para escritura (append o nuevo). Retorna (file_handle, writer)."""
    if append:
        f = open(ruta, "a", newline="", encoding="utf-8")
        writer = csv.writer(f)
    else:
        f = open(ruta, "w", newline="", encoding="utf-8")
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
    return f, writer


# ---------------------------------------------------------------------------
# Scraping del listado
# ---------------------------------------------------------------------------

def scrape_listado_page(url: str) -> list[dict]:
    """Extrae metadatos de noticias de una página del listado."""
    soup = get_page(url)
    if soup is None:
        log.error("No se pudo obtener listado: %s", url)
        return []

    items = []
    for doc in soup.select("div.documento"):
        # Fecha
        fecha_tag = doc.select_one("p.fecha")
        fecha_raw = fecha_tag.get_text(strip=True) if fecha_tag else ""
        fecha = parse_fecha(fecha_raw) if fecha_raw else ""

        # Título y URL
        h5_link = doc.select_one("h5 a")
        if not h5_link:
            continue
        titulo = h5_link.get_text(strip=True)
        href = h5_link.get("href", "")
        if href.startswith("/"):
            url_noticia = BASE_URL + href
        elif href.startswith("http"):
            url_noticia = href
        else:
            url_noticia = BASE_URL + "/" + href

        # Resumen (primer <p> de .detalle que no sea p.fecha)
        detalle = doc.select_one("div.detalle")
        resumen = ""
        if detalle:
            for p in detalle.find_all("p"):
                if "fecha" not in p.get("class", []):
                    resumen = p.get_text(strip=True)
                    if resumen:
                        break

        items.append({
            "fecha": fecha,
            "titulo": titulo,
            "resumen": resumen,
            "url": url_noticia,
        })

    return items


def get_total_pages(soup: BeautifulSoup) -> int:
    """Obtiene el número total de páginas desde el bloque de paginación."""
    pag_div = soup.select_one("div#paginacion")
    if not pag_div:
        return 1

    max_page = 1
    for a in pag_div.find_all("a"):
        href = a.get("href", "")
        match = re.search(r"/page/(\d+)", href)
        if match:
            n = int(match.group(1))
            if n > max_page:
                max_page = n
    return max_page


# ---------------------------------------------------------------------------
# Scraping del artículo individual
# ---------------------------------------------------------------------------

def scrape_articulo(url: str) -> dict:
    """Extrae fecha, resumen y contenido completo de un artículo individual."""
    soup = get_page(url)
    if soup is None:
        return {"fecha_articulo": "", "resumen_articulo": "", "contenido": ""}

    contenido_div = soup.select_one("div#contenido")
    if not contenido_div:
        return {"fecha_articulo": "", "resumen_articulo": "", "contenido": ""}

    # Fecha del artículo (más fiable que la del listado)
    fecha_tag = contenido_div.select_one("p.fecha")
    fecha_raw = fecha_tag.get_text(strip=True) if fecha_tag else ""
    fecha_articulo = parse_fecha(fecha_raw) if fecha_raw else ""

    # Resumen/breve
    breve_tag = contenido_div.select_one("p.breve")
    resumen_articulo = breve_tag.get_text(strip=True) if breve_tag else ""

    # Eliminar elementos que no son cuerpo del texto
    for tag in contenido_div.select(
        "div.addthis_toolbox, div#sidebar, script, style, "
        "p.fecha, p.breve, p.epigrafe, p.bajada, h3, "
        "div#auxiliar"
    ):
        tag.decompose()

    # Cuerpo: todos los <p> restantes
    paragraphs = []
    for p in contenido_div.find_all("p"):
        text = p.get_text(separator=" ", strip=True)
        if text:
            paragraphs.append(text)

    contenido = "\n\n".join(paragraphs)

    return {
        "fecha_articulo": fecha_articulo,
        "resumen_articulo": resumen_articulo,
        "contenido": contenido,
    }


# ---------------------------------------------------------------------------
# Obtener total de páginas con Playwright (por si hay JS)
# ---------------------------------------------------------------------------

async def get_total_pages_playwright() -> int:
    """Usa Playwright para cargar la primera página y detectar el total de páginas."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers(HEADERS)

        log.info("Cargando primera página con Playwright...")
        await page.goto(FIRST_PAGE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_selector("div.documento", timeout=15000)

        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, "html.parser")
    total = get_total_pages(soup)
    log.info("Total de páginas detectadas: %d", total)
    return total


# ---------------------------------------------------------------------------
# Orquestador principal con escritura incremental y reanudación
# ---------------------------------------------------------------------------

def scrape_all(max_pages: int | None = None) -> int:
    """
    Extrae todas las noticias con escritura incremental.
    Soporta reanudación: omite URLs ya presentes en el CSV.
    Retorna el total de filas escritas en esta ejecución.
    """
    # Detectar total de páginas
    total_pages = asyncio.run(get_total_pages_playwright())
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)

    # Cargar URLs ya procesadas para reanudación
    urls_ya_procesadas = cargar_urls_procesadas(OUTPUT_CSV)
    append_mode = len(urls_ya_procesadas) > 0

    log.info("Procesando %d páginas del listado (modo %s)...",
             total_pages, "append/reanudación" if append_mode else "nuevo")

    # Recolectar metadatos de todas las páginas del listado
    all_metadata = []

    log.info("Página 1/%d", total_pages)
    items_p1 = scrape_listado_page(FIRST_PAGE_URL)
    all_metadata.extend(items_p1)
    time.sleep(DELAY_BETWEEN_PAGES)

    for page_num in range(2, total_pages + 1):
        url = LIST_URL_TEMPLATE.format(page=page_num)
        if page_num % 50 == 0 or page_num <= 5:
            log.info("Página %d/%d (%d en listado)", page_num, total_pages, len(all_metadata))
        items = scrape_listado_page(url)
        if not items:
            log.warning("Página %d vacía, continuando...", page_num)
        all_metadata.extend(items)
        time.sleep(DELAY_BETWEEN_PAGES)

    log.info("Listado completo: %d entradas totales", len(all_metadata))

    # Deduplicar por URL
    seen = set()
    unique_metadata = []
    for item in all_metadata:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique_metadata.append(item)
    log.info("Entradas únicas en listado: %d", len(unique_metadata))

    # Filtrar las ya procesadas
    pendientes = [m for m in unique_metadata if m["url"] not in urls_ya_procesadas]
    log.info("Artículos pendientes de procesar: %d (ya procesados: %d)",
             len(pendientes), len(urls_ya_procesadas))

    if not pendientes:
        log.info("No hay artículos nuevos. CSV ya está completo.")
        return 0

    # Abrir CSV para escritura incremental
    f, writer = abrir_csv_writer(OUTPUT_CSV, append=append_mode)
    escritas = 0

    try:
        for i, meta in enumerate(pendientes, 1):
            log.info("Artículo %d/%d: %s", i, len(pendientes), meta["url"])
            art = scrape_articulo(meta["url"])

            fecha = art["fecha_articulo"] or meta["fecha"]
            resumen = art["resumen_articulo"] or meta["resumen"]

            writer.writerow([
                fecha,
                INSTITUCION,
                meta["titulo"],
                resumen,
                meta["url"],
                art["contenido"],
            ])
            f.flush()
            escritas += 1
            time.sleep(DELAY_BETWEEN_ARTICLES)

    finally:
        f.close()

    log.info("Escritura incremental completada: %d artículos nuevos escritos", escritas)
    return escritas


# ---------------------------------------------------------------------------
# Validación del CSV
# ---------------------------------------------------------------------------

def validar_csv(ruta: str):
    """Validaciones básicas del CSV generado."""
    if not os.path.exists(ruta):
        log.error("CSV no encontrado: %s", ruta)
        return

    with open(ruta, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        expected = COLUMNS
        if cols != expected:
            log.error("Columnas incorrectas: %s (esperado: %s)", cols, expected)
            return

        errors = 0
        total = 0
        contenido_vacios = 0
        for row in reader:
            total += 1
            fecha = row.get("fecha", "")
            if fecha and not re.match(r"^\d{4}-\d{2}-\d{2}$", fecha):
                log.warning("Fecha inválida fila %d: %r", total, fecha)
                errors += 1
            url = row.get("url", "")
            if not url.startswith("http"):
                log.warning("URL inválida fila %d: %r", total, url)
                errors += 1
            if not row.get("contenido", "").strip():
                contenido_vacios += 1

        log.info("Validación: %d filas | %d errores fecha/URL | %d contenidos vacíos",
                 total, errors, contenido_vacios)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--validate":
        validar_csv(OUTPUT_CSV)
        sys.exit(0)

    max_pages = None
    if len(sys.argv) > 1:
        try:
            max_pages = int(sys.argv[1])
            log.info("Modo limitado: máximo %d páginas", max_pages)
        except ValueError:
            pass

    escritas = scrape_all(max_pages=max_pages)
    log.info("Proceso finalizado. Artículos nuevos escritos: %d", escritas)
    validar_csv(OUTPUT_CSV)
