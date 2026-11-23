"""
Scraper — Junta Nacional de Auxilio Escolar y Becas (Junaeb)
URL: https://www.junaeb.cl/noticias/
Método: WP REST API (/wp-json/wp/v2/posts), sin paginación por clics.

Estructura del sitio:
  - WordPress (tema Divi). La API de posts es pública.
  - Las noticias nacionales y las regionales son posts del mismo tipo; se
    distinguen solo por categoría (Nacional, Antofagasta, Maule, etc.), así
    que un solo recorrido de /posts las trae todas.
  - content.rendered trae el cuerpo completo, por lo que no hace falta
    entrar a cada URL (se limpian galerías, estilos y scripts incrustados).
  - Los posts más antiguos son de noviembre de 2021 (el sitio actual no
    conserva el archivo anterior).
  - Algunos posts son solo galería o video y no tienen texto: quedan con
    contenido vacío.
"""

import argparse
import csv
import html
import logging
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Configuración ────────────────────────────────────────────────────────────
API_URL = "https://www.junaeb.cl/wp-json/wp/v2/posts"
INSTITUCION = "Junta Nacional de Auxilio Escolar y Becas (Junaeb)"
OUTPUT_CSV = Path(__file__).resolve().parent.parent / "data" / "junaeb.csv"
COLUMNAS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
POR_PAGINA = 100
PAUSA = 1.5      # segundos entre páginas de la API
RETRY_MAX = 4
TIMEOUT = 40

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


# ── Limpieza de texto ────────────────────────────────────────────────────────

def limpiar_espacios(txt: str) -> str:
    txt = re.sub(r"[ ​]+", " ", txt)
    return re.sub(r"[ \t]+", " ", txt).strip()


def html_a_texto(fragmento: str) -> str:
    """Convierte el HTML de un post en texto plano, un párrafo por bloque."""
    soup = BeautifulSoup(fragmento or "", "html.parser")
    for t in soup(["style", "script", "iframe", "noscript"]):
        t.decompose()
    # galerías de imágenes (solo traen fotos y pies de foto)
    for t in soup.select("dl.gallery-item, div.gallery, figure"):
        t.decompose()

    bloques = []
    for el in soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td"]):
        # evitar duplicar un <p> dentro de <blockquote> o <li> ya contado
        if el.name == "p" and el.find_parent(["li", "td"]):
            continue
        t = limpiar_espacios(el.get_text(" ", strip=True))
        if t:
            bloques.append(t)
    if not bloques:
        t = limpiar_espacios(soup.get_text(" ", strip=True))
        if t:
            bloques.append(t)
    return "\n\n".join(bloques)


def titulo_limpio(txt: str) -> str:
    return limpiar_espacios(html.unescape(BeautifulSoup(txt or "", "html.parser").get_text(" ")))


# ── Acceso a la API ──────────────────────────────────────────────────────────

def pedir_pagina(sesion: requests.Session, pagina: int):
    """Devuelve (lista_de_posts, total_paginas). Reintenta con espera creciente."""
    params = {
        "per_page": POR_PAGINA,
        "page": pagina,
        "orderby": "date",
        "order": "desc",
        "_fields": "id,date,link,title,excerpt,content",
    }
    for intento in range(1, RETRY_MAX + 1):
        try:
            r = sesion.get(API_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 400:
                # página fuera de rango: no hay más resultados
                return [], pagina - 1
            r.raise_for_status()
            total = int(r.headers.get("X-WP-TotalPages", pagina))
            return r.json(), total
        except (requests.RequestException, ValueError) as e:
            log.warning("Error en página %d (intento %d/%d): %s", pagina, intento, RETRY_MAX, e)
            if intento == RETRY_MAX:
                raise
            time.sleep(4 * intento)


# ── Resume ───────────────────────────────────────────────────────────────────

def cargar_urls_procesadas() -> set:
    urls = set()
    if OUTPUT_CSV.exists():
        with open(OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("url"):
                    urls.add(row["url"].strip())
        log.info("Resume: %d URLs ya presentes en el CSV.", len(urls))
    return urls


def escribir_filas(filas: list[dict]):
    nuevo = not OUTPUT_CSV.exists()
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS)
        if nuevo:
            writer.writeheader()
        writer.writerows(filas)


# ── Scraping principal ───────────────────────────────────────────────────────

def post_a_fila(p: dict) -> dict:
    contenido = html_a_texto(p["content"]["rendered"])
    resumen = html_a_texto(p["excerpt"]["rendered"])
    if not resumen and contenido:
        resumen = contenido.split("\n\n")[0]
    return {
        "fecha": p["date"][:10],
        "institucion": INSTITUCION,
        "titulo": titulo_limpio(p["title"]["rendered"]),
        "resumen": resumen[:500],
        "url": p["link"].strip(),
        "contenido": contenido,
    }


def scrape(max_pages: int = 0) -> int:
    procesadas = cargar_urls_procesadas()
    sesion = requests.Session()
    total_escritos = 0
    pagina = 1
    total_paginas = 1

    while pagina <= total_paginas and (max_pages == 0 or pagina <= max_pages):
        posts, total_paginas = pedir_pagina(sesion, pagina)
        if not posts:
            break
        filas = []
        for p in posts:
            fila = post_a_fila(p)
            if not fila["titulo"] or fila["url"] in procesadas:
                continue
            procesadas.add(fila["url"])
            filas.append(fila)
        escribir_filas(filas)
        total_escritos += len(filas)
        log.info("Página %d/%d: %d posts, %d nuevos.", pagina, total_paginas, len(posts), len(filas))
        pagina += 1
        time.sleep(PAUSA)

    return total_escritos


# ── Validación ───────────────────────────────────────────────────────────────

def validar_csv():
    if not OUTPUT_CSV.exists():
        log.error("CSV no encontrado: %s", OUTPUT_CSV)
        return
    with open(OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNAS:
            log.error("Columnas incorrectas: %s", reader.fieldnames)
        filas = list(reader)
    errores = 0
    vistas = set()
    for i, row in enumerate(filas, 2):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["fecha"]):
            log.warning("Fila %d: fecha inválida %r", i, row["fecha"])
            errores += 1
        if not row["url"].startswith("http") or row["url"] in vistas:
            log.warning("Fila %d: URL inválida o duplicada %r", i, row["url"])
            errores += 1
        vistas.add(row["url"])
        if not row["titulo"].strip():
            log.warning("Fila %d: título vacío", i)
            errores += 1
    largas = sum(1 for r in filas if len(r["contenido"].split()) >= 40)
    fechas = sorted(r["fecha"] for r in filas)
    log.info("Validación: %d filas, %d errores.", len(filas), errores)
    if filas:
        log.info("Rango de fechas: %s a %s", fechas[0], fechas[-1])
        log.info("Contenido >= 40 palabras: %.1f%%", 100 * largas / len(filas))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scraper Junaeb (WP REST API)")
    parser.add_argument(
        "--max-pages", type=int, default=0,
        help="Máximo de páginas de la API (100 posts c/u); 0 = todas",
    )
    args = parser.parse_args()

    n = scrape(max_pages=args.max_pages)
    print(f"\nScraping finalizado. Noticias nuevas: {n}")
    validar_csv()
