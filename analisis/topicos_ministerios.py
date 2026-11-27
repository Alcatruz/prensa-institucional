#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tópicos de la prensa de los ministerios y su evolución en el tiempo (BERTopic).

Toma las notas de los ministerios y sus subsecretarías (grupo "Ministerios y
subsecretarías" de scrapers/fuentes.py), asigna cada fuente a su ministerio y
modela los tópicos sin filtrar por tema, para ver qué comunica cada ministerio
y qué tópicos aparecen o crecen con los años.

Etapas (cada una deja su resultado en data/topicos_ministerios/ y se corre aparte):

    preparar  junta las notas, quita duplicados y arma el texto de cada una
    embeber   embeddings con paraphrase-multilingual-MiniLM-L12-v2 (por tramos, reanudable)
    modelar   UMAP + HDBSCAN + c-TF-IDF y asignación de tópicos
    analizar  participación de cada tópico por año y por ministerio, tópicos emergentes
    figuras   mapa de calor de tópicos y barras de agenda de autoridad (carpeta figuras/)
    explorador  datos y página HTML para recorrer los tópicos (explorador/index.html)

Uso:
    python analisis/topicos_ministerios.py preparar
    python analisis/topicos_ministerios.py embeber
    python analisis/topicos_ministerios.py modelar
    python analisis/topicos_ministerios.py analizar
    python analisis/topicos_ministerios.py figuras
    python analisis/topicos_ministerios.py explorador
"""
import csv
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scrapers"))
from fuentes import FUENTES  # noqa: E402

DATA = RAIZ / "data"
SALIDA = DATA / "topicos_ministerios"
DOCS = SALIDA / "docs.parquet"
EMB = SALIDA / "embeddings.npy"
TRAMOS = SALIDA / "embeddings_tramos"
MODELO_EMB = "paraphrase-multilingual-MiniLM-L12-v2"
SEMILLA = 42

csv.field_size_limit(2**31 - 1)

# fuente -> ministerio (las subsecretarías van con su ministerio)
MINISTERIO = {
    "interior": "Interior", "subdere": "Interior",
    "seguridad": "Seguridad Pública",
    "minrel": "Relaciones Exteriores",
    "hacienda": "Hacienda",
    "economia": "Economía", "subpesca": "Economía", "subturismo": "Economía",
    "segegob": "Secretaría General de Gobierno",
    "segpres": "Secretaría General de la Presidencia",
    "minjusticia": "Justicia y DD.HH.", "sujetos/derechoshumanos": "Justicia y DD.HH.",
    "trabajo": "Trabajo y Previsión Social", "previsionsocial": "Trabajo y Previsión Social",
    "mop": "Obras Públicas",
    "minsal": "Salud",
    "minvu": "Vivienda y Urbanismo",
    "minagri": "Agricultura",
    "minmineria": "Minería",
    "transportes": "Transportes y Telecomunicaciones", "subtel": "Transportes y Telecomunicaciones",
    "sujetos/subtrans": "Transportes y Telecomunicaciones",
    "bienes_nacionales": "Bienes Nacionales",
    "energia": "Energía",
    "mma": "Medio Ambiente",
    "mineduc": "Educación", "sujetos/parvularia_mineduc": "Educación",
    "sujetos/educacionsuperior_mineduc": "Educación",
    "midesof": "Desarrollo Social y Familia",
    "minmujer": "Mujer y Equidad de Género",
    "culturas": "Culturas, Artes y Patrimonio",
    "mindep": "Deporte",
    "minciencia": "Ciencia y Tecnología",
    "defensa": "Defensa Nacional", "subsecretaria_defensa": "Defensa Nacional",
    "subsecretaria_ffaa": "Defensa Nacional",
}

RE_FECHA = re.compile(r"^(\d{4})-\d{2}-\d{2}$")
RE_URL = re.compile(r"https?://\S+|www\.\S+")
PALABRAS_EMB = 100      # el modelo trunca en 128 piezas: título + primer párrafo
PALABRAS_REP = 600      # texto para las palabras clave de cada tópico
MIN_PALABRAS = 20


def limpiar(t: str) -> str:
    return " ".join(RE_URL.sub(" ", t or "").split())


def clave_dup(titulo: str, fecha: str) -> str:
    return re.sub(r"\W+", " ", titulo.lower()).strip() + "|" + fecha


# ---------------------------------------------------------------- preparar
def preparar():
    fuentes = [(a, e) for a, e, g in FUENTES if g == "Ministerios y subsecretarías"]
    faltan = [a for a, _ in fuentes if a not in MINISTERIO]
    if faltan:
        sys.exit(f"Fuentes sin ministerio asignado: {faltan}")
    filas, vistos = [], set()
    leidas = sin_fecha = cortas = duplicadas = 0
    for archivo, etiqueta in fuentes:
        ruta = DATA / f"{archivo}.csv"
        if not ruta.exists():
            print(f"  ! falta {ruta.name}")
            continue
        with open(ruta, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                leidas += 1
                fecha = (r.get("fecha") or "").strip()
                m = RE_FECHA.match(fecha)
                if not m or not ("2006" <= m.group(1) <= "2026"):
                    sin_fecha += 1
                    continue
                titulo = limpiar(r.get("titulo"))
                cuerpo = limpiar(r.get("contenido"))
                if len(cuerpo.split()) < MIN_PALABRAS:
                    cuerpo = limpiar(r.get("resumen")) if len(limpiar(r.get("resumen")).split()) > len(cuerpo.split()) else cuerpo
                palabras = cuerpo.split()
                if len(palabras) < MIN_PALABRAS:
                    cortas += 1
                    continue
                k = clave_dup(titulo, fecha)
                if k in vistos:
                    duplicadas += 1
                    continue
                vistos.add(k)
                filas.append({
                    "fecha": fecha, "anio": int(m.group(1)), "fuente": archivo, "organismo": etiqueta,
                    "ministerio": MINISTERIO[archivo], "titulo": titulo, "url": (r.get("url") or "").strip(),
                    "palabras": len(palabras),
                    "texto_emb": f"{titulo}. " + " ".join(palabras[:PALABRAS_EMB]),
                    "texto_rep": f"{titulo}. " + " ".join(palabras[:PALABRAS_REP]),
                })
    d = pd.DataFrame(filas).sort_values(["fecha", "fuente"]).reset_index(drop=True)
    SALIDA.mkdir(parents=True, exist_ok=True)
    d.to_parquet(DOCS, index=False)
    print(f"Leídas {leidas:,} | sin fecha válida {sin_fecha:,} | menos de {MIN_PALABRAS} palabras {cortas:,} "
          f"| duplicadas {duplicadas:,} | quedan {len(d):,} notas de {d.ministerio.nunique()} ministerios")
    print(d.groupby("ministerio").agg(notas=("fecha", "size"), desde=("anio", "min"), hasta=("anio", "max"))
          .sort_values("notas", ascending=False).to_string())


# ---------------------------------------------------------------- embeber
def embeber(tramo=4000):
    from sentence_transformers import SentenceTransformer
    d = pd.read_parquet(DOCS, columns=["texto_emb"])
    textos = d.texto_emb.tolist()
    TRAMOS.mkdir(parents=True, exist_ok=True)
    modelo = SentenceTransformer(MODELO_EMB, device="cpu")
    modelo.max_seq_length = 128
    n = (len(textos) + tramo - 1) // tramo
    t0 = time.time()
    hechos = 0
    for i in range(n):
        ruta = TRAMOS / f"tramo_{i:03d}.npy"
        if ruta.exists():
            continue
        e = modelo.encode(textos[i * tramo:(i + 1) * tramo], batch_size=64, normalize_embeddings=True,
                          show_progress_bar=False)
        np.save(ruta, e.astype(np.float32))
        hechos += 1
        ritmo = hechos * tramo / (time.time() - t0)
        resto = (n - i - 1) * tramo / ritmo / 60
        print(f"  tramo {i + 1}/{n}  {ritmo:.0f} notas/s  faltan ~{resto:.0f} min", flush=True)
    e = np.vstack([np.load(TRAMOS / f"tramo_{i:03d}.npy") for i in range(n)])
    assert len(e) == len(textos)
    np.save(EMB, e)
    print(f"Embeddings: {e.shape} -> {EMB}")


# ---------------------------------------------------------------- modelar
MESES = "enero febrero marzo abril mayo junio julio agosto septiembre setiembre octubre noviembre diciembre"
DIAS = "lunes martes miércoles jueves viernes sábado domingo"
# verbos de cita y muletillas propias de los comunicados, que no distinguen temas
PRENSA = ("señaló indicó afirmó explicó destacó agregó sostuvo comentó manifestó aseguró dijo precisó enfatizó "
          "añadió expresó recalcó puntualizó relevó valoró informó detalló subrayó declaró planteó mencionó "
          "además través así solo sólo hoy ayer año años día días mes meses semana semanas ser hacer puede "
          "pueden cada gran mismo misma parte forma manera tras según junto mediante ello ahora mientras "
          "cual cuales cuál cuáles también tan gracias importante nuevo nueva nuevos nuevas")
UMBRAL_FD = 0.25        # palabras presentes en más de 1 de cada 4 notas


def palabras_vacias(textos) -> list[str]:
    from sklearn.feature_extraction.text import CountVectorizer
    base = [l.strip() for l in open(Path(__file__).with_name("stopwords_es.txt"), encoding="utf-8")
            if l.strip() and not l.startswith("#")]
    propias = set(f"{MESES} {DIAS} {PRENSA}".split())
    cv = CountVectorizer(stop_words=base, token_pattern=r"(?u)\b[^\W\d_]{3,}\b", binary=True, min_df=50)
    x = cv.fit_transform(textos)
    fd = np.asarray(x.sum(0)).ravel() / x.shape[0]
    frecuentes = {w for w, f in zip(cv.get_feature_names_out(), fd) if f > UMBRAL_FD}
    print(f"Palabras vacías: {len(base)} de la lista base, {len(propias)} propias de la prensa, "
          f"{len(frecuentes - propias)} por frecuencia (> {UMBRAL_FD:.0%} de las notas):")
    print("  ", " ".join(sorted(frecuentes - propias)))
    return sorted(set(base) | propias | frecuentes)


class Precalculado:
    """Reducción UMAP ya calculada (se guarda en disco para no repetirla al ajustar HDBSCAN)."""

    def __init__(self, reducido):
        self.reducido = reducido

    def fit(self, X, y=None):
        return self

    def transform(self, X):
        assert len(X) == len(self.reducido)
        return self.reducido


def reducir(emb):
    from umap import UMAP
    ruta = SALIDA / "umap_5d.npy"
    if ruta.exists() and len(r := np.load(ruta)) == len(emb):
        return r
    r = UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine",
             random_state=SEMILLA, low_memory=True).fit_transform(emb)
    np.save(ruta, r)
    return r


def modelar(min_cluster=40, min_samples=10, percentil_ruido=25):
    from bertopic import BERTopic
    from bertopic.vectorizers import ClassTfidfTransformer
    from hdbscan import HDBSCAN
    from sklearn.feature_extraction.text import CountVectorizer

    d = pd.read_parquet(DOCS, columns=["texto_rep"])
    emb = np.load(EMB)
    docs = d.texto_rep.tolist()
    sw = palabras_vacias(docs)
    modelo = BERTopic(
        language="multilingual",   # con "english" BERTopic borra tildes y eñes antes de contar palabras
        umap_model=Precalculado(reducir(emb)),
        # "leaf" da tópicos más finos; con "eom" algunos ministerios caen casi enteros en un solo tópico
        hdbscan_model=HDBSCAN(min_cluster_size=min_cluster, min_samples=min_samples, metric="euclidean",
                              cluster_selection_method="leaf", prediction_data=True),
        vectorizer_model=CountVectorizer(stop_words=sw, ngram_range=(1, 2), min_df=10,
                                         token_pattern=r"(?u)\b[^\W\d_]{3,}\b"),
        ctfidf_model=ClassTfidfTransformer(reduce_frequent_words=True),
        top_n_words=12, calculate_probabilities=False, verbose=True,
    )
    t0 = time.time()
    topicos, _ = modelo.fit_transform(docs, emb)
    topicos = np.array(topicos)
    print(f"Ajuste en {(time.time() - t0) / 60:.1f} min")

    # Notas sin tópico: se asignan al tópico más parecido solo si se parecen a él tanto como las
    # notas propias del tópico (umbral = percentil de la similitud de las notas asignadas a su tópico).
    te = modelo.topic_embeddings_[1:] if -1 in topicos else modelo.topic_embeddings_
    te = te / np.linalg.norm(te, axis=1, keepdims=True)
    dentro = topicos >= 0
    sim_propia = (emb[dentro] * te[topicos[dentro]]).sum(1)
    umbral = float(np.percentile(sim_propia, percentil_ruido))
    sim = emb[~dentro] @ te.T
    ext = topicos.copy()
    ext[~dentro] = np.where(sim.max(1) >= umbral, sim.argmax(1), -1)
    print(f"Umbral de similitud para reasignar: {umbral:.3f} (percentil {percentil_ruido} de las notas asignadas)")

    pd.DataFrame({"topico": topicos, "topico_ext": ext}).to_parquet(SALIDA / "topicos.parquet", index=False)
    info = modelo.get_topic_info()
    info["palabras"] = [", ".join(w for w, _ in modelo.get_topic(t)) for t in info.Topic]
    info["notas_ext"] = info.Topic.map(pd.Series(ext).value_counts())
    info[["Topic", "Count", "notas_ext", "Name", "palabras"]].to_csv(SALIDA / "topicos_info.csv", index=False,
                                                                       encoding="utf-8")
    modelo.save(SALIDA / "modelo", serialization="safetensors", save_ctfidf=True, save_embedding_model=MODELO_EMB)
    n = len(docs)
    print(f"{len(info) - 1} tópicos; sin tópico: {(topicos == -1).sum():,} notas ({100 * (topicos == -1).mean():.1f} %), "
          f"tras reasignar: {(ext == -1).sum():,} ({100 * (ext == -1).mean():.1f} %)")


# ---------------------------------------------------------------- analizar
# notas que nombran la IA (la sigla solo en mayúsculas y sin puntos, para no confundirla con iniciales)
RE_IA = re.compile(
    r"(?i:inteligencia\s+artificial|machine\s+learning|deep\s+learning|redes\s+neuronales|chat\s?gpt|chatbots?"
    r"|aprendizaje\s+(?:autom[aá]tico|de\s+m[aá]quinas?|profundo)|modelos?\s+(?:grandes?\s+)?de\s+lenguaje)"
    r"|(?<![\w.])IA(?!\w|\.\w)|\bLLMs?\b")
DESDE, ANTES, RECIENTE = 2014, (2018, 2022), (2023, 2026)


def etiqueta(palabras: str, n=4) -> str:
    return " · ".join(palabras.split(", ")[:n])


PARTICULAS = {"de", "del", "la", "las", "los", "van", "von", "san", "santa", "y"}
# apellidos de ministros que también son palabras comunes o nombres de otra cosa en las etiquetas
APELLIDOS_AMBIGUOS = {"mesa", "iglesias", "ríos", "paris", "guzmán"}
# presidentes y subsecretarios que encabezan tópicos y no están en la base de ministros
APELLIDOS_EXTRA = {"bachelet", "piñera", "boric", "kast", "bolívar", "salaberry"}


def apellidos_autoridades() -> set[str]:
    """Apellidos (dos últimas palabras del nombre) de ministros y ministras 2006-2026."""
    m = pd.read_csv(DATA / "ministros_2006_2026.csv", dtype=str)
    ap = set(APELLIDOS_EXTRA)
    for n in m.nombre.dropna():
        t = [w for w in re.findall(r"[^\W\d_]+", n.lower()) if w not in PARTICULAS]
        ap.update(w for w in t[-2:] if len(w) >= 3)
    return ap - APELLIDOS_AMBIGUOS


def es_agenda(palabras: str, apellidos: set[str]) -> bool:
    """Tópico organizado en torno a una autoridad: su primera palabra clave, o dos de las
    cuatro primeras, llevan el apellido de un ministro o subsecretario."""
    top = palabras.split(", ")[:4]
    con = [bool(set(t.split()) & apellidos) for t in top]
    return con[0] or sum(con) >= 2


def participacion(d):
    """Notas de cada tópico por mil notas del año, en bruto y pesando igual a cada ministerio
    (promedio de la participación del tópico dentro de cada ministerio con notas ese año)."""
    cruda = pd.crosstab(d.topico, d.anio).div(d.groupby("anio").size(), axis=1) * 1000
    por_min = pd.crosstab([d.ministerio, d.topico], d.anio)
    por_min = por_min.reindex(pd.MultiIndex.from_product(  # ceros explícitos para los tópicos ausentes
        [sorted(d.ministerio.unique()), sorted(d.topico.unique())]), fill_value=0)
    # años sin notas del ministerio quedan como NaN y no entran al promedio
    por_min = por_min.div(por_min.groupby(level=0).transform("sum").replace(0, np.nan))
    pareja = por_min.groupby(level=1).mean() * 1000
    return cruda.drop(index=-1, errors="ignore"), pareja.drop(index=-1, errors="ignore")


def analizar():
    d = pd.read_parquet(DOCS, columns=["fecha", "anio", "ministerio", "titulo", "texto_rep"])
    asign = pd.read_parquet(SALIDA / "topicos.parquet")
    d["topico"] = asign.topico_ext.values      # incluye las notas reasignadas por similitud
    info = pd.read_csv(SALIDA / "topicos_info.csv").set_index("Topic")
    info["etiqueta"] = info.palabras.map(etiqueta)
    apellidos = apellidos_autoridades()
    info["tipo"] = np.where(info.palabras.map(lambda p: es_agenda(p, apellidos)), "agenda de autoridad", "tema")
    info.loc[-1, "tipo"] = "sin tópico"
    print("Tópicos de agenda de autoridad:", (info.tipo == "agenda de autoridad").sum())
    for t, r in info[info.tipo == "agenda de autoridad"].iterrows():
        print(f"   {t:4} {r.notas_ext:5}  {r.etiqueta}")
    d["ia"] = d.texto_rep.map(lambda t: bool(RE_IA.search(t)))
    d = d[d.anio >= DESDE]

    por_anio = d.groupby("anio").size()
    print("Notas por año:", " ".join(f"{a}:{n}" for a, n in por_anio.items()))
    print(f"Sin tópico: {100 * (d.topico == -1).mean():.1f} %")

    cruda, pareja = participacion(d)
    cruda.round(2).assign(etiqueta=info.etiqueta).to_csv(SALIDA / "topicos_por_anio.csv", encoding="utf-8")

    def media(t, a, b):
        return t.loc[:, a:b].mean(axis=1)

    em = pd.DataFrame({
        "etiqueta": info.etiqueta.reindex(cruda.index), "tipo": info.tipo.reindex(cruda.index),
        "notas": info.notas_ext.reindex(cruda.index),
        "antes": media(cruda, *ANTES), "reciente": media(cruda, *RECIENTE),
        "antes_pareja": media(pareja, *ANTES), "reciente_pareja": media(pareja, *RECIENTE),
        "pct_ia": d[d.topico >= 0].groupby("topico").ia.mean().mul(100).round(1),
        "ministerio_principal": d[d.topico >= 0].groupby("topico").ministerio.agg(lambda s: s.value_counts().index[0]),
    })
    em["razon"] = (em.reciente + 0.5) / (em.antes + 0.5)
    em["razon_pareja"] = (em.reciente_pareja + 0.5) / (em.antes_pareja + 0.5)
    em.round(2).to_csv(SALIDA / "topicos_emergentes.csv", encoding="utf-8")
    cols = ["etiqueta", "notas", "antes", "reciente", "razon", "razon_pareja", "pct_ia", "ministerio_principal"]
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 60)
    print(f"\nTópicos que más crecen ({RECIENTE[0]}-{RECIENTE[1]} contra {ANTES[0]}-{ANTES[1]}, por mil notas):")
    temas = em[em.tipo == "tema"]
    print(temas[temas.reciente >= 2].sort_values("razon", ascending=False).head(30)[cols].round(1).to_string())
    print("\nTópicos que más caen:")
    print(temas[temas.antes >= 2].sort_values("razon").head(15)[cols].round(1).to_string())
    print("\nTópicos con más notas sobre IA (>= 20 % de sus notas):")
    ia = em[em.pct_ia >= 20].sort_values("pct_ia", ascending=False)
    print(ia[cols].round(1).to_string())
    for t in ia.index:
        print(f"\n  Tópico {t} por año (por mil):", " ".join(f"{a}:{cruda.loc[t, a]:.1f}" for a in cruda.columns))
        print("  ", d[d.topico == t].groupby("ministerio").size().sort_values(ascending=False).head(8).to_dict())
    # en qué tópicos caen las notas que nombran la IA
    dist = d[d.ia].topico.value_counts()
    print(f"\nNotas que nombran la IA: {d.ia.sum():,}; en tópico propio de IA: "
          f"{d[d.ia & d.topico.isin(ia.index)].shape[0]:,}; sin tópico: {dist.get(-1, 0):,}")
    print("Otros tópicos donde aparece:",
          "; ".join(f"{info.etiqueta[t]} ({n})" for t, n in dist.drop(index=[-1, *ia.index], errors="ignore").head(10).items()))

    # perfil de cada ministerio: sus tópicos principales
    filas = []
    for m, g in d.groupby("ministerio"):
        vc = g[g.topico >= 0].topico.value_counts()
        for t, n in vc.head(6).items():
            filas.append({"ministerio": m, "topico": t, "pct": round(100 * n / len(g), 1),
                          "etiqueta": ("[autoridad] " if info.tipo[t] != "tema" else "") + info.etiqueta[t]})
    perfil = pd.DataFrame(filas)
    perfil.to_csv(SALIDA / "perfil_ministerios.csv", index=False, encoding="utf-8")
    d["tipo"] = d.topico.map(info.tipo)
    ag = d.tipo == "agenda de autoridad"
    print(f"\nNotas en tópicos de agenda de autoridad: {100 * ag.mean():.1f} % del total; por año:",
          " ".join(f"{a}:{100 * v:.0f}%" for a, v in ag.groupby(d.anio).mean().items()))
    print("Por ministerio:", " | ".join(f"{m} {100 * v:.0f} %" for m, v in
                                         ag.groupby(d.ministerio).mean().sort_values(ascending=False).items()))
    print("\nPerfil de cada ministerio (% de sus notas):")
    for m, g in perfil.groupby("ministerio"):
        print(f"  {m}: " + " | ".join(f"{r.etiqueta} {r.pct}" for r in g.itertuples()))


# ---------------------------------------------------------------- figuras
FIGURAS = RAIZ / "figuras"
SUP, TINTA, TINTA2, MUTED, EJE = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#c3c2b7"
# escala azul por tramos (por mil notas del año); bajo el primer tramo la celda queda vacía
TRAMOS_FIG = [(0.25, "#cde2fb"), (1, "#9ec5f4"), (2, "#6da7ec"), (4, "#3987e5"), (8, "#256abf"),
              (16, "#184f95"), (32, "#0d366b")]


def _color(v):
    c = None
    for corte, col in TRAMOS_FIG:
        if v >= corte:
            c = col
    return c


def figuras():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.transforms import blended_transform_factory

    em = pd.read_csv(SALIDA / "topicos_emergentes.csv", index_col=0)
    anual = pd.read_csv(SALIDA / "topicos_por_anio.csv", index_col=0).drop(columns="etiqueta")
    anual.columns = anual.columns.astype(int)
    temas = em[em.tipo == "tema"]
    ia = temas[(temas.pct_ia >= 50) | temas.etiqueta.str.startswith("transformación digital")
               | temas.etiqueta.str.startswith("digital")].sort_values("pct_ia", ascending=False)
    suben = temas[(temas.reciente >= 2) & ~temas.index.isin(ia.index)].sort_values("razon", ascending=False).head(14)
    bajan = temas[temas.antes >= 2].sort_values("razon").head(6)
    bloques = [("Inteligencia artificial y transformación digital", ia),
               (f"Los que más crecen ({RECIENTE[0]}–{RECIENTE[1]} contra {ANTES[0]}–{ANTES[1]})", suben),
               ("Los que más caen", bajan)]
    anios = list(range(DESDE, 2027))

    filas = []
    for titulo, b in bloques:
        filas.append(("titulo", titulo, None))
        for t, r in b.iterrows():
            filas.append(("fila", r, anual.loc[t]))
    alto = 0.34 * len(filas) + 1.9
    fig = plt.figure(figsize=(12, alto), dpi=120, facecolor=SUP)
    ax = fig.add_axes([0.40, 0.6 / alto, 0.40, 1 - 1.75 / alto])
    izq = blended_transform_factory(fig.transFigure, ax.transData)
    ax.set_facecolor(SUP)
    ax.set_xlim(0, len(anios))
    ax.set_ylim(len(filas), -0.6)
    ax.axis("off")
    for i, (tipo, r, serie) in enumerate(filas):
        if tipo == "titulo":
            ax.text(0.015, i + 0.62, r, fontsize=9.5, fontweight="semibold", color=TINTA, va="center",
                    transform=izq, clip_on=False)
            continue
        ax.text(-0.25, i + 0.5, " · ".join(r.etiqueta.split(" · ")[:3]), ha="right", va="center", fontsize=8.6,
                color=TINTA, clip_on=False)
        ax.text(len(anios) + 0.25, i + 0.5, f"{r.ministerio_principal}", ha="left", va="center", fontsize=7.6,
                color=MUTED, clip_on=False)
        for j, a in enumerate(anios):
            c = _color(serie.get(a, 0))
            if c:   # 2 px de separación entre celdas (borde del color de la superficie)
                ax.add_patch(Rectangle((j, i + 0.08), 1, 0.84, facecolor=c, edgecolor=SUP, linewidth=1.6))
    for j, a in enumerate(anios):
        ax.text(j + 0.5, -0.25, str(a)[2:] if j else str(a), ha="center", va="bottom", fontsize=7.6, color=MUTED)
    ax.text(len(anios) + 0.25, -0.25, "ministerio principal", ha="left", va="bottom", fontsize=7.6, color=MUTED)
    fig.text(0.015, 1 - 0.32 / alto, "Tópicos de la prensa ministerial que aparecen y desaparecen, 2014–2026",
             fontsize=12.5, fontweight="semibold", color=TINTA, va="top")
    fig.text(0.015, 1 - 0.62 / alto, "Notas de cada tópico por cada mil notas ministeriales del año. Tópicos de "
             "BERTopic sobre 91.753 notas de 25 ministerios y sus subsecretarías; se omiten los centrados en una autoridad.",
             fontsize=8.4, color=TINTA2, va="top")
    # leyenda de la escala
    x0 = 0.015
    fig.text(x0, 0.22 / alto, "Por mil notas:", fontsize=7.8, color=TINTA2, va="center")
    for k, (corte, col) in enumerate(TRAMOS_FIG):
        xx = x0 + 0.075 + k * 0.052
        fig.patches.append(Rectangle((xx, 0.13 / alto), 0.014, 0.18 / alto, transform=fig.transFigure,
                                     facecolor=col, edgecolor="none"))
        txt = f"{corte:g}".replace(".", ",")
        fig.text(xx + 0.017, 0.22 / alto, txt + "+" if k == len(TRAMOS_FIG) - 1 else txt,
                 fontsize=7.6, color=TINTA2, va="center")
    fig.savefig(FIGURAS / "topicos_ministerios.png", facecolor=SUP)
    plt.close(fig)

    # proporción de notas en tópicos centrados en una autoridad, por ministerio
    d = pd.read_parquet(DOCS, columns=["anio", "ministerio"])
    d["topico"] = pd.read_parquet(SALIDA / "topicos.parquet").topico_ext.values
    tipo = em.tipo.reindex(d.topico).values
    d["agenda"] = tipo == "agenda de autoridad"
    p = d[d.anio >= DESDE].groupby("ministerio").agenda.mean().mul(100).sort_values()
    fig, ax = plt.subplots(figsize=(8.6, 7.2), dpi=120, facecolor=SUP)
    ax.set_facecolor(SUP)
    ax.barh(range(len(p)), p.values, height=0.62, color="#2a78d6", zorder=3)
    for i, v in enumerate(p.values):
        ax.text(v + 0.6, i, f"{v:.0f} %", va="center", fontsize=8, color=TINTA2)
    ax.set_yticks(range(len(p)), p.index, fontsize=8.6, color=TINTA)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", colors=MUTED, labelsize=8, length=0)
    ax.xaxis.grid(True, color="#e1e0d9", linewidth=0.7)
    ax.set_axisbelow(True)
    for s in ("top", "right", "bottom"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(EJE)
    ax.set_xlim(0, max(p.max() * 1.12, 10))
    fig.suptitle("Notas ministeriales organizadas en torno a una autoridad", x=0.02, ha="left", y=0.975,
                 fontsize=12, fontweight="semibold", color=TINTA)
    fig.text(0.02, 0.925, "Porcentaje de las notas de cada ministerio (2014–2026) que caen en tópicos encabezados por el "
             "apellido\nde un ministro, ministra o subsecretario (agenda, reuniones, visitas).",
             fontsize=8.4, color=TINTA2, va="top")
    fig.subplots_adjust(left=0.33, right=0.96, top=0.86, bottom=0.05)
    fig.savefig(FIGURAS / "agenda_autoridades.png", facecolor=SUP)
    plt.close(fig)
    print("Figuras:", FIGURAS / "topicos_ministerios.png", FIGURAS / "agenda_autoridades.png")


# ---------------------------------------------------------------- explorador
EXPLORADOR = SALIDA / "explorador"
PLANTILLA = Path(__file__).resolve().parent / "explorador_topicos.html"
MARCA_DATOS = '<script id="datos" type="application/json"></script>'


def explorador(n_rep=5, n_rec=5):
    """Datos del explorador HTML de tópicos. Escribe explorador/datos.json y explorador/index.html
    (la plantilla analisis/explorador_topicos.html con los datos incrustados, para abrirla sin servidor)."""
    import json
    from umap import UMAP

    d = pd.read_parquet(DOCS, columns=["fecha", "anio", "organismo", "ministerio", "titulo", "url", "texto_rep"])
    asign = pd.read_parquet(SALIDA / "topicos.parquet")
    d["topico"] = asign.topico_ext.values
    d["ia"] = d.texto_rep.map(lambda t: bool(RE_IA.search(t)))
    d = d.drop(columns="texto_rep")
    emb = np.load(EMB)
    info = pd.read_csv(SALIDA / "topicos_info.csv").set_index("Topic")
    em = pd.read_csv(SALIDA / "topicos_emergentes.csv", index_col=0)
    reciente = d.anio >= DESDE
    dr = d[reciente]
    cruda, pareja = participacion(dr)
    anios = sorted(dr.anio.unique().tolist())
    cruda, pareja = cruda.reindex(columns=anios, fill_value=0), pareja.reindex(columns=anios)

    ministerios = sorted(d.ministerio.unique())
    im = {m: i for i, m in enumerate(ministerios)}
    organismos = sorted(d.organismo.unique())
    io = {o: i for i, o in enumerate(organismos)}

    def nota(i):
        r = d.iloc[i]
        return [r.fecha, io[r.organismo], str(r.titulo).strip(), r.url]

    # centroide de cada tópico: base del mapa y de las notas más representativas
    ids = [int(t) for t in em.index if t >= 0]
    centros = np.vstack([emb[d.topico.values == t].mean(0) for t in ids])
    centros /= np.linalg.norm(centros, axis=1, keepdims=True)
    xy = UMAP(n_neighbors=12, n_components=2, min_dist=0.4, metric="cosine",
              random_state=SEMILLA).fit_transform(centros)
    xy = (xy - xy.min(0)) / (xy.max(0) - xy.min(0))

    topicos = []
    for k, t in enumerate(ids):
        pos = np.flatnonzero(d.topico.values == t)
        sim = emb[pos] @ centros[k]
        rep = pos[np.argsort(-sim)[:n_rep]]
        rec = [i for i in pos[np.argsort(d.fecha.values[pos])[::-1]] if i not in set(rep)][:n_rec]
        r = em.loc[t]
        g = d.iloc[pos]
        gr = g[g.anio >= DESDE]
        # notas de cada ministerio en el tópico: 2014-2026, período anterior y reciente
        pm = pd.DataFrame({"n": gr.ministerio.value_counts(),
                           "a": gr[gr.anio.between(*ANTES)].ministerio.value_counts(),
                           "r": gr[gr.anio.between(*RECIENTE)].ministerio.value_counts()}).fillna(0).astype(int)
        topicos.append({
            "id": t, "palabras": info.palabras[t].split(", "),
            "tipo": "autoridad" if r.tipo == "agenda de autoridad" else "tema",
            "notas": int(len(pos)), "ia": int(g.ia.sum()), "pct_ia": float(r.pct_ia),
            "x": round(float(xy[k, 0]), 4), "y": round(float(xy[k, 1]), 4),
            "antes": float(r.antes), "reciente": float(r.reciente),
            "razon": float(r.razon), "razon_pareja": float(r.razon_pareja),
            "serie": [round(float(v), 2) for v in cruda.loc[t]] if t in cruda.index else [0] * len(anios),
            "pareja": [None if pd.isna(v) else round(float(v), 2) for v in pareja.loc[t]]
                      if t in pareja.index else [0] * len(anios),
            "ministerios": [[im[m], int(n)] for m, n in g.ministerio.value_counts().items()],
            "por_periodo": [[im[m], *map(int, f)] for m, f in pm.sort_values("n", ascending=False).iterrows()],
            "representativas": [nota(i) for i in rep], "recientes": [nota(i) for i in rec],
        })

    tipo = em.tipo.reindex(dr.topico).values
    agenda = pd.Series(tipo == "agenda de autoridad", index=dr.index)
    mins = []
    for m in ministerios:
        g = dr[dr.ministerio == m]
        vc = g[g.topico >= 0].topico.value_counts()
        mins.append({
            "nombre": m, "notas": int(len(g)), "ia": int(g.ia.sum()),
            "agenda": round(100 * float(agenda[g.index].mean()), 1),
            "por_anio": [int((g.anio == a).sum()) for a in anios],
            "topicos": [[int(t), round(100 * n / len(g), 1)] for t, n in vc.head(10).items()],
            "sin_topico": [int((g.topico == -1).sum()), int(((g.topico == -1) & g.anio.between(*ANTES)).sum()),
                           int(((g.topico == -1) & g.anio.between(*RECIENTE)).sum())],
        })
    ia_top = dr[dr.ia].topico.value_counts()
    datos = {
        "anios": anios, "desde": DESDE, "antes": list(ANTES), "reciente": list(RECIENTE),
        "notas_total": int(len(d)), "notas": int(len(dr)),
        "notas_por_anio": [int((dr.anio == a).sum()) for a in anios],
        "sin_topico": round(100 * float((dr.topico == -1).mean()), 1),
        "sin_topico_hdbscan": round(100 * float((asign.topico == -1).mean()), 1),
        "agenda_por_anio": [round(100 * float(agenda[dr.anio == a].mean()), 1) for a in anios],
        "ia_por_anio": [int((dr.ia & (dr.anio == a)).sum()) for a in anios],
        "ia_por_topico": [[int(t), int(n)] for t, n in ia_top.items()],
        "ministerios": mins, "organismos": organismos, "topicos": topicos,
    }
    EXPLORADOR.mkdir(exist_ok=True)
    texto = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
    (EXPLORADOR / "datos.json").write_text(texto, encoding="utf-8")
    plantilla = PLANTILLA.read_text(encoding="utf-8")
    html = plantilla.replace(MARCA_DATOS, MARCA_DATOS.replace("></", ">" + texto.replace("</", "<\\/") + "</"))
    (EXPLORADOR / "index.html").write_text(
        '<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>\n<body>\n'
        + html + "\n</body>\n</html>\n", encoding="utf-8")
    print(f"{len(topicos)} tópicos, {len(texto) / 1e6:.1f} MB de datos:", EXPLORADOR / "index.html")


if __name__ == "__main__":
    etapas = {"preparar": preparar, "embeber": embeber, "modelar": modelar, "analizar": analizar,
              "figuras": figuras, "explorador": explorador}
    if len(sys.argv) != 2 or sys.argv[1] not in etapas:
        sys.exit(__doc__)
    etapas[sys.argv[1]]()
