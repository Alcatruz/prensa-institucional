"""
Scraper — Presidencia de la República de Chile

Fuentes (en orden de calidad):

  1. api  : https://www.presidencia.gob.cl  (sitio actual, gobierno de Kast)
       - Es una SPA en Vue; los datos salen de una API JSON propia:
           GET /api/noticias?page=N&pageSize=100   -> listado (items, hasNext)
           GET /api/noticias/by-slug?slug=...      -> detalle (body HTML)
           GET /api/discursos ... y /api/discursos/by-slug (igual estructura)
       - El dominio antiguo prensa.presidencia.cl redirige (301) al sitio nuevo,
         de modo que los IDs viejos (comunicado.aspx?id=NNNN) ya no se pueden
         recorrer en vivo.
       - Solo contiene lo publicado desde marzo de 2026.

  2. wayback : archivo del sitio anterior prensa.presidencia.cl en web.archive.org
       - comunicado.aspx?id=NNNN y discurso.aspx?id=NNNN
       - La lista de IDs capturados se obtiene del índice CDX; cada página se
         baja en bruto (modificador id_) y se parsea con BeautifulSoup.
       - Cubre de forma parcial 2016-2026 (Bachelet II final, Piñera II, Boric).

  3. gobcl : https://www.gob.cl/noticias/?p=N  (noticias de todo el gobierno)
       - No hay campo de institución, así que se filtra por título (menciones
         al Presidente / Presidenta / Mandatario / Jefe de Estado). Es una
         heurística, por eso va a un CSV aparte con la columna extra `criterio`.
       - El archivo llega hasta junio de 2013.

Salidas:
  data/presidencia.csv            comunicados y noticias (6 columnas canónicas)
  data/presidencia_discursos.csv  discursos (6 columnas canónicas)
  data/presidencia_gobcl.csv      candidatas de gob.cl (6 columnas + criterio)

Es reanudable: si el CSV existe, se saltan las URLs que ya contiene.

Uso:
  python scrapers/scraper_presidencia.py                    # solo api
  python scrapers/scraper_presidencia.py --fuente wayback
  python scrapers/scraper_presidencia.py --fuente gobcl
  python scrapers/scraper_presidencia.py --fuente todo
  python scrapers/scraper_presidencia.py --fuente wayback --muestra 150
  python scrapers/scraper_presidencia.py --resumen          # conteo por gobierno
"""

import argparse
import asyncio
import csv
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# ── Configuración ────────────────────────────────────────────────────────────
INSTITUCION = "Presidencia de la República"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CSV_PRINCIPAL = DATA_DIR / "presidencia.csv"
CSV_DISCURSOS = DATA_DIR / "presidencia_discursos.csv"
CSV_GOBCL = DATA_DIR / "presidencia_gobcl.csv"
CSV_GOBCL_INDICE = DATA_DIR / "gobcl_indice.csv"   # todas las notas del listado (sin cuerpo)

COLUMNAS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
COLUMNAS_GOBCL = COLUMNAS + ["criterio"]

API_BASE = "https://www.presidencia.gob.cl"
PAGE_SIZE_API = 100

WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK_RAW = "https://web.archive.org/web/{ts}id_/{url}"
OLD_BASE = "https://prensa.presidencia.cl"

GOBCL_BASE = "https://www.gob.cl"

CONCURRENCIA = 4          # máximo de requests simultáneas al mismo sitio
CONCURRENCIA_WAYBACK = 1  # el archivo responde 429 en cuanto se le insiste
PAUSA = 0.4               # segundos tras cada request
PAUSA_WAYBACK = 3.0
CONCURRENCIA_GOBCL = 2    # gob.cl responde 403 si se le exige más
PAUSA_GOBCL = 1.0
REINTENTOS = 4
TIMEOUT = 45

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Cambios de mando, para clasificar por gobierno
GOBIERNOS = [
    ("Bachelet I", date(2006, 3, 11), date(2010, 3, 10)),
    ("Piñera I", date(2010, 3, 11), date(2014, 3, 10)),
    ("Bachelet II", date(2014, 3, 11), date(2018, 3, 10)),
    ("Piñera II", date(2018, 3, 11), date(2022, 3, 10)),
    ("Boric", date(2022, 3, 11), date(2026, 3, 10)),
    ("Kast", date(2026, 3, 11), date(2099, 1, 1)),
]

# Título de gob.cl que sugiere nota propia de la Presidencia
RE_PRESIDENCIA = re.compile(
    r"\b(Presidente|Presidenta|Mandatario|Mandataria|Jefe de Estado|"
    r"Jefa de Estado|Primera Dama|La Moneda)\b",
    re.IGNORECASE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("presidencia")

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}


# ── Utilidades de texto y fecha ──────────────────────────────────────────────
def html_a_texto(html: str) -> str:
    """Convierte HTML a texto plano con un párrafo por línea."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    texto = soup.get_text("\n")
    lineas = []
    for linea in texto.replace("\xa0", " ").replace("​", "").splitlines():
        linea = re.sub(r"[ \t]+", " ", linea).strip()
        if linea:
            lineas.append(linea)
    return "\n".join(lineas)


def limpiar(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "").replace("\xa0", " ")).strip()


def parse_fecha(texto: str) -> str:
    """Normaliza distintos formatos de fecha a YYYY-MM-DD ('' si no se puede)."""
    texto = limpiar(texto).lower()
    if not texto:
        return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if m:
        return m.group(0)
    # "5 jul 2020", "24 de marzo de 2026"
    m = re.match(r"(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+)\.?\s+(?:de\s+)?(\d{4})", texto)
    if m:
        mes = MESES.get(m.group(2)[:3])
        if mes:
            return f"{m.group(3)}-{mes:02d}-{int(m.group(1)):02d}"
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", texto)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""


def gobierno_de(fecha: str) -> str:
    try:
        d = datetime.strptime(fecha, "%Y-%m-%d").date()
    except ValueError:
        return "sin fecha"
    for nombre, ini, fin in GOBIERNOS:
        if ini <= d <= fin:
            return nombre
    return "anterior a 2006"


# ── Salida CSV (reanudable) ──────────────────────────────────────────────────
class Salida:
    """CSV incremental. Si el archivo ya existe, recuerda las URLs presentes."""

    def __init__(self, ruta: Path, columnas: list[str]):
        self.ruta = ruta
        self.columnas = columnas
        self.urls: set[str] = set()
        self.nuevas = 0
        ruta.parent.mkdir(parents=True, exist_ok=True)
        if ruta.exists() and ruta.stat().st_size > 0:
            with open(ruta, "r", encoding="utf-8", newline="") as f:
                for fila in csv.DictReader(f):
                    if fila.get("url"):
                        self.urls.add(fila["url"].strip())
            log.info("Resume %s: %d URLs ya guardadas", ruta.name, len(self.urls))
        else:
            with open(ruta, "w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(columnas)

    def agregar(self, fila: dict) -> bool:
        url = fila["url"].strip()
        if url in self.urls or not fila.get("titulo"):
            return False
        with open(self.ruta, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self.columnas)
            w.writerow({c: fila.get(c, "") for c in self.columnas})
        self.urls.add(url)
        self.nuevas += 1
        return True


# ── HTTP con reintentos ──────────────────────────────────────────────────────
class Cliente:
    def __init__(self, concurrencia: int, pausa: float):
        self.sem = asyncio.Semaphore(concurrencia)
        self.pausa = pausa
        self.http: httpx.AsyncClient | None = None
        self.libre_desde = 0.0  # tras un 429 se frenan todas las tareas a la vez

    async def __aenter__(self):
        self.http = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept-Language": "es-CL,es;q=0.9"},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc):
        await self.http.aclose()

    async def get(self, url: str, params: dict | None = None, json: bool = False):
        """Devuelve texto o JSON; None si falla tras los reintentos o es 404."""
        for intento in range(1, REINTENTOS + 1):
            try:
                async with self.sem:
                    espera_global = self.libre_desde - asyncio.get_running_loop().time()
                    if espera_global > 0:
                        await asyncio.sleep(espera_global)
                    r = await self.http.get(url, params=params)
                    estado = r.status_code
                    if estado == 200:
                        datos = r.json() if json else r.text
                        await asyncio.sleep(self.pausa)
                        return datos
                    if estado in (404, 410):
                        return None
                    espera = 60 if estado == 429 else (30 if estado == 403 else 5 * intento)
                    if estado in (403, 429):
                        self.libre_desde = asyncio.get_running_loop().time() + espera
                    log.warning("HTTP %s en %s (intento %d)", estado, url[:110], intento)
                await asyncio.sleep(espera)
            except (httpx.HTTPError, ValueError) as e:
                log.warning("Error de red en %s (intento %d): %s", url[:110], intento, type(e).__name__)
                await asyncio.sleep(5 * intento)
        return None


# ── Fuente 1: API del sitio actual ───────────────────────────────────────────
async def listar_api(cli: Cliente, endpoint: str) -> list[dict]:
    items, pagina = [], 1
    while True:
        j = await cli.get(f"{API_BASE}/api/{endpoint}",
                          params={"page": pagina, "pageSize": PAGE_SIZE_API}, json=True)
        if not j:
            break
        items.extend(j.get("items", []))
        if not j.get("hasNext"):
            break
        pagina += 1
    log.info("API %s: %d items en el listado", endpoint, len(items))
    return items


async def detalle_api(cli: Cliente, salida: Salida, endpoint: str, item: dict) -> None:
    slug = item["slug"]
    url = f"{API_BASE}/{endpoint}/{slug}"
    if url in salida.urls:
        return
    d = await cli.get(f"{API_BASE}/api/{endpoint}/by-slug", params={"slug": slug}, json=True)
    if not d:
        log.warning("Sin detalle: %s", slug)
        return
    contenido = html_a_texto(d.get("body", ""))
    if not contenido:
        contenido = limpiar(d.get("leadParagraph", ""))
    salida.agregar({
        "fecha": parse_fecha(d.get("date") or item.get("date", "")),
        "institucion": INSTITUCION,
        "titulo": limpiar(d.get("title") or item.get("title", "")),
        "resumen": limpiar(d.get("excerpt") or item.get("excerpt", "")),
        "url": url,
        "contenido": contenido,
    })


async def fuente_api(muestra: int | None) -> None:
    principal, discursos = Salida(CSV_PRINCIPAL, COLUMNAS), Salida(CSV_DISCURSOS, COLUMNAS)
    async with Cliente(CONCURRENCIA, PAUSA) as cli:
        for endpoint, salida in (("noticias", principal), ("discursos", discursos)):
            items = await listar_api(cli, endpoint)
            if muestra:
                items = items[:: max(1, len(items) // muestra)]
            pendientes = [i for i in items if f"{API_BASE}/{endpoint}/{i['slug']}" not in salida.urls]
            log.info("%s: %d por descargar", endpoint, len(pendientes))
            await asyncio.gather(*(detalle_api(cli, salida, endpoint, i) for i in pendientes))
            log.info("%s: %d filas nuevas", endpoint, salida.nuevas)


# ── Fuente 2: Wayback Machine (prensa.presidencia.cl) ────────────────────────
async def ids_wayback(cli: Cliente, pagina: str) -> dict[int, list[tuple[str, str]]]:
    """
    Devuelve {id: [(timestamp, url_original), ...]} con las capturas 200 del
    índice CDX, ordenadas de la más antigua a la más nueva.
    """
    params = {
        "url": f"prensa.presidencia.cl/{pagina}.aspx*",
        "output": "json",
        "fl": "original,timestamp",
        "filter": "statuscode:200",
    }
    filas = await cli.get(WAYBACK_CDX, params=params, json=True)
    ids: dict[int, list[tuple[str, str]]] = {}
    for original, ts in (filas or [])[1:]:
        m = re.search(r"[?&]id=(\d+)", original)
        if m:
            ids.setdefault(int(m.group(1)), []).append((ts, original))
    for k in ids:
        ids[k].sort()
    log.info("CDX %s: %d IDs distintos", pagina, len(ids))
    return ids


def parsear_pagina_antigua(html: str) -> dict | None:
    """Extrae título, fecha, bajada y cuerpo de comunicado.aspx / discurso.aspx."""
    s = BeautifulSoup(html, "html.parser")
    cuerpo = s.find(id="main_ltContenido")
    titulo = s.find(id="main_ltTitulo")
    if not cuerpo or not titulo:
        return None
    fecha = s.find(id="main_ltFEcha") or s.find(id="main_ltFecha")
    bajada = s.find(id="main_ltBajada")
    return {
        "titulo": limpiar(titulo.get_text(" ")),
        "fecha": parse_fecha(fecha.get_text(" ")) if fecha else "",
        "resumen": limpiar(bajada.get_text(" ")) if bajada else "",
        "contenido": html_a_texto(str(cuerpo)),
    }


async def detalle_wayback(cli: Cliente, salida: Salida, pagina: str, id_: int,
                          capturas: list[tuple[str, str]]) -> None:
    url_canonica = f"{OLD_BASE}/{pagina}.aspx?id={id_}"
    if url_canonica in salida.urls:
        return
    for ts, original in capturas[:3]:  # si la primera captura viene rota, prueba la siguiente
        html = await cli.get(WAYBACK_RAW.format(ts=ts, url=original))
        if not html:
            continue
        datos = parsear_pagina_antigua(html)
        if datos and datos["contenido"]:
            if not datos["fecha"]:
                log.warning("Sin fecha en id=%d (%s)", id_, pagina)
            salida.agregar({
                "fecha": datos["fecha"],
                "institucion": INSTITUCION,
                "titulo": datos["titulo"],
                "resumen": datos["resumen"],
                "url": url_canonica,
                "contenido": datos["contenido"],
            })
            return
    log.warning("No se pudo recuperar id=%d (%s)", id_, pagina)


async def fuente_wayback(muestra: int | None) -> None:
    destinos = (("comunicado", Salida(CSV_PRINCIPAL, COLUMNAS)),
                ("discurso", Salida(CSV_DISCURSOS, COLUMNAS)))
    async with Cliente(CONCURRENCIA_WAYBACK, PAUSA_WAYBACK) as cli:
        for pagina, salida in destinos:
            ids = await ids_wayback(cli, pagina)
            orden = sorted(ids)
            if muestra:
                # muestra repartida de forma pareja sobre todo el rango de IDs
                n = muestra // 2
                paso = max(1, len(orden) // n)
                orden = orden[::paso][:n]
            pendientes = [i for i in orden if f"{OLD_BASE}/{pagina}.aspx?id={i}" not in salida.urls]
            log.info("%s: %d por descargar de Wayback", pagina, len(pendientes))
            for lote in range(0, len(pendientes), 20):
                tareas = [detalle_wayback(cli, salida, pagina, i, ids[i])
                          for i in pendientes[lote:lote + 20]]
                await asyncio.gather(*tareas)
                log.info("%s: %d/%d (nuevas %d)", pagina,
                         min(lote + 20, len(pendientes)), len(pendientes), salida.nuevas)


# ── Fuente 3: gob.cl/noticias (filtrado por título) ──────────────────────────
def parsear_listado_gobcl(html: str) -> list[dict]:
    s = BeautifulSoup(html, "html.parser")
    items = {}
    for a in s.select("a.card-news-a[href^='/noticias/']"):
        small = a.select_one("small")
        h3 = a.select_one("h3")
        if not small or not h3:
            continue
        items[a["href"]] = {
            "url": GOBCL_BASE + a["href"],
            "fecha": parse_fecha(small.get_text()),
            "titulo": limpiar(h3.get_text(" ")),
        }
    return list(items.values())


async def detalle_gobcl(cli: Cliente, salida: Salida, item: dict, criterio: str) -> None:
    if item["url"] in salida.urls:
        return
    html = await cli.get(item["url"])
    if not html:
        return
    s = BeautifulSoup(html, "html.parser")
    cuerpo = s.select_one(".article-detail")
    lead = s.select_one(".lead-in")
    contenido = html_a_texto(str(cuerpo)) if cuerpo else ""
    salida.agregar({
        "fecha": item["fecha"],
        "institucion": INSTITUCION,
        "titulo": item["titulo"],
        "resumen": limpiar(lead.get_text(" ")) if lead else "",
        "url": item["url"],
        "contenido": contenido,
        "criterio": criterio,
    })


async def fuente_gobcl(muestra: int | None) -> None:
    salida = Salida(CSV_GOBCL, COLUMNAS_GOBCL)
    async with Cliente(CONCURRENCIA_GOBCL, PAUSA_GOBCL) as cli:
        html = await cli.get(f"{GOBCL_BASE}/noticias/")
        paginas = [int(n) for n in re.findall(r"\?p=(\d+)", html or "")]
        total = max(paginas) if paginas else 1560
        log.info("gob.cl: %d páginas de listado", total)
        numeros = list(range(1, total + 1))
        if muestra:
            numeros = numeros[:: max(1, total // max(1, muestra // 3))]

        candidatas: list[tuple[dict, str]] = []
        indice: list[dict] = []

        async def una_pagina(n: int) -> None:
            h = await cli.get(f"{GOBCL_BASE}/noticias/", params={"p": n})
            for it in parsear_listado_gobcl(h or ""):
                indice.append(it)
                m = RE_PRESIDENCIA.search(it["titulo"])
                if m and it["url"] not in salida.urls:
                    candidatas.append((it, m.group(1).lower()))

        for lote in range(0, len(numeros), 40):
            await asyncio.gather(*(una_pagina(n) for n in numeros[lote:lote + 40]))
            log.info("gob.cl listado: %d/%d páginas, %d candidatas",
                     min(lote + 40, len(numeros)), len(numeros), len(candidatas))

        # índice completo del listado: permite filtrar distinto más adelante sin volver a recorrerlo
        if not muestra:
            vistos = {}
            for it in indice:
                vistos[it["url"]] = it
            with open(CSV_GOBCL_INDICE, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["fecha", "titulo", "url"])
                w.writeheader()
                for it in sorted(vistos.values(), key=lambda x: x["fecha"], reverse=True):
                    w.writerow({k: it[k] for k in ("fecha", "titulo", "url")})
            log.info("gob.cl índice: %d notas -> %s", len(vistos), CSV_GOBCL_INDICE.name)

        if muestra:
            candidatas = candidatas[:: max(1, len(candidatas) // muestra)][:muestra]
        for lote in range(0, len(candidatas), 40):
            await asyncio.gather(*(detalle_gobcl(cli, salida, it, c)
                                   for it, c in candidatas[lote:lote + 40]))
            log.info("gob.cl detalle: %d/%d (nuevas %d)",
                     min(lote + 40, len(candidatas)), len(candidatas), salida.nuevas)


# ── Resumen por gobierno ─────────────────────────────────────────────────────
def resumen_csv() -> None:
    for ruta in (CSV_PRINCIPAL, CSV_DISCURSOS, CSV_GOBCL):
        if not ruta.exists():
            continue
        cuenta: dict[str, int] = {}
        largo = total = 0
        fechas = []
        with open(ruta, "r", encoding="utf-8", newline="") as f:
            for fila in csv.DictReader(f):
                total += 1
                g = gobierno_de(fila["fecha"])
                cuenta[g] = cuenta.get(g, 0) + 1
                if len(fila["contenido"].split()) >= 40:
                    largo += 1
                if fila["fecha"]:
                    fechas.append(fila["fecha"])
        if not total:
            continue
        print(f"\n{ruta.name}: {total} filas, {min(fechas)} a {max(fechas)}, "
              f"contenido >= 40 palabras: {100 * largo / total:.1f}%")
        for nombre in [g[0] for g in GOBIERNOS] + ["anterior a 2006", "sin fecha"]:
            if nombre in cuenta:
                print(f"  {nombre:<16}{cuenta[nombre]}")


# ── Main ─────────────────────────────────────────────────────────────────────
async def main() -> None:
    ap = argparse.ArgumentParser(description="Scraper de la prensa de la Presidencia de Chile")
    ap.add_argument("--fuente", choices=["api", "wayback", "gobcl", "todo"], default="api")
    ap.add_argument("--muestra", type=int, default=None,
                    help="procesa solo N notas repartidas en todo el rango (prueba)")
    ap.add_argument("--resumen", action="store_true", help="muestra conteos por gobierno y sale")
    args = ap.parse_args()

    if args.resumen:
        resumen_csv()
        return
    if args.fuente in ("api", "todo"):
        await fuente_api(args.muestra)
    if args.fuente in ("wayback", "todo"):
        await fuente_wayback(args.muestra)
    if args.fuente in ("gobcl", "todo"):
        await fuente_gobcl(args.muestra)
    resumen_csv()


if __name__ == "__main__":
    asyncio.run(main())
