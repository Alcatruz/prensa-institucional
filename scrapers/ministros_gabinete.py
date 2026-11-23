"""
Base de ministros y ministras de Estado de Chile, desde el 11-03-2006 a la fecha.

Reúne a los titulares de todos los ministerios del gabinete (y, cuando la
fuente los registra, a interinos y subrogantes) desde el primer gobierno de
Michelle Bachelet hasta el gobierno de José Antonio Kast. Agrega además a los
subsecretarios de Telecomunicaciones (SUBTEL), con ministerio_normalizado =
"SUBTEL".

Fuentes
-------
1. Wikipedia en español, a través de la API de MediaWiki (action=parse y
   action=query; no se descarga el HTML público de las páginas):
     - Anexos de gabinetes ministeriales de cada gobierno (tabla "Ministros").
     - Artículo "Subsecretaría de Telecomunicaciones de Chile" (tabla
       "Subsecretarios"), contrastado con las tablas de subsecretarios de
       los anexos.
     - Biografías de cada persona, solo para leer el título del cargo
       ("ministra"/"ministro", "subsecretaria"/"subsecretario") con el que
       se codifica el género.
2. González-Bustamante y Olivares, base "chilean-ministers" (1990-2014),
   para validar el tramo 2006-2014. Se lee solo su CSV de datos.

Salida : ../data/ministros_2006_2026.csv (UTF-8, separado por comas)

Columnas
--------
gobierno, ministerio, ministerio_normalizado, nombre, fecha_inicio, fecha_fin,
partido, genero, tipo, fuente

Criterios de codificación
-------------------------
- ministerio: nombre oficial vigente en la fecha de inicio de la fila. Si un
  período atraviesa un cambio de nombre legal, la fila se divide en esa fecha
  (Wikipedia ya lo hace en casi todos los casos).
- Organismos con rango de ministro anteriores a su ministerio (Comisión
  Nacional de Energía, Comisión Nacional del Medio Ambiente, Servicio
  Nacional de la Mujer, Consejo Nacional de la Cultura y las Artes) se
  incluyen y se normalizan al ministerio que los sucedió.
- tipo: "titular" por defecto. Si la fuente marca a la persona como
  subrogante: "subrogante" cuando la subrogancia dura hasta 30 días e
  "interino" cuando dura más (reemplazos prolongados).
- genero: según el título del cargo que usa la fuente (ficha de la
  biografía en Wikipedia; si no existe, el texto de los anexos). Nunca a
  partir del nombre de pila. Queda vacío si la fuente no da el título.

Uso
---
  python ministros_gabinete.py
  python ministros_gabinete.py --cache C:/ruta/cache    # guarda las respuestas de la API
  python ministros_gabinete.py --variables-gbo          # agrega variables de perfil de GB&O
"""

import argparse
import copy
import csv
import hashlib
import io
import json
import logging
import re
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
API_WIKI = "https://es.wikipedia.org/w/api.php"
URL_WIKI = "https://es.wikipedia.org/w/index.php"
URL_GBO = ("https://raw.githubusercontent.com/bgonzalezbustamante/"
           "chilean-ministers/HEAD/data/tidy/Chilean_cabinets_1990_2014.csv")
USER_AGENT = ("prensa-institucional-ministros/1.0 "
              "(investigacion academica; python-requests)")
SALIDA = Path(__file__).resolve().parent.parent / "data" / "ministros_2006_2026.csv"

COLUMNAS = ["gobierno", "ministerio", "ministerio_normalizado", "nombre",
            "fecha_inicio", "fecha_fin", "partido", "genero", "tipo", "fuente"]

INICIO_SERIE = date(2006, 3, 11)
UMBRAL_SUBROGANCIA = 30     # días: una subrogancia más larga se codifica "interino"
UMBRAL_LAGUNA = 30          # días entre titulares sucesivos
TOLERANCIA_GBO = 7          # días de tolerancia al comparar fechas con GB&O
PAUSA_API = 1.0             # segundos entre llamadas no cacheadas

# (gobierno, inicio, fin, página del anexo, texto que identifica la sección)
GOBIERNOS = [
    ("Bachelet I", date(2006, 3, 11), date(2010, 3, 11),
     "Anexo:Gabinetes ministeriales de los gobiernos de la Concertación",
     "Michelle Bachelet"),
    ("Piñera I", date(2010, 3, 11), date(2014, 3, 11),
     "Anexo:Gabinetes ministeriales del primer gobierno de Sebastián Piñera", None),
    ("Bachelet II", date(2014, 3, 11), date(2018, 3, 11),
     "Anexo:Gabinetes ministeriales del segundo gobierno de Michelle Bachelet", None),
    ("Piñera II", date(2018, 3, 11), date(2022, 3, 11),
     "Anexo:Gabinetes ministeriales del segundo gobierno de Sebastián Piñera", None),
    ("Boric", date(2022, 3, 11), date(2026, 3, 11),
     "Anexo:Gabinetes ministeriales del gobierno de Gabriel Boric", None),
    ("Kast", date(2026, 3, 11), None,
     "Anexo:Gabinetes ministeriales del gobierno de José Antonio Kast", None),
]
ORDEN_GOBIERNOS = [g[0] for g in GOBIERNOS]
PAGINA_SUBTEL = "Subsecretaría de Telecomunicaciones de Chile"

# Reglas para el nombre estable del ministerio. Se aplican en orden sobre el
# texto y el destino del enlace de la celda "Ministerio".
REGLAS_MINISTERIO = [
    (r"interior", "Interior"),
    (r"seguridad p[uú]blica", "Seguridad Pública"),
    (r"relaciones exteriores", "Relaciones Exteriores"),
    (r"defensa", "Defensa Nacional"),
    (r"hacienda", "Hacienda"),
    (r"presidencia", "Secretaría General de la Presidencia"),
    (r"secretar[ií]a general de gobierno", "Secretaría General de Gobierno"),
    (r"econom[ií]a", "Economía"),
    (r"planificaci[oó]n|desarrollo social", "Desarrollo Social"),
    (r"educaci[oó]n", "Educación"),
    (r"justicia", "Justicia"),
    (r"trabajo", "Trabajo y Previsión Social"),
    (r"obras p[uú]blicas", "Obras Públicas"),
    (r"salud", "Salud"),
    (r"vivienda", "Vivienda y Urbanismo"),
    (r"agricultura", "Agricultura"),
    (r"miner[ií]a", "Minería"),
    (r"transporte", "Transportes y Telecomunicaciones"),
    (r"bienes nacionales", "Bienes Nacionales"),
    (r"energ[ií]a", "Energía"),
    (r"medio ambiente", "Medio Ambiente"),
    (r"deporte", "Deporte"),
    (r"mujer", "Mujer"),
    (r"cultura", "Culturas"),
    (r"ciencia", "Ciencia"),
]

# Nombre oficial de cada cartera según la fecha (desde, nombre). Las fechas de
# cambio corresponden a la ley respectiva y coinciden con las que usa
# Wikipedia para dividir los períodos de quienes estaban en el cargo.
NOMBRES_OFICIALES = {
    "Interior": [
        (None, "Ministerio del Interior"),
        (date(2011, 2, 21), "Ministerio del Interior y Seguridad Pública"),  # Ley 20.502
        (date(2025, 4, 1), "Ministerio del Interior"),                       # Ley 21.730
    ],
    "Seguridad Pública": [(date(2025, 4, 1), "Ministerio de Seguridad Pública")],
    "Relaciones Exteriores": [(None, "Ministerio de Relaciones Exteriores")],
    "Defensa Nacional": [(None, "Ministerio de Defensa Nacional")],
    "Hacienda": [(None, "Ministerio de Hacienda")],
    "Secretaría General de la Presidencia": [
        (None, "Ministerio Secretaría General de la Presidencia")],
    "Secretaría General de Gobierno": [
        (None, "Ministerio Secretaría General de Gobierno")],
    "Economía": [
        (None, "Ministerio de Economía, Fomento y Reconstrucción"),
        (date(2010, 2, 12), "Ministerio de Economía, Fomento y Turismo"),     # Ley 20.423
    ],
    "Desarrollo Social": [
        (None, "Ministerio de Planificación"),
        (date(2011, 10, 13), "Ministerio de Desarrollo Social"),             # Ley 20.530
        (date(2019, 4, 2), "Ministerio de Desarrollo Social y Familia"),     # Ley 21.150
    ],
    "Educación": [(None, "Ministerio de Educación")],
    "Justicia": [
        (None, "Ministerio de Justicia"),
        (date(2016, 1, 5), "Ministerio de Justicia y Derechos Humanos"),     # Ley 20.885
    ],
    "Trabajo y Previsión Social": [(None, "Ministerio del Trabajo y Previsión Social")],
    "Obras Públicas": [(None, "Ministerio de Obras Públicas")],
    "Salud": [(None, "Ministerio de Salud")],
    "Vivienda y Urbanismo": [(None, "Ministerio de Vivienda y Urbanismo")],
    "Agricultura": [(None, "Ministerio de Agricultura")],
    "Minería": [(None, "Ministerio de Minería")],
    "Transportes y Telecomunicaciones": [
        (None, "Ministerio de Transportes y Telecomunicaciones")],
    "Bienes Nacionales": [(None, "Ministerio de Bienes Nacionales")],
    "Energía": [
        (None, "Comisión Nacional de Energía"),
        (date(2010, 2, 1), "Ministerio de Energía"),                         # Ley 20.402
    ],
    "Medio Ambiente": [
        (None, "Comisión Nacional del Medio Ambiente"),
        (date(2010, 10, 1), "Ministerio del Medio Ambiente"),                # Ley 20.417
    ],
    "Deporte": [(date(2013, 11, 13), "Ministerio del Deporte")],             # Ley 20.686
    "Mujer": [
        (None, "Servicio Nacional de la Mujer"),
        (date(2016, 6, 3), "Ministerio de la Mujer y la Equidad de Género"), # Ley 20.820
    ],
    "Culturas": [
        (None, "Consejo Nacional de la Cultura y las Artes"),
        (date(2018, 3, 1), "Ministerio de las Culturas, las Artes y el Patrimonio"),  # Ley 21.045
    ],
    "Ciencia": [
        (None, "Ministerio de Ciencia, Tecnología, Conocimiento e Innovación")],  # Ley 21.105
    "SUBTEL": [(None, "Subsecretaría de Telecomunicaciones")],
}
ORDEN_MINISTERIOS = list(NOMBRES_OFICIALES)

# Nombres canónicos de partidos a partir del destino del enlace en Wikipedia.
PARTIDOS_CANONICOS = {
    "Partido Demócrata Cristiano de Chile": "Partido Demócrata Cristiano",
    "Partido Demócrata Cristiano (Chile)": "Partido Demócrata Cristiano",
    "Partido Demócrata Cristiano": "Partido Demócrata Cristiano",
    "Partido Por la Democracia": "Partido por la Democracia",
    "Partido Radical Social Demócrata": "Partido Radical Socialdemócrata",
    "Evópoli": "Evolución Política",
    "Frente Amplio (partido político de Chile)": "Frente Amplio",
    "Comunes (Chile)": "Comunes",
    "Partido Liberal de Chile (2013)": "Partido Liberal de Chile",
    "Partido Republicano de Chile (2019)": "Partido Republicano de Chile",
    "Partido Social Cristiano (Chile)": "Partido Social Cristiano",
    "Demócratas (Chile)": "Demócratas",
}
ENLACES_INDEPENDIENTE = {"político independiente", "politico independiente",
                         "candidato independiente", "independiente"}

# Siglas que aparecen sin enlace en las tablas.
SIGLAS = {
    "IND": "Independiente", "UDI": "Unión Demócrata Independiente",
    "RN": "Renovación Nacional", "EVO": "Evolución Política",
    "EVOP": "Evolución Política", "PRCH": "Partido Republicano de Chile",
    "PREP": "Partido Republicano de Chile", "PS": "Partido Socialista de Chile",
    "PPD": "Partido por la Democracia", "PDC": "Partido Demócrata Cristiano",
    "DC": "Partido Demócrata Cristiano", "PCCH": "Partido Comunista de Chile",
    "PC": "Partido Comunista de Chile", "PR": "Partido Radical de Chile",
    "PRSD": "Partido Radical Socialdemócrata", "RD": "Revolución Democrática",
    "CS": "Convergencia Social", "COM": "Comunes", "FA": "Frente Amplio",
    "PL": "Partido Liberal de Chile", "FRVS": "Federación Regionalista Verde Social",
    "IC": "Izquierda Ciudadana", "MAS": "Movimiento Amplio Social",
    "PSC": "Partido Social Cristiano", "PNL": "Partido Nacional Libertario",
    "DEM": "Demócratas",
}

MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "julo": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}
RE_FECHA = re.compile(
    r"(\d{1,2})\s*(?:º|°)?\s*de\s+([a-záéíóúñ]+)\s+(?:de|del)\s+(\d{4})", re.I)
RE_ABIERTO = re.compile(r"en el cargo|en funciones|actualidad|a la fecha|presente", re.I)
RE_SUBROGANTE = re.compile(r"subrogante|\(s\)", re.I)
RE_INTERINO = re.compile(r"interin[oa]", re.I)

# Variables de perfil de GB&O que se pueden agregar con --variables-gbo.
VARIABLES_GBO = {
    "age": "gbo_edad", "non_party": "gbo_sin_partido",
    "president_party": "gbo_partido_presidente", "economist": "gbo_economista",
    "lawyer": "gbo_abogado", "inner_circle": "gbo_circulo_cercano",
    "party_leader": "gbo_dirigente_partido", "exp_executive": "gbo_exp_ejecutivo",
    "exp_congress": "gbo_exp_congreso", "exp_ngo": "gbo_exp_ong",
    "exp_thinktanks": "gbo_exp_centro_estudios", "exp_business": "gbo_exp_empresa",
    "political_kinship": "gbo_parentesco_politico",
}
MINISTERIOS_GBO = {
    "SEGEGOB": "Secretaría General de Gobierno",
    "SEGPRES": "Secretaría General de la Presidencia",
    "MIDEPLAN": "Desarrollo Social",
    "SERNAM": "Mujer",
    "Consejo Nacional de la Cultura y las Artes": "Culturas",
    "Comisión Nacional de Energía": "Energía",
    "Comisión Nacional de Medio Ambiente": "Medio Ambiente",
    "Deportes": "Deporte",
}
GOBIERNOS_GBO = {("Michelle Bachelet", "2006-03-11"): "Bachelet I",
                 ("Sebastián Piñera", "2010-03-11"): "Piñera I"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Acceso a la API de MediaWiki
# ---------------------------------------------------------------------------
class ClienteWiki:
    """Cliente mínimo de la API, con pausa entre llamadas y caché opcional."""

    def __init__(self, cache=None, pausa=PAUSA_API):
        self.sesion = requests.Session()
        self.sesion.headers["User-Agent"] = USER_AGENT
        self.cache = Path(cache) if cache else None
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)
        self.pausa = pausa
        self.llamadas = 0

    def _archivo_cache(self, clave):
        if not self.cache:
            return None
        return self.cache / (hashlib.md5(clave.encode("utf-8")).hexdigest() + ".json")

    def obtener(self, url, params=None, json_=True):
        clave = url + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        archivo = self._archivo_cache(clave)
        if archivo and archivo.exists():
            contenido = archivo.read_text(encoding="utf-8")
            return json.loads(contenido) if json_ else contenido
        for intento in range(8):
            r = self.sesion.get(url, params=params, timeout=60)
            self.llamadas += 1
            if r.status_code == 200:
                break
            if r.status_code in (429, 500, 502, 503, 504):
                espera = int(r.headers.get("retry-after", 5 * (intento + 1)))
                log.warning("HTTP %s, reintento en %s s", r.status_code, espera)
                time.sleep(espera + 1)
                continue
            r.raise_for_status()
        else:
            raise RuntimeError(f"No se pudo obtener {url} {params}")
        time.sleep(self.pausa)
        if json_:
            datos = r.json()
            if "error" in datos:
                raise RuntimeError(f"Error de la API: {datos['error']}")
            if archivo:
                archivo.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
            return datos
        r.encoding = "utf-8"
        if archivo:
            archivo.write_text(r.text, encoding="utf-8")
        return r.text

    def api(self, **params):
        params.update(format="json", formatversion=2)
        return self.obtener(API_WIKI, params)

    def parsear(self, pagina, seccion=None):
        """Devuelve (html, wikitexto, revid, título) de una página o sección."""
        params = dict(action="parse", page=pagina, redirects=1,
                      prop="text|wikitext|revid", disableeditsection=1,
                      disablelimitreport=1)
        if seccion is not None:
            params["section"] = seccion
        p = self.api(**params)["parse"]
        return p["text"], p["wikitext"], p["revid"], p["title"]

    def seccion(self, pagina, texto):
        """Índice de la primera sección cuyo título contiene `texto`."""
        secciones = self.api(action="parse", page=pagina, redirects=1,
                             prop="sections")["parse"]["sections"]
        for s in secciones:
            if texto.lower() in s["line"].lower():
                return s["index"]
        raise ValueError(f"No se encontró la sección '{texto}' en {pagina}")

    def wikitextos(self, titulos):
        """Wikitexto de varias páginas (de a 50). Devuelve {título pedido: texto}."""
        salida = {}
        titulos = sorted(set(titulos))
        for i in range(0, len(titulos), 50):
            lote = titulos[i:i + 50]
            q = self.api(action="query", prop="revisions", rvprop="content",
                         rvslots="main", redirects=1, titles="|".join(lote))["query"]
            destino = {t: t for t in lote}
            for paso in ("normalized", "redirects"):
                mapa = {x["from"]: x["to"] for x in q.get(paso, [])}
                destino = {t: mapa.get(d, d) for t, d in destino.items()}
            textos = {}
            for p in q["pages"]:
                if p.get("missing") or not p.get("revisions"):
                    continue
                textos[p["title"]] = p["revisions"][0]["slots"]["main"]["content"]
            for t, d in destino.items():
                if d in textos:
                    salida[t] = textos[d]
        return salida


def enlace_permanente(titulo, revid):
    return f"{URL_WIKI}?title={titulo.replace(' ', '_')}&oldid={revid}"


# ---------------------------------------------------------------------------
# Utilidades de texto y fechas
# ---------------------------------------------------------------------------
def limpiar(texto):
    texto = (texto or "").replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", texto).strip()


def sin_tildes(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def celda_limpia(celda):
    """Copia de la celda sin notas al pie ni estilos."""
    c = copy.copy(celda)
    for basura in c.select("sup.reference, sup.mw-references, span.mw-ref, style, "
                           "span.sortkey, span[style*='display:none']"):
        basura.decompose()
    return c


def texto_celda(celda, sep=" "):
    return limpiar(celda_limpia(celda).get_text(sep))


def enlaces_celda(celda):
    """Lista de (texto, destino, existe) de los enlaces internos de la celda."""
    salida = []
    for a in celda_limpia(celda).find_all("a", href=True):
        href = a["href"]
        if not href.startswith("/wiki/") and "redlink=1" not in href:
            continue
        destino = a.get("title", "")
        if destino.startswith(("Archivo:", "File:", "Anexo:Alcaldes")):
            continue
        existe = "new" not in (a.get("class") or [])
        if not existe:
            destino = re.sub(r"\s*\(aún no redactado\)$", "", destino)
        salida.append((limpiar(a.get_text(" ")), destino, existe))
    return salida


def leer_fechas(texto):
    fechas = []
    for d, m, a in RE_FECHA.findall(texto):
        mes = MESES.get(sin_tildes(m.lower()))
        if not mes:
            log.warning("Mes no reconocido en %r", texto)
            continue
        try:
            fechas.append(date(int(a), mes, int(d)))
        except ValueError:
            log.warning("Fecha inválida en %r", texto)
    return fechas


def extraer_periodos(texto):
    """Convierte el texto de la celda Período en una lista de (inicio, fin)."""
    fechas = leer_fechas(texto)
    periodos = [(fechas[i], fechas[i + 1] if i + 1 < len(fechas) else None)
                for i in range(0, len(fechas), 2)]
    if periodos and periodos[-1][1] is None and not RE_ABIERTO.search(texto):
        log.warning("Período sin fecha de término ni 'en el cargo': %r", texto)
    return periodos


def normalizar_ministerio(texto):
    t = texto.lower()
    for patron, nombre in REGLAS_MINISTERIO:
        if re.search(patron, t):
            return nombre
    return None


def nombre_oficial(normalizado, inicio):
    vigente = None
    for desde, nombre in NOMBRES_OFICIALES[normalizado]:
        if desde is None or inicio >= desde:
            vigente = nombre
    return vigente or NOMBRES_OFICIALES[normalizado][0][1]


def fechas_de_cambio(normalizado):
    return [d for d, _ in NOMBRES_OFICIALES[normalizado] if d is not None]


def canonizar_partido(destino):
    destino = limpiar(destino)
    if destino.lower() in ENLACES_INDEPENDIENTE:
        return "Independiente"
    if destino in PARTIDOS_CANONICOS:
        return PARTIDOS_CANONICOS[destino]
    return re.sub(r"\s*\([^)]*\)$", "", destino)


def leer_partido(celda):
    """Texto legible del partido a partir de la celda (enlaces o siglas)."""
    texto = texto_celda(celda)
    enlaces = {t: d for t, d, _ in enlaces_celda(celda) if t}
    partes = [p for p in re.split(r"\s*(?:\s-\s|–|/|,|\bpro\b-?|\(|\))\s*", texto) if p]
    independiente, partidos = False, []
    for parte in partes:
        parte = parte.strip(" .-")
        if not parte:
            continue
        destino = enlaces.get(parte) or enlaces.get(parte + ".")
        if destino:
            nombre = canonizar_partido(destino)
        else:
            nombre = SIGLAS.get(re.sub(r"[^A-Za-z]", "", parte).upper())
            if not nombre:
                log.warning("Partido no reconocido: %r", texto)
                nombre = parte
        if nombre == "Independiente":
            independiente = True
        elif nombre not in partidos:
            partidos.append(nombre)
    if independiente and partidos:
        return "Independiente (pro " + " / ".join(partidos) + ")"
    if independiente:
        return "Independiente"
    return " / ".join(partidos)


# ---------------------------------------------------------------------------
# Lectura de tablas
# ---------------------------------------------------------------------------
def _entero(valor):
    m = re.search(r"\d+", str(valor or ""))
    return int(m.group()) if m else 1


def tabla_a_grilla(tabla):
    """Expande rowspan/colspan: devuelve filas de celdas (objetos bs4)."""
    filas, pendientes = [], {}
    for tr in tabla.find_all("tr"):
        if tr.find_parent("table") is not tabla:
            continue
        celdas = tr.find_all(["td", "th"], recursive=False)
        fila, col, i = [], 0, 0
        while i < len(celdas) or col in pendientes:
            if col in pendientes:
                restantes, celda = pendientes[col]
                fila.append(celda)
                if restantes <= 1:
                    del pendientes[col]
                else:
                    pendientes[col] = (restantes - 1, celda)
                col += 1
                continue
            celda = celdas[i]
            i += 1
            filas_extra = _entero(celda.get("rowspan")) - 1
            for _ in range(_entero(celda.get("colspan"))):
                fila.append(celda)
                if filas_extra > 0:
                    pendientes[col] = (filas_extra, celda)
                col += 1
        filas.append(fila)
    return filas


def encabezados(grilla):
    for i, fila in enumerate(grilla):
        if fila and all(c.name == "th" for c in fila):
            return i, [sin_tildes(texto_celda(c).lower()) for c in fila]
    return None, []


def buscar_tabla(html, requeridos, excluidos=()):
    sopa = BeautifulSoup(html, "html.parser")
    for tabla in sopa.find_all("table", class_="wikitable"):
        grilla = tabla_a_grilla(tabla)
        i, enc = encabezados(grilla)
        if i is None:
            continue
        if all(any(r in e for e in enc) for r in requeridos) and \
                not any(any(x in e for e in enc) for x in excluidos):
            return grilla[i + 1:], enc
    raise ValueError(f"No se encontró una tabla con {requeridos}")


def indice(enc, *claves, ultimo=False):
    idx = [i for i, e in enumerate(enc) if any(c in e for c in claves)]
    if not idx:
        raise ValueError(f"Columna {claves} no encontrada en {enc}")
    return idx[-1] if ultimo else idx[0]


def filas_de_persona(gobierno, celda_min, celda_nombre, celda_partido, celda_periodo,
                     fuente, normalizado=None):
    """Convierte una fila de tabla en uno o más registros (uno por período)."""
    texto_nombre = texto_celda(celda_nombre)
    texto_periodo = texto_celda(celda_periodo, "\n")
    if not RE_FECHA.search(texto_periodo):
        return []
    enlaces = [e for e in enlaces_celda(celda_nombre) if e[1]]
    pagina = enlaces[0][1] if enlaces and enlaces[0][2] else None
    nombre = enlaces[0][0] if enlaces else texto_nombre
    nombre = limpiar(re.sub(r"\((?:subrogante|s|interin[oa])\)", "", nombre, flags=re.I))
    if normalizado is None:
        enl_min = enlaces_celda(celda_min)
        clave = " ".join([texto_celda(celda_min)] + [d for _, d, _ in enl_min[:1]])
        normalizado = normalizar_ministerio(clave)
        if normalizado is None:
            log.warning("Ministerio no reconocido: %r", clave)
            return []
    marca = texto_nombre + " " + texto_periodo
    registros = []
    for inicio, fin in extraer_periodos(texto_periodo):
        registros.append({
            "gobierno": gobierno, "ministerio_normalizado": normalizado,
            "nombre": nombre, "pagina": pagina, "fecha_inicio": inicio, "fecha_fin": fin,
            "partido": leer_partido(celda_partido),
            "subrogante": bool(RE_SUBROGANTE.search(marca)),
            "interino": bool(RE_INTERINO.search(marca)),
            "fuente": fuente,
        })
    return registros


def leer_gabinete(cliente, gobierno, pagina, seccion):
    indice_sec = cliente.seccion(pagina, seccion) if seccion else None
    html, wikitexto, revid, titulo = cliente.parsear(pagina, indice_sec)
    fuente = enlace_permanente(titulo, revid)
    filas, enc = buscar_tabla(html, ["ministerio", "nombre", "partido", "period"],
                              excluidos=["subsecretar"])
    columnas = (indice(enc, "ministerio"), indice(enc, "nombre"),
                indice(enc, "partido"), indice(enc, "period"))
    registros = []
    for fila in filas:
        i_min, i_nom, i_par, i_per = columnas
        if len(fila) != len(enc):
            log.warning("%s: fila con %d celdas (se esperaban %d): %s", gobierno,
                        len(fila), len(enc), [texto_celda(c)[:30] for c in fila])
            if len(fila) < 4:
                continue
            # Fila irregular: ministerio al inicio y nombre, partido y período al final.
            i_min, i_nom, i_par, i_per = 0, len(fila) - 3, len(fila) - 2, len(fila) - 1
        registros += filas_de_persona(gobierno, fila[i_min], fila[i_nom], fila[i_par],
                                      fila[i_per], fuente)
    log.info("%s: %d registros desde %s", gobierno, len(registros), titulo)
    return registros, wikitexto, html


def contar_cambios_wiki(wikitexto):
    """Número de cambios de gabinete que el anexo enumera (subsecciones de 'Cambios')."""
    m = re.search(r"^===\s*Cambios[^=]*===\s*$(.*?)(?=^={2,3}[^=])", wikitexto, re.M | re.S)
    if not m:
        return None
    titulos = re.findall(r"^====\s*([^=]+?)\s*====\s*$", m.group(1), re.M)
    cambios = [t for t in titulos if not t.lower().startswith("renuncia")]
    return len(cambios) or None


def leer_subtel(cliente):
    html, wikitexto, revid, titulo = cliente.parsear(PAGINA_SUBTEL)
    fuente = enlace_permanente(titulo, revid)
    filas, enc = buscar_tabla(html, ["subsecretari", "partido", "period"])
    i_nom = indice(enc, "subsecretari")
    i_par = indice(enc, "partido", ultimo=True)
    i_per = indice(enc, "period")
    registros = []
    for fila in filas:
        if len(fila) <= max(i_nom, i_par, i_per):
            continue
        for r in filas_de_persona(None, None, fila[i_nom], fila[i_par], fila[i_per],
                                  fuente, normalizado="SUBTEL"):
            if r["fecha_fin"] is None or r["fecha_fin"] > INICIO_SERIE:
                registros.append(r)
    log.info("SUBTEL: %d registros desde %s", len(registros), titulo)
    return registros, wikitexto


def subtel_en_anexos(htmls):
    """Filas de SUBTEL en las tablas de subsecretarios de los anexos (para contraste)."""
    salida = []
    for gobierno, html in htmls.items():
        try:
            filas, enc = buscar_tabla(html, ["subsecretar", "nombre", "period"])
        except ValueError:
            continue
        i_sub, i_nom = indice(enc, "subsecretar"), indice(enc, "nombre")
        i_par, i_per = indice(enc, "partido"), indice(enc, "period")
        for fila in filas:
            if len(fila) != len(enc) or "telecomunicaciones" not in texto_celda(fila[i_sub]).lower():
                continue
            salida += filas_de_persona(gobierno, None, fila[i_nom], fila[i_par],
                                       fila[i_per], "", normalizado="SUBTEL")
    return salida


# ---------------------------------------------------------------------------
# Construcción de la base
# ---------------------------------------------------------------------------
def gobierno_de(fecha):
    for nombre, inicio, fin, _, _ in GOBIERNOS:
        if fecha >= inicio and (fin is None or fecha < fin):
            return nombre
    return None


def dividir_por_cambio_de_nombre(registros):
    """Divide períodos que atraviesan un cambio legal de nombre del ministerio."""
    salida = []
    for r in registros:
        tramos = [r]
        for corte in fechas_de_cambio(r["ministerio_normalizado"]):
            nuevos = []
            for t in tramos:
                fin = t["fecha_fin"] or date.today()
                if t["fecha_inicio"] < corte < fin:
                    log.info("Se divide %s (%s) en %s por cambio de nombre",
                             t["nombre"], t["ministerio_normalizado"], corte)
                    nuevos += [dict(t, fecha_fin=corte), dict(t, fecha_inicio=corte)]
                else:
                    nuevos.append(t)
            tramos = nuevos
        salida += tramos
    return salida


def asignar_tipo(r):
    dias = ((r["fecha_fin"] or date.today()) - r["fecha_inicio"]).days
    if r["interino"]:
        return "interino"
    if r["subrogante"]:
        return "subrogante" if dias <= UMBRAL_SUBROGANCIA else "interino"
    return "titular"


def clave_persona(r):
    if r.get("pagina"):
        return r["pagina"]
    return "nombre:" + sin_tildes(r["nombre"].lower())


def unificar_nombres(registros):
    """Usa una sola grafía del nombre por persona (la más frecuente; si empatan, la
    primera en orden cronológico). Devuelve las variantes reemplazadas."""
    grafias = defaultdict(list)
    for r in sorted(registros, key=lambda x: x["fecha_inicio"]):
        grafias[r["persona"]].append(r["nombre"])
    elegido, variantes = {}, []
    for persona, nombres in grafias.items():
        conteo = Counter(nombres)
        maximo = max(conteo.values())
        elegido[persona] = next(n for n in nombres if conteo[n] == maximo)
        otras = sorted(set(nombres) - {elegido[persona]})
        if otras:
            variantes.append((elegido[persona], otras))
    for r in registros:
        r["nombre"] = elegido[r["persona"]]
    return variantes


# ---------------------------------------------------------------------------
# Género según el título del cargo
# ---------------------------------------------------------------------------
RE_CARGO_FICHA = re.compile(r"^\s*\|\s*cargo\s*\d*\s*=(.*)$", re.M | re.I)
RE_MINISTR = re.compile(r"\b(?:bi|tri)?ministr([ao])s?\b", re.I)
RE_SUBSEC = re.compile(r"\bsubsecretari([ao])s?\b", re.I)


def wikitexto_a_plano(texto):
    texto = re.sub(r"<ref[^>/]*/>", "", texto)
    texto = re.sub(r"<ref[^>]*>.*?</ref>", "", texto, flags=re.S)
    texto = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]", r"\1", texto)
    return texto.replace("'''", "").replace("''", "")


def titulo_en_bio(wikitexto, subtel):
    """Busca 'ministra/ministro' (o 'subsecretaria/o') en la ficha o la entradilla.

    Orden: título principal del cargo (ministro, o subsecretario para SUBTEL) en
    la ficha y luego en la entradilla; si no aparece, el otro título (p. ej. un
    subrogante cuya ficha solo registra que fue subsecretario).
    Devuelve (letra 'a'/'o', lugar, hubo_ambos).
    """
    cargos = [wikitexto_a_plano(c) for c in RE_CARGO_FICHA.findall(wikitexto)]
    if subtel:
        cargos = [c for c in cargos if "telecomunicaciones" in c.lower()] or cargos
    cuerpo = wikitexto_a_plano(wikitexto)
    m = re.search(r"^'*[^|{}\n]*\(.*?\)\s*(?:es|fue)\b.*$", cuerpo, re.M)
    entradilla = (cuerpo[m.start():m.start() + 2500] if m else cuerpo[:4000]).split("\n==")[0]

    for patron in ((RE_SUBSEC, RE_MINISTR) if subtel else (RE_MINISTR, RE_SUBSEC)):
        votos = Counter(x.group(1).lower() for c in cargos for x in patron.finditer(c))
        if votos:
            return votos.most_common(1)[0][0], "ficha", len(votos) > 1
        previo = r"\b(?:fue|es|como|siendo)\s+(?:la |el )?(?:primer[ao]\s+)?"
        votos = Counter(x.group(1).lower() for x in
                        re.finditer(previo + patron.pattern[2:], entradilla, re.I))
        if votos:
            return votos.most_common(1)[0][0], "entradilla", len(votos) > 1
    return None, None, False


def titulo_en_anexos(nombre, textos_anexos, subtel):
    """Busca el título junto al nombre en el texto de los anexos y del artículo SUBTEL."""
    partes = nombre.split()
    if len(partes) < 2:
        return None
    corto = re.escape(" ".join(partes[:2]))
    patron = RE_SUBSEC if subtel else RE_MINISTR
    votos = Counter()
    for texto in textos_anexos:
        for m in re.finditer(patron.pattern + r"[^.\n|]{0,80}?" + corto, texto, re.I):
            votos[m.group(1).lower()] += 1
    return votos.most_common(1)[0][0] if votos else None


# Casos en que el título del cargo solo aparece fuera del texto que se analiza.
# Se codifican igual por el título que usan las fuentes, nunca por el nombre.
GENERO_MANUAL = {
    # Anexo SUBTEL: solo en el pie de foto ("Subsecretario Telecomunicaciones
    # Rodrigo Ramírez Pino"); los comunicados de SUBTEL dicen "subsecretario".
    ("Rodrigo Ramírez Pino", "SUBTEL"): "Masculino",
}


def codificar_genero(cliente, registros, textos_anexos):
    paginas = sorted({r["pagina"] for r in registros if r["pagina"]})
    bios = cliente.wikitextos(paginas)
    metodo, conflictos, resultado = Counter(), [], {}
    for r in registros:
        subtel = r["ministerio_normalizado"] == "SUBTEL"
        clave = (clave_persona(r), subtel)
        if clave in resultado:
            continue
        letra, via, ambiguo = None, None, False
        if r["pagina"] in bios:
            letra, via, ambiguo = titulo_en_bio(bios[r["pagina"]], subtel)
        if letra is None:
            letra = titulo_en_anexos(r["nombre"], textos_anexos, subtel)
            via = "anexos" if letra else "sin dato"
        genero = {"a": "Femenino", "o": "Masculino"}.get(letra, "")
        manual = GENERO_MANUAL.get((r["nombre"], r["ministerio_normalizado"]))
        if not genero and manual:
            genero, via = manual, "manual"
        if ambiguo:
            conflictos.append(r["nombre"])
        metodo[via] += 1
        resultado[clave] = genero
    for r in registros:
        r["genero"] = resultado[(clave_persona(r), r["ministerio_normalizado"] == "SUBTEL")]
    return metodo, conflictos


# ---------------------------------------------------------------------------
# Controles de calidad
# ---------------------------------------------------------------------------
def fmt(d):
    return d.isoformat() if d else ""


def control_traslapes_y_lagunas(df):
    traslapes, lagunas = [], []
    tit = df[df.tipo == "titular"].sort_values(["ministerio_normalizado", "fecha_inicio"])
    otros = df[df.tipo != "titular"]
    for ministerio, g in tit.groupby("ministerio_normalizado", sort=False):
        filas = g.to_dict("records")
        for a, b in zip(filas, filas[1:]):
            fin_a = a["fecha_fin"] or date.today()
            if b["fecha_inicio"] < fin_a:
                traslapes.append((ministerio, a, b, (fin_a - b["fecha_inicio"]).days))
            elif a["fecha_fin"] and (b["fecha_inicio"] - a["fecha_fin"]).days > UMBRAL_LAGUNA:
                cubre = otros[(otros.ministerio_normalizado == ministerio)
                              & (otros.fecha_inicio <= a["fecha_fin"] + timedelta(days=1))
                              & (otros.fecha_fin.fillna(date.today()) >= b["fecha_inicio"]
                                 - timedelta(days=1))]
                lagunas.append((ministerio, a, b, (b["fecha_inicio"] - a["fecha_fin"]).days,
                                ", ".join(f"{x.nombre} ({x.tipo})" for x in cubre.itertuples())))
    return traslapes, lagunas


def resumen_por_gobierno(df):
    mins = df[df.ministerio_normalizado != "SUBTEL"]
    filas = []
    for gob in ORDEN_GOBIERNOS:
        g = mins[mins.gobierno == gob]
        inicio_gob = next(x[1] for x in GOBIERNOS if x[0] == gob)
        tit = g[g.tipo == "titular"].sort_values("fecha_inicio")
        # Entradas de titulares posteriores al inicio del gobierno que reemplazan a
        # otra persona (no cuentan renombramientos, retornos ni carteras nuevas).
        entradas = []
        for ministerio, h in mins[mins.tipo == "titular"].sort_values("fecha_inicio") \
                .groupby("ministerio_normalizado"):
            previo = None
            for x in h.itertuples():
                if x.gobierno == gob and x.fecha_inicio > inicio_gob and previo is not None \
                        and previo.persona != x.persona:
                    entradas.append((x.fecha_inicio, ministerio))
                previo = x
        fechas = sorted({f for f, _ in entradas})
        eventos = []
        for f in fechas:
            if eventos and (f - eventos[-1][-1]).days <= 7:
                eventos[-1].append(f)
            else:
                eventos.append([f])
        carteras_evento = [len({m for f, m in entradas if ev[0] <= f <= ev[-1]})
                           for ev in eventos]
        filas.append({
            "gobierno": gob,
            "filas": int((df.gobierno == gob).sum()),
            "filas_subtel": int(((df.gobierno == gob) & (df.ministerio_normalizado == "SUBTEL")).sum()),
            "ministros_titulares": tit.persona.nunique(),
            "ministros_total": g.persona.nunique(),
            "ministras_titulares": tit[tit.genero == "Femenino"].persona.nunique(),
            "relevos": len(entradas),
            "fechas_con_relevo": len(fechas),
            "cambios_gabinete": sum(1 for n in carteras_evento if n >= 2),
            "relevos_individuales": sum(1 for n in carteras_evento if n == 1),
            "detalle_cambios": [(fmt(ev[0]), n) for ev, n in zip(eventos, carteras_evento)],
        })
    return filas


def tokens_nombre(nombre):
    if "," in nombre:
        apellidos, nombres = nombre.split(",", 1)
        nombre = nombres + " " + apellidos
    nombre = sin_tildes(nombre.lower()).replace("-", " ")
    nombre = re.sub(r"[^a-z ]", " ", nombre)
    return {t for t in nombre.split() if t not in {"de", "la", "del", "las", "los", "y"}}


def coincide_nombre(a, b):
    ta, tb = tokens_nombre(a), tokens_nombre(b)
    comunes = len(ta & tb)
    jaccard = comunes / max(len(ta | tb), 1)
    razon = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return (comunes >= 2 and jaccard >= 0.5) or razon >= 0.9, max(jaccard, razon)


def unir_tramos(filas, clave):
    """Une filas contiguas de una misma persona en una misma cartera."""
    filas = sorted(filas, key=lambda x: (clave(x), x["ministerio_normalizado"], x["fecha_inicio"]))
    salida = []
    for f in filas:
        u = salida[-1] if salida else None
        if u and clave(u) == clave(f) and u["ministerio_normalizado"] == f["ministerio_normalizado"] \
                and u["gobierno"] == f["gobierno"] and u["fecha_fin"] \
                and 0 <= (f["fecha_inicio"] - u["fecha_fin"]).days <= 1:
            u["fecha_fin"] = f["fecha_fin"]
            u["indices"].append(f["indice"])
        else:
            salida.append(dict(f, indices=[f["indice"]]))
    return salida


def comparar_gbo(df, gbo):
    gbo = gbo.copy()
    gbo["gobierno"] = [GOBIERNOS_GBO.get((p, s)) for p, s in
                       zip(gbo.president, gbo.start_president.astype(str))]
    gbo = gbo[gbo.gobierno.notna()].copy()
    gbo["ministerio_normalizado"] = [MINISTERIOS_GBO.get(m) or normalizar_ministerio(m)
                                     for m in gbo.ministry]
    gbo["fecha_inicio"] = pd.to_datetime(gbo.start_minister).dt.date
    gbo["fecha_fin"] = pd.to_datetime(gbo.end_minister).dt.date
    gbo["indice"] = gbo.index
    tramos_gbo = unir_tramos(gbo.to_dict("records"), lambda x: x["name"])

    propios = df[df.gobierno.isin(["Bachelet I", "Piñera I"])
                 & (df.ministerio_normalizado != "SUBTEL")].copy()
    propios["indice"] = propios.index
    tramos_propios = unir_tramos(propios.to_dict("records"), lambda x: x["persona"])

    candidatos = []
    for i, g in enumerate(tramos_gbo):
        for j, p in enumerate(tramos_propios):
            if p["gobierno"] != g["gobierno"] or \
                    p["ministerio_normalizado"] != g["ministerio_normalizado"]:
                continue
            ok, puntaje = coincide_nombre(g["name"], p["nombre"])
            if ok:
                cercania = abs((p["fecha_inicio"] - g["fecha_inicio"]).days)
                candidatos.append((-puntaje, cercania, i, j))
    usados_g, usados_p, pares = set(), set(), []
    for _, _, i, j in sorted(candidatos):
        if i not in usados_g and j not in usados_p:
            usados_g.add(i)
            usados_p.add(j)
            pares.append((tramos_gbo[i], tramos_propios[j]))

    def dif(a, b):
        if a is None or b is None:
            return None if a is b else 9999
        return (a - b).days

    detalle_fechas = []
    ok_inicio = ok_fin = ok_ambas = 0
    for g, p in pares:
        di, df_ = dif(p["fecha_inicio"], g["fecha_inicio"]), dif(p["fecha_fin"], g["fecha_fin"])
        bi = abs(di) <= TOLERANCIA_GBO
        bf = df_ is not None and abs(df_) <= TOLERANCIA_GBO
        ok_inicio += bi
        ok_fin += bf
        ok_ambas += bi and bf
        if not (bi and bf):
            detalle_fechas.append((g, p, di, df_))
    sin_par_gbo = [g for i, g in enumerate(tramos_gbo) if i not in usados_g]
    sin_par_propios = [p for j, p in enumerate(tramos_propios) if j not in usados_p]

    # Contraste de género y de independencia para las personas emparejadas.
    genero_dif, indep_dif = [], []
    for g, p in pares:
        sexo = {"woman": "Femenino", "man": "Masculino"}.get(g["sex"], "")
        if p["genero"] and sexo and p["genero"] != sexo:
            genero_dif.append((p["nombre"], p["genero"], g["sex"]))
        indep_propio = p["partido"].startswith("Independiente")
        if bool(g["non_party"]) != indep_propio:
            indep_dif.append((p["gobierno"], p["ministerio_normalizado"], p["nombre"],
                              p["partido"], "sin partido" if g["non_party"] else "con partido"))
    return {
        "n_gbo": len(tramos_gbo), "n_propios": len(tramos_propios), "pares": pares,
        "ok_inicio": ok_inicio, "ok_fin": ok_fin, "ok_ambas": ok_ambas,
        "detalle_fechas": detalle_fechas, "sin_par_gbo": sin_par_gbo,
        "sin_par_propios": sin_par_propios, "genero_dif": genero_dif,
        "indep_dif": indep_dif,
    }


def comparar_subtel(df, anexos):
    """Diferencias entre el artículo SUBTEL y las tablas de subsecretarios de los anexos."""
    propios = df[df.ministerio_normalizado == "SUBTEL"]
    difs = []
    for a in anexos:
        cand = [p for p in propios.itertuples() if coincide_nombre(p.nombre, a["nombre"])[0]]
        if not cand:
            difs.append(f"{a['gobierno']}: {a['nombre']} aparece en el anexo y no en el artículo SUBTEL")
            continue
        p = min(cand, key=lambda x: abs((x.fecha_inicio - a["fecha_inicio"]).days))
        if abs((p.fecha_inicio - a["fecha_inicio"]).days) > TOLERANCIA_GBO or \
                (p.fecha_fin != a["fecha_fin"] and (p.fecha_fin is None or a["fecha_fin"] is None
                 or abs((p.fecha_fin - a["fecha_fin"]).days) > TOLERANCIA_GBO)):
            difs.append(f"{p.nombre}: artículo {fmt(p.fecha_inicio)}/{fmt(p.fecha_fin)} "
                        f"vs anexo {fmt(a['fecha_inicio'])}/{fmt(a['fecha_fin'])}")
        if p.partido != a["partido"]:
            difs.append(f"{p.nombre}: partido '{p.partido}' (artículo) vs '{a['partido']}' (anexo)")
    return difs


def imprimir_reporte(df, traslapes, lagunas, resumen, gbo, difs_subtel, metodo_genero,
                     conflictos_genero, divisiones, variantes, cambios_wiki):
    linea = "=" * 78
    print("\n" + linea + "\nCONTROLES DE CALIDAD\n" + linea)
    print(f"Filas totales: {len(df)}  |  tipo: {dict(Counter(df.tipo))}")
    for x in df[df.tipo != "titular"].itertuples():
        dias = ((x.fecha_fin or date.today()) - x.fecha_inicio).days
        print(f"  {x.tipo:<10} {x.gobierno:<11} {x.ministerio_normalizado:<32} {x.nombre} "
              f"({fmt(x.fecha_inicio)} a {fmt(x.fecha_fin)}, {dias} días)")
    if variantes:
        print("Grafías unificadas por persona:")
        for elegido, otras in variantes:
            print(f"  {elegido}  <-  {', '.join(otras)}")
    print(f"Género por fuente del título: {dict(metodo_genero)}")
    if conflictos_genero:
        print("  Biografías con ambos títulos (se usó el más frecuente):",
              ", ".join(conflictos_genero))
    sin_genero = df[df.genero == ""]
    if len(sin_genero):
        print("  Sin título en la fuente (género vacío):",
              ", ".join(sorted(set(sin_genero.nombre))))
    if divisiones:
        print(f"Filas divididas por cambio de nombre del ministerio (además de las de la fuente): {divisiones}")

    print("\n-- Filas y personas por gobierno --")
    print(f"{'gobierno':<12}{'filas':>7}{'subtel':>8}{'min.tit':>9}{'min.tot':>9}"
          f"{'mujeres':>9}{'relevos':>9}{'cambios':>9}{'individ.':>10}")
    for r in resumen:
        print(f"{r['gobierno']:<12}{r['filas']:>7}{r['filas_subtel']:>8}"
              f"{r['ministros_titulares']:>9}{r['ministros_total']:>9}"
              f"{r['ministras_titulares']:>9}{r['relevos']:>9}{r['cambios_gabinete']:>9}"
              f"{r['relevos_individuales']:>10}")
    print("  min.tit = personas distintas como titulares; min.tot incluye interinos y "
          "subrogantes;\n  cambios = eventos (fechas a <=7 días) con relevo en 2 o más "
          "carteras; individ. = eventos con una sola cartera.")
    for r in resumen:
        print(f"  {r['gobierno']}: " + "; ".join(f"{f} ({n})" for f, n in r["detalle_cambios"]))
    print("  Cambios de gabinete enumerados en el propio anexo de Wikipedia: " +
          ", ".join(f"{g} {'n/d' if n is None else n}" for g, n in cambios_wiki.items()))

    print("\n-- Traslapes entre titulares de un mismo ministerio --")
    if not traslapes:
        print("  Ninguno.")
    for m, a, b, dias in traslapes:
        print(f"  {m}: {a['nombre']} ({fmt(a['fecha_inicio'])} a {fmt(a['fecha_fin'])}) y "
              f"{b['nombre']} ({fmt(b['fecha_inicio'])} a {fmt(b['fecha_fin'])}): {dias} días")

    print(f"\n-- Lagunas de más de {UMBRAL_LAGUNA} días entre titulares --")
    if not lagunas:
        print("  Ninguna.")
    for m, a, b, dias, cubre in lagunas:
        print(f"  {m}: {a['nombre']} termina {fmt(a['fecha_fin'])}, {b['nombre']} inicia "
              f"{fmt(b['fecha_inicio'])}: {dias} días" + (f"  [cubierta por {cubre}]" if cubre else ""))

    print("\n-- Contraste con González-Bustamante y Olivares (2006-2014) --")
    n_par = len(gbo["pares"])
    print(f"  Tramos GB&O: {gbo['n_gbo']}  |  tramos propios: {gbo['n_propios']}  |  "
          f"emparejados: {n_par}")
    print(f"  Coincidencia de nombres: {100 * n_par / gbo['n_gbo']:.1f}% de GB&O, "
          f"{100 * n_par / gbo['n_propios']:.1f}% de la base propia")
    if n_par:
        print(f"  Fechas (±{TOLERANCIA_GBO} días) entre emparejados: inicio "
              f"{100 * gbo['ok_inicio'] / n_par:.1f}%, término {100 * gbo['ok_fin'] / n_par:.1f}%, "
              f"ambas {100 * gbo['ok_ambas'] / n_par:.1f}%")
    for g in gbo["sin_par_gbo"]:
        print(f"  Solo en GB&O: {g['gobierno']} | {g['ministry']} | {g['name']} | "
              f"{fmt(g['fecha_inicio'])} a {fmt(g['fecha_fin'])}")
    for p in gbo["sin_par_propios"]:
        print(f"  Solo en la base propia: {p['gobierno']} | {p['ministerio_normalizado']} | "
              f"{p['nombre']} ({p['tipo']}) | {fmt(p['fecha_inicio'])} a {fmt(p['fecha_fin'])}")
    for g, p, di, dfin in gbo["detalle_fechas"]:
        print(f"  Fechas distintas: {p['ministerio_normalizado']} | {p['nombre']} | propia "
              f"{fmt(p['fecha_inicio'])} a {fmt(p['fecha_fin'])} | GB&O {fmt(g['fecha_inicio'])} a "
              f"{fmt(g['fecha_fin'])} | dif. inicio {di}, término {dfin}")
    for nombre, propio, sexo in gbo["genero_dif"]:
        print(f"  Género distinto: {nombre}: {propio} (título) vs '{sexo}' (GB&O)")
    for gob, m, nombre, partido, gb in gbo["indep_dif"]:
        print(f"  Independencia distinta: {gob} | {m} | {nombre}: '{partido}' vs GB&O {gb}")

    print("\n-- SUBTEL: artículo vs tablas de subsecretarios de los anexos --")
    print("  Sin diferencias." if not difs_subtel else "\n".join("  " + d for d in difs_subtel))
    print(linea)


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--salida", default=str(SALIDA), help="Ruta del CSV de salida")
    ap.add_argument("--cache", default=None, help="Carpeta para guardar las respuestas de la API")
    ap.add_argument("--pausa", type=float, default=PAUSA_API, help="Segundos entre llamadas")
    ap.add_argument("--variables-gbo", action="store_true",
                    help="Agrega al final las variables de perfil de GB&O (solo 2006-2014)")
    args = ap.parse_args()

    cliente = ClienteWiki(cache=args.cache, pausa=args.pausa)

    # 1. Gabinetes por gobierno
    registros, textos_anexos, htmls, cambios_wiki = [], [], {}, {}
    for gobierno, _, _, pagina, seccion in GOBIERNOS:
        regs, wikitexto, html = leer_gabinete(cliente, gobierno, pagina, seccion)
        registros += regs
        textos_anexos.append(wikitexto_a_plano(wikitexto))
        htmls[gobierno] = html
        cambios_wiki[gobierno] = contar_cambios_wiki(wikitexto)

    # 2. Subsecretarios de Telecomunicaciones
    regs_subtel, wikitexto_subtel = leer_subtel(cliente)
    for r in regs_subtel:
        r["gobierno"] = gobierno_de(r["fecha_inicio"])
    registros += regs_subtel
    textos_anexos.append(wikitexto_a_plano(wikitexto_subtel))

    # 3. Cambios de nombre, tipo y nombre oficial
    n_antes = len(registros)
    registros = dividir_por_cambio_de_nombre(registros)
    divisiones = len(registros) - n_antes
    for r in registros:
        r["tipo"] = asignar_tipo(r)
        r["ministerio"] = nombre_oficial(r["ministerio_normalizado"], r["fecha_inicio"])
        r["persona"] = clave_persona(r)
    variantes = unificar_nombres(registros)

    # 4. Género según el título del cargo
    metodo_genero, conflictos = codificar_genero(cliente, registros, textos_anexos)

    df = pd.DataFrame(registros)
    df["orden_gob"] = df.gobierno.map(ORDEN_GOBIERNOS.index)
    df["orden_min"] = df.ministerio_normalizado.map(ORDEN_MINISTERIOS.index)
    df = df.sort_values(["orden_gob", "orden_min", "fecha_inicio", "fecha_fin"]) \
           .reset_index(drop=True)

    # 5. Controles de calidad
    traslapes, lagunas = control_traslapes_y_lagunas(df)
    resumen = resumen_por_gobierno(df)
    texto_gbo = cliente.obtener(URL_GBO, json_=False)
    gbo = pd.read_csv(io.StringIO(texto_gbo))
    comparacion = comparar_gbo(df, gbo)
    difs_subtel = comparar_subtel(df, subtel_en_anexos(htmls))
    imprimir_reporte(df, traslapes, lagunas, resumen, comparacion, difs_subtel,
                     metodo_genero, conflictos, divisiones, variantes, cambios_wiki)

    # 6. Variables de perfil de GB&O (opcional)
    columnas = list(COLUMNAS)
    if args.variables_gbo:
        for col in VARIABLES_GBO.values():
            df[col] = ""
        for g, p in comparacion["pares"]:
            for idx in p["indices"]:
                for origen, destino in VARIABLES_GBO.items():
                    valor = g[origen]
                    if pd.isna(valor):
                        valor = ""
                    elif origen == "age":
                        valor = str(int(valor))
                    else:
                        valor = "Sí" if int(valor) == 1 else "No"
                    df.at[idx, destino] = valor
        columnas += list(VARIABLES_GBO.values())

    # 7. Escritura
    salida = df.copy()
    salida["fecha_inicio"] = salida.fecha_inicio.map(fmt)
    salida["fecha_fin"] = salida.fecha_fin.map(fmt)
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    salida[columnas].to_csv(args.salida, index=False, encoding="utf-8",
                            quoting=csv.QUOTE_MINIMAL)
    log.info("Escritas %d filas en %s (%d llamadas a la red)", len(salida), args.salida,
             cliente.llamadas)


if __name__ == "__main__":
    main()
