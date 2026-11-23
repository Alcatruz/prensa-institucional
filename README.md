# Prensa institucional

Extracción de las notas de prensa que publican en sus sitios web distintos organismos del Estado de Chile: Presidencia, ministerios, subsecretarías, servicios públicos, organismos autónomos y el Congreso Nacional. Cada scraper recorre el listado de noticias de la sala de prensa y entra a cada nota para obtener el texto completo.

![Cobertura temporal del corpus](figuras/cobertura.gif)

## Cobertura

Estado del corpus a la última extracción (noviembre de 2026; la Cámara, abril de 2026). Notas por gobierno, con el cambio de mando el 11 de marzo:

<!-- tabla: python scrapers/cobertura_por_gobierno.py --markdown -->
| Organismo | Lagos | Bachelet I | Piñera I | Bachelet II | Piñera II | Boric | Kast | Total | Desde | Hasta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| **Ejecutivo** | | | | | | | | | | |
| Presidencia (comunicados) | – | – | – | 30 | 699 | 611 | 463 | 1.803 | feb 2016 | nov 2026 |
| Presidencia (discursos) | – | – | – | 2 | 388 | 296 | 258 | 944 | mar 2015 | nov 2026 |
| Presidencia (gob.cl, por título) | – | – | – | 2.289 | 960 | 288 | 56 | 3.593 | mar 2014 | oct 2026 |
| Interior | – | – | – | – | 153 | 590 | 222 | 965 | mar 2018 | nov 2026 |
| Seguridad Pública | – | – | – | – | – | 122 | 55 | 177 | mar 2025 | nov 2026 |
| Relaciones Exteriores | – | – | – | 73 | 1.710 | 2.468 | 601 | 4.852 | ene 2017 | nov 2026 |
| Obras Públicas | – | – | – | 123 | 671 | 811 | 124 | 1.729 | ago 2017 | nov 2026 |
| Medio Ambiente | – | – | – | 2.011 | 760 | 894 | 227 | 3.892 | mar 2014 | nov 2026 |
| Agricultura | – | – | – | – | 1.940 | 2.194 | 289 | 4.425 | may 2018 | nov 2026 |
| Servicio Agrícola y Ganadero (SAG) | – | 214 | 733 | 1.022 | 696 | 886 | 137 | 3.688 | ene 2009 | nov 2026 |
| Instituto de Desarrollo Agropecuario (INDAP) | – | – | 1.626 | 4.344 | 3.534 | 2.846 | 468 | 12.818 | ago 2012 | nov 2026 |
| Educación | – | – | – | 922 | 677 | 1.189 | 87 | 2.875 | jul 2015 | nov 2026 |
| Junaeb | – | – | – | – | 12 | 550 | 81 | 643 | nov 2021 | nov 2026 |
| Desarrollo Social y Familia | – | – | – | – | 1.940 | 2.193 | 255 | 4.388 | ene 2020 | nov 2026 |
| Energía | – | – | – | 1.272 | 2.807 | 2.940 | 637 | 7.656 | abr 2014 | nov 2026 |
| Superintendencia de Electricidad y Combustibles (SEC) | – | – | 271 | 300 | 534 | 535 | 32 | 1.672 | ene 2011 | nov 2026 |
| Subsecretaría de Telecomunicaciones (SUBTEL) | – | 24 | 304 | 706 | 456 | 213 | 50 | 1.763 | jul 2006 | nov 2026 |
| **Otros organismos** | | | | | | | | | | |
| Defensoría Penal Pública | 2 | 1.959 | 2.765 | 2.955 | 3.677 | 3.684 | 712 | 15.754 | ene 2006 | nov 2026 |
| Consejo para la Transparencia | – | 100 | 438 | 335 | 764 | 349 | 40 | 2.027 | abr 2008 | nov 2026 |
| **Congreso** | | | | | | | | | | |
| Senado | – | 31 | 4.736 | 4.312 | 4.053 | 3.532 | 690 | 17.354 | feb 2010 | nov 2026 |
| Cámara de Diputadas y Diputados | – | – | – | 464 | 5.353 | 4.457 | 59 | 10.333 | sep 2017 | abr 2026 |
<!-- fin tabla -->

Presidencia se arma con tres fuentes. El sitio actual (presidencia.gob.cl) solo tiene lo publicado desde marzo de 2026, así que los comunicados y discursos anteriores salen del archivo de prensa.presidencia.cl en la Wayback Machine, que es parcial. Para Bachelet II y Piñera II se agregan las notas de gob.cl cuyo título menciona al Presidente o la Presidenta (Mandatario/a, Jefe/a de Estado, Primera Dama, La Moneda); es un filtro por título y por eso queda en un archivo aparte, con la columna `criterio`. Los discursos y la selección de gob.cl no entran al consolidado.

Los datos no se incluyen en el repositorio. Se generan localmente corriendo los scrapers.

## Formato de salida

Cada scraper escribe `data/<organismo>.csv` en UTF-8, separado por comas, con los campos que contienen comas o saltos de línea entre comillas. Las columnas son siempre las mismas y en este orden:

| Columna | Contenido |
|---|---|
| `fecha` | Fecha de publicación (`AAAA-MM-DD`) |
| `institucion` | Nombre del organismo emisor |
| `titulo` | Título de la nota |
| `resumen` | Bajada o extracto que aparece en el listado |
| `url` | Enlace a la nota original |
| `contenido` | Texto completo de la nota |

## Uso

Requiere Python 3.10 o superior.

```bash
python -m venv .venv
source .venv/bin/activate          # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### Actualizar todo

```bash
python scrapers/run_todos_paralelo.py --jobs 3
python scrapers/consolidar_prensa.py
python scrapers/cobertura_por_gobierno.py
python figuras/animacion_cobertura.py
```

El orquestador corre todos los scrapers en paralelo (los registros quedan en `logs/`). Antes de cada uno copia su CSV a `data/_respaldo_<fecha>/` y al terminar une ambas versiones por URL, de modo que no se pierden las notas que un sitio deja de mostrar ni lo ya descargado si un scraper falla. La mayoría de los scrapers omite las notas que ya están en el CSV; el del Senado recorre las 200 páginas más recientes de su listado (1.800 notas). Para correr solo algunos:

```bash
python scrapers/run_todos_paralelo.py --only mma minrel
```

`consolidar_prensa.py` une en `data/prensa_institucional_consolidado.csv` y `.xlsx` los CSV del Ejecutivo y los otros organismos públicos (sin el Congreso). `cobertura_por_gobierno.py` escribe `data/cobertura_por_gobierno.csv`; con `--markdown` imprime la tabla de arriba.

### Archivo histórico de Presidencia

No forma parte de la actualización periódica. Se corre una vez y es reanudable:

```bash
python scrapers/scraper_presidencia.py --fuente wayback   # comunicados y discursos 2016-2026 (lento: la Wayback limita las consultas)
python scrapers/scraper_presidencia.py --fuente gobcl     # notas presidenciales de gob.cl desde 2014
python scrapers/scraper_presidencia.py --resumen          # notas por gobierno
```

### Ministros y ministras 2006-2026

```bash
python scrapers/ministros_gabinete.py
```

Escribe `data/ministros_2006_2026.csv` con una fila por persona y cargo: gobierno, ministerio (nombre oficial y normalizado), nombre, fechas de inicio y término, partido, género y tipo de nombramiento (titular, subrogante o interino). Incluye a quienes encabezaron SUBTEL. La fuente son los anexos de gabinetes de Wikipedia en español, contrastados con la base de gabinetes chilenos de González-Bustamante y Olivares (1990-2014); `--variables-gbo` agrega sus variables de perfil para 2006-2014. El género se codifica según el título del cargo que usa la fuente (ministra o ministro), nunca a partir del nombre.

### Recuperar un respaldo

Si un scraper se corrió fuera del orquestador y reescribió su CSV con menos notas:

```bash
python scrapers/fusionar_respaldo.py data/_respaldo_AAAA-MM-DD
python scrapers/fusionar_respaldo.py data/_respaldo_AAAA-MM-DD --solo interior
```

Los scrapers incluyen pausas entre solicitudes. Úsalos con moderación y respetando las condiciones de cada sitio.

## Estructura

```
scrapers/
  scraper_<organismo>.py      un scraper por sala de prensa
  run_todos_paralelo.py       corre los scrapers en paralelo, con respaldo y unión por URL
  fusionar_respaldo.py        une un CSV con su respaldo (por URL)
  consolidar_prensa.py        une los CSV en un solo archivo (CSV y XLSX)
  cobertura_por_gobierno.py   notas por organismo y gobierno
  ministros_gabinete.py       base de ministros y ministras 2006-2026
figuras/
  animacion_cobertura.py      genera cobertura.gif y cobertura.png
data/                         salida de los scrapers (no versionada)
```

## Limitaciones conocidas

- El corpus contiene lo que cada organismo mantiene publicado. Los sitios se rediseñan y pierden archivo, así que la fecha de inicio indica lo disponible y no el comienzo de la actividad de prensa. Interior y Agricultura ya dejaron de mostrar notas que sí están en extracciones anteriores; una extracción desde cero tendrá menos.
- Interior no tiene notas entre 2019 y 2021.
- Seguridad Pública parte en marzo de 2025, con la creación del ministerio, y Junaeb en noviembre de 2021.
- SUBTEL no tiene notas en 2008 y 2009, y 10 notas no tienen fecha.
- Energía y Relaciones Exteriores tienen notas aisladas antes de su cobertura regular, que empieza en 2016 y 2018, respectivamente.
- Unas 450 notas de Energía y 240 de Educación no tienen el texto completo.
- Una nota del Consejo para la Transparencia no tiene fecha: el sitio la publica con el año 0201.
- El archivo de Presidencia en la Wayback Machine es parcial, sobre todo antes de 2018, y la selección de gob.cl depende del título: puede incluir notas de gabinete que nombran al Presidente y omitir actividades presidenciales con otro título. Unas 370 notas de gob.cl también están entre los comunicados, con otra URL.
- Desde noviembre de 2026 el sitio de la Cámara responde con una verificación anti-bots que bloquea la API, por lo que sus notas llegan a abril de 2026.

## Licencia

Código bajo licencia MIT. El contenido de las notas pertenece a cada organismo.
