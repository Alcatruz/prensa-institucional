#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper de las salas de prensa de las Delegaciones Presidenciales Regionales.

Las 16 delegaciones comparten plataforma (WordPress con el tema del Ministerio del
Interior) y un dominio por región: https://dpr<región>.dpr.gob.cl. El listado de
noticias está en /noticias/ con paginación /noticias/page/N/ (10 notas por página,
con fecha "12 de Noviembre de 2026", título y extracto) y cada nota se lee entrando
a su enlace.

Algunos dominios responden con intermitencia (conexión reiniciada): antes de
recorrer el listado se prueba el sitio con paciencia, con y sin "www" y en https y
http; si no responde, se informa y se termina sin error de datos.

Uso:  python scrapers/scraper_dpr.py <región> [--muestra N] [--completo] [--salida CSV]
                                              [--hilos N] [--pausa S]
      python scrapers/scraper_dpr.py --lista

Escribe data/dpr_<región>.csv por defecto. Es reanudable: las URLs ya presentes en el
CSV no se vuelven a bajar. Por defecto el recorrido es incremental (se corta tras 3
páginas seguidas sin notas nuevas); --completo recorre todas las páginas.
"""
import sys
import time

import comun
import listados as L
from comun import fecha_iso, fecha_meta, log

# clave (parte del dominio dpr<clave>.dpr.gob.cl) -> nombre oficial de la institución
REGIONES = {
    "aricayparinacota": "Delegación Presidencial Regional de Arica y Parinacota",
    "tarapaca": "Delegación Presidencial Regional de Tarapacá",
    "antofagasta": "Delegación Presidencial Regional de Antofagasta",
    "atacama": "Delegación Presidencial Regional de Atacama",
    "coquimbo": "Delegación Presidencial Regional de Coquimbo",
    "valparaiso": "Delegación Presidencial Regional de Valparaíso",
    "metropolitana": "Delegación Presidencial Regional Metropolitana de Santiago",
    "ohiggins": "Delegación Presidencial Regional del Libertador General Bernardo O'Higgins",
    "maule": "Delegación Presidencial Regional del Maule",
    "nuble": "Delegación Presidencial Regional de Ñuble",
    "biobio": "Delegación Presidencial Regional del Biobío",
    "laaraucania": "Delegación Presidencial Regional de La Araucanía",
    "losrios": "Delegación Presidencial Regional de Los Ríos",
    "loslagos": "Delegación Presidencial Regional de Los Lagos",
    "aysen": "Delegación Presidencial Regional de Aysén del General Carlos Ibáñez del Campo",
    "magallanes": "Delegación Presidencial Regional de Magallanes y de la Antártica Chilena",
}

CUERPO = "div.page div.row-col"
# encabezado y pie de la nota que no son texto del comunicado
QUITAR = ("small.date", "h2.title", "a.btn", "figure", "img", ".wp-block-image")


def encontrar_base(s, clave: str, intentos: int = 4) -> str | None:
    """Raíz del sitio que responde (con o sin www, https o http), o None.

    Los reinicios de conexión suelen ser pasajeros: se reintenta con esperas
    crecientes antes de dar el sitio por caído.
    """
    host = f"dpr{clave}.dpr.gob.cl"
    candidatas = [f"https://{host}/", f"https://www.{host}/", f"http://{host}/", f"http://www.{host}/"]
    for n in range(intentos):
        for base in candidatas:
            r = comun.obtener(s, base + "noticias/", pausa=1.0, timeout=60)
            if r is not None and "box-category" in r.text:
                return base
        if n < intentos - 1:
            espera = 15 * (n + 1)
            log.info("dpr/%s: sin respuesta, se reintenta en %d s", clave, espera)
            time.sleep(espera)
    return None


def parsear_listado(base):
    """Tarjetas div.box-category: fecha, título, extracto y enlace 'Leer más'."""
    def parsear(soup):
        out = []
        for d in soup.select("div.box-category"):
            # el enlace "Leer más" es /noticias/<slug>/ o /AAAA/MM/DD/<slug>/ según el sitio
            a = d.select_one("a.btn[href]") or d.select_one("a[href]")
            if not a:
                continue
            f = d.select_one("small")
            t = d.select_one(".title")
            p = d.select_one(".post-excerpt")
            out.append({"url": L.url_limpia(base, a["href"]),
                        "titulo": t.get_text(" ", strip=True) if t else "",
                        "fecha": fecha_iso(f.get_text(" ")) if f else "",
                        "resumen": p.get_text(" ", strip=True) if p else ""})
        return out
    return parsear


def main():
    ap = comun.argumentos("Scraper de prensa de las Delegaciones Presidenciales Regionales.")
    ap.add_argument("region", nargs="?", choices=sorted(REGIONES))
    ap.add_argument("--lista", action="store_true", help="muestra las regiones disponibles")
    ap.add_argument("--completo", action="store_true", help="recorre todas las páginas (sin corte por novedad)")
    args = ap.parse_args()
    if args.lista or not args.region:
        for clave, nombre in REGIONES.items():
            print(f"{clave:18s} {nombre}")
        return
    clave = args.region
    institucion = REGIONES[clave]
    comun.configurar_log()
    s = comun.sesion()
    base = encontrar_base(s, clave)
    if base is None:
        log.error("dpr/%s: el sitio no responde (https/http, con y sin www); no se genera CSV", clave)
        sys.exit(3)
    log.info("dpr/%s: sitio en %s", clave, base)
    salida = comun.SalidaCSV(args.salida or comun.DATA / f"dpr_{clave}.csv")

    def procesar(it):
        soup = L.bajar_soup(s, it["url"], args.pausa)
        if soup is None:
            return
        h = soup.select_one("div.page h2.title") or soup.select_one("h1")
        titulo = (h.get_text(" ", strip=True) if h else "") or it["titulo"]
        d = soup.select_one("div.page small.date")
        fecha = (fecha_iso(d.get_text(" ")) if d else "") or fecha_meta(soup) or it["fecha"]
        texto = L.cuerpo_de(soup, CUERPO, QUITAR)
        if not L.valido(texto):
            log.warning("Sin cuerpo utilizable: %s", it["url"])
            return
        # algunos extractos del listado vienen cortados con "[…]": se usa el inicio del cuerpo
        resumen = it["resumen"]
        if not resumen or resumen.rstrip().endswith("[…]"):
            resumen = L.resumen_de(texto)
        salida.agregar(L.fila(fecha, institucion, titulo, resumen, it["url"], texto))

    try:
        items = L.recorrer_listado(
            s, salida, lambda n: base + "noticias/" + (f"page/{n}/" if n > 1 else ""),
            parsear_listado(base), args.pausa, completo=args.completo, muestra=args.muestra,
            etiqueta=f"dpr/{clave}")
        L.ejecutar(items, procesar, salida, args.hilos, etiqueta=f"dpr/{clave}")
    except L.Desafio as e:
        log.error("Desafío anti-bots en %s: se detiene dpr/%s sin intentar saltarlo", e, clave)
        salida.cerrar()
        sys.exit(2)


if __name__ == "__main__":
    main()
