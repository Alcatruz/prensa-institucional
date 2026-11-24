#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Utilidades compartidas por los scrapers de prensa.

- sesion(): requests.Session con reintentos (429 y 5xx, respetando Retry-After)
  y User-Agent de navegador.
- obtener(): GET cortés (pausa por hilo) que reintenta sin verificar el
  certificado cuando el sitio publica una cadena SSL incompleta.
- fecha_iso(): normaliza fechas en español o inglés a AAAA-MM-DD.
- texto_de(): texto limpio de un elemento HTML, un párrafo por línea.
- SalidaCSV: escritura incremental en el formato del proyecto. Cada nota se
  agrega al CSV apenas se descarga (no se pierde lo avanzado si el proceso se
  corta) y al cerrar se deduplica por URL y se ordena por fecha descendente.
  Las URLs ya presentes se informan con ya_esta() para no volver a bajarlas.
- argumentos(): opciones comunes (--muestra, --salida, --hilos, --pausa).
- en_paralelo(): aplica una función a una lista con un pool de hilos.
"""
import argparse
import csv
import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COLUMNAS = ["fecha", "institucion", "titulo", "resumen", "url", "contenido"]
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("prensa")


def configurar_log(nombre: str = "prensa") -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    return logging.getLogger(nombre)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def sesion(reintentos: int = 4) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=reintentos, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "HEAD"], respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=8))
    s.mount("http://", HTTPAdapter(max_retries=retry, pool_maxsize=8))
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-CL,es;q=0.9"})
    return s


_sin_verificar: set[str] = set()
_local = threading.local()


def obtener(s: requests.Session, url: str, pausa: float = 1.0, timeout: int = 40,
            **kw) -> requests.Response | None:
    """GET con pausa posterior. Devuelve la respuesta si es 200; si no, None."""
    host = re.sub(r"^https?://([^/]+).*", r"\1", url)
    try:
        r = s.get(url, timeout=timeout, verify=host not in _sin_verificar, **kw)
    except requests.exceptions.SSLError:
        if host not in _sin_verificar:
            log.warning("Cadena SSL incompleta en %s: se sigue sin verificar el certificado", host)
            _sin_verificar.add(host)
        try:
            r = s.get(url, timeout=timeout, verify=False, **kw)
        except requests.RequestException as e:
            log.warning("Error en %s: %s", url, e)
            return None
    except requests.RequestException as e:
        log.warning("Error en %s: %s", url, e)
        return None
    finally:
        time.sleep(pausa)
    if r.status_code != 200:
        log.warning("HTTP %s en %s", r.status_code, url)
        return None
    # Sin charset declarado, requests asume Latin-1 para text/*; si el contenido
    # es UTF-8 válido, se decodifica como UTF-8 (evita "MÃ¡s" en vez de "Más").
    if "charset" not in r.headers.get("Content-Type", "").lower():
        try:
            r.content.decode("utf-8")
            r.encoding = "utf-8"
        except UnicodeDecodeError:
            pass
    return r


# ---------------------------------------------------------------------------
# Fechas y texto
# ---------------------------------------------------------------------------
MESES = {
    "enero": 1, "ene": 1, "january": 1, "jan": 1,
    "febrero": 2, "feb": 2, "february": 2,
    "marzo": 3, "mar": 3, "march": 3,
    "abril": 4, "abr": 4, "april": 4, "apr": 4,
    "mayo": 5, "may": 5,
    "junio": 6, "jun": 6, "june": 6,
    "julio": 7, "jul": 7, "july": 7,
    "agosto": 8, "ago": 8, "august": 8, "aug": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9, "set": 9, "september": 9,
    "octubre": 10, "oct": 10, "october": 10,
    "noviembre": 11, "nov": 11, "november": 11,
    "diciembre": 12, "dic": 12, "december": 12, "dec": 12,
}
_RE_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_RE_NUM = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")
_RE_DMY = re.compile(r"\b(\d{1,2})(?:\s+de\s+|\s+|-)([a-záéíóú]+)\.?,?(?:\s+de\s+|\s+|-)(\d{4})\b", re.I)
_RE_MDY = re.compile(r"\b([a-záéíóú]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
_RE_URL = re.compile(r"/(20\d{2}|19\d{2})/(\d{2})/(\d{2})/")


def _valida(a: int, m: int, d: int) -> str:
    try:
        f = date(a, m, d)
    except ValueError:
        return ""
    return f.isoformat() if date(1990, 1, 1) <= f <= date.today() else ""


def fecha_iso(texto: str, orden: str = "dmy") -> str:
    """Primera fecha reconocible en `texto`, como AAAA-MM-DD ('' si no hay).

    Acepta ISO, dd/mm/aaaa (o mm/dd/aaaa con orden='mdy'), '12 de marzo de 2024',
    '18 de nov, 2026', 'Marzo 12, 2024', 'November 22, 2026'. Descarta fechas
    anteriores a 1990 o futuras (erratas de los sitios).
    """
    if not texto:
        return ""
    t = texto.strip()
    m = _RE_ISO.search(t)
    if m:
        f = _valida(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if f:
            return f
    m = _RE_NUM.search(t)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        f = _valida(y, a, b) if orden == "mdy" else _valida(y, b, a)
        if f:
            return f
    for m in _RE_DMY.finditer(t):
        mes = MESES.get(m.group(2).lower().rstrip("."))
        if mes:
            f = _valida(int(m.group(3)), mes, int(m.group(1)))
            if f:
                return f
    for m in _RE_MDY.finditer(t):
        mes = MESES.get(m.group(1).lower().rstrip("."))
        if mes:
            f = _valida(int(m.group(3)), mes, int(m.group(2)))
            if f:
                return f
    return ""


def fecha_de_url(url: str) -> str:
    m = _RE_URL.search(url)
    return _valida(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else ""


def fecha_meta(soup) -> str:
    """Fecha de publicación desde metadatos estándar (Open Graph, JSON-LD, time)."""
    for sel, attr in (('meta[property="article:published_time"]', "content"),
                      ('meta[name="date"]', "content"),
                      ('meta[itemprop="datePublished"]', "content"),
                      ("time[datetime]", "datetime")):
        el = soup.select_one(sel)
        if el and el.get(attr):
            f = fecha_iso(el[attr])
            if f:
                return f
    for s in soup.select('script[type="application/ld+json"]'):
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', s.string or "")
        if m:
            f = fecha_iso(m.group(1))
            if f:
                return f
    return ""


_BASURA = "script, style, noscript, iframe, form, button, svg, nav"
_BLOQUES = ["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote",
            "section", "article", "header", "footer", "figcaption", "table", "ul", "ol"]


def texto_de(el) -> str:
    """Texto de un elemento BeautifulSoup, un bloque por línea, sin scripts ni estilos.

    Las etiquetas en línea (b, a, span...) no cortan la línea; los bloques sí.
    """
    if el is None:
        return ""
    for b in el.select(_BASURA):
        b.decompose()
    for br in el.find_all("br"):
        br.replace_with("\n")
    for blq in el.find_all(_BLOQUES):
        blq.append("\n")
    lineas = (re.sub(r"\s+", " ", x).strip() for x in el.get_text().split("\n"))
    return "\n".join(x for x in lineas if x)


def limpiar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


# ---------------------------------------------------------------------------
# CSV incremental
# ---------------------------------------------------------------------------
class SalidaCSV:
    """CSV del proyecto con escritura incremental y reanudación por URL."""

    def __init__(self, ruta: Path | str):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.urls: set[str] = set()
        nuevo = not self.ruta.exists() or self.ruta.stat().st_size == 0
        if not nuevo:
            with open(self.ruta, encoding="utf-8", newline="") as f:
                self.urls = {r["url"] for r in csv.DictReader(f) if r.get("url")}
        self.f = open(self.ruta, "a", encoding="utf-8", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=COLUMNAS, quoting=csv.QUOTE_MINIMAL)
        if nuevo:
            self.w.writeheader()
        self.previas = len(self.urls)
        self.nuevas = 0

    def ya_esta(self, url: str) -> bool:
        return url in self.urls

    def agregar(self, fila: dict) -> bool:
        url = (fila.get("url") or "").strip()
        if not url:
            return False
        with self.lock:
            if url in self.urls:
                return False
            self.w.writerow({c: (fila.get(c) or "").strip() for c in COLUMNAS})
            self.f.flush()
            self.urls.add(url)
            self.nuevas += 1
            return True

    def cerrar(self) -> int:
        """Deduplica por URL, ordena por fecha desc y reescribe. Devuelve el total."""
        self.f.close()
        with open(self.ruta, encoding="utf-8", newline="") as f:
            filas = {r["url"]: r for r in csv.DictReader(f) if r.get("url")}
        orden = sorted(filas.values(), reverse=True,
                       key=lambda r: r["fecha"] if re.match(r"\d{4}-\d{2}-\d{2}$", r["fecha"] or "") else "")
        with open(self.ruta, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNAS, quoting=csv.QUOTE_MINIMAL)
            w.writeheader()
            w.writerows({c: r.get(c, "") for c in COLUMNAS} for r in orden)
        log.info("CSV %s: %d filas (%d nuevas)", self.ruta.name, len(orden), self.nuevas)
        return len(orden)


# ---------------------------------------------------------------------------
# CLI y paralelismo
# ---------------------------------------------------------------------------
def argumentos(descripcion: str, salida_por_defecto: Path | None = None) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=descripcion)
    ap.add_argument("--muestra", type=int, default=0, help="solo las N notas más recientes (prueba)")
    ap.add_argument("--salida", type=Path, default=salida_por_defecto, help="ruta del CSV de salida")
    ap.add_argument("--hilos", type=int, default=2, help="descargas simultáneas (por defecto 2)")
    ap.add_argument("--pausa", type=float, default=1.0, help="segundos de espera tras cada solicitud")
    return ap


def en_paralelo(items: list, funcion, hilos: int = 2, cada: int = 50, etiqueta: str = "notas") -> None:
    """Aplica `funcion` a cada item con un pool de hilos e informa el avance."""
    total = len(items)
    hechos = 0
    with ThreadPoolExecutor(max_workers=max(1, hilos)) as ex:
        futs = [ex.submit(funcion, it) for it in items]
        for fut in as_completed(futs):
            hechos += 1
            try:
                fut.result()
            except Exception as e:  # una nota con error no detiene el resto
                log.warning("Error procesando un ítem: %s", e)
            if hechos % cada == 0 or hechos == total:
                log.info("%s: %d/%d", etiqueta, hechos, total)
