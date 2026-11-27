#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Orquestador: corre en paralelo los scrapers de prensa (Ejecutivo, otros organismos y Congreso).

- Pool de concurrencia configurable (--jobs, por defecto 4) para no saturar
  la maquina con demasiados Chromium a la vez.
- Cada scraper escribe su propio CSV en data/<nombre>.csv (diseno existente).
- Antes de correr cada scraper se copia su CSV a data/_respaldo_<fecha>/ y al
  terminar se une con esa copia por URL. Asi no se pierden las notas que un
  sitio deja de mostrar ni lo descargado si un scraper falla a medio camino.
- Salida (stdout+stderr) de cada scraper -> logs/<nombre>.log (UTF-8).
- Estado en vivo -> logs/_status.json ; resumen final por consola.

Uso:
    python scrapers/run_todos_paralelo.py
    ... scrapers/run_todos_paralelo.py --jobs 4 --timeout 7200
"""
import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

from fuentes import SUJETOS
from fusionar_respaldo import fusionar

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
DATA = ROOT / "data"
STATUS = LOGS / "_status.json"
RESPALDO = DATA / f"_respaldo_{date.today().isoformat()}"

# Argumentos extra por scraper. El Senado ordena su listado por fecha, de modo
# que 200 paginas (1.800 notas) cubren mas de un ano de novedades.
ARGS_EXTRA = {
    "senado": ["--max-pages", "200"],
}

# (nombre, script relativo, csv de salida)
SCRAPERS = [
    ("interior",              "scrapers/scraper_interior.py",              "data/interior.csv"),
    ("minrel",                "scrapers/scraper_minrel.py",                "data/minrel.csv"),
    ("mop",                   "scrapers/scraper_mop.py",                   "data/mop.csv"),
    ("mma",                   "scrapers/scraper_mma.py",                   "data/mma.csv"),
    ("minagri",               "scrapers/scraper_minagri.py",               "data/minagri.csv"),
    ("mineduc",               "scrapers/scraper_mineduc.py",               "data/mineduc.csv"),
    ("midesof",               "scrapers/scraper_midesof.py",               "data/midesof.csv"),
    ("energia",               "scrapers/scraper_energia.py",               "data/energia.csv"),
    ("subtel",                "scrapers/scraper_subtel.py",                "data/subtel.csv"),
    ("dpp",                   "scrapers/scraper_dpp.py",                   "data/dpp.csv"),
    ("consejo_transparencia", "scrapers/scraper_consejo_transparencia.py", "data/consejo_transparencia.csv"),
    ("seguridad",             "scrapers/scraper_seguridad.py",             "data/seguridad.csv"),
    ("presidencia",           "scrapers/scraper_presidencia.py",           "data/presidencia.csv"),
    ("sag",                   "scrapers/scraper_sag.py",                   "data/sag.csv"),
    ("indap",                 "scrapers/scraper_indap.py",                 "data/indap.csv"),
    ("sec",                   "scrapers/scraper_sec.py",                   "data/sec.csv"),
    ("junaeb",                "scrapers/scraper_junaeb.py",                "data/junaeb.csv"),
    ("senado",                "scrapers/scraper_senado.py",                "data/senado.csv"),
    ("camara_diputados",      "scrapers/scraper_camara_diputados.py",      "data/camara_diputados.csv"),
]

# Scrapers con un script propio (data/<nombre>.csv)
for _nombre in ["agencia_educacion", "hacienda", "contraloria", "corfo", "gendarmeria", "prochile",
                "sii", "mindep", "pdi", "ine", "aduanas", "ips", "subdere", "siss", "scj", "subturismo"]:
    SCRAPERS.append((_nombre, f"scrapers/scraper_{_nombre}.py", f"data/{_nombre}.csv"))

# Scripts de familia: un mismo script sirve a varios sitios (argumento posicional)
FAMILIAS = {
    "scrapers/scraper_wp_api.py": [
        "trabajo", "transportes", "minmujer", "indh", "servel", "tdlc", "tribunal_constitucional",
        "fne", "chilecompra", "junji", "sernatur", "inia", "sma", "cchen", "fonasa", "sercotec", "cne",
        "sernameg", "serviciocivil", "sernageomin", "superir", "previsionsocial",
        "gore_tarapaca", "gore_atacama", "gore_rm", "gore_biobio", "gore_losrios", "gore_magallanes",
        "gore_maule", "gore_nuble",
        "emco", "efe", "casamoneda", "asmar", "enaer", "famae",
        "utem", "umce", "upla", "uta", "uda", "ufro", "ulagos", "uaysen", "uoh", "ubiobio",
        "ifop", "chilevalora", "parquemet", "cochilco"],
    "scrapers/scraper_sitemap.py": [
        "culturas", "economia", "segegob", "minmineria", "minvu", "bienes_nacionales", "sernapesca",
        "conaf", "cde", "cenabast", "supereduc", "isp", "senapred", "odepa", "senda", "migraciones",
        "uantof", "umag", "segpres", "anci", "coordinador_electrico", "convencion",
        "puerto_antofagasta"],
    "scrapers/scraper_wp_html.py": ["minjusticia", "sml", "defensoria_ninez", "minsal", "minciencia"],
    "scrapers/scraper_universidades.py": ["uchile", "usach", "uv", "unap", "userena", "utalca"],
    "scrapers/scraper_defensa_empresas.py": [
        "defensa", "armada", "directemar", "dgac", "codelco", "enap", "zofri", "enami", "metro"],
    "scrapers/scraper_gores.py": [
        "gore_arica", "gore_antofagasta", "gore_coquimbo", "gore_valparaiso", "gore_ohiggins",
        "gore_araucania", "gore_aysen"],
    "scrapers/scraper_listados_institucionales.py": [
        "fiscalia", "sea", "patrimonio", "injuv", "sence", "fosis", "senama", "conadi", "senadis",
        "gobierno_digital", "laboratorio_gobierno", "cnep", "subsecretaria_defensa", "subsecretaria_ffaa",
        "cfa", "sbap", "capredena", "inn", "ciren", "infor", "inapi", "uaf", "agcid", "auditoria_interna",
        "econssa", "puerto_valparaiso", "puerto_arica", "puerto_chacabuco", "puerto_montt"],
    "scrapers/scraper_listados_web.py": [
        "dt", "sernac", "suseso", "spensiones", "subpesca", "servicio_proteccion", "dipres", "cmf",
        "superdesalud"],
}
# delegaciones presidenciales: el script recibe la región y escribe data/dpr_<región>.csv
for _region in ["aricayparinacota", "tarapaca", "antofagasta", "atacama", "coquimbo", "valparaiso",
                "metropolitana", "ohiggins", "maule", "nuble", "biobio", "laaraucania", "losrios",
                "loslagos", "aysen", "magallanes"]:
    SCRAPERS.append((f"dpr_{_region}", "scrapers/scraper_dpr.py", f"data/dpr_{_region}.csv"))
    ARGS_EXTRA[f"dpr_{_region}"] = [_region]
for _script, _sitios in FAMILIAS.items():
    for _sitio in _sitios:
        SCRAPERS.append((_sitio, _script, f"data/{_sitio}.csv"))
        ARGS_EXTRA[_sitio] = [_sitio] + ARGS_EXTRA.get(_sitio, [])
# sujetos obligados del catálogo (scrapers/catalogo_sujetos.csv): data/sujetos/<clave>.csv
_SCRIPT_SUJETO = {"wp_api": "scrapers/scraper_wp_api.py", "wp_api_rest_route": "scrapers/scraper_wp_api.py",
                  "wp_sitemap": "scrapers/scraper_sitemap.py", "wp_rss": "scrapers/scraper_sitemap.py"}
for _s in SUJETOS:
    if _s["metodo"] in _SCRIPT_SUJETO:
        SCRAPERS.append((_s["clave"], _SCRIPT_SUJETO[_s["metodo"]], f"data/sujetos/{_s['clave']}.csv"))
        ARGS_EXTRA[_s["clave"]] = [_s["clave"]]

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def contar_filas(csv_path: Path) -> int:
    """Cuenta filas de datos (sin cabecera) de forma robusta ante saltos de linea internos."""
    if not csv_path.exists():
        return 0
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            n = sum(1 for _ in csv.reader(f))
        return max(0, n - 1)  # menos la cabecera
    except Exception:
        return -1  # ilegible


class State:
    def __init__(self, jobs, timeout):
        self.lock_free = {}  # nombre -> dict
        for nombre, script, csvrel in SCRAPERS:
            self.lock_free[nombre] = {
                "estado": "pendiente",
                "script": script,
                "csv": csvrel,
                "returncode": None,
                "duracion_s": None,
                "filas_antes": contar_filas(ROOT / csvrel),
                "filas_despues": None,
                "log": f"logs/{nombre}.log",
                "inicio": None,
                "fin": None,
            }
        self.meta = {"jobs": jobs, "timeout": timeout, "inicio": datetime.now().isoformat(timespec="seconds")}

    def dump(self):
        STATUS.write_text(
            json.dumps({"meta": self.meta, "scrapers": self.lock_free}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def correr_uno(nombre, script, csvrel, state, timeout):
    info = state.lock_free[nombre]
    info["estado"] = "corriendo"
    info["inicio"] = datetime.now().isoformat(timespec="seconds")
    state.dump()

    log_path = LOGS / f"{nombre}.log"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    # Respaldo previo (si ya hay uno de hoy, se conserva el primero)
    csv_path = ROOT / csvrel
    relativo = csv_path.relative_to(DATA).with_suffix("").as_posix()   # p. ej. sujetos/<clave>
    respaldo = RESPALDO / f"{relativo}.csv"
    if csv_path.exists() and not respaldo.exists():
        respaldo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(csv_path, respaldo)

    t0 = time.monotonic()
    with open(log_path, "w", encoding="utf-8", errors="replace") as logf:
        logf.write(f"# === {nombre} :: {sys.executable} {script} ===\n")
        logf.write(f"# inicio: {info['inicio']}\n\n")
        logf.flush()
        try:
            r = subprocess.run(
                [sys.executable, str(ROOT / script), *ARGS_EXTRA.get(nombre, [])],
                cwd=str(ROOT),
                stdout=logf,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=timeout,
            )
            rc = r.returncode
            estado = "ok" if rc == 0 else "error"
        except subprocess.TimeoutExpired:
            rc = None
            estado = "timeout"
            logf.write(f"\n\n# !! TIMEOUT tras {timeout}s\n")

    # Union con el respaldo por URL
    if respaldo.exists():
        try:
            fusionar(relativo, RESPALDO)
        except Exception as e:  # el CSV queda como lo dejo el scraper
            print(f"  ! {nombre}: no se pudo unir con el respaldo ({e})", flush=True)

    dur = time.monotonic() - t0
    info["estado"] = estado
    info["returncode"] = rc
    info["duracion_s"] = round(dur, 1)
    info["fin"] = datetime.now().isoformat(timespec="seconds")
    info["filas_despues"] = contar_filas(ROOT / csvrel)
    state.dump()

    delta = ""
    if isinstance(info["filas_antes"], int) and isinstance(info["filas_despues"], int) and info["filas_antes"] >= 0:
        d = info["filas_despues"] - info["filas_antes"]
        delta = f" (+{d})" if d >= 0 else f" ({d})"
    print(f"[{estado.upper():7}] {nombre:22} filas={info['filas_despues']}{delta} "
          f"t={info['duracion_s']}s rc={rc}", flush=True)
    return nombre, estado


def main():
    ap = argparse.ArgumentParser(description="Corre en paralelo los scrapers de prensa institucional")
    ap.add_argument("--jobs", type=int, default=4, help="Scrapers en paralelo (default 4)")
    ap.add_argument("--timeout", type=int, default=7200, help="Timeout por scraper en segundos (default 7200 = 2h)")
    ap.add_argument("--only", nargs="*", help="Correr solo estos nombres (subset)")
    args = ap.parse_args()

    LOGS.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)

    lista = SCRAPERS
    if args.only:
        lista = [s for s in SCRAPERS if s[0] in set(args.only)]

    state = State(args.jobs, args.timeout)
    state.dump()

    print(f"== Orquestador prensa institucional ==")
    print(f"Interprete : {sys.executable}")
    print(f"Scrapers   : {len(lista)}  |  concurrencia={args.jobs}  |  timeout={args.timeout}s")
    print(f"Logs       : {LOGS}")
    print(f"Estado     : {STATUS}")
    print("-" * 70, flush=True)

    t0 = time.monotonic()
    resultados = []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(correr_uno, n, s, c, state, args.timeout): n for (n, s, c) in lista}
        for fut in as_completed(futs):
            resultados.append(fut.result())

    total = time.monotonic() - t0
    state.meta["fin"] = datetime.now().isoformat(timespec="seconds")
    state.meta["duracion_total_s"] = round(total, 1)
    state.dump()

    print("-" * 70)
    ok = [n for n, e in resultados if e == "ok"]
    mal = [(n, e) for n, e in resultados if e != "ok"]
    print(f"RESUMEN: {len(ok)}/{len(lista)} OK  en {round(total,1)}s")
    print("\nCSV generados:")
    for nombre, _s, csvrel in lista:
        info = state.lock_free[nombre]
        print(f"  {nombre:22} -> {csvrel:38} filas={info['filas_despues']} [{info['estado']}]")
    if mal:
        print("\nCon problemas (revisar logs/<nombre>.log):")
        for n, e in mal:
            print(f"  - {n}: {e}")
    print("\nEstado detallado:", STATUS)


if __name__ == "__main__":
    main()
