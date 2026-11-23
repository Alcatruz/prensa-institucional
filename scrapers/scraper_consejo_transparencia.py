"""
Scraper para el Consejo para la Transparencia (CPLT)
Fuente: https://www.consejotransparencia.cl/noticias/
Extrae listado completo con paginación y el texto íntegro de cada noticia.

Por defecto es incremental: si ya existe el CSV, omite las URLs conocidas y se
detiene tras PAGINAS_SIN_NOVEDAD páginas seguidas del listado sin notas nuevas.
El resultado se une con lo existente. Con --completo recorre todas las páginas.

Uso:
    python scrapers/scraper_consejo_transparencia.py
    python scrapers/scraper_consejo_transparencia.py --completo
"""

import argparse
import asyncio
import csv
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BASE_URL = "https://www.consejotransparencia.cl"
LISTADO_URL = "https://www.consejotransparencia.cl/noticias/"
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "consejo_transparencia.csv"
INSTITUCION = "Consejo para la Transparencia"

DELAY_ENTRE_PAGINAS = 2.0   # segundos entre páginas del listado
DELAY_ENTRE_NOTICIAS = 1.5  # segundos entre noticias individuales
MAX_REINTENTOS = 3
TIMEOUT_MS = 30_000
PAGINAS_SIN_NOVEDAD = 3     # modo incremental: corta tras N páginas sin notas nuevas
COLUMNAS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

MESES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parsear_fecha(texto: str) -> str:
    """
    Convierte fechas del sitio a YYYY-MM-DD.
    Acepta:
      - ISO 8601:  2026-03-31T12:02:31+00:00
      - Texto:     "Publicada el 31 de Marzo de 2026"
    """
    if not texto:
        return ""
    # Primero intenta ISO (descarta años imposibles: el sitio tiene erratas como 0201-07-17)
    m = re.search(r"(\d{4}-\d{2}-\d{2})", texto)
    if m:
        return m.group(1) if m.group(1) >= "2008" else ""
    # Luego texto en español
    texto_l = texto.lower()
    m = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", texto_l)
    if m:
        dia, mes_str, anio = m.group(1), m.group(2), m.group(3)
        mes = MESES.get(mes_str, "01")
        return f"{anio}-{mes.zfill(2)}-{dia.zfill(2)}"
    return ""


def limpiar_texto(texto: str) -> str:
    """Elimina espacios y saltos de línea redundantes."""
    return re.sub(r"\s{2,}", " ", texto.strip())


# ---------------------------------------------------------------------------
# Extracción de una noticia individual
# ---------------------------------------------------------------------------

async def extraer_contenido_noticia(page, url: str) -> dict:
    """
    Navega a la URL de la noticia y extrae:
      - titulo (h1)
      - fecha (JSON-LD o meta o texto visible)
      - contenido completo (.entry-content)
    Devuelve dict con claves 'titulo', 'fecha', 'contenido'.
    """
    resultado = {"titulo": "", "fecha": "", "contenido": ""}

    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_load_state("domcontentloaded")

            # --- Título ---
            # El sitio tiene 2 h1: uno en el banner ("Noticias") y otro en el article
            # Usar "article .entry-title" para obtener el título real del artículo
            try:
                loc = page.locator("article .entry-title, h1.entry-title")
                titulo = await loc.first.inner_text(timeout=5000) if await loc.count() else ""
                resultado["titulo"] = limpiar_texto(titulo)
            except Exception:
                resultado["titulo"] = ""

            # --- Fecha desde JSON-LD ---
            fecha = ""
            try:
                scripts = await page.locator('script[type="application/ld+json"]').all()
                for s in scripts:
                    raw = await s.inner_text()
                    try:
                        data = json.loads(raw)
                        # Puede ser lista o dict
                        items = data if isinstance(data, list) else [data]
                        # Yoast agrupa los nodos bajo "@graph"
                        items = [n for it in items
                                 for n in ((it.get("@graph") or [it]) if isinstance(it, dict) else [it])]
                        for item in items:
                            if isinstance(item, dict):
                                dp = item.get("datePublished", "")
                                if dp:
                                    fecha = parsear_fecha(dp)
                                    break
                    except json.JSONDecodeError:
                        continue
                    if fecha:
                        break
            except Exception:
                pass

            # Fallback: meta tag
            if not fecha:
                try:
                    meta = page.locator('meta[property="article:published_time"]')
                    meta_date = await meta.first.get_attribute("content", timeout=2000) if await meta.count() else ""
                    if meta_date:
                        fecha = parsear_fecha(meta_date)
                except Exception:
                    pass

            # Fallback: texto visible "Publicada el..."
            if not fecha:
                try:
                    loc = page.locator("text=/Publicada el/")
                    if await loc.count():
                        fecha = parsear_fecha(await loc.first.inner_text(timeout=3000))
                except Exception:
                    pass

            resultado["fecha"] = fecha

            # --- Contenido completo ---
            try:
                # Selectores más comunes en temas WordPress
                for selector in [".entry-content", ".post-content", "article .content", ".article-content", "main article"]:
                    locs = page.locator(selector)
                    if await locs.count() > 0:
                        contenido = await locs.first.inner_text()
                        resultado["contenido"] = limpiar_texto(contenido)
                        break
            except Exception:
                resultado["contenido"] = ""

            return resultado

        except PlaywrightTimeoutError:
            log.warning("Timeout en %s (intento %d/%d)", url, intento, MAX_REINTENTOS)
            if intento < MAX_REINTENTOS:
                await asyncio.sleep(3)
        except Exception as e:
            log.warning("Error en %s: %s (intento %d/%d)", url, e, intento, MAX_REINTENTOS)
            if intento < MAX_REINTENTOS:
                await asyncio.sleep(3)

    return resultado


# ---------------------------------------------------------------------------
# Extracción del listado de una página
# ---------------------------------------------------------------------------

async def extraer_listado_pagina(page, url_pagina: str) -> list[dict]:
    """
    Extrae los artículos del listado de una página.
    Devuelve lista de dicts con 'titulo_lista', 'resumen', 'url', 'fecha_lista'.
    """
    for intento in range(1, MAX_REINTENTOS + 1):
        try:
            await page.goto(url_pagina, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            await page.wait_for_load_state("domcontentloaded")

            # Esperar que carguen las cards
            # El sitio usa h1.entry-title a en el listado (tema WordPress personalizado)
            await page.wait_for_selector(".entry-title a[href]", timeout=10000)

            articulos = []

            # Cada noticia está en un <article> con h1.entry-title y time[datetime]
            articles = await page.locator("article").all()

            for art_el in articles:
                try:
                    # Título y URL
                    link = art_el.locator(".entry-title a[href]").first
                    url_noticia = await link.get_attribute("href")
                    titulo_lista = limpiar_texto(await link.inner_text())

                    if not url_noticia or not url_noticia.startswith("http"):
                        continue
                    if not titulo_lista:
                        continue

                    # Fecha desde atributo datetime del <time>
                    fecha_lista = ""
                    try:
                        datetime_val = await art_el.locator("time.entry-date[datetime]").first.get_attribute("datetime")
                        if datetime_val:
                            fecha_lista = parsear_fecha(datetime_val)
                    except Exception:
                        pass

                    # Resumen desde .entry-content del listado (texto truncado)
                    resumen = ""
                    try:
                        resumen_raw = await art_el.locator(".entry-content").first.inner_text()
                        resumen = limpiar_texto(resumen_raw)
                    except Exception:
                        pass

                    articulos.append({
                        "titulo_lista": titulo_lista,
                        "resumen": resumen,
                        "url": url_noticia,
                        "fecha_lista": fecha_lista,
                    })
                except Exception as e:
                    log.debug("Error procesando item del listado: %s", e)

            return articulos

        except PlaywrightTimeoutError:
            log.warning("Timeout en listado %s (intento %d/%d)", url_pagina, intento, MAX_REINTENTOS)
            if intento < MAX_REINTENTOS:
                await asyncio.sleep(3)
        except Exception as e:
            log.warning("Error en listado %s: %s (intento %d/%d)", url_pagina, e, intento, MAX_REINTENTOS)
            if intento < MAX_REINTENTOS:
                await asyncio.sleep(3)

    return []


# ---------------------------------------------------------------------------
# Detectar número total de páginas
# ---------------------------------------------------------------------------

async def detectar_total_paginas(page) -> int:
    """Navega al listado y detecta la última página disponible."""
    await page.goto(LISTADO_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    await page.wait_for_load_state("domcontentloaded")

    # Busca el enlace a la última página en la paginación
    try:
        # Patrón: href="/noticias/page/166/"
        hrefs = await page.locator(".pagination a[href], nav.pagination a[href], .page-numbers a[href]").all()
        max_pagina = 1
        for a in hrefs:
            href = await a.get_attribute("href")
            if href:
                m = re.search(r"/page/(\d+)/", href)
                if m:
                    n = int(m.group(1))
                    if n > max_pagina:
                        max_pagina = n
        if max_pagina > 1:
            return max_pagina
    except Exception:
        pass

    # Fallback: buscar texto de paginación
    try:
        texto_pag = await page.locator(".pagination, nav.pagination").first.inner_text()
        nums = re.findall(r"\b(\d+)\b", texto_pag)
        if nums:
            return max(int(n) for n in nums)
    except Exception:
        pass

    log.warning("No se pudo detectar el total de páginas; se usará 1")
    return 1


# ---------------------------------------------------------------------------
# Scraper principal
# ---------------------------------------------------------------------------

def leer_existentes(ruta: Path) -> list[list]:
    """Filas ya guardadas, en el orden de COLUMNAS."""
    if not ruta.exists():
        return []
    with open(ruta, encoding="utf-8", newline="") as f:
        return [[(r.get(c) or "") for c in COLUMNAS] for r in csv.DictReader(f)]


async def scrape(conocidas: set[str] | None = None, max_paginas: int | None = None):
    """Recorre el listado. Si `conocidas` trae URLs, las omite y corta tras
    PAGINAS_SIN_NOVEDAD páginas seguidas sin notas nuevas."""
    datos = []
    urls_vistas = set(conocidas or ())
    incremental = bool(conocidas)
    sin_novedad = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="es-CL",
        )

        # Página para listados
        page_lista = await context.new_page()
        # Página para noticias individuales
        page_noticia = await context.new_page()

        # Detectar total de páginas
        log.info("Detectando total de páginas...")
        total_paginas = await detectar_total_paginas(page_lista)
        log.info("Total de páginas detectadas: %d", total_paginas)
        if max_paginas:
            total_paginas = min(total_paginas, max_paginas)

        for num_pagina in range(1, total_paginas + 1):
            if incremental and sin_novedad >= PAGINAS_SIN_NOVEDAD:
                log.info("Modo incremental: %d páginas seguidas sin notas nuevas, se detiene en la %d",
                         sin_novedad, num_pagina - 1)
                break
            if num_pagina == 1:
                url_pagina = LISTADO_URL
            else:
                url_pagina = f"{LISTADO_URL}page/{num_pagina}/"

            log.info("Procesando página %d/%d: %s", num_pagina, total_paginas, url_pagina)

            articulos_pagina = await extraer_listado_pagina(page_lista, url_pagina)

            if not articulos_pagina:
                log.warning("Página %d sin artículos, continuando...", num_pagina)
                await asyncio.sleep(DELAY_ENTRE_PAGINAS)
                continue

            log.info("  -> %d artículos encontrados en página %d", len(articulos_pagina), num_pagina)

            nuevas_pagina = [a for a in articulos_pagina if a["url"] not in urls_vistas]
            sin_novedad = 0 if nuevas_pagina else sin_novedad + 1

            for art in articulos_pagina:
                url = art["url"]
                if url in urls_vistas:
                    log.debug("URL duplicada, omitiendo: %s", url)
                    continue
                urls_vistas.add(url)

                log.info("  Extrayendo: %s", url[:80])
                detalle = await extraer_contenido_noticia(page_noticia, url)

                # Priorizar datos del detalle sobre el listado
                fecha = detalle.get("fecha") or art.get("fecha_lista", "")
                titulo = detalle.get("titulo") or art.get("titulo_lista", "")
                contenido = detalle.get("contenido", "")
                resumen = art.get("resumen", "")

                # Si no hay resumen del listado, usar primeros 300 chars del contenido
                if not resumen and contenido:
                    resumen = contenido[:300].rstrip() + ("..." if len(contenido) > 300 else "")

                datos.append([
                    fecha,
                    INSTITUCION,
                    titulo,
                    resumen,
                    url,
                    contenido,
                ])

                await asyncio.sleep(DELAY_ENTRE_NOTICIAS)

            await asyncio.sleep(DELAY_ENTRE_PAGINAS)

        await browser.close()

    log.info("Total de noticias extraídas: %d", len(datos))
    return datos


# ---------------------------------------------------------------------------
# Guardado CSV
# ---------------------------------------------------------------------------

def guardar_csv(datos: list, ruta: Path, previas: list | None = None):
    """Escribe `datos` unido a `previas` (por URL; gana la versión nueva), por fecha desc."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    por_url = {fila[4]: fila for fila in (previas or [])}
    for fila in datos:
        por_url[fila[4]] = fila
    filas = sorted(por_url.values(),
                   key=lambda r: r[0] if re.match(r"\d{4}-\d{2}-\d{2}$", r[0]) else "",
                   reverse=True)
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(COLUMNAS)
        writer.writerows(filas)
    log.info("CSV guardado en: %s (%d filas, %d nuevas)", ruta, len(filas), len(datos))


# ---------------------------------------------------------------------------
# Validación básica
# ---------------------------------------------------------------------------

def validar_csv(ruta: Path):
    errores = []
    with open(ruta, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columnas = reader.fieldnames or []
        esperadas = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
        if columnas != esperadas:
            errores.append(f"Columnas incorrectas: {columnas}")
        for i, fila in enumerate(reader, start=2):
            fecha = fila.get("fecha", "")
            if fecha and not re.match(r"^\d{4}-\d{2}-\d{2}$", fecha):
                errores.append(f"Fila {i}: fecha con formato incorrecto: {fecha!r}")
            if not fila.get("url", "").startswith("http"):
                errores.append(f"Fila {i}: URL inválida: {fila.get('url', '')!r}")

    if errores:
        log.warning("Advertencias de validación (%d):", len(errores))
        for e in errores[:20]:
            log.warning("  %s", e)
    else:
        log.info("Validación OK — sin errores detectados.")


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scraper de noticias del CPLT")
    ap.add_argument("--completo", action="store_true",
                    help="recorre todas las páginas y reescribe el CSV desde cero")
    ap.add_argument("--max-paginas", type=int, help="tope de páginas del listado (pruebas)")
    ap.add_argument("--salida", type=Path, default=OUTPUT_PATH, help="ruta del CSV de salida")
    args = ap.parse_args()

    log.info("Iniciando scraper del Consejo para la Transparencia")
    log.info("Output: %s", args.salida)

    previas = [] if args.completo else leer_existentes(args.salida)
    if previas:
        log.info("Modo incremental: %d notas ya guardadas", len(previas))

    datos = asyncio.run(scrape({f[4] for f in previas}, args.max_paginas))

    if datos or previas:
        guardar_csv(datos, args.salida, previas)
        validar_csv(args.salida)
    else:
        log.error("No se extrajeron datos. Revisa los logs.")
