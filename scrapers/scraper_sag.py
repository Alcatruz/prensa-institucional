"""
Scraper - Servicio Agrícola y Ganadero (SAG)
Fuente: https://www.sag.gob.cl/noticias
Estrategia: requests + BeautifulSoup (sitio Drupal sin WP API ni sitemap; HTML renderizado en servidor)
  - Listado paginado: /noticias?page=N (N desde 0 hasta que la página venga vacía, ~308 páginas, 12 notas por página)
  - Las notas regionales (SAG Tarapacá, SAG Coquimbo, etc.) están en el mismo listado nacional;
    el sitio solo ofrece un filtro por región sobre ese mismo listado, por lo que no hace falta recorrerlo aparte.
  - Selectores listado:
      a[href^="/noticias/"] -> tarjeta de noticia (URL)
      h5.node__title        -> título
      div.sag_card-blog__body p -> extracto (cortado con "...")
  - ATENCIÓN: la fecha que muestra el listado en las notas antiguas es la de la migración del sitio
    (27 de septiembre de 2012), no la de publicación. Se usa la fecha de la nota individual:
      div.field--name-field-fecha-noticias time[datetime] -> fecha (12:00 UTC fijo)
  - En cada noticia individual:
      article.node--type-article .node__content > .field--name-body -> contenido completo
Reanudable: si el CSV existe, omite las URLs que ya contiene.
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""

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
BASE_URL = "https://www.sag.gob.cl"
LIST_URL = f"{BASE_URL}/noticias"
INSTITUCION = "Servicio Agrícola y Ganadero (SAG)"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "sag.csv"
COLUMNAS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]

NODO_MIN_MIGRADAS = 3458  # primer id de nodo de una nota migrada (bajo ese id no hay notas)

CONCURRENCIA = 4          # máximo de requests simultáneos
PAUSA = 0.4               # segundos de espera tras cada request, por hilo
BATCH_ARTICULOS = 100     # filas que se acumulan antes de escribir al CSV
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
    for a in soup.select('a[href^="/noticias/"]'):
        title_el = a.select_one(".node__title")
        if not title_el:
            continue
        p = a.select_one(".sag_card-blog__body p")
        resumen = clean_text(p.get_text()) if p else ""
        resumen = re.sub(r"\.{3}$", "", resumen).strip()
        fecha_el = a.select_one(".sag_title--date")
        items.append({
            "titulo": clean_text(title_el.get_text()),
            "resumen": resumen,
            "url": BASE_URL + a["href"],
            "nid": int(a.get("data-history-node-id") or 0),
            "fecha_listado": clean_text(fecha_el.get_text()) if fecha_el else "",
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
            log.info(f"Listado: páginas {lote[0]}-{lote[-1]} | notas únicas: {len(vistos)}")
            if fin:
                break
            pagina += CONCURRENCIA
    return list(vistos.values())


def sondear_nodo(nid: int) -> dict | None:
    """Resuelve /node/<id>: si redirige a una nota (/noticias/<slug>) la retorna, si no None."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = get_session().get(f"{BASE_URL}/node/{nid}", timeout=TIMEOUT, allow_redirects=False)
            time.sleep(PAUSA)
            destino = r.headers.get("Location", "")
            if r.status_code in (301, 302) and "/noticias/" in destino:
                slug = destino.split("/noticias/", 1)[1]
                return {"titulo": "", "resumen": "", "url": f"{BASE_URL}/noticias/{slug}", "nid": nid}
            return None
        except requests.RequestException as e:
            log.warning(f"node/{nid}: {e} (intento {attempt}/{MAX_RETRIES})")
            time.sleep(3 * attempt)
    return None


def completar_notas_antiguas(items: list[dict]) -> list[dict]:
    """
    Las notas migradas desde el sitio anterior comparten la misma fecha de ordenamiento
    (27 de septiembre de 2012), así que el paginado del listado las repite y omite
    de forma inconsistente. Para no perderlas se prueban los ids de nodo del tramo antiguo
    (/node/<id> redirige a la URL de la nota) y se agregan las que falten.
    """
    conocidas = {it["url"] for it in items}
    ids_migrados = [it["nid"] for it in items if it["fecha_listado"].endswith("27 de September 2012")]
    if not ids_migrados:
        return []
    candidatos = range(NODO_MIN_MIGRADAS, max(ids_migrados) + 1)
    log.info(f"Sondeando {len(candidatos)} ids de nodo del tramo antiguo")
    nuevas = []
    with ThreadPoolExecutor(max_workers=CONCURRENCIA) as ex:
        for it in ex.map(sondear_nodo, candidatos):
            if it and it["url"] not in conocidas:
                conocidas.add(it["url"])
                nuevas.append(it)
    log.info(f"Notas antiguas adicionales encontradas: {len(nuevas)}")
    return nuevas


# ---------------------------------------------------------------------------
# Noticia individual
# ---------------------------------------------------------------------------
def scrape_article(item: dict) -> dict | None:
    """Entra a la nota y extrae fecha y cuerpo completo. None si no se pudo descargar."""
    soup = fetch(item["url"])
    if soup is None:
        return None

    # Fecha de publicación (campo propio de la nota)
    fecha = ""
    t = soup.select_one(".field--name-field-fecha-noticias time[datetime]")
    if t:
        fecha = t["datetime"][:10]
    if not fecha:
        # Respaldo: fecha de creación del nodo
        c = soup.select_one('article.node--type-article span[property="dc:date dc:created"]')
        if c and c.get("content"):
            fecha = c["content"][:10]

    # Contenido
    body = soup.select_one("article.node--type-article .node__content > .field--name-body")
    if body is None:
        body = soup.select_one("article.node--type-article .field--name-body")
    contenido = clean_text(body.get_text(" ")) if body else ""
    resumen = item["resumen"] or contenido[:200]

    titulo = item["titulo"]
    if not titulo:
        h = soup.select_one("article.node--type-article span.rdf-meta[property='dc:title']")
        titulo = clean_text(h.get("content", "")) if h else ""
        if not titulo and soup.title:
            titulo = clean_text(soup.title.get_text()).removesuffix("| SAG").strip()

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
def guardar_csv(rows: list[dict], ruta: Path, modo: str = "a") -> None:
    """Agrega filas al CSV en UTF-8 (escribe cabecera si el archivo es nuevo)."""
    nuevo = modo == "w" or not ruta.exists()
    with open(ruta, "w" if nuevo else "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS, quoting=csv.QUOTE_ALL)
        if nuevo:
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    existentes = cargar_urls_existentes(OUTPUT_PATH)
    log.info(f"Iniciando scraper SAG | URLs ya guardadas: {len(existentes)}")

    items = recorrer_listado()
    items += completar_notas_antiguas(items)
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
            guardar_csv(filas, OUTPUT_PATH)
            total += len(filas)
            log.info(f"Guardadas {total}/{len(pendientes)} | fallidas: {fallidas}")

    log.info(f"Scraping finalizado. Nuevas filas: {total} | fallidas (se reintentan al reanudar): {fallidas}")
    log.info(f"CSV: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
