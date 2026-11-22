"""
Scraper - Ministerio de Relaciones Exteriores de Chile
Fuente: https://www.minrel.gob.cl/minrel/sala-de-prensa/p/{n}

Estructura del sitio:
  - Listado: article.card > a.card__auxi[href], .card__title, .card__date
  - Paginacion: /minrel/sala-de-prensa/p/{n}, hasta p/84 aprox.
  - Articulo: h2.enc-main__title, p.enc-main__date,
              section.art-content .CUERPO (cuerpo completo),
              JSON-LD script[type=application/ld+json] -> datePublished (ISO 8601)
"""

import asyncio
from pathlib import Path
import csv
import json
import logging
import argparse
import re
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

BASE_URL = 'https://www.minrel.gob.cl'
LIST_URL = BASE_URL + '/minrel/sala-de-prensa/p/{n}'
INSTITUCION = 'Ministerio de Relaciones Exteriores'
OUTPUT_CSV = str(Path(__file__).resolve().parent.parent / "data" / "minrel.csv")

MESES = {
    'enero': '01', 'febrero': '02', 'marzo': '03', 'abril': '04',
    'mayo': '05', 'junio': '06', 'julio': '07', 'agosto': '08',
    'septiembre': '09', 'octubre': '10', 'noviembre': '11', 'diciembre': '12'
}


def parse_fecha_iso(iso_str):
    """Convierte '2026-03-26T18:46:00-03:00' -> '2026-03-26'."""
    if not iso_str:
        return ''
    return iso_str[:10]


def parse_fecha_texto(texto):
    """
    Convierte 'Jueves, 26 de marzo de 2026' o 'Jueves 26 de marzo de 2026'
    -> '2026-03-26'.
    """
    texto = texto.lower().strip()
    # Remover nombre del dia si existe
    # Patron: (dia_semana,?) dia de mes de anio
    m = re.search(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', texto)
    if m:
        dia = m.group(1).zfill(2)
        mes_nombre = m.group(2)
        anio = m.group(3)
        mes = MESES.get(mes_nombre, '00')
        return f'{anio}-{mes}-{dia}'
    return ''


async def get_article_content(page, url, retries=3):
    """
    Navega a la URL del articulo y extrae:
      titulo, fecha (YYYY-MM-DD), resumen, contenido completo
    """
    for attempt in range(1, retries + 1):
        try:
            await page.goto(url, wait_until='networkidle', timeout=30000)

            # --- Titulo ---
            titulo = ''
            if await page.locator('h2.enc-main__title').count() > 0:
                titulo = await page.evaluate(
                    'document.querySelector("h2.enc-main__title").textContent'
                )
                titulo = titulo.strip()

            # --- Fecha via JSON-LD (mas confiable) ---
            fecha = ''
            if await page.locator('script[type="application/ld+json"]').count() > 0:
                try:
                    jsonld_text = await page.locator(
                        'script[type="application/ld+json"]'
                    ).first.inner_text()
                    data = json.loads(jsonld_text)
                    fecha = parse_fecha_iso(data.get('datePublished', ''))
                except Exception:
                    pass

            # Fallback: fecha del banner
            if not fecha and await page.locator('p.enc-main__date').count() > 0:
                fecha_raw = await page.evaluate(
                    'document.querySelector("p.enc-main__date").textContent'
                )
                fecha = parse_fecha_texto(fecha_raw)

            # --- Contenido completo ---
            contenido = ''
            if await page.locator('section.art-content .CUERPO').count() > 0:
                contenido = await page.evaluate(
                    'document.querySelector("section.art-content .CUERPO").textContent'
                )
                contenido = ' '.join(contenido.split())  # normalizar espacios

            # --- Resumen: primer parrafo del contenido ---
            resumen = ''
            if await page.locator('section.art-content .CUERPO p').count() > 0:
                resumen = await page.evaluate(
                    'document.querySelector("section.art-content .CUERPO p").textContent'
                )
                resumen = ' '.join(resumen.split())
            elif contenido:
                resumen = contenido[:300]

            return titulo, fecha, resumen, contenido

        except PlaywrightTimeoutError:
            log.warning(f'Timeout en {url} (intento {attempt}/{retries})')
            if attempt == retries:
                return '', '', '', ''
            await asyncio.sleep(3)
        except Exception as e:
            log.error(f'Error en {url}: {e}')
            return '', '', '', ''


async def scrape_page(browser, page_num):
    """
    Extrae todos los items del listado de una pagina numerada.
    Devuelve lista de dicts con: url, titulo_lista, fecha_lista
    """
    url = LIST_URL.format(n=page_num)
    page = await browser.new_page()
    try:
        await page.set_extra_http_headers({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36'
        })
        await page.goto(url, wait_until='networkidle', timeout=30000)

        items = []
        articles = await page.locator('article.card').all()
        log.info(f'Pagina {page_num}: {len(articles)} articulos encontrados')

        for art in articles:
            try:
                link = art.locator('a.card__auxi')
                if await link.count() == 0:
                    # Algunos cards tienen el link en otro lugar
                    link = art.locator('a')
                if await link.count() == 0:
                    continue

                href = await link.first.get_attribute('href')
                if not href:
                    continue

                # Construir URL absoluta
                if href.startswith('/'):
                    full_url = BASE_URL + href
                elif href.startswith('http'):
                    full_url = href
                else:
                    full_url = BASE_URL + '/' + href

                # Titulo del listado (referencia)
                titulo_lista = ''
                if await art.locator('.card__title').count() > 0:
                    titulo_lista = await art.evaluate(
                        'el => el.querySelector(".card__title").textContent'
                    )
                    titulo_lista = titulo_lista.strip()

                # Fecha del listado (referencia; se sobreescribira con la del articulo)
                fecha_lista = ''
                if await art.locator('.card__date').count() > 0:
                    fecha_raw = await art.evaluate(
                        'el => el.querySelector(".card__date").textContent'
                    )
                    fecha_lista = parse_fecha_texto(fecha_raw)

                items.append({
                    'url': full_url,
                    'titulo_lista': titulo_lista,
                    'fecha_lista': fecha_lista,
                })
            except Exception as e:
                log.warning(f'Error procesando article en pagina {page_num}: {e}')

        return items

    except PlaywrightTimeoutError:
        log.error(f'Timeout cargando pagina de listado {page_num}')
        return []
    finally:
        await page.close()


async def worker(browser, queue, results, worker_id):
    """Worker que procesa items de la cola."""
    page = await browser.new_page()
    await page.set_extra_http_headers({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/120.0.0.0 Safari/537.36'
    })

    while True:
        try:
            item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        url = item['url']
        log.info(f'[W{worker_id}] Procesando: {url}')

        titulo, fecha, resumen, contenido = await get_article_content(page, url)

        # Usar datos del listado como fallback
        if not titulo:
            titulo = item.get('titulo_lista', '')
        if not fecha:
            fecha = item.get('fecha_lista', '')

        results.append([
            fecha,
            INSTITUCION,
            titulo,
            resumen,
            url,
            contenido,
        ])

        await asyncio.sleep(1.2)  # respetar el servidor

    await page.close()


def guardar_csv(datos, ruta):
    """Guarda los datos en CSV con las 6 columnas obligatorias en UTF-8."""
    with open(ruta, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['fecha', 'institucion', 'titulo', 'resumen', 'url', 'contenido'])
        writer.writerows(datos)
    log.info(f'CSV guardado en {ruta} ({len(datos)} filas)')


async def scrape(max_pages=None, num_workers=4):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Determinar numero de paginas
        if max_pages is None:
            # Detectar total de paginas desde la primera pagina
            page = await browser.new_page()
            await page.set_extra_http_headers({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            await page.goto(LIST_URL.format(n=1), wait_until='networkidle', timeout=30000)
            # Buscar el ultimo numero de paginacion
            pagination_links = await page.locator('a[href*="/sala-de-prensa/p/"]').all()
            max_num = 1
            for lnk in pagination_links:
                href = await lnk.get_attribute('href')
                m = re.search(r'/p/(\d+)', href or '')
                if m:
                    n = int(m.group(1))
                    if n > max_num:
                        max_num = n
            await page.close()
            max_pages = max_num
            log.info(f'Total de paginas detectadas: {max_pages}')
        else:
            log.info(f'Modo limitado: scrapeando {max_pages} pagina(s)')

        # FASE 1: recolectar todos los items del listado
        all_items = []
        for page_num in range(1, max_pages + 1):
            items = await scrape_page(browser, page_num)
            all_items.extend(items)
            log.info(f'Acumulado hasta pagina {page_num}: {len(all_items)} items')
            await asyncio.sleep(1.5)

        log.info(f'Total items a procesar: {len(all_items)}')

        # Deduplicar por URL
        seen = set()
        unique_items = []
        for item in all_items:
            if item['url'] not in seen:
                seen.add(item['url'])
                unique_items.append(item)
        log.info(f'Items unicos: {len(unique_items)}')

        # FASE 2: procesar cada noticia con workers en paralelo
        queue = asyncio.Queue()
        for item in unique_items:
            await queue.put(item)

        results = []
        workers = [
            asyncio.create_task(worker(browser, queue, results, i + 1))
            for i in range(min(num_workers, len(unique_items)))
        ]
        await asyncio.gather(*workers)

        await browser.close()

        # Ordenar por fecha descendente
        results.sort(key=lambda x: x[0] if x[0] else '', reverse=True)

        return results


def main():
    parser = argparse.ArgumentParser(
        description='Scraper Ministerio de Relaciones Exteriores de Chile'
    )
    parser.add_argument(
        '--max-pages', type=int, default=None,
        help='Numero maximo de paginas a procesar (default: todas)'
    )
    parser.add_argument(
        '--workers', type=int, default=4,
        help='Numero de workers paralelos para contenido (default: 4)'
    )
    parser.add_argument(
        '--output', type=str, default=OUTPUT_CSV,
        help='Ruta del CSV de salida'
    )
    args = parser.parse_args()

    log.info('Iniciando scraper MINREL')
    datos = asyncio.run(scrape(max_pages=args.max_pages, num_workers=args.workers))

    if datos:
        guardar_csv(datos, args.output)
        log.info(f'Completado: {len(datos)} noticias extraidas')
    else:
        log.warning('No se extrajeron datos')


if __name__ == '__main__':
    main()
