#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Piezas compartidas por los scrapers de listados paginados con nota individual.

- bajar_soup(): descarga una página y la entrega como BeautifulSoup; detecta los
  desafíos anti-bots (Cloudflare) y detiene el sitio en lugar de intentar saltarlos.
- recorrer_listado(): pagina un listado, junta las notas pendientes y corta por
  novedad (3 páginas seguidas sin URLs nuevas) salvo que se pida --completo.
- resumen_de(), fila(), cuerpo_de(): armado de filas en el formato del proyecto.
- ejecutar(): descarga las notas pendientes en paralelo y cierra el CSV.
"""
import re
import threading
import time
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

import comun
from comun import log

PALABRAS_MIN = 5  # una nota con menos palabras se descarta (página vacía o de prueba)


class Desafio(Exception):
    """El sitio respondió con un desafío anti-bots: se detiene sin intentar saltarlo."""


_RE_DESAFIO = re.compile(r"<title>\s*(Just a moment|Attention Required|Verificando)", re.I)
_RE_COMENTARIOS = re.compile(r"<!--.*?-->", re.S)


def bajar_soup(s, url: str, pausa: float, sin_comentarios: bool = False):
    """Página como BeautifulSoup, o None si no se pudo bajar. Lanza Desafio si hay bloqueo."""
    r = comun.obtener(s, url, pausa)
    if r is None:
        return None
    if _RE_DESAFIO.search(r.text[:4000]):
        raise Desafio(url)
    if sin_comentarios:
        return BeautifulSoup(_RE_COMENTARIOS.sub("", r.text), "html.parser")
    return BeautifulSoup(r.content, "html.parser")


def url_limpia(base: str, href: str) -> str:
    """URL absoluta sin fragmento."""
    return urldefrag(urljoin(base, href.strip()))[0]


def mismo_sitio(url: str, base: str) -> bool:
    return urlparse(url).netloc.lower().removeprefix("www.") == urlparse(base).netloc.lower().removeprefix("www.")


def resumen_de(texto: str, n: int = 300) -> str:
    """Primeros ~n caracteres del texto, cortados en límite de palabra."""
    t = re.sub(r"\s+", " ", texto or "").strip()
    if len(t) <= n:
        return t
    return t[:n].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def cuerpo_de(soup, selector: str, quitar: tuple = ()) -> str:
    """Texto del primer elemento que calza con `selector`, sin los bloques `quitar`."""
    el = soup.select_one(selector)
    if el is None:
        return ""
    for q in quitar:
        for x in el.select(q):
            x.decompose()
    return comun.texto_de(el)


def fila(fecha: str, institucion: str, titulo: str, resumen: str, url: str, contenido: str) -> dict:
    return {"fecha": fecha, "institucion": institucion, "titulo": comun.limpiar(titulo),
            "resumen": resumen, "url": url, "contenido": contenido}


def valido(contenido: str) -> bool:
    return len(contenido.split()) >= PALABRAS_MIN


def recorrer_listado(s, salida, url_pagina, parsear, pausa: float, completo: bool = False,
                     muestra: int = 0, max_paginas: int = 3000, etiqueta: str = "listado",
                     sin_comentarios: bool = False, vacias_para_cortar: int = 3,
                     seen: set | None = None, estado: dict | None = None) -> list[dict]:
    """Recorre las páginas de un listado y devuelve las notas que aún no están en el CSV.

    url_pagina(n) -> URL de la página n (n desde 1). parsear(soup) -> lista de dicts con
    al menos 'url'. El recorrido termina cuando una página no existe, viene vacía,
    repite URLs ya vistas en esta corrida o, en modo incremental, tras
    `vacias_para_cortar` páginas seguidas sin notas nuevas respecto del CSV (en ese
    caso `estado['corte_por_novedad']` queda en True).
    """
    seen = set() if seen is None else seen
    pendientes: list[dict] = []
    sin_nuevas = 0
    for n in range(1, max_paginas + 1):
        url = url_pagina(n)
        soup = bajar_soup(s, url, pausa, sin_comentarios)
        if soup is None:  # un reintento antes de dar la página por inexistente
            time.sleep(5)
            soup = bajar_soup(s, url, pausa, sin_comentarios)
        if soup is None:
            log.info("%s: fin en la página %d (sin respuesta)", etiqueta, n)
            break
        filas = parsear(soup)
        if not filas and n > 1:  # una página vacía a mitad de camino puede ser un error pasajero
            time.sleep(15)
            soup = bajar_soup(s, url, pausa, sin_comentarios)
            filas = parsear(soup) if soup is not None else []
        if not filas:
            log.info("%s: fin en la página %d (vacía)", etiqueta, n)
            break
        nuevas_vistas = [f for f in filas if f["url"] not in seen]
        if not nuevas_vistas:
            log.info("%s: fin en la página %d (repite la anterior)", etiqueta, n)
            break
        seen.update(f["url"] for f in nuevas_vistas)
        pend = [f for f in nuevas_vistas if not salida.ya_esta(f["url"])]
        pendientes += pend
        if n % 20 == 0:
            log.info("%s: página %d, %d notas pendientes", etiqueta, n, len(pendientes))
        if muestra and len(pendientes) >= muestra:
            break
        if not completo:
            sin_nuevas = 0 if pend else sin_nuevas + 1
            if sin_nuevas >= vacias_para_cortar:
                if estado is not None:
                    estado["corte_por_novedad"] = True
                log.info("%s: %d páginas seguidas sin notas nuevas, se corta en la %d "
                         "(use --completo para recorrer todo)", etiqueta, vacias_para_cortar, n)
                break
    return pendientes[:muestra] if muestra else pendientes


def ejecutar(items: list[dict], procesar, salida, hilos: int, etiqueta: str = "notas") -> int:
    """Procesa las notas en paralelo y cierra el CSV. Devuelve el total de filas."""
    log.info("%s: %d notas por descargar", etiqueta, len(items))
    bloqueo = threading.Event()

    def seguro(item):
        if bloqueo.is_set():
            return
        try:
            procesar(item)
        except Desafio as e:
            bloqueo.set()
            log.error("Desafío anti-bots en %s: se detiene la descarga en este sitio", e)

    try:
        comun.en_paralelo(items, seguro, hilos=hilos, etiqueta=etiqueta)
    finally:
        total = salida.cerrar()
    return total
