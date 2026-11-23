"""
Scraper - Instituto de Desarrollo Agropecuario (INDAP)
Fuente: https://www.indap.gob.cl/noticias
Estrategia: requests + BeautifulSoup (sitio Drupal sin WP API ni sitemap; HTML renderizado en servidor)
  - Listado paginado: /noticias?page=N (N desde 0 hasta que la página venga vacía, ~1069 páginas, 12 notas por página)
  - Las notas regionales (INDAP Maule, INDAP Araucanía, etc.) están en el mismo listado nacional;
    las páginas de cada región solo enlazan a esas mismas notas (/noticias/<slug>) y el listado
    ofrece filtros por región, por lo que no se recorren aparte.
  - Selectores listado:
      a.idp-noticia -> tarjeta de noticia (URL)
      h4            -> título
      time[datetime] -> fecha
  - En cada noticia individual:
      main h1                      -> título
      main time[datetime]          -> fecha (ISO con zona horaria de Chile)
      .idp-noticia-body h4 a       -> oficina/región emisora (ej. "Indap Maule")
      div#newsCaption + div        -> contenido completo
      meta[name=description]       -> resumen
Reanudable: si el CSV existe, omite las URLs que ya contiene.
Uso:
  python scraper_indap.py                       corrida completa -> data/indap.csv
  python scraper_indap.py --muestra 120 --salida ruta.csv
                                                prueba con notas repartidas en todo el rango del listado
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""

import argparse
import csv
import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from bs4 import BeautifulSoup

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
BASE_URL = "https://www.indap.gob.cl"
LIST_URL = f"{BASE_URL}/noticias"
INSTITUCION = "Instituto de Desarrollo Agropecuario (INDAP)"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "indap.csv"
COLUMNAS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

CONCURRENCIA = 4          # máximo de requests simultáneos
PAUSA = 0.3               # segundos de espera tras cada request, por hilo
BATCH_ARTICULOS = 200     # filas que se acumulan antes de escribir al CSV
MAX_RETRIES = 3
TIMEOUT = 40              # segundos por request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_local = threading.local()


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def clean_text(text: str) -> str:
    """Normaliza espacios en blanco en texto extraído."""
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def get_session() -> requests.Session:
    """Una sesión por hilo para reutilizar conexiones."""
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-CL,es;q=0.9"})
    return _local.s


def fetch(url: str) -> BeautifulSoup | None:
    """GET con reintentos y backoff. Retorna el HTML parseado o None si falla."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = get_session().get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                time.sleep(PAUSA)
                return BeautifulSoup(r.content, "html.parser")
            log.warning(f"{url}: HTTP {r.status_code} (intento {attempt}/{MAX_RETRIES})")
        except requests.RequestException as e:
            log.warning(f"{url}: {e} (intento {attempt}/{MAX_RETRIES})")
        time.sleep(3 * attempt)
    log.error(f"{url}: fallo tras {MAX_RETRIES} intentos")
    return None


def cargar_urls_existentes(ruta: Path) -> set[str]:
    """Lee las URLs ya guardadas en el CSV para poder reanudar."""
    if not ruta.exists():
        return set()
    with open(ruta, newline="", encoding="utf-8") as f:
        return {row["url"] for row in csv.DictReader(f) if row.get("url")}


# ---------------------------------------------------------------------------
# Listado
# ---------------------------------------------------------------------------
def scrape_listing_page(page_num: int) -> list[dict] | None:
    """Retorna las notas de una página del listado (None si la descarga falla)."""
    soup = fetch(f"{LIST_URL}?page={page_num}")
    if soup is None:
        return None
    items = []
    for a in soup.select("a.idp-noticia"):
        h = a.select_one("h4")
        if not h:
            continue
        items.append({
            "titulo": clean_text(h.get_text()),
            "url": BASE_URL + a["href"],
        })
    return items


def recorrer_listado() -> list[dict]:
    """Recorre el listado completo en lotes paralelos hasta encontrar páginas vacías."""
    vistos: dict[str, dict] = {}
    pagina = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCIA) as ex:
        while True:
            lote = list(range(pagina, pagina + CONCURRENCIA))
            resultados = list(ex.map(scrape_listing_page, lote))
            fin = False
            for pn, items in zip(lote, resultados):
                if items is None:
                    log.error(f"Página de listado {pn} no se pudo descargar")
                    continue
                if not items:
                    fin = True
                    continue
                for it in items:
                    vistos.setdefault(it["url"], it)
            if pagina % 40 == 0:
                log.info(f"Listado: página {pagina} | notas únicas: {len(vistos)}")
            if fin:
                break
            pagina += CONCURRENCIA
    log.info(f"Listado completo: {len(vistos)} notas únicas")
    return list(vistos.values())


def listado_muestra(n: int, total_paginas: int = 1069) -> list[dict]:
    """Toma notas de páginas espaciadas a lo largo de todo el listado (para pruebas)."""
    paginas = sorted({round(i * (total_paginas - 1) / (n // 3)) for i in range(n // 3 + 1)})
    items: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=CONCURRENCIA) as ex:
        for lista in ex.map(scrape_listing_page, paginas):
            for it in (lista or [])[:3]:
                items.setdefault(it["url"], it)
    return list(items.values())


# ---------------------------------------------------------------------------
# Noticia individual
# ---------------------------------------------------------------------------
def scrape_article(item: dict) -> dict | None:
    """Entra a la nota y extrae fecha, resumen y cuerpo completo. None si no se pudo descargar."""
    soup = fetch(item["url"])
    if soup is None:
        return None
    main = soup.select_one("main") or soup

    h1 = main.select_one("h1")
    titulo = clean_text(h1.get_text()) if h1 else item["titulo"]

    fecha = ""
    t = main.select_one("time[datetime]")
    if t:
        fecha = t["datetime"][:10]

    # Contenido: el div que sigue al bloque de pie de foto
    contenido = ""
    caption = main.select_one("#newsCaption")
    if caption:
        cuerpo = caption.find_next_sibling("div")
        if cuerpo:
            contenido = clean_text(cuerpo.get_text(" "))
    if not contenido:
        # Respaldo: el div hijo más largo de la columna principal
        col = main.select_one(".col-lg-9")
        if col:
            divs = [d for d in col.find_all("div", recursive=False)]
            if divs:
                contenido = clean_text(max(divs, key=lambda d: len(d.get_text())).get_text(" "))

    # Resumen
    resumen = ""
    meta = soup.select_one('meta[name="description"]')
    if meta and meta.get("content"):
        resumen = clean_text(meta["content"])
    if not resumen:
        resumen = contenido[:300]

    return {
        "fecha": fecha,
        "institucion": INSTITUCION,
        "titulo": titulo,
        "resumen": resumen,
        "url": item["url"],
        "contenido": contenido,
    }


# ---------------------------------------------------------------------------
# Guardado
# ---------------------------------------------------------------------------
def guardar_csv(rows: list[dict], ruta: Path) -> None:
    """Agrega filas al CSV en UTF-8 (escribe cabecera si el archivo es nuevo)."""
    nuevo = not ruta.exists()
    with open(ruta, "w" if nuevo else "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS, quoting=csv.QUOTE_ALL)
        if nuevo:
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Scraper de noticias de INDAP")
    ap.add_argument("--muestra", type=int, default=0, help="procesar solo ~N notas repartidas en todo el listado")
    ap.add_argument("--salida", type=Path, default=OUTPUT_PATH, help="ruta del CSV de salida")
    args = ap.parse_args()
    salida: Path = args.salida

    salida.parent.mkdir(exist_ok=True)
    existentes = cargar_urls_existentes(salida)
    log.info(f"Iniciando scraper INDAP | URLs ya guardadas: {len(existentes)}")

    items = listado_muestra(args.muestra) if args.muestra else recorrer_listado()
    pendientes = [it for it in items if it["url"] not in existentes]
    log.info(f"Notas en el listado: {len(items)} | pendientes: {len(pendientes)}")

    total = 0
    fallidas = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCIA) as ex:
        for i in range(0, len(pendientes), BATCH_ARTICULOS):
            lote = pendientes[i : i + BATCH_ARTICULOS]
            resultados = list(ex.map(scrape_article, lote))
            filas = [r for r in resultados if r and r["titulo"]]
            fallidas += sum(1 for r in resultados if r is None)
            guardar_csv(filas, salida)
            total += len(filas)
            log.info(f"Guardadas {total}/{len(pendientes)} | fallidas: {fallidas}")

    log.info(f"Scraping finalizado. Nuevas filas: {total} | fallidas (se reintentan al reanudar): {fallidas}")
    log.info(f"CSV: {salida}")


if __name__ == "__main__":
    main()
