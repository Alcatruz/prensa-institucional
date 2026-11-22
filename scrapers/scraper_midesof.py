"""
Scraper: Ministerio de Desarrollo Social y Familia (MIDESOF)
Fuente:  https://www.midesof.gob.cl/noticias/
API:     WP REST API  /wp-json/wp/v2/posts?categories=58
Output:  ../data/midesof.csv

Estrategia:
- WP REST API devuelve contenido completo (content.rendered) en cada post,
  por lo que NO se necesita navegar a cada URL individualmente.
- Se descargan todas las páginas (100 posts/página) con concurrencia moderada
  (batches de 5 requests simultáneos usando ThreadPoolExecutor).
- Escritura incremental al CSV para no perder datos si se interrumpe.
- Modo resume: detecta las URLs ya procesadas en el CSV existente y las omite.
- Reintentos automáticos (3 intentos) con backoff exponencial.
"""

import csv
import json
import logging
import os
import re
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BASE_URL = "https://www.midesof.gob.cl/wp-json/wp/v2/posts"
CATEGORY_ID = 58          # categoría "Noticias"
PER_PAGE = 100            # máximo permitido por la API
BATCH_SIZE = 5            # páginas simultáneas
DELAY_BETWEEN_BATCHES = 2 # segundos entre batches
MAX_RETRIES = 3
BACKOFF_BASE = 3          # segundos base para backoff exponencial
INSTITUCION = "Ministerio de Desarrollo Social y Familia"

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "midesof.csv")

CSV_COLUMNS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Utilidades HTML
# ---------------------------------------------------------------------------
class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return re.sub(r"\s+", " ", " ".join(self._parts)).strip()


def strip_html(html: str) -> str:
    """Elimina etiquetas HTML y normaliza espacios."""
    if not html:
        return ""
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


def parse_date(date_str: str) -> str:
    """Convierte 'YYYY-MM-DDTHH:MM:SS' → 'YYYY-MM-DD'."""
    if not date_str:
        return ""
    try:
        return datetime.fromisoformat(date_str).strftime("%Y-%m-%d")
    except ValueError:
        return date_str[:10]


# ---------------------------------------------------------------------------
# Requests con reintentos
# ---------------------------------------------------------------------------
def fetch_json(url: str, retries: int = MAX_RETRIES) -> dict | list | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8"))
        except (HTTPError, URLError) as exc:
            wait = BACKOFF_BASE * attempt
            log.warning("Intento %d/%d fallido para %s: %s. Esperando %ds…",
                        attempt, retries, url, exc, wait)
            if attempt < retries:
                time.sleep(wait)
    log.error("Agotados %d reintentos para %s", retries, url)
    return None


def get_total_pages() -> tuple[int, int]:
    """Devuelve (total_posts, total_pages) desde los headers de la API."""
    url = f"{BASE_URL}?categories={CATEGORY_ID}&per_page={PER_PAGE}&page=1"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }
    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as resp:
        total = int(resp.headers.get("X-WP-Total", 0))
        pages = int(resp.headers.get("X-WP-TotalPages", 0))
    return total, pages


# ---------------------------------------------------------------------------
# Transformación de un post en una fila CSV
# ---------------------------------------------------------------------------
def post_to_row(post: dict) -> list:
    fecha = parse_date(post.get("date", ""))
    titulo = strip_html(post.get("title", {}).get("rendered", ""))
    resumen = strip_html(post.get("excerpt", {}).get("rendered", ""))
    url = post.get("link", "")
    contenido = strip_html(post.get("content", {}).get("rendered", ""))
    return [fecha, INSTITUCION, titulo, resumen, url, contenido]


# ---------------------------------------------------------------------------
# Descarga de una página de la API
# ---------------------------------------------------------------------------
def fetch_page(page_num: int) -> list[list]:
    url = (
        f"{BASE_URL}?categories={CATEGORY_ID}"
        f"&per_page={PER_PAGE}&page={page_num}"
        f"&_fields=id,date,link,title,excerpt,content"
    )
    data = fetch_json(url)
    if not data:
        log.warning("Página %d devolvió datos vacíos.", page_num)
        return []
    rows = [post_to_row(p) for p in data]
    log.info("Página %d: %d posts extraídos.", page_num, len(rows))
    return rows


# ---------------------------------------------------------------------------
# Modo resume: leer URLs ya guardadas
# ---------------------------------------------------------------------------
def load_existing_urls(filepath: str) -> set:
    existing = set()
    if not os.path.exists(filepath):
        return existing
    try:
        with open(filepath, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("url"):
                    existing.add(row["url"].strip())
        log.info("Resume: %d URLs ya en el CSV.", len(existing))
    except Exception as exc:
        log.warning("No se pudo leer el CSV existente para resume: %s", exc)
    return existing


# ---------------------------------------------------------------------------
# Escritura incremental
# ---------------------------------------------------------------------------
def open_csv_writer(filepath: str, append: bool):
    mode = "a" if append else "w"
    f = open(filepath, mode, newline="", encoding="utf-8")
    writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
    if not append:
        writer.writerow(CSV_COLUMNS)
    return f, writer


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    log.info("Obteniendo total de posts y páginas…")
    try:
        total_posts, total_pages = get_total_pages()
    except Exception as exc:
        log.error("No se pudo conectar a la API: %s", exc)
        sys.exit(1)

    log.info("Total posts en categoría 'noticias': %d | Páginas: %d", total_posts, total_pages)

    # Modo resume
    existing_urls = load_existing_urls(OUTPUT_FILE)
    append_mode = len(existing_urls) > 0

    if append_mode:
        log.info("Modo RESUME activo: se agregarán solo los posts nuevos.")
    else:
        log.info("Modo FULL: se creará el CSV desde cero.")

    csv_file, writer = open_csv_writer(OUTPUT_FILE, append=append_mode)

    total_written = 0
    total_skipped = 0

    try:
        # Procesar las páginas en batches para concurrencia moderada
        pages_range = list(range(1, total_pages + 1))

        for batch_start in range(0, len(pages_range), BATCH_SIZE):
            batch = pages_range[batch_start: batch_start + BATCH_SIZE]
            batch_rows = []

            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                futures = {executor.submit(fetch_page, p): p for p in batch}
                for future in as_completed(futures):
                    page_num = futures[future]
                    try:
                        rows = future.result()
                        batch_rows.extend((page_num, r) for r in rows)
                    except Exception as exc:
                        log.error("Error en página %d: %s", page_num, exc)

            # Ordenar por número de página para escritura ordenada
            batch_rows.sort(key=lambda x: x[0])

            for _, row in batch_rows:
                url = row[4]
                if url in existing_urls:
                    total_skipped += 1
                    continue
                writer.writerow(row)
                existing_urls.add(url)
                total_written += 1

            # Flush incremental después de cada batch
            csv_file.flush()

            log.info(
                "Batch páginas %s completado. Escritos: %d | Omitidos (dup): %d",
                batch,
                total_written,
                total_skipped,
            )

            # Delay entre batches para no saturar el servidor
            if batch_start + BATCH_SIZE < len(pages_range):
                time.sleep(DELAY_BETWEEN_BATCHES)

    finally:
        csv_file.close()

    log.info(
        "Finalizado. Total rows escritas: %d | Omitidas: %d | Archivo: %s",
        total_written,
        total_skipped,
        os.path.abspath(OUTPUT_FILE),
    )


if __name__ == "__main__":
    main()
