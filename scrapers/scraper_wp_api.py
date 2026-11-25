#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper genérico para salas de prensa en WordPress con la API REST abierta.

La API entrega, por página de 100, la fecha, el enlace, el título, la bajada y
el cuerpo completo de cada nota, de modo que basta con una solicitud cada 100
notas. Si el cuerpo que entrega la API viene vacío o muy corto (p. ej. páginas
armadas con Elementor), se entra a la nota y se toma el texto de la página.

Cada sitio se define en SITIOS: URL base, nombre de la institución, tipo de
contenido (posts o pages), categorías de prensa a incluir y filtros de URL.
Escribe data/<sitio>.csv.

Por defecto es incremental: se detiene cuando una página completa de la API
solo trae notas que ya están en el CSV. Con --completo recorre todo.

Uso:
    python scrapers/scraper_wp_api.py trabajo
    python scrapers/scraper_wp_api.py trabajo --muestra 20 --salida prueba.csv
    python scrapers/scraper_wp_api.py --lista
"""
import html
import re
import sys

from bs4 import BeautifulSoup

from comun import (DATA, SalidaCSV, argumentos, configurar_log, cuerpo_por_densidad, en_paralelo,
                   fecha_iso, fecha_meta, limpiar, obtener, sesion, texto_de)
from fuentes import SUJETOS

log = configurar_log("wp_api")

# rest_route: usar /?rest_route= cuando /wp-json/ está bloqueado.
# categorias: IDs de categorías de prensa (se incluye una nota si está en alguna).
# excluir_categorias: IDs de categorías que se descartan (p. ej. recortes de otros medios).
# por_pagina: notas por solicitud a la API, para servidores que no aguantan 100 con cuerpo.
SITIOS = {
    "trabajo": dict(base="https://www.mintrab.gob.cl",
                    institucion="Ministerio del Trabajo y Previsión Social",
                    categorias=[7, 1204, 850, 1619]),   # noticias, regiones, comunicados, noticias de gobierno
    "transportes": dict(base="https://www.mtt.gob.cl",
                        institucion="Ministerio de Transportes y Telecomunicaciones"),
    "minmujer": dict(base="https://minmujeryeg.gob.cl", rest_route=True,
                     institucion="Ministerio de la Mujer y la Equidad de Género"),
    "indh": dict(base="https://www.indh.cl", institucion="Instituto Nacional de Derechos Humanos"),
    "servel": dict(base="https://www.servel.cl", institucion="Servicio Electoral",
                   categorias=[14, 15]),                 # noticias, noticias electorales
    "tdlc": dict(base="https://www.tdlc.cl", institucion="Tribunal de Defensa de la Libre Competencia",
                 categorias=[53]),                       # noticias (excluye resoluciones e informes)
    "tribunal_constitucional": dict(base="https://www1.tribunalconstitucional.cl",
                                    institucion="Tribunal Constitucional"),
    "fne": dict(base="https://www.fne.gob.cl", institucion="Fiscalía Nacional Económica",
                categorias=[108]),                       # noticias (excluye agenda, resoluciones, jurisprudencia)
    "chilecompra": dict(base="https://www.chilecompra.cl", institucion="Dirección ChileCompra",
                        categorias=[1, 109]),            # noticias (excluye normativa y documentos)
    "junji": dict(base="https://www.junji.gob.cl", institucion="Junta Nacional de Jardines Infantiles"),
    "sernatur": dict(base="https://www.sernatur.cl", institucion="Servicio Nacional de Turismo",
                     cuerpo=["#noticias-interna .espacio_texto_video"]),   # notas 2018-2021 sin cuerpo en la API
    "inia": dict(base="https://www.inia.cl", institucion="Instituto de Investigaciones Agropecuarias"),
    "sma": dict(base="https://portal.sma.gob.cl", rest_route=True,
                institucion="Superintendencia del Medio Ambiente"),
    "cchen": dict(base="https://www.cchen.cl", rest_route=True,
                  institucion="Comisión Chilena de Energía Nuclear"),
    "fonasa": dict(base="https://www.fonasa.gob.cl", institucion="Fondo Nacional de Salud"),
    "sercotec": dict(base="https://www.sercotec.cl", institucion="Servicio de Cooperación Técnica"),
    "cne": dict(base="https://www.cne.cl", institucion="Comisión Nacional de Energía",
                tipo="pages", filtro_url=r"/prensa/prensa-\d{4}/[^/]+/[^/]+/?$"),
    "sernameg": dict(base="https://www.sernameg.gob.cl",
                     institucion="Servicio Nacional de la Mujer y la Equidad de Género"),
    "serviciocivil": dict(base="https://www.serviciocivil.cl", institucion="Servicio Civil"),
    "sernageomin": dict(base="https://www.sernageomin.cl",
                        institucion="Servicio Nacional de Geología y Minería"),
    "superir": dict(base="https://www.superir.gob.cl", categorias=[8],
                    institucion="Superintendencia de Insolvencia y Reemprendimiento"),
    "previsionsocial": dict(base="https://previsionsocial.gob.cl",
                            institucion="Subsecretaría de Previsión Social"),
    # gobiernos regionales
    "gore_tarapaca": dict(base="https://www.goretarapaca.gov.cl", institucion="Gobierno Regional de Tarapacá"),
    "gore_atacama": dict(base="https://www.goreatacama.cl", institucion="Gobierno Regional de Atacama"),
    "gore_rm": dict(base="https://www.gobiernosantiago.cl", categorias=[4],
                    institucion="Gobierno Regional Metropolitano de Santiago"),
    "gore_biobio": dict(base="https://gorebiobio.cl", categorias=[9, 236, 10, 233],
                        institucion="Gobierno Regional del Biobío"),
    "gore_losrios": dict(base="https://www.goredelosrios.cl", rest_route=True,
                         institucion="Gobierno Regional de Los Ríos"),
    "gore_magallanes": dict(base="https://www.goremagallanes.cl",
                            institucion="Gobierno Regional de Magallanes y de la Antártica Chilena"),
    "gore_maule": dict(base="https://www.goremaule.cl/goremauleVII", categorias=[3],   # prensa
                       institucion="Gobierno Regional del Maule"),
    "gore_nuble": dict(base="https://goredenuble.cl", categorias=[3],                 # Noticias
                       institucion="Gobierno Regional de Ñuble"),
    # defensa y empresas públicas
    "emco": dict(base="https://www.emco.mil.cl", rest_route=True, categorias=[3],
                 institucion="Estado Mayor Conjunto"),
    "efe": dict(base="https://www.efe.cl", institucion="Empresa de los Ferrocarriles del Estado"),
    "casamoneda": dict(base="https://www.casamoneda.cl", institucion="Casa de Moneda de Chile"),
    "asmar": dict(base="https://www.asmar.cl", institucion="Astilleros y Maestranzas de la Armada"),
    "enaer": dict(base="https://www.enaer.cl", institucion="Empresa Nacional de Aeronáutica"),
    "famae": dict(base="https://www.famae.cl", categorias=[50],
                  institucion="Fábricas y Maestranzas del Ejército"),
    # universidades estatales
    "utem": dict(base="https://www.utem.cl", institucion="Universidad Tecnológica Metropolitana"),
    "umce": dict(base="https://www.umce.cl",
                 institucion="Universidad Metropolitana de Ciencias de la Educación"),
    "upla": dict(base="https://www.upla.cl/noticias",   # las noticias son otra instalación
                 excluir_categorias=[45],                 # "UPLA en los medios": recortes de prensa
                 institucion="Universidad de Playa Ancha"),
    "uta": dict(base="https://www.uta.cl", institucion="Universidad de Tarapacá"),
    "uda": dict(base="https://uda.cl", institucion="Universidad de Atacama"),
    "ufro": dict(base="https://www.ufro.cl", institucion="Universidad de La Frontera"),
    "ulagos": dict(base="https://www.ulagos.cl", institucion="Universidad de Los Lagos"),
    "uaysen": dict(base="https://uaysen.cl", institucion="Universidad de Aysén"),
    "uoh": dict(base="https://www.uoh.cl", por_pagina=20,   # con 100 por página la API no responde
                institucion="Universidad de O'Higgins"),
    "ubiobio": dict(base="https://noticias.ubiobio.cl",   # portal de noticias aparte del sitio Liferay
                    institucion="Universidad del Bío-Bío"),
}

# Sujetos obligados por la Ley de Transparencia del catálogo (auditoria_salas_prensa.py
# --catalogo): se toman todas las entradas (posts) del sitio, que en estos sitios son
# las noticias; las notas quedan en data/sujetos/<clave>.csv.
for _s in SUJETOS:
    if _s["metodo"].startswith("wp_api") and _s["clave"] not in SITIOS:
        SITIOS[_s["clave"]] = dict(base=_s["base"], institucion=_s["institucion"],
                                   archivo=f"sujetos/{_s['clave']}",
                                   rest_route=_s["metodo"] == "wp_api_rest_route")

POR_PAGINA = 100
SELECTORES_CUERPO = [".elementor-widget-theme-post-content", ".entry-content", ".post-content",
                     "article .content", ".single-content", "article", "main"]


def palabras(t: str) -> int:
    return len(t.split())


def html_a_texto(fragmento: str) -> str:
    return texto_de(BeautifulSoup(fragmento or "", "html.parser"))


def url_api(cfg: dict, pagina: int, por_pagina: int) -> tuple[str, dict]:
    ruta = f"wp/v2/{cfg.get('tipo', 'posts')}"
    params = {"per_page": por_pagina, "page": pagina, "orderby": "date", "order": "desc",
              "_fields": "date,link,title,excerpt,content"}
    if cfg.get("categorias"):
        params["categories"] = ",".join(map(str, cfg["categorias"]))
    if cfg.get("excluir_categorias"):
        params["categories_exclude"] = ",".join(map(str, cfg["excluir_categorias"]))
    if cfg.get("rest_route"):
        return f"{cfg['base']}/", {"rest_route": f"/{ruta}", **params}
    return f"{cfg['base']}/wp-json/{ruta}", params


def cuerpo_desde_pagina(s, url: str, pausa: float, propios=()) -> tuple[str, str]:
    """Texto y fecha tomados de la página de la nota (si la API trae el cuerpo vacío).

    Los selectores propios del sitio van primero y se unen todas sus coincidencias."""
    r = obtener(s, url, pausa)
    if r is None:
        return "", ""
    soup = BeautifulSoup(r.text, "html.parser")
    fecha = fecha_meta(soup)
    for sel in propios:
        t = "\n".join(texto_de(el) for el in soup.select(sel)).strip()
        if palabras(t) >= 40:
            return t, fecha
    for sel in SELECTORES_CUERPO:
        el = soup.select_one(sel)
        t = texto_de(el) if el else ""
        if palabras(t) >= 40:
            return t, fecha
    t = cuerpo_por_densidad(soup)   # plantilla desconocida: el bloque con más texto en párrafos
    return (t, fecha) if palabras(t) >= 40 else ("", fecha)


def main():
    ap = argumentos("Salas de prensa WordPress vía API REST")
    ap.add_argument("sitio", nargs="?", help="clave del sitio (ver --lista)")
    ap.add_argument("--lista", action="store_true", help="muestra los sitios configurados")
    ap.add_argument("--completo", action="store_true", help="recorre todas las páginas de la API")
    args = ap.parse_args()

    if args.lista or not args.sitio:
        for k, v in SITIOS.items():
            print(f"  {k:24} {v['institucion']}  ({v['base']})")
        return
    if args.sitio not in SITIOS:
        sys.exit(f"Sitio desconocido: {args.sitio}. Usa --lista.")
    cfg = SITIOS[args.sitio]
    salida = SalidaCSV(args.salida or DATA / f"{cfg.get('archivo', args.sitio)}.csv")
    s = sesion()
    filtro = re.compile(cfg["filtro_url"]) if cfg.get("filtro_url") else None
    log.info("%s: %s (%d notas ya guardadas)", args.sitio, cfg["institucion"], salida.previas)

    por_pagina = cfg.get("por_pagina", POR_PAGINA)
    por_pagina = min(por_pagina, args.muestra) if args.muestra else por_pagina
    pendientes = []   # notas cuyo cuerpo hay que ir a buscar a la página
    pagina, total_paginas, vistas = 1, None, 0
    while True:
        url, params = url_api(cfg, pagina, por_pagina)
        r = obtener(s, url, args.pausa, params=params)
        if r is None:
            if pagina == 1 and por_pagina > 20:   # algunos servidores no aguantan 100 con cuerpo
                por_pagina = 20
                continue
            break
        total_paginas = total_paginas or int(r.headers.get("X-WP-TotalPages", "1"))
        items = r.json()
        if not items:
            break
        nuevas_pagina = 0
        for it in items:
            link = it.get("link", "")
            if filtro and not filtro.search(link):
                continue
            vistas += 1
            if salida.ya_esta(link):
                continue
            nuevas_pagina += 1
            titulo = limpiar(html.unescape(BeautifulSoup(it["title"]["rendered"], "html.parser").get_text()))
            cuerpo = html_a_texto(it.get("content", {}).get("rendered", ""))
            resumen = limpiar(html_a_texto(it.get("excerpt", {}).get("rendered", "")))
            fila = {"fecha": fecha_iso(it.get("date", "")), "institucion": cfg["institucion"],
                    "titulo": titulo, "resumen": resumen, "url": link, "contenido": cuerpo}
            if palabras(cuerpo) < 40:
                pendientes.append(fila)
            else:
                if not fila["resumen"]:
                    fila["resumen"] = cuerpo[:300].rsplit(" ", 1)[0] + "…"
                salida.agregar(fila)
            if args.muestra and vistas >= args.muestra:
                break
        log.info("Página %d/%s: %d nuevas", pagina, total_paginas, nuevas_pagina)
        if args.muestra and vistas >= args.muestra:
            break
        if not args.completo and nuevas_pagina == 0 and salida.previas:
            log.info("Página sin notas nuevas: fin del modo incremental")
            break
        if pagina >= total_paginas:
            break
        pagina += 1

    if pendientes:
        log.info("%d notas sin cuerpo en la API: se toman de su página", len(pendientes))

        def completar(fila):
            cuerpo, fecha = cuerpo_desde_pagina(s, fila["url"], args.pausa, cfg.get("cuerpo", ()))
            if palabras(cuerpo) > palabras(fila["contenido"]):
                fila["contenido"] = cuerpo
            fila["fecha"] = fila["fecha"] or fecha
            if not fila["resumen"] and fila["contenido"]:
                fila["resumen"] = fila["contenido"][:300].rsplit(" ", 1)[0] + "…"
            salida.agregar(fila)

        en_paralelo(pendientes, completar, args.hilos, etiqueta="páginas de nota")

    salida.cerrar()


if __name__ == "__main__":
    main()
