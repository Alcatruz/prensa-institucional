# Prensa institucional

Extracción de las notas de prensa que publican en sus sitios web los organismos del Estado de Chile: Presidencia, ministerios y subsecretarías, servicios públicos, gobiernos regionales y delegaciones presidenciales, Fuerzas Armadas, empresas públicas, universidades estatales, organismos autónomos y el Congreso Nacional. Cada scraper recorre la sala de prensa del organismo y obtiene el texto completo de cada nota.

![Notas por año y organismo](figuras/cobertura.png)

## Cobertura

Estado a la última extracción (24 de noviembre de 2026): 473.079 notas de 154 fuentes, el 97 % con al menos 40 palabras de texto. El consolidado reúne 440.855 notas de 150 organismos; el Congreso (27.687 notas, la Cámara hasta abril de 2026), los discursos presidenciales y la selección de gob.cl quedan aparte. Ocho delegaciones presidenciales están pendientes (ver limitaciones).

<details>
<summary>Notas por organismo y gobierno (cambio de mando el 11 de marzo)</summary>

<!-- tabla: python scrapers/cobertura_por_gobierno.py --markdown -->
| Organismo | Lagos | Bachelet I | Piñera I | Bachelet II | Piñera II | Boric | Kast | Total | Desde | Hasta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| **Presidencia** | | | | | | | | | | |
| Presidencia (comunicados) | – | – | – | 30 | 699 | 611 | 463 | 1.803 | feb 2016 | nov 2026 |
| Presidencia (discursos) | – | – | – | 2 | 388 | 296 | 258 | 944 | mar 2015 | nov 2026 |
| Presidencia (gob.cl, por título) | – | – | – | 2.289 | 960 | 288 | 56 | 3.593 | mar 2014 | oct 2026 |
| **Ministerios y subsecretarías** | | | | | | | | | | |
| Interior | – | – | – | – | 153 | 590 | 222 | 965 | mar 2018 | nov 2026 |
| Seguridad Pública | – | – | – | – | – | 122 | 55 | 177 | mar 2025 | nov 2026 |
| Relaciones Exteriores | – | – | – | 73 | 1.710 | 2.468 | 601 | 4.852 | ene 2017 | nov 2026 |
| Hacienda | – | – | – | – | 862 | 1.434 | 58 | 2.367 | mar 2018 | nov 2026 |
| Economía, Fomento y Turismo | – | – | – | 1.171 | 530 | 725 | 351 | 2.777 | mar 2014 | nov 2026 |
| Secretaría General de Gobierno | – | – | – | 829 | 1.546 | 806 | 168 | 3.349 | mar 2015 | nov 2026 |
| Justicia y Derechos Humanos | – | – | – | – | 594 | 588 | 133 | 1.315 | mar 2018 | nov 2026 |
| Trabajo y Previsión Social | – | 10 | 764 | 1.896 | 617 | 1.085 | 138 | 4.510 | feb 2009 | nov 2026 |
| Obras Públicas | – | – | – | 123 | 671 | 811 | 124 | 1.729 | ago 2017 | nov 2026 |
| Salud | – | – | – | 4.908 | 4.818 | 2.286 | 287 | 12.299 | jul 2015 | nov 2026 |
| Vivienda y Urbanismo | – | – | – | 23 | 563 | 1.011 | 107 | 1.704 | ene 2018 | nov 2026 |
| Agricultura | – | – | – | – | 1.940 | 2.194 | 289 | 4.425 | may 2018 | nov 2026 |
| Minería | – | – | – | – | 137 | 507 | 139 | 783 | dic 2020 | nov 2026 |
| Transportes y Telecomunicaciones | – | – | 221 | 707 | 1.041 | 538 | 67 | 2.574 | ene 2013 | nov 2026 |
| Bienes Nacionales | – | – | – | – | 15 | 584 | 123 | 722 | ene 2022 | nov 2026 |
| Energía | – | – | – | 1.272 | 2.807 | 2.940 | 637 | 7.656 | abr 2014 | nov 2026 |
| Medio Ambiente | – | – | – | 2.011 | 760 | 894 | 227 | 3.892 | mar 2014 | nov 2026 |
| Educación | – | – | – | 922 | 677 | 1.189 | 87 | 2.875 | jul 2015 | nov 2026 |
| Desarrollo Social y Familia | – | – | – | – | 1.940 | 2.193 | 255 | 4.388 | ene 2020 | nov 2026 |
| Mujer y Equidad de Género | – | – | – | – | 978 | 1.206 | 207 | 2.391 | may 2019 | nov 2026 |
| Culturas, Artes y Patrimonio | – | – | 975 | 2.316 | 1.956 | 2.174 | 423 | 7.859 | abr 2010 | nov 2026 |
| Deporte | – | – | – | – | 663 | 462 | 52 | 1.177 | mar 2018 | nov 2026 |
| Ciencia, Tecnología, Conocimiento e Innovación | – | – | – | – | 425 | 582 | 52 | 1.059 | dic 2018 | nov 2026 |
| Subsecretaría de Telecomunicaciones | – | 24 | 304 | 706 | 456 | 213 | 50 | 1.763 | jul 2006 | nov 2026 |
| Subsecretaría de Pesca y Acuicultura | – | 4 | 218 | 936 | 815 | 374 | 88 | 2.444 | mar 2009 | nov 2026 |
| Subsecretaría de Previsión Social | – | – | – | – | – | 365 | 48 | 413 | ene 2024 | nov 2026 |
| Subsecretaría de Desarrollo Regional | – | – | – | 1.604 | 1.790 | 1.302 | 281 | 4.977 | mar 2014 | nov 2026 |
| Subsecretaría de Turismo | – | – | – | 178 | 497 | 259 | 56 | 990 | mar 2014 | nov 2026 |
| Defensa Nacional | – | – | – | – | – | – | 181 | 181 | mar 2026 | nov 2026 |
| **Servicios públicos** | | | | | | | | | | |
| Servicio Agrícola y Ganadero | – | 214 | 733 | 1.022 | 696 | 886 | 137 | 3.688 | ene 2009 | nov 2026 |
| INDAP | – | – | 1.626 | 4.344 | 3.534 | 2.846 | 468 | 12.818 | ago 2012 | nov 2026 |
| CONAF | – | – | – | – | 1 | 1.452 | 428 | 1.883 | mar 2022 | nov 2026 |
| ODEPA | 952 | 96 | 643 | 352 | 24 | 30 | 11 | 2.108 | ago 2001 | nov 2026 |
| INIA | – | – | – | – | – | 722 | 270 | 992 | ago 2022 | nov 2026 |
| Sernapesca | – | – | – | – | 828 | 918 | 227 | 1.973 | mar 2018 | nov 2026 |
| Junaeb | – | – | – | – | 12 | 550 | 81 | 643 | nov 2021 | nov 2026 |
| JUNJI | – | – | – | 70 | 2.477 | 2.050 | 340 | 4.937 | jun 2014 | nov 2026 |
| Agencia de Calidad de la Educación | – | – | – | – | 97 | 297 | 78 | 472 | oct 2019 | nov 2026 |
| Superintendencia de Educación | – | – | 654 | 1.142 | 1.181 | 558 | 94 | 3.629 | ene 2012 | nov 2026 |
| Superintendencia de Electricidad y Combustibles | – | – | 271 | 300 | 534 | 535 | 32 | 1.672 | ene 2011 | nov 2026 |
| Comisión Nacional de Energía | – | – | – | 51 | 125 | 230 | 113 | 519 | sep 2015 | nov 2026 |
| Comisión Chilena de Energía Nuclear | – | – | – | 8 | 244 | 231 | 51 | 534 | ene 2018 | nov 2026 |
| Servicio de Evaluación Ambiental | – | – | – | 292 | 342 | 742 | 93 | 1.469 | mar 2014 | nov 2026 |
| Superintendencia del Medio Ambiente | – | – | 149 | 534 | 633 | 872 | 77 | 2.265 | jun 2012 | nov 2026 |
| FOSIS | – | – | – | – | 951 | 1.064 | 217 | 2.232 | mar 2018 | nov 2026 |
| SENAMA | – | – | – | 3.947 | 1.516 | 738 | 59 | 6.260 | mar 2014 | nov 2026 |
| CONADI | – | – | – | 51 | 917 | 725 | 83 | 1.776 | oct 2017 | nov 2026 |
| INJUV | – | – | – | – | 51 | 135 | 15 | 201 | feb 2021 | nov 2026 |
| SENADIS | – | 368 | 2.947 | 2.836 | 1.131 | 713 | 120 | 8.115 | ene 2009 | nov 2026 |
| SENAPRED | – | – | 8 | 4 | 32 | 287 | 38 | 369 | abr 2010 | nov 2026 |
| Policía de Investigaciones | – | – | – | 18 | 427 | 266 | 51 | 762 | nov 2017 | nov 2026 |
| Gendarmería | – | – | – | – | – | 407 | 86 | 493 | ene 2023 | nov 2026 |
| Servicio Médico Legal | – | – | – | – | 165 | 247 | 72 | 484 | may 2019 | nov 2026 |
| Servicio de Protección Especializada | – | – | – | – | 19 | 462 | 44 | 525 | oct 2021 | sep 2026 |
| Servicio de Impuestos Internos | – | – | 132 | 430 | 352 | 303 | 100 | 1.317 | ene 2012 | nov 2026 |
| Aduanas | – | 679 | 604 | 1.197 | 1.419 | 1.091 | 187 | 5.177 | feb 2007 | nov 2026 |
| Dirección de Presupuestos | 7 | 16 | 62 | 144 | 132 | 312 | 26 | 699 | abr 2004 | nov 2026 |
| ChileCompra | – | 16 | 507 | 806 | 653 | 1.167 | 224 | 3.373 | ene 2010 | nov 2026 |
| FONASA | – | – | – | – | – | 251 | 32 | 283 | dic 2022 | nov 2026 |
| Superintendencia de Salud | 29 | 389 | 252 | 212 | 144 | 95 | 20 | 1.141 | mar 2005 | nov 2026 |
| Instituto de Salud Pública | – | – | 504 | 214 | 361 | 307 | 116 | 1.502 | abr 2011 | nov 2026 |
| CENABAST | – | – | 95 | 341 | 372 | 116 | 20 | 952 | jun 2011 | nov 2026 |
| Dirección del Trabajo | 398 | 485 | 228 | 688 | 333 | 351 | 52 | 2.562 | abr 2004 | nov 2026 |
| SENCE | – | – | – | – | 1.279 | 1.887 | 291 | 3.457 | mar 2019 | nov 2026 |
| Instituto de Previsión Social | – | – | – | 136 | 307 | 550 | 124 | 1.117 | nov 2015 | nov 2026 |
| Superintendencia de Pensiones | 36 | 109 | 151 | 192 | 360 | 291 | 26 | 1.169 | jul 2001 | nov 2026 |
| Superintendencia de Seguridad Social | – | – | – | – | 68 | 252 | 172 | 492 | ene 2020 | nov 2026 |
| CORFO | – | – | – | 1 | 771 | 2.819 | 759 | 4.350 | jul 2016 | nov 2026 |
| SERNAC | 186 | 523 | 1.159 | 1.441 | 791 | 1.558 | 264 | 5.922 | jul 2002 | nov 2026 |
| Fiscalía Nacional Económica | – | 5 | 235 | 264 | 423 | 522 | 125 | 1.574 | ene 2010 | nov 2026 |
| INE | – | – | – | – | 393 | 1.010 | 147 | 1.550 | ene 2019 | nov 2026 |
| SERNATUR | – | – | 456 | 2.913 | 1.366 | 973 | 138 | 5.846 | sep 2013 | nov 2026 |
| SERCOTEC | – | – | – | – | – | 225 | 58 | 283 | abr 2022 | nov 2026 |
| ProChile | – | – | – | 7 | 183 | 1.687 | 316 | 2.193 | may 2015 | nov 2026 |
| Servicio Nacional del Patrimonio Cultural | 158 | 831 | 547 | 642 | 709 | 962 | 207 | 4.056 | mar 2004 | nov 2026 |
| SERNAMEG | – | – | – | – | – | 194 | 212 | 406 | jun 2024 | nov 2026 |
| Servicio Civil | – | 110 | 199 | 293 | 320 | 293 | 47 | 1.262 | mar 2009 | nov 2026 |
| SERNAGEOMIN | – | – | – | – | – | 53 | 117 | 170 | dic 2025 | nov 2026 |
| Superintendencia de Insolvencia y Reemprendimiento | – | 2 | 102 | 1.109 | 1.580 | 1.163 | 197 | 4.153 | ene 2010 | nov 2026 |
| SENDA | – | – | 2.705 | 4.793 | 1.428 | 1.246 | 209 | 10.381 | abr 2011 | nov 2026 |
| Servicio Nacional de Migraciones | – | – | – | – | – | 258 | 309 | 567 | abr 2022 | nov 2026 |
| Superintendencia de Servicios Sanitarios | 206 | 189 | 128 | 702 | 691 | 642 | 108 | 2.667 | feb 2001 | nov 2026 |
| Superintendencia de Casinos de Juego | 55 | 188 | 55 | 107 | 496 | 78 | 10 | 989 | may 2005 | nov 2026 |
| **Gobiernos regionales y delegaciones** | | | | | | | | | | |
| GORE Arica y Parinacota | – | – | – | – | – | 41 | 153 | 194 | ene 2025 | nov 2026 |
| GORE Tarapacá | – | – | – | 1.913 | 1.911 | 498 | 119 | 4.441 | dic 2015 | nov 2026 |
| GORE Antofagasta | – | – | – | – | – | 420 | 79 | 499 | ago 2022 | nov 2026 |
| GORE Atacama | – | – | – | 1.057 | 432 | 367 | 140 | 1.996 | mar 2015 | nov 2026 |
| GORE Coquimbo | – | – | – | – | 1.447 | 635 | 128 | 2.210 | mar 2018 | nov 2026 |
| GORE Valparaíso | – | – | – | – | 66 | 602 | 138 | 806 | jul 2021 | nov 2026 |
| GORE Metropolitano | – | – | – | 230 | 235 | 864 | 176 | 1.505 | abr 2014 | nov 2026 |
| GORE O'Higgins | – | – | – | – | 181 | 511 | 47 | 739 | jul 2021 | nov 2026 |
| GORE Maule | – | – | – | – | 171 | 828 | 161 | 1.160 | mar 2021 | nov 2026 |
| GORE Ñuble | – | – | – | – | 48 | 1.097 | 227 | 1.372 | ene 2022 | nov 2026 |
| GORE Biobío | – | – | – | 1 | 647 | 839 | 95 | 1.582 | mar 2015 | nov 2026 |
| GORE La Araucanía | – | – | – | – | – | 190 | 94 | 284 | ene 2025 | nov 2026 |
| GORE Los Ríos | – | – | – | – | – | 277 | 198 | 475 | ago 2024 | nov 2026 |
| GORE Aysén | – | – | – | – | – | 103 | 88 | 191 | ene 2024 | nov 2026 |
| GORE Magallanes | – | – | – | – | 26 | 599 | 95 | 720 | ago 2021 | nov 2026 |
| Delegación Arica y Parinacota | – | – | – | – | 156 | 446 | 7 | 609 | jun 2021 | sep 2026 |
| Delegación Tarapacá | – | – | – | – | 202 | 594 | 149 | 945 | jun 2021 | nov 2026 |
| Delegación Antofagasta | – | – | – | – | 76 | 773 | 56 | 905 | jun 2021 | sep 2026 |
| Delegación Atacama | – | – | – | – | 104 | 336 | 109 | 549 | jun 2021 | nov 2026 |
| Delegación Coquimbo | – | – | – | – | 232 | 1.032 | 283 | 1.547 | jun 2021 | nov 2026 |
| Delegación Valparaíso | – | – | – | – | 118 | 546 | 235 | 899 | jun 2021 | nov 2026 |
| Delegación Metropolitana | – | – | – | – | 63 | 97 | 22 | 182 | jun 2021 | nov 2026 |
| Delegación Maule | – | – | – | – | 305 | 950 | 270 | 1.525 | jun 2021 | nov 2026 |
| **Fuerzas Armadas** | | | | | | | | | | |
| Estado Mayor Conjunto | – | – | 42 | 363 | 335 | 349 | 65 | 1.154 | jun 2013 | nov 2026 |
| Armada de Chile | 2.272 | 4.805 | 3.152 | 2.310 | 3.062 | 3.143 | 436 | 19.183 | abr 2004 | nov 2026 |
| DIRECTEMAR | – | – | – | 156 | 875 | 460 | 141 | 1.632 | ene 2017 | nov 2026 |
| Dirección General de Aeronáutica Civil | – | – | – | – | – | 783 | 133 | 916 | ene 2023 | nov 2026 |
| **Empresas públicas** | | | | | | | | | | |
| Codelco | 394 | 729 | 710 | 428 | 365 | 165 | 63 | 2.854 | mar 2002 | nov 2026 |
| ENAP | – | – | – | – | 3 | 161 | 22 | 186 | ene 2022 | nov 2026 |
| ENAMI | – | – | – | – | – | 51 | 49 | 100 | may 2025 | nov 2026 |
| EFE | – | – | – | 160 | 370 | 536 | 104 | 1.170 | nov 2014 | nov 2026 |
| Metro de Santiago | 10 | 147 | 342 | 247 | 191 | 294 | 54 | 1.285 | nov 2005 | nov 2026 |
| ZOFRI | – | – | – | – | – | 29 | 51 | 80 | feb 2025 | nov 2026 |
| Casa de Moneda | – | – | – | – | 30 | 103 | 15 | 148 | feb 2021 | nov 2026 |
| ASMAR | – | – | – | – | 1 | 50 | 10 | 61 | mar 2022 | oct 2026 |
| ENAER | – | – | – | – | 7 | 6 | 14 | 27 | sep 2018 | oct 2026 |
| FAMAE | – | – | – | – | – | 5 | 30 | 35 | feb 2025 | nov 2026 |
| **Universidades estatales** | | | | | | | | | | |
| Universidad de Chile | 501 | 4.274 | 5.054 | 4.540 | 5.172 | 6.315 | 1.184 | 27.040 | jun 2005 | nov 2026 |
| Universidad de Santiago | – | – | – | 1 | 1 | 1.605 | 779 | 2.386 | mar 2017 | nov 2026 |
| UTEM | – | – | – | – | – | 44 | 186 | 230 | ene 2026 | nov 2026 |
| UMCE | – | – | 505 | 1.417 | 1.459 | 1.693 | 410 | 5.484 | dic 2011 | nov 2026 |
| Universidad de Tarapacá | – | – | 1.059 | 3.151 | 1.968 | 1.182 | 211 | 7.571 | oct 2011 | nov 2026 |
| Universidad Arturo Prat | – | – | – | 17 | 13 | 1.680 | 440 | 2.150 | jun 2014 | nov 2026 |
| Universidad de Antofagasta | – | – | – | – | 5 | 2.069 | 416 | 2.490 | oct 2021 | nov 2026 |
| Universidad de Atacama | – | – | – | 11 | 38 | 1.145 | 531 | 1.725 | jun 2016 | nov 2026 |
| Universidad de La Serena | – | – | – | 1.930 | 1.214 | 3.149 | 424 | 6.717 | mar 2014 | nov 2026 |
| Universidad de Valparaíso | – | 794 | 2.942 | 3.392 | 3.537 | 4.313 | 1.012 | 15.990 | abr 2008 | nov 2026 |
| Universidad de Playa Ancha | – | 1.307 | 3.377 | 5.279 | 3.723 | 4.567 | 675 | 18.928 | ene 2007 | nov 2026 |
| Universidad de O'Higgins | – | – | – | – | – | 2.767 | 619 | 3.386 | abr 2022 | nov 2026 |
| Universidad de Talca | – | – | – | – | 1.586 | 1.894 | 315 | 3.795 | ene 2020 | nov 2026 |
| Universidad del Bío-Bío | – | – | 390 | 3.112 | 2.553 | 2.668 | 529 | 9.252 | dic 2012 | nov 2026 |
| Universidad de La Frontera | – | – | – | – | – | 1.524 | 704 | 2.228 | ene 2024 | nov 2026 |
| Universidad de Los Lagos | – | – | – | 230 | 2.579 | 3.430 | 740 | 6.979 | abr 2017 | nov 2026 |
| Universidad de Aysén | – | – | – | – | 416 | 374 | 85 | 875 | ago 2018 | nov 2026 |
| Universidad de Magallanes | – | – | – | – | – | 430 | 360 | 790 | jul 2024 | nov 2026 |
| **Organismos autónomos y otros** | | | | | | | | | | |
| Contraloría General de la República | – | – | – | 265 | 570 | 496 | 160 | 1.522 | dic 2016 | nov 2026 |
| Ministerio Público | – | – | 902 | 8.936 | 3.986 | 3.668 | 999 | 18.491 | jul 2010 | nov 2026 |
| Defensoría Penal Pública | 2 | 1.959 | 2.765 | 2.955 | 3.677 | 3.684 | 712 | 15.754 | ene 2006 | nov 2026 |
| Tribunal Constitucional | – | – | – | – | 12 | 449 | 86 | 547 | nov 2021 | nov 2026 |
| Servicio Electoral | – | – | – | 308 | 543 | 699 | 100 | 1.650 | jun 2015 | nov 2026 |
| Comisión para el Mercado Financiero | 165 | 221 | 218 | 127 | 375 | 708 | 171 | 1.985 | ene 2002 | nov 2026 |
| Tribunal de Defensa de la Libre Competencia | – | 98 | 115 | 108 | 450 | 577 | 107 | 1.455 | oct 2007 | nov 2026 |
| Consejo de Defensa del Estado | – | – | – | 71 | 508 | 304 | 39 | 923 | sep 2015 | nov 2026 |
| Consejo para la Transparencia | – | 100 | 438 | 335 | 764 | 349 | 40 | 2.027 | abr 2008 | nov 2026 |
| Instituto Nacional de Derechos Humanos | – | – | 900 | 1.125 | 1.220 | 1.268 | 220 | 4.733 | sep 2010 | nov 2026 |
| Defensoría de la Niñez | – | – | – | – | 211 | 479 | 51 | 741 | ago 2018 | nov 2026 |
| **Congreso** | | | | | | | | | | |
| Senado | – | 31 | 4.736 | 4.312 | 4.053 | 3.532 | 690 | 17.354 | feb 2010 | nov 2026 |
| Cámara de Diputadas y Diputados | – | – | – | 464 | 5.353 | 4.457 | 59 | 10.333 | sep 2017 | abr 2026 |
<!-- fin tabla -->

</details>

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
python scrapers/run_todos_paralelo.py --jobs 4
python scrapers/consolidar_prensa.py
python scrapers/cobertura_por_gobierno.py
python figuras/cobertura.py
```

El orquestador corre todos los scrapers en paralelo (los registros quedan en `logs/`). Antes de cada uno copia su CSV a `data/_respaldo_<fecha>/` y al terminar une ambas versiones por URL, de modo que no se pierden las notas que un sitio deja de mostrar ni lo ya descargado si un scraper falla. Los scrapers omiten las notas que ya están en el CSV y, en los listados paginados, se detienen tras varias páginas sin novedades. Para correr solo algunos:

```bash
python scrapers/run_todos_paralelo.py --only minsal fiscalia
```

`consolidar_prensa.py` une en `data/prensa_institucional_consolidado.csv` y `.xlsx` todas las fuentes salvo el Congreso, los discursos presidenciales y la selección de gob.cl. `cobertura_por_gobierno.py` escribe `data/cobertura_por_gobierno.csv`; con `--markdown` imprime la tabla de arriba.

Cada scraper acepta `--muestra N` (solo las N notas más recientes) y `--salida RUTA` para probarlo sin tocar `data/`. Los scripts de familia reciben el sitio como argumento y listan sus sitios con `--lista`:

```bash
python scrapers/scraper_wp_api.py trabajo --muestra 20 --salida prueba.csv
python scrapers/scraper_sitemap.py --lista
python scrapers/scraper_dpr.py valparaiso          # delegación presidencial de una región
```

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

Los scrapers incluyen pausas entre solicitudes (por defecto, dos descargas simultáneas y un segundo de espera). Úsalos con moderación y respetando las condiciones de cada sitio.

## Estructura

```
scrapers/
  fuentes.py                     catálogo de fuentes: archivo, etiqueta y grupo
  comun.py                       sesión HTTP, fechas, texto y escritura incremental del CSV
  listados.py                    paginación y corte por novedad para listados HTML
  scraper_<organismo>.py         un scraper por sala de prensa con estructura propia
  scraper_wp_api.py              familia: sitios WordPress con la API REST abierta
  scraper_sitemap.py             familia: sitios que publican un sitemap XML
  scraper_wp_html.py             familia: sitios WordPress sin API (listado HTML)
  scraper_listados_institucionales.py   familia: listados paginados (Drupal y similares)
  scraper_listados_web.py        familia: listados por año (CMS Newtenberg y similares)
  scraper_gores.py               familia: gobiernos regionales sin API
  scraper_dpr.py                 familia: delegaciones presidenciales regionales
  scraper_universidades.py       familia: universidades estatales sin API
  scraper_defensa_empresas.py    familia: Defensa, Armada y empresas públicas
  run_todos_paralelo.py          corre todos los scrapers en paralelo, con respaldo y unión por URL
  fusionar_respaldo.py           une un CSV con su respaldo (por URL)
  consolidar_prensa.py           une los CSV en un solo archivo (CSV y XLSX)
  cobertura_por_gobierno.py      notas por organismo y gobierno
  ministros_gabinete.py          base de ministros y ministras 2006-2026
figuras/
  cobertura.py                   genera cobertura.png
data/                            salida de los scrapers (no versionada)
```

## Limitaciones conocidas

**Lo que el corpus no incluye.** Varios sitios exigen una verificación anti-bots, que estos scrapers no intentan sortear: Banco Central, Registro Civil, Tesorería General de la República, Ejército, Fuerza Aérea, BancoEstado, Correos de Chile, ANID, el Gobierno Regional de Los Lagos y la Subsecretaría de Relaciones Económicas Internacionales. Carabineros y la Agencia de Cooperación Internacional (AGCID) estaban fuera de servicio al momento de la extracción, y el dominio de la SEGPRES no respondía. Polla Chilena y el Sistema de Empresas Públicas no publican notas de prensa en su sitio. El Poder Judicial no tiene un listado navegable de su archivo: habría que recorrer unas 150 mil notas por número, varias de ellas de más de 1 MB por las imágenes incrustadas. Las Subsecretarías de Seguridad Pública y de Prevención del Delito no se incluyeron. Desde noviembre de 2026 la Cámara responde con una verificación anti-bots que bloquea su API, por lo que sus notas llegan a abril de 2026. Los servidores de las delegaciones presidenciales dejaron de aceptar conexiones a mitad de la extracción: faltan las de O'Higgins y de Ñuble a Magallanes, y la Metropolitana quedó incompleta.

**Profundidad del archivo.** El corpus contiene lo que cada organismo mantiene publicado. Los sitios se rediseñan y pierden archivo, así que la fecha de inicio indica lo disponible y no el comienzo de la actividad de prensa. Interior y Agricultura ya dejaron de mostrar notas que sí están en extracciones anteriores; una extracción desde cero tendrá menos. Interior no tiene notas entre 2019 y 2021, y SUBTEL no tiene notas en 2008 y 2009. Varios sitios son recientes y su archivo parte en 2024 o después (por ejemplo, Defensa en marzo de 2026, la UTEM en 2026 y varios gobiernos regionales en 2024 y 2025); las delegaciones presidenciales parten en junio de 2021, cuando reemplazaron a las intendencias.

**Qué se considera nota de prensa.**
- En Culturas solo entran las secciones de actualidad, institucional y ministra o ministro; quedan fuera la agenda cultural, los eventos, las convocatorias y los programas.
- En Economía solo entran las categorías de noticias, porque el sitio mezcla en el mismo listado páginas de transparencia, resoluciones e informes; lo anterior a marzo de 2014 son fichas de proyectos de ley y se descarta.
- En ODEPA solo entran las noticias institucionales, no los recortes de otros medios ("agro en la prensa").
- En Trabajo, la FNE, el TDLC, Servel y ChileCompra se toman solo las categorías de noticias, sin resoluciones, normativa ni documentos.
- En Aduanas las noticias se separan de la normativa con un filtro por URL y contenido; en una muestra, cerca del 4 % de lo que pasa el filtro no era noticia.
- En la Universidad de Playa Ancha se descarta la sección "UPLA en los medios", que reúne recortes de diarios. En Codelco entran las notas de la casa matriz y no las de cada división. La Universidad de Chile publica columnas de opinión en el mismo portal y se mantienen.
- Algunas delegaciones presidenciales (p. ej. Arica y Parinacota) publican como noticias los llamados a concurso de sus programas.

**Textos incompletos.** Los comunicados de Hacienda solo existen en PDF y no se incluyen; en DIPRES, unas 140 notas solo enlazan a un PDF. Unos 300 comunicados de la CMF, casi todos de 2002 a 2009, traen solo un PDF y quedan sin cuerpo. El sitio de SENDA conserva el texto de sus notas desde 2023: las anteriores (unas 9 mil) solo tienen título, fecha y una bajada truncada, que queda en `resumen`. Unas 450 notas de Energía y 240 de Educación no tienen el texto completo, y unas 110 del Senado solo traen la bajada.

**Fechas.** Algunos sitios muestran una fecha de migración en sus notas más antiguas: SENAMA y CONADI (octubre de 2017) y SENCE (marzo de 2019). Unas 30 notas de la Contraloría y una del Consejo para la Transparencia no tienen fecha porque el sitio publica una fecha imposible (1970 y el año 0201). Diez notas de SUBTEL no tienen fecha.

**Presidencia.** El archivo en la Wayback Machine es parcial, sobre todo antes de 2018, y la selección de gob.cl depende del título: puede incluir notas de gabinete que nombran al Presidente y omitir actividades presidenciales con otro título. Unas 370 notas de gob.cl también están entre los comunicados, con otra URL.

**Detalles técnicos.** Gendarmería no tiene una página por nota: la URL registrada es la de su API de detalle. El Instituto de Previsión Social y ENAMI arman su listado en el navegador, por lo que sus scrapers usan Playwright. La Superintendencia de Educación tiene un sitemap incompleto y sus notas recientes se toman del RSS. En Economía, unas cien notas tienen la página rota y su título y texto se toman del RSS. Los sitios de las delegaciones presidenciales cortan las conexiones cuando reciben muchas a la vez: si una región termina antes de tiempo, basta con volver a correrla con `--completo`.

## Licencia

Código bajo licencia MIT. El contenido de las notas pertenece a cada organismo.
