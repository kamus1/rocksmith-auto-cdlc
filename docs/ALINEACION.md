# Alineación tab ↔ audio: diagnóstico y plan

**Estado (24/10/2026, 2.ª sesión):** `cdlc build` usa la **alineación híbrida** (`align_hybrid`) con **DP de tempo** en la etapa gruesa. Banco: desarrollo 1,2 % de compases a más de ½ beat (11/12 canciones bien); validación 6,7 % (10/12). Pendiente de validar en el juego.

**Master of Puppets con el método nuevo** (tab ver 7, ventanas de 10 s frente a Nacholede): mediana 10 ms, 0 % de ventanas > 100 ms, 49/49 ventanas fiables. Corrige la intro (+210 → −30 ms) y el final (−2.850 → +30 ms) del método anterior. Tarda 118 s (antes 60). El pico de memoria, 2,8 GB, es el mismo de antes y viene de la DTW completa de `chroma_warp`, que ahora solo da el tempo global: se puede sustituir por algo barato (repitiendo el banco).
**Seven Nation Army:** coincide con la rejilla validada (mediana 9 ms, máximo 48 ms). La híbrida anterior se separaba hasta 4,7 s en los últimos 18 compases.
Desplegados para probar: `rsMetalMaste_v2_p.psarc` y `rsTheWhSeven_v7_p.psarc`.

## Método actual: `align_hybrid`
1. **DTW de armonía** (`chroma_warp`): solo para estimar la relación de tempo global (mediana).
2. **DP de tempo** (`tempo_dp`), la etapa gruesa. Estado en cada frontera de compás = (instante en una rejilla de 25 ms, relación de tempo en pasos de 1,5 %, ±35 % alrededor de la global). Cada compás dura su longitud nominal × la relación. El tempo puede cambiar hasta ±3 pasos (±4,5 %) por compás con un coste casi nulo (0,002 × pasos²). Un cambio mayor (cambio de sección) cuesta 1,0. Emisión = coseno entre la croma de la tab (16 instantes del compás) y la croma CQT del audio, menos su media en todo el audio. Croma sin suavizar. El retraso de la croma del audio respecto al ataque (160 ms) se compensa al final. Restricción: ninguna nota antes de que suene el audio. Los compases vacíos de los extremos prolongan el tempo de los extremos.
   - Así no puede saltarse ciclos de acordes repetidos: para ganar un ciclo tendría que acelerar de forma irreal.
   - La **rejilla fina es imprescindible**: con frames de 100 ms, un compás de 1,6 s solo podía cambiar de duración en pasos del ~6 %, y los parámetros del modelo de tempo no hacían nada.
   - El modelo útil es el **límite de banda** más el salto de sección, no la penalización cuadrática: con más rigidez (0,02–1,0) empeoran las canciones de tempo variable.
3. **Paso fino por programación dinámica:** cada compás se desplaza hasta ±2,5 beats sobre la curva gruesa. Emisión = golpes de batería de la tab según su tipo (bombo → graves, caja → medios, platos → agudos) más guitarras y bajo con poco peso. El coste de cambiar el desplazamiento entre compases es cuadrático. Envolventes compensadas 40 ms por el retraso del pico de onset.
   Además, **armonía fina**: cada compás se divide en 4 trozos y se compara la croma de la tab en cada trozo con la croma CQT del audio en el intervalo correspondiente (peso ×3). Resuelve los pasajes de acordes sueltos con silencios (F5/E5 en 1:50, 3:10 y 7:40), donde la batería sola se desplazaba dos beats.
   **Ancla** a la curva gruesa: 0,3 × (desplazamiento en beats)². Sin ella, en tramos de ritmo ambiguo se iba a ±1 beat. Barrido: 0,1 → 3,4 %, 0,3 → 1,9 %, 1,0 → 3,3 %.
4. Restricción de inicio también en el paso fino, con **penalización finita**. Con `inf`, si ningún desplazamiento la cumplía, todos los costes eran `inf` y el `argmin` devolvía el índice 0: toda la canción quedaba desplazada −2,5 beats.

Resultados del método anterior (DTW + suavizado; ventanas de 10 s frente a Nacholede): tab nueva → mediana 10 ms, 0 % de ventanas > 300 ms. Tab antigua → mediana 20 ms, 27 % > 300 ms (en las partes donde la tab no coincide con la grabación). Seven Nation Army: coincide con la rejilla validada (11 ms) salvo en el final, donde la híbrida acierta el fin de la música (235 s frente a 230 s).

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
   **Resuelto** con la DP de tempo (ver Método actual).
4. **Sesgo de la croma:** la etapa gruesa colocaba todo entre 100 y 300 ms tarde en todas las canciones → croma sin suavizar y retraso compensado.
5. **El paso fino se iba a ±1 beat** en tramos de ritmo ambiguo, aunque la etapa gruesa acertara (Joy Division) → ancla.
6. **La restricción de inicio con `inf` colapsaba la DP fina** (Queensrÿche, −2 beats en toda la canción) → penalización finita.
7. **Fallo del banco:** algunos charts marcan los compases de 7/8 con un beat por corchea. El banco los contaba como negras y el compás duraba el doble → ahora cuenta las unidades de ½ (Queensrÿche y Yes). Los intervalos largos (×2) no se tocan: pueden ser ritardandos.

### Probado y descartado
- **Ataques en la emisión de la DP de tempo:** desplaza canciones enteras varios beats (Joy Division −3 s, Machine Head +1,5 s). Sin el radio corto del paso fino, los riffs repetitivos encajan igual de bien desplazados.
- **Tempo más rígido** (peso 0,02–1,0, o salto de 3): empeora Machine Head, Mastodon y Lively Ones, que tienen variaciones de tempo reales.

### Resultados de esta sesión
| Versión | Desarrollo: mediana / >½ beat / bien | Validación: mediana / >½ beat / bien |
|---|---|---|
| Partida (`aacdc94`, DTW + suavizado) | 17 ms / 25,7 % / 4 de 12 | 29 ms / 30,0 % / 3 de 12 |
| DP de tempo (frames de 100 ms) + inicio finito | 11 ms / 5,8 % / 7 de 12 | — |
| + rejilla de 25 ms, croma compensada, ancla 0,3 | 10 ms / 1,9 % / 10 de 12 | — |
| + banco con compases en corcheas | **10 ms / 1,2 % / 11 de 12** | **14 ms / 6,7 % / 10 de 12** |

La validación se ejecutó una sola vez, con el algoritmo final. La mejora se generaliza (30 % → 6,7 %), pero queda una diferencia con desarrollo debida a dos canciones.

### Fallos que quedan (no corregidos, para no ajustar con la validación)
- **Mastodon (desarrollo, 6 %):** compases 128–150, donde la tab casi no tiene notas (5 de 20 compases, notas sueltas). El tempo real es constante y la DP deriva ±5 % porque nada la sujeta.
- **Queen – Love of My Life (validación, 29 %):** la etapa gruesa acierta (±0,3 beats) y el paso fino se va a +1 beat en tramos acústicos lentos (beats de ~780 ms). Es el mismo fallo que tenía Joy Division: el ancla 0,3 no basta. Idea general: que la fuerza del ancla dependa de lo clara que sea la emisión del paso fino, o limitar el radio del paso fino en milisegundos además de en beats.
- **Yes – I've Seen All Good People (validación, 43 %):** la etapa gruesa tiene un desfase casi constante de +1,5 a +2,8 beats durante 90 compases de la parte acústica con voces. La armonía es ambigua a ±2 beats y el paso fino no llega.
- Rendimiento: ~20–30 s por canción en la DP de tempo. Memoria de los punteros de retroceso: compases × 41 × (duración / 25 ms) bytes, unos 350 MB para los 8,5 min de MoP. Si molesta, limitar la ventana temporal de cada compás.

