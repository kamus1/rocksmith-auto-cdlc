# Alineación tab ↔ audio: diagnóstico y plan

**Estado (24/10/2026):** `cdlc build` usa la **alineación híbrida** (`align_hybrid`). Con la referencia de Master of Puppets el error típico es de **~10 ms** (antes ~840 ms con la rejilla de beats). Pendiente de validar en el juego.

## Método actual: `align_hybrid`
1. **Armonía:** DTW entre la croma de la tab y la croma CENS del audio → en qué compás estamos.
2. **Suavizado anclado localmente:** relación de tempo con mediana móvil (9 compases), integrada y anclada con la mediana del desfase en ±7 compases. Los saltos sueltos de la DTW se descartan.
3. **Paso fino por programación dinámica:** cada compás se desplaza hasta ±2,5 beats sobre esa curva. Emisión = golpes de batería de la tab según su tipo (bombo → graves, caja → medios, platos → agudos) más guitarras y bajo con poco peso. El coste de cambiar el desplazamiento entre compases es cuadrático. Envolventes compensadas 40 ms por el retraso del pico de onset.
   Además, **armonía fina**: cada compás se divide en 4 trozos y se compara la croma de la tab en cada trozo con la croma CQT del audio en el intervalo correspondiente (peso ×3). Resuelve los pasajes de acordes sueltos con silencios (F5/E5 en 1:50, 3:10 y 7:40), donde la batería sola se desplazaba dos beats.
4. Preferencia suave para que la primera nota coincida con el inicio del audio.

Resultados (ventanas de 10 s frente a Nacholede): tab nueva → mediana 10 ms, 0 % de ventanas > 300 ms. Tab antigua → mediana 20 ms, 27 % > 300 ms (en las partes donde la tab no coincide con la grabación). Seven Nation Army: coincide con la rejilla validada (11 ms) salvo en el final, donde la híbrida acierta el fin de la música (235 s frente a 230 s).

Pendiente: los primeros ~5 s de MoP (+210 ms: el compás 1 se estira; poca señal en la intro), probar más canciones y quitar `align_beats`, `align_beats_dp`, `refine_local` y `onset_error` cuando la híbrida esté validada en el juego.

## Referencia real
CDLC de Master of Puppets de *Nacholede* (CustomsForge) en `samples/Metallica_Master-of-Puppets_Nacholede_v1_p.psarc`: audio y beat map sincronizados a mano.

Para preparar la referencia:
- Charts SNG → XML: `build/cmp/sng2xml.fsx`.
- Audio: `.wem` → `.ogg` con `ww2ogg` y `revorb`, que están en `external/Rocksmith2014.NET/src/Rocksmith2014.Audio/Tools/win`.

Evaluador en `build/ref/eval_notes.py` (copiarlo a `tests/` si se mantiene). Para cada ventana de 10 s busca el desplazamiento que más notas **de igual altura** hace coincidir (±40 ms) entre nuestro chart y el de referencia. Así tolera partes que las tabs transcriben distinto. Las ventanas con menos del 30 % de coincidencias se marcan como poco fiables.

## Resultados en Master of Puppets (|error| mediano por ventana)
| Método | Error | Ventanas > 300 ms |
|---|---|---|
| Rejilla de beats + DP (`align_beats_dp`, la que está en `cdlc build`) | ~840 ms | 75 % |
| + `refine_local` (versión `_v3` desplegada) | ~820 ms; además añade un sesgo de ~35 ms | 77 % |
| DTW con croma CENS | ~380 ms | 62 % |
| DTW con croma CENS + ajuste al beat más cercano | ~310 ms | 54 % |

Por beats, la rejilla solo acierta entre 1:30 y 2:20. En el resto del tema se desvía entre 0,5 y 3 s.

### Con una tab mejor (`samples/Metallica - Master Of Puppets (ver 7 by cfhbyron666).gp`)
Esta tab tiene compases de 11/16 (la antigua ponía 5/8 y le faltaba una semicorchea en 36 compases), tempo escrito de 210/104/212 y 416 compases sin repeticiones.
| Método | Error | Ventanas > 300 ms |
|---|---|---|
| **Sin alinear** (tempo escrito, empezando en la 1ª nota) | ~390 ms; deriva suave (+900 / −450), **sin saltos** | 59 % |
| Rejilla de beats + DP | ~1.410 ms | 92 % |
| Rejilla + DP + tempo local | ~1.690 ms | 84 % |
| Croma CENS + ajuste al beat más cercano | **~260 ms**; muchas ventanas a 0–40 ms, los fallos son **+1 beat** (~300 ms) | 25 % |

Conclusiones:
- La tab nueva es correcta estructuralmente: le falta y le sobra nada. El problema es el alineador.
- La rejilla de beats es inservible en MoP.
- La croma acierta el compás, pero el ajuste al beat más cercano a veces elige el siguiente. Hay que elegir el beat con continuidad de tempo, no por cercanía.

## Lecciones
- **La métrica de onsets/batería engaña** con riffs repetitivos: un desplazamiento de uno o varios beats puntúa casi igual. No usarla para decidir. Usar referencias reales.
- Los **beats de beat_this** son buenos en tramos regulares, pero en la intro, el interludio y el final de MoP se saltan o inventan beats (intervalos ×0,5 y ×2). Cada error desplaza todo lo que viene después si se sigue la rejilla al pie de la letra.
- **`refine_local` empeora** la precisión fina, porque el pico de la envolvente de onsets llega después del ataque. Quitarlo o corregir el sesgo.
- La **croma** distingue compases que tienen el mismo ritmo (el riff cromático E–D#–D–C#), pero sola es imprecisa (~1 beat).
- Comparar beats por índice no sirve: las referencias marcan los beats con otras unidades (5/8 en corcheas, el interludio a doble tempo).

## Plan
1. **Banco de pruebas** con varias referencias. Además de MoP, coger 10–20 CDLC de la biblioteca (`dlc/`) con tab de Guitar Pro disponible. Si no hay tab, usar el propio chart de referencia como "tab" con tiempos nominales (beats por compás a tempo fijo) para probar solo la alineación.
2. **Alineación híbrida en una sola DP por compases.** Emisión = croma del compás (tab frente a audio, CENS) + onsets. Candidatos = posiciones sobre la rejilla de beats (medio beat). Transición con continuidad de tempo local y saltos para corregir beats perdidos o inventados.
3. Quitar `refine_local` o sustituirlo por un ajuste sin sesgo (comparar el inicio del ataque, no el pico).
4. Hacer que el informe de `cdlc build` avise cuando la alineación es dudosa (compases con duración absurda, desacuerdo entre croma y beats).
5. Validar en el juego: Master of Puppets y Seven Nation Army.

## Cambios sin commit
`cdlc/align.py` tiene en la working copy una penalización de duración por tempo local en `align_beats_dp` (`tempo_weight`, `tempo_tolerance`). No empeora SNA y en MoP no está demostrado que mejore. Revisar con el banco de pruebas antes de hacer commit.

## Banco de pruebas (24 canciones oficiales/CDLC con beat map hecho a mano)
- Selección (`bench/select.py` sobre `bench/survey.fsx`): 4 grupos × 6 canciones (metrónomo o tempo variable × compás regular o irregular), una por artista, repartidas 3/3 entre **desarrollo** y **validación**. La validación no se mira al ajustar.
- `bench/extract.fsx` extrae charts y audio; `bench/run.py` construye una "tab" a tempo fijo con la misma estructura y notas (Lead + Rhythm + Bass, **sin batería**), alinea y compara cada compás con notas con el real.
- Uso: `.venv/Scripts/python bench/run.py --set desarrollo --tag nombre [--params '{"onset_lag_ms": 20}']`.

### Resultados (desarrollo, 12 canciones)
| Versión | Mediana de medianas | Compases a más de ½ beat | Canciones bien (<5 % de compases mal) |
|---|---|---|---|
| Híbrida tal como quedó tras MoP (contando todos los compases) | 22 ms | 33,6 % | 0/12 |
| + armonía solo en el tramo con notas, midiendo compases con notas | 17 ms | 25,7 % | 4/12 |
| + restricción de inicio general | 17 ms | 25,7 % | 4/12 |

### Fallos generales encontrados
1. **Compases vacíos al principio** (count-in) desplazaban la DTW de armonía → corregido: la DTW solo usa el tramo entre la primera y la última nota.
2. **La preferencia "la primera nota coincide con el inicio del audio" era un sobreajuste a MoP** → sustituida por una restricción que siempre es cierta (ninguna nota antes de que suene el audio).
3. **Música repetitiva (ciclos de acordes iguales): la DTW se salta ciclos enteros** (Deep Blue Something: −2 compases entre el compás 11 y el 31; Machine Head: segundos de error). El paso fino (±2,5 beats) no llega a corregirlo. **Pendiente:** imponer un tempo plausible, ya sea limitando la pendiente de la DTW o con una búsqueda amplia y un modelo de tempo de segundo orden, y volver a medir con el banco.
4. Pendiente de revisar: Joy Division y Mastodon (la mitad de los compases a más de ½ beat) y Queensrÿche (p90 de 1,1 s).
