"""
Scraper — Superintendencia de Electricidad y Combustibles (SEC)
URL: https://www.sec.cl/categoria/noticias/
Método: sitemap de WordPress + requests/BeautifulSoup (la REST API está
        restringida: responde 401 "rest_login_required").

Estructura del sitio:
  Listado de URLs:
    - https://www.sec.cl/wp-sitemap-posts-post-1.xml (todos los posts, ~1.700)
    - El <lastmod> del sitemap NO es la fecha de publicación; se toma del detalle.
    - Se conservan solo los posts de categoría "noticias" o "regiones"
      (noticias regionales de las direcciones regionales de la SEC). Se
      descartan "educacion-energetica", "multimedia", "consejos-invierno" y
      "sin-categoria" (videos, campañas y páginas sueltas).
  Detalle:
    - Título:     h1 dentro de article
    - Fecha:      time[itemprop=datePublished] (atributo datetime)
    - Categorías: clases "category-*" del <article>
    - Contenido:  div.post-content (párrafos <p>)
  Notas:
    - Las páginas mezclan UTF-8 con algún byte inválido (scripts en latin-1),
      por eso se decodifica a mano en vez de dejar que bs4 adivine la
      codificación (si no, salen tildes corruptas).
    - Los posts antiguos repiten la fecha, el título y la bajada al inicio del
      cuerpo; se quitan esos párrafos duplicados.
"""

import argparse
import csv
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Configuración ────────────────────────────────────────────────────────────
SITEMAP_URL = "https://www.sec.cl/wp-sitemap-posts-post-1.xml"
INSTITUCION = "Superintendencia de Electricidad y Combustibles (SEC)"
OUTPUT_CSV = Path(__file__).resolve().parent.parent / "data" / "sec.csv"
COLUMNAS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
CATEGORIAS_VALIDAS = {"category-noticias", "category-regiones"}
WORKERS = 4
PAUSA = 0.6          # segundos base tras cada request, por worker
RETRY_MAX = 4
TIMEOUT = 40
LOTE = 40            # filas por escritura incremental

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

_local = threading.local()


def sesion() -> requests.Session:
    """Una sesión por hilo (requests.Session no es del todo thread-safe)."""
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def descargar(url: str) -> str | None:
    """Descarga una URL con reintentos y devuelve el HTML ya decodificado."""
    for intento in range(1, RETRY_MAX + 1):
        try:
            r = sesion().get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content.decode("utf-8", errors="replace")
        except requests.RequestException as e:
            log.warning("Error en %s (intento %d/%d): %s", url, intento, RETRY_MAX, e)
            if intento == RETRY_MAX:
                return None
            time.sleep(4 * intento)


# ── Limpieza de texto ────────────────────────────────────────────────────────

def limpiar(txt: str) -> str:
    txt = re.sub(r"[\u0080-\u009f]", "", txt)   # controles C1 sueltos (viñetas rotas)
    txt = re.sub(r"[\u00a0\u200b]+", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


RE_FECHA_SUELTA = re.compile(
    r"^\d{1,2}\s+de\s+[A-Za-záéíóúÁÉÍÓÚ]+\s+(de\s+)?\d{4}$", re.IGNORECASE
)


def extraer_cuerpo(bloque, titulo: str) -> list[str]:
    """Párrafos del cuerpo sin la fecha/título repetidos ni duplicados."""
    for t in bloque(["script", "style", "iframe", "noscript"]):
        t.decompose()
    parrafos = []
    vistos = set()
    for p in bloque.find_all("p"):
        if p.find_parent("li"):
            continue
        t = limpiar(p.get_text(" ", strip=True))
        if not t:
            continue
        if "titulo" in (p.get("class") or []):
            continue
        if RE_FECHA_SUELTA.match(t):
            continue
        if t.lower() == titulo.lower():
            continue
        if t in vistos:      # bajada repetida
            continue
        vistos.add(t)
        parrafos.append(t)
    return parrafos


def parsear_detalle(html: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    art = soup.find("article")
    if art is None:
        return None
    clases = set(art.get("class") or [])
    if not (clases & CATEGORIAS_VALIDAS):
        return {"_omitido": True}

    h1 = art.find("h1")
    tm = art.find("time", attrs={"itemprop": "datePublished"})
    cuerpo = art.select_one(".post-content")
    if h1 is None or tm is None or not tm.get("datetime"):
        return None

    titulo = limpiar(h1.get_text(" ", strip=True))
    parrafos = extraer_cuerpo(cuerpo, titulo) if cuerpo is not None else []
    contenido = "\n\n".join(parrafos)
    return {
        "fecha": tm["datetime"][:10],
        "institucion": INSTITUCION,
        "titulo": titulo,
        "resumen": (parrafos[0] if parrafos else "")[:500],
        "url": url,
        "contenido": contenido,
    }


def procesar_url(url: str) -> dict | None:
    html = descargar(url)
    time.sleep(PAUSA + random.uniform(0, 0.6))
    if html is None:
        return None
    return parsear_detalle(html, url)


# ── Resume y escritura ───────────────────────────────────────────────────────

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
    if not filas:
        return
    nuevo = not OUTPUT_CSV.exists()
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS)
        if nuevo:
            writer.writeheader()
        writer.writerows(filas)


# ── Scraping principal ───────────────────────────────────────────────────────

def obtener_urls_sitemap() -> list[str]:
    xml = descargar(SITEMAP_URL)
    if not xml:
        raise RuntimeError("No se pudo descargar el sitemap de posts")
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    log.info("Sitemap: %d URLs de posts.", len(urls))
    return urls


def scrape(limite: int = 0, muestra: int = 0) -> int:
    urls = obtener_urls_sitemap()
    procesadas = cargar_urls_procesadas()
    pendientes = [u for u in urls if u not in procesadas]
    if muestra:
        random.seed(7)
        pendientes = random.sample(pendientes, min(muestra, len(pendientes)))
    elif limite:
        pendientes = pendientes[:limite]
    log.info("Por procesar: %d URLs.", len(pendientes))

    total = omitidos = fallidos = 0
    lote = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futuros = {ex.submit(procesar_url, u): u for u in pendientes}
        for i, fut in enumerate(as_completed(futuros), 1):
            url = futuros[fut]
            try:
                fila = fut.result()
            except Exception as e:
                log.error("Fallo inesperado en %s: %s", url, e)
                fila = None
            if fila is None:
                fallidos += 1
            elif fila.get("_omitido"):
                omitidos += 1
            else:
                lote.append(fila)
                total += 1
            if len(lote) >= LOTE:
                escribir_filas(lote)
                lote = []
            if i % 100 == 0:
                log.info("Avance: %d/%d (guardadas %d, omitidas %d, fallidas %d)",
                         i, len(pendientes), total, omitidos, fallidos)
    escribir_filas(lote)
    log.info("Fin: %d guardadas, %d omitidas por categoría, %d fallidas.",
             total, omitidos, fallidos)
    return total


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
    parser = argparse.ArgumentParser(description="Scraper SEC (sitemap + detalle)")
    parser.add_argument("--limite", type=int, default=0,
                        help="Procesar solo las primeras N URLs pendientes (0 = todas)")
    parser.add_argument("--muestra", type=int, default=0,
                        help="Procesar N URLs al azar (para pruebas)")
    args = parser.parse_args()

    n = scrape(limite=args.limite, muestra=args.muestra)
    print(f"\nScraping finalizado. Noticias nuevas: {n}")
    validar_csv()
