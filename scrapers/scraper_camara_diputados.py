"""
Scraper - Cámara de Diputadas y Diputados de Chile
Fuente: https://www.camara.cl/prensa/prensa_cms.aspx
Estrategia: API REST de WordPress (abierta, sin autenticación)
  Endpoint: https://www.camara.cl/cms/wp-json/wp/v2/posts
  - per_page=100, paginación por ?page=N
  - ~10.333 posts (104 páginas con per_page=100)
  - excerpt contiene resumen limpio
  - content contiene HTML completo (se limpia con HTMLParser)
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido

Notas de rendimiento:
  - Usa urllib nativo (no Playwright) — mucho más rápido para JSON puro
  - Concurrencia con asyncio + ThreadPoolExecutor para descarga paralela
  - Soporte --resume para continuar desde donde quedó
  - Soporte --max-pages para limitar la descarga
"""

import argparse
from pathlib import Path
import asyncio
import csv
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser

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
BASE_API = "https://www.camara.cl/cms/wp-json/wp/v2/posts"
FIELDS = "id,date,title,excerpt,content,link"
PER_PAGE = 100
INSTITUCION = "Cámara de Diputadas y Diputados"
OUTPUT_PATH = str(Path(__file__).resolve().parent.parent / "data" / "camara_diputados.csv")
MAX_RETRIES = 5
DELAY_BETWEEN_PAGES = 1.0   # segundos entre páginas (en modo secuencial)
REQUEST_TIMEOUT = 30        # segundos por request HTTP
MAX_WORKERS = 4             # páginas paralelas en modo concurrente


# ---------------------------------------------------------------------------
# Utilidades HTML
# ---------------------------------------------------------------------------
class _MLStripper(HTMLParser):
    """Parser simple para eliminar etiquetas HTML."""

    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []

    def handle_data(self, d):
        self.fed.append(d)

    def get_data(self):
        return " ".join(self.fed)


def strip_html(html_text: str) -> str:
    """Elimina etiquetas HTML y normaliza espacios en blanco."""
    if not html_text:
        return ""
    s = _MLStripper()
    s.feed(html_text)
    text = s.get_data()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_fecha(date_str: str) -> str:
    """Convierte ISO 8601 (2026-03-24T20:56:32) a YYYY-MM-DD."""
    try:
        return datetime.fromisoformat(date_str).strftime("%Y-%m-%d")
    except Exception:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
        return m.group(1) if m else date_str


# ---------------------------------------------------------------------------
# HTTP con reintentos (síncrono — se llama desde ThreadPoolExecutor)
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _http_get(url: str) -> tuple[int, dict | None]:
    """
    Realiza un GET HTTP con reintentos exponenciales.
    Devuelve (status_code, parsed_json_or_None).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read()
                data = json.loads(raw.decode("utf-8"))
                return resp.status, data
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                # Fuera de rango — no tiene sentido reintentar
                return e.code, None
            log.warning(f"HTTP {e.code} en {url} (intento {attempt}/{MAX_RETRIES})")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            log.warning(f"Error de red en {url} (intento {attempt}/{MAX_RETRIES}): {e}")
        except json.JSONDecodeError as e:
            log.warning(f"JSON inválido en {url} (intento {attempt}/{MAX_RETRIES}): {e}")

        if attempt < MAX_RETRIES:
            sleep_secs = 2 ** attempt  # 2, 4, 8, 16 s
            log.info(f"  Reintentando en {sleep_secs}s...")
            time.sleep(sleep_secs)

    log.error(f"Fallaron todos los reintentos para {url}")
    return 0, None


# ---------------------------------------------------------------------------
# Consultar totales de la API
# ---------------------------------------------------------------------------
def get_api_totals() -> tuple[int, int]:
    """Devuelve (total_posts, total_pages) consultando la cabecera de la API."""
    url = f"{BASE_API}?per_page={PER_PAGE}&page=1&_fields=id"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        total_posts = int(resp.headers.get("x-wp-total", 0))
        total_pages = int(resp.headers.get("x-wp-totalpages", 0))
    return total_posts, total_pages


# ---------------------------------------------------------------------------
# Descarga de una página de la API
# ---------------------------------------------------------------------------
def fetch_page_sync(api_page: int) -> list[dict]:
    """Descarga la página N de la API. Retorna lista de posts JSON."""
    url = (
        f"{BASE_API}?per_page={PER_PAGE}&page={api_page}"
        f"&_fields={FIELDS}&orderby=date&order=desc"
    )
    status, data = _http_get(url)
    if status == 200 and isinstance(data, list):
        log.info(f"  Página {api_page}: {len(data)} posts.")
        return data
    if status in (400, 404):
        log.info(f"  Página {api_page}: fuera de rango (HTTP {status}).")
        return []
    log.warning(f"  Página {api_page}: sin datos (status={status}).")
    return []


def post_to_row(post: dict) -> list:
    """Convierte un post JSON de la API en una fila CSV."""
    fecha = parse_fecha(post.get("date", ""))
    titulo = strip_html(post.get("title", {}).get("rendered", ""))
    resumen = strip_html(post.get("excerpt", {}).get("rendered", ""))
    contenido = strip_html(post.get("content", {}).get("rendered", ""))
    url = post.get("link", "")
    return [fecha, INSTITUCION, titulo, resumen, url, contenido]


# ---------------------------------------------------------------------------
# Scraper principal — concurrente con ThreadPoolExecutor
# ---------------------------------------------------------------------------
async def scrape(
    max_pages: int,
    resume: bool,
    output_path: str,
) -> list[list]:
    """
    Extrae posts de la API.

    Args:
        max_pages: Límite de páginas a descargar (0 = todas).
        resume: Si True y existe el CSV, carga URLs ya descargadas y omite
                las páginas que ya estén completas.
        output_path: Ruta del CSV de salida.
    """
    # Determinar qué URLs ya tenemos (para modo resume)
    seen_urls: set[str] = set()
    existing_rows: list[list] = []
    if resume and os.path.exists(output_path):
        log.info(f"Modo resume: leyendo CSV existente en {output_path}...")
        with open(output_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # saltar header
            for row in reader:
                if len(row) >= 5:
                    seen_urls.add(row[4])
                    existing_rows.append(row)
        log.info(f"  {len(existing_rows)} filas ya descargadas ({len(seen_urls)} URLs únicas).")

    # Consultar totales
    log.info("Consultando totales en la API...")
    total_posts, total_api_pages = get_api_totals()
    log.info(f"Total posts disponibles: {total_posts} | Total páginas API: {total_api_pages}")

    # Calcular rango de páginas a descargar
    pages_to_fetch = min(max_pages, total_api_pages) if max_pages > 0 else total_api_pages

    # En modo resume: calcular desde qué página continuar
    # Las páginas de la API están ordenadas desc por fecha, así que las últimas
    # páginas descargadas son las que tienen posts más antiguos.
    # Estimamos la página de inicio según cuántos posts ya tenemos.
    start_page = 1
    if resume and existing_rows:
        # Páginas completas ya descargadas
        complete_pages = len(existing_rows) // PER_PAGE
        # Siempre re-descargamos la última página parcial para evitar huecos
        start_page = max(1, complete_pages)
        log.info(
            f"Resume: {len(existing_rows)} filas existentes → "
            f"~{complete_pages} páginas completas → continuando desde página {start_page}"
        )
        # Quitar las filas de la página de inicio (serán re-descargadas)
        keep_rows = complete_pages * PER_PAGE - PER_PAGE if complete_pages > 1 else 0
        existing_rows = existing_rows[:keep_rows]
        seen_urls = {row[4] for row in existing_rows}

    log.info(f"Descargando páginas {start_page}–{pages_to_fetch} con {MAX_WORKERS} workers paralelos...")

    # Descargar en paralelo con ThreadPoolExecutor
    loop = asyncio.get_event_loop()
    all_new_rows: list[list] = []

    # Agrupar en batches para no abrir demasiadas conexiones a la vez
    page_nums = list(range(start_page, pages_to_fetch + 1))
    batch_size = MAX_WORKERS

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for batch_start in range(0, len(page_nums), batch_size):
            batch = page_nums[batch_start: batch_start + batch_size]
            futures = [
                loop.run_in_executor(executor, fetch_page_sync, p)
                for p in batch
            ]
            results = await asyncio.gather(*futures)

            # Ordenar resultados por número de página (gather preserva orden)
            for i, posts in enumerate(results):
                page_num = batch[i]
                if not posts:
                    log.warning(f"  Página {page_num} sin datos — puede indicar fin de contenido.")
                    continue
                for post in posts:
                    row = post_to_row(post)
                    url = row[4]
                    # Validar campos mínimos y no duplicar
                    if row[0] and row[2] and url and url not in seen_urls:
                        seen_urls.add(url)
                        all_new_rows.append(row)

            total_so_far = len(existing_rows) + len(all_new_rows)
            log.info(
                f"  Batch páginas {batch[0]}–{batch[-1]} procesado. "
                f"Total acumulado: {total_so_far} filas."
            )

            # Pequeña pausa entre batches para no saturar el servidor
            if batch_start + batch_size < len(page_nums):
                await asyncio.sleep(DELAY_BETWEEN_PAGES)

    all_rows = existing_rows + all_new_rows
    log.info(f"Extracción completa: {len(all_rows)} filas totales.")
    return all_rows


# ---------------------------------------------------------------------------
# Guardado CSV
# ---------------------------------------------------------------------------
def guardar_csv(datos: list[list], ruta: str) -> None:
    """Guarda los datos en CSV UTF-8 con las 6 columnas requeridas."""
    # Deduplicación final por URL
    seen_urls: set[str] = set()
    datos_unicos = []
    for row in datos:
        url = row[4] if len(row) > 4 else ""
        if url not in seen_urls:
            seen_urls.add(url)
            datos_unicos.append(row)

    duplicados = len(datos) - len(datos_unicos)
    log.info(
        f"Guardando {len(datos_unicos)} filas "
        f"({duplicados} duplicados eliminados)."
    )

    with open(ruta, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["fecha", "institucion", "titulo", "resumen", "url", "contenido"])
        writer.writerows(datos_unicos)

    log.info(f"CSV guardado en: {ruta}")


# ---------------------------------------------------------------------------
# Validación del CSV resultante
# ---------------------------------------------------------------------------
def validar_csv(ruta: str) -> None:
    """Lee el CSV y reporta estadísticas básicas de calidad."""
    errores = []
    with open(ruta, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        expected = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
        if header != expected:
            errores.append(f"Cabecera incorrecta: {header}")

        fecha_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        filas_ok = 0
        fechas_invalidas = 0
        filas_vacias = 0

        for i, row in enumerate(reader, start=2):
            if len(row) != 6:
                filas_vacias += 1
                continue
            if not fecha_re.match(row[0]):
                fechas_invalidas += 1
                if fechas_invalidas <= 3:
                    errores.append(f"Fila {i}: fecha inválida '{row[0]}'")
            filas_ok += 1

    log.info("--- Validación CSV ---")
    log.info(f"  Filas válidas: {filas_ok}")
    log.info(f"  Fechas inválidas: {fechas_invalidas}")
    log.info(f"  Filas con columnas incorrectas: {filas_vacias}")
    if errores:
        for e in errores:
            log.warning(f"  PROBLEMA: {e}")
    else:
        log.info("  Sin errores detectados.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Scraper de prensa — Cámara de Diputadas y Diputados (API WP REST)"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Límite de páginas API a descargar (0 = todas). Cada página = 100 posts.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Continúa desde donde quedó el CSV existente (evita re-descargar todo).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=OUTPUT_PATH,
        help=f"Ruta del CSV de salida (default: {OUTPUT_PATH})",
    )
    args = parser.parse_args()

    log.info("=== Scraper Cámara de Diputadas y Diputados ===")
    log.info(f"max-pages: {args.max_pages if args.max_pages > 0 else 'todas'}")
    log.info(f"resume: {args.resume}")
    log.info(f"output: {args.output}")

    datos = asyncio.run(scrape(args.max_pages, args.resume, args.output))

    if not datos:
        log.warning("No se obtuvieron datos. Verifica la conectividad con la API.")
        sys.exit(1)

    guardar_csv(datos, args.output)
    validar_csv(args.output)
    log.info(f"=== Finalizado. Total filas guardadas: {len(datos)} ===")


if __name__ == "__main__":
    main()
