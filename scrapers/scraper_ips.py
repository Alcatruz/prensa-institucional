#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Scraper - Instituto de Previsión Social (IPS)
Fuente: https://www.ips.gob.cl/noticias

El sitio es una aplicación Nuxt cuya API (ips-api.ips.gob.cl) exige una
cabecera que agrega el propio frontend, así que no se consulta por fuera.
Se navega el sitio con Playwright como un lector normal:
  - Listado: se abre /noticias y se pagina con el botón "siguiente"; el
    scraper lee las respuestas JSON de /api/pages/type/3?page=N que el
    navegador recibe (id, título, alias, fecha; 20 por página, ~57 páginas,
    ~1.120 notas).
  - Nota: se abre https://www.ips.gob.cl/<alias> y se lee el texto ya
    renderizado dentro de <main>: título (h1), bajada, fecha (p en negrita)
    y cuerpo (secciones).
  - Se necesita Playwright con Chromium instalado (python -m playwright install chromium).
    Con --hilos se abren esa cantidad de pestañas en paralelo (por defecto 2).

Reanudable: si el CSV existe, omite las URLs que ya contiene.
Columnas CSV: fecha, institucion, titulo, resumen, url, contenido
"""
import asyncio
import re

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

import comun
from comun import DATA, SalidaCSV, USER_AGENT, argumentos, fecha_iso, limpiar, texto_de

INSTITUCION = "Instituto de Previsión Social"
BASE = "https://www.ips.gob.cl"
URL_LISTADO = f"{BASE}/noticias"
RE_API = re.compile(r"ips-api\.ips\.gob\.cl/api/pages/type/3\?")

log = comun.configurar_log("ips")


async def listar(contexto, pausa, limite=0, desde_el_final=False):
    """Recorre el listado paginando con el botón 'siguiente' y recoge las respuestas de la API."""
    pg = await contexto.new_page()
    notas, vistas = [], set()
    cola: asyncio.Queue = asyncio.Queue()

    async def al_responder(resp):
        if RE_API.search(resp.url) and resp.status == 200:
            try:
                await cola.put(await resp.json())
            except Exception:
                pass

    pg.on("response", lambda r: asyncio.ensure_future(al_responder(r)))
    await pg.goto(URL_LISTADO, wait_until="networkidle", timeout=60000)
    ultima = None
    while True:
        try:
            j = await asyncio.wait_for(cola.get(), timeout=20)
        except asyncio.TimeoutError:
            log.warning("Sin respuesta de la API del listado")
            break
        pagina = j.get("page", {})
        actual, ultima = pagina.get("current_page"), pagina.get("last_page", ultima)
        for d in pagina.get("data", []):
            if d["id"] in vistas:
                continue
            vistas.add(d["id"])
            fecha = fecha_iso(d.get("fecha") or "") or fecha_iso((d.get("created_at") or "")[:10])
            notas.append({"url": f"{BASE}/{d['alias']}", "fecha": fecha, "titulo": limpiar(d.get("title"))})
        log.info("Listado: página %s/%s (%d notas)", actual, ultima, len(notas))
        if limite and not desde_el_final and len(notas) >= limite:
            break
        if ultima and actual and actual >= ultima:
            break
        sig = pg.locator("a.pagination-next")
        if await sig.count() == 0 or await sig.first.get_attribute("disabled") is not None:
            break
        await asyncio.sleep(pausa)
        await sig.first.click()
    await pg.close()
    return notas


def extraer(html, nota):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("main")
    if main is None:
        return None
    h1 = main.find("h1")
    titulo = nota["titulo"] or (limpiar(h1.get_text(" ")) if h1 else "")
    fecha = nota["fecha"]
    for p in main.select("p.has-text-weight-bold"):
        fecha = fecha or fecha_iso(p.get_text(" "))
        p.decompose()
    if h1:
        h1.decompose()
    secciones = []
    for sec in main.select("section"):
        t = texto_de(sec)
        if t and not re.fullmatch(r"[\d\s]+", t):  # paginador de galería ("1 2")
            secciones.append(t)
    if not secciones:
        return None
    contenido = "\n".join(secciones)
    return {"fecha": fecha, "institucion": INSTITUCION, "titulo": titulo,
            "resumen": secciones[0][:300] if len(secciones) > 1 else contenido[:300],
            "url": nota["url"], "contenido": contenido}


async def trabajador(contexto, cola, salida, pausa):
    pg = await contexto.new_page()
    while True:
        try:
            nota = cola.get_nowait()
        except asyncio.QueueEmpty:
            break
        if salida.ya_esta(nota["url"]):
            continue
        fila = None
        for intento in range(3):
            try:
                await pg.goto(nota["url"], wait_until="networkidle", timeout=60000)
                await pg.wait_for_selector("main h1", timeout=15000)
                fila = extraer(await pg.content(), nota)
                if fila:
                    break
            except Exception as e:
                log.warning("Intento %d en %s: %s", intento + 1, nota["url"], e)
            await asyncio.sleep(2 * (intento + 1))
        if fila and fila["titulo"]:
            salida.agregar(fila)
        else:
            log.warning("Sin cuerpo o título: %s", nota["url"])
        await asyncio.sleep(pausa)
    await pg.close()


async def principal(a):
    async with async_playwright() as p:
        nav = await p.chromium.launch(headless=True)
        contexto = await nav.new_context(user_agent=USER_AGENT, locale="es-CL")
        notas = await listar(contexto, a.pausa, a.muestra, a.antiguas)
        notas.sort(key=lambda n: n["fecha"], reverse=True)
        log.info("Listado: %d notas", len(notas))
        if a.muestra:
            notas = notas[::-1][:a.muestra] if a.antiguas else notas[:a.muestra]
        salida = SalidaCSV(a.salida)
        cola: asyncio.Queue = asyncio.Queue()
        for n in notas:
            cola.put_nowait(n)
        await asyncio.gather(*(trabajador(contexto, cola, salida, a.pausa) for _ in range(max(1, a.hilos))))
        salida.cerrar()
        await nav.close()


def main():
    ap = argumentos("Scraper del Instituto de Previsión Social", DATA / "ips.csv")
    ap.add_argument("--antiguas", action="store_true", help="con --muestra, toma las más antiguas")
    asyncio.run(principal(ap.parse_args()))


if __name__ == "__main__":
    main()
