#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper - Policía de Investigaciones de Chile (PDI)
Fuente: https://www.pdichile.cl/centro-de-prensa/noticias

Estrategia: requests + BeautifulSoup (el sitio rechaza clientes sin User-Agent
de navegador; con el de comun.sesion() responde normalmente).
  - Listado: ?TodasLasNoticiaspage=N (N desde 1 hasta que la página venga sin
    tarjetas; ~79 páginas de 10 notas, desde noviembre de 2017). Cada tarjeta
    (section.box_contain) trae título (h1), fecha dd-mm-aaaa (primer span) y
    enlace /centro-de-prensa/detalle-prensa/AAAA/MM/DD/slug.
  - Las URL llevan tildes y entidades HTML: se decodifican y se codifican en
    porcentaje para que la URL guardada sea válida.
  - Nota: cuerpo en div.contNewParr (dos bloques: bajada y texto). El título
    del listado se usa como título; la fecha sale del listado o de la URL.

Reanudable: si el CSV existe, omite las URLs que ya contiene.
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""
from urllib.parse import quote, urlsplit, urlunsplit

from bs4 import BeautifulSoup

import comun
from comun import (DATA, SalidaCSV, argumentos, en_paralelo, fecha_de_url, fecha_iso, limpiar,
                   obtener, sesion, texto_de)

INSTITUCION = "Policía de Investigaciones de Chile"
BASE = "https://www.pdichile.cl"
URL_LISTADO = f"{BASE}/centro-de-prensa/noticias?TodasLasNoticiaspage="
MAX_PAGINAS = 200  # tope de seguridad; el listado real termina antes

log = comun.configurar_log("pdi")


def codificar(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, quote(p.path, safe="/%-._~!$&'()*+,;=:@"), p.query, ""))


def tarjetas(s, pagina, pausa):
    r = obtener(s, f"{URL_LISTADO}{pagina}", pausa)
    if not r:
        return None
    soup = BeautifulSoup(r.content, "html.parser")
    notas = []
    for caja in soup.select("section.box_contain"):
        a = caja.find("a", href=True)
        h1 = caja.find("h1")
        if a is None or "/detalle-prensa/" not in a["href"]:
            continue
        url = codificar(a["href"])
        span = caja.select_one(".static span")
        fecha = fecha_iso(span.get_text()) if span else ""
        notas.append({"url": url, "titulo": limpiar(h1.get_text(" ")) if h1 else "",
                      "fecha": fecha or fecha_de_url(url)})
    return notas


def listar(s, pausa, limite=0, desde_el_final=False):
    notas, vistas, vacias = [], set(), 0
    for pagina in range(1, MAX_PAGINAS + 1):
        lote = tarjetas(s, pagina, pausa)
        if not lote:
            # la página 23 del listado llega sin tarjetas (falla del sitio): se
            # tolera una racha corta de páginas vacías antes de dar por terminado
            vacias += 1
            log.warning("Página %d del listado sin notas", pagina)
            if vacias >= 3:
                break
            continue
        vacias = 0
        notas += [n for n in lote if n["url"] not in vistas]
        vistas.update(n["url"] for n in lote)
        if limite and not desde_el_final and len(notas) >= limite:
            break
    return notas


def procesar(nota, s, salida, pausa):
    if salida.ya_esta(nota["url"]):
        return
    r = obtener(s, nota["url"], pausa)
    if not r:
        return
    soup = BeautifulSoup(r.content, "html.parser")
    bloques = soup.select("div.contNewParr")
    textos = [t for t in (texto_de(b) for b in bloques) if t]
    # el botón de descarga de adjuntos queda como línea suelta al final
    contenido = "\n".join(l for t in textos for l in t.split("\n") if l.strip() != "Descargar")
    titulo = nota["titulo"]
    if not titulo and soup.title:
        titulo = limpiar(soup.title.get_text())
    if not contenido or not titulo:
        log.warning("Sin cuerpo o título: %s", nota["url"])
        return
    resumen = textos[0] if len(textos) > 1 else contenido
    salida.agregar({"fecha": nota["fecha"], "institucion": INSTITUCION, "titulo": titulo,
                    "resumen": resumen[:300], "url": nota["url"], "contenido": contenido})


def main():
    ap = argumentos("Scraper de la Policía de Investigaciones de Chile", DATA / "pdi.csv")
    ap.add_argument("--antiguas", action="store_true", help="con --muestra, toma las más antiguas")
    a = ap.parse_args()
    s = sesion()
    notas = listar(s, a.pausa, a.muestra, a.antiguas)
    log.info("Listado: %d notas", len(notas))
    if a.muestra:
        notas = notas[::-1][:a.muestra] if a.antiguas else notas[:a.muestra]
    salida = SalidaCSV(a.salida)
    en_paralelo(notas, lambda n: procesar(n, s, salida, a.pausa), a.hilos)
    salida.cerrar()


if __name__ == "__main__":
    main()
