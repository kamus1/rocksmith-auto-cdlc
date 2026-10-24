# Plan: interfaz web local

**Estado:** pendiente. Escrito el 24/10/2026; no hay código hecho.

## Objetivo
Una web que **solo corre en el PC del usuario**: se arranca con `python -m cdlc web`, se abre en `http://127.0.0.1:8000` y solo escucha en localhost. Sustituye a la terminal para el uso diario y será la cara del proyecto cuando el repo sea público.

Tiene que ser local por tres motivos:
- Necesita **Wwise, la GPU y la carpeta `dlc/`** del usuario.
- Una web pública donde la gente sube audio con copyright sería un problema legal, y la licencia de Wwise no está pensada para eso.
- Así no hay costes de servidor.

## Decisiones tomadas
- **FastAPI + una página HTML con JavaScript sin frameworks**, en vez de Gradio o una app de escritorio. Motivos:
  - Reutiliza todo el Python de `cdlc/`.
  - Permite mostrar el progreso en vivo (SSE) y una interfaz a medida.
  - Es ligera y sin paso de compilación.
  - Más adelante puede servir de interfaz para el agente "hazme X canción".
- **Primero la interfaz y después preparar el repo público** (ver "Después").

## Pasos

### 1. Separar la lógica del build de la CLI
- Mover de `cdlc/cli.py` a un módulo nuevo `cdlc/pipeline.py`: constantes, `find_wwise`, `rsbuild`, `resolve_tone`, `choose_tracks`, `lookup_song`, `download_cover`, `deploy` y el cuerpo de `cmd_build`.
- Convertir `cmd_build` en `build_song(opts, on_step, on_log) -> BuildResult`:
  - `opts` es un dataclass con los mismos campos que los argumentos actuales.
  - `on_step(n, título)` y `on_log(línea)` sustituyen a los `print`.
  - Devuelve las rutas del psarc, el informe, la portada y la canción desplegada, además de las métricas.
- Cambiar `SystemExit` por una excepción propia `PipelineError`, para que la web pueda mostrar el error.
- Los `print` sueltos de `lookup_song`, `download_cover` y `deploy` pasan a llamar a `on_log`.
- `cli.py` queda como una capa fina de argparse sobre `pipeline.build_song`, con la misma salida que ahora.
- Verificación: `python -m cdlc build --audio … --tab …` da el mismo resultado que antes con Seven Nation Army.

### 2. Backend (`cdlc/web/app.py`)
- Dependencias: `fastapi`, `uvicorn`, `python-multipart` (añadirlas a `requirements.txt`).
- Archivos subidos en `build/_uploads/<id>/`, que git ya ignora.
- Endpoints:
  - `GET /`: la página.
  - `POST /api/inspect` (tab): pistas (`RsBuild inspect-json`), la elección automática de `choose_tracks` y los metadatos sacados de la tab.
  - `POST /api/build` (audio, id de la tab, lead, rhythm, tono, metadatos, desplegar sí/no): lanza `build_song` en un hilo y devuelve el id del trabajo. Solo un build a la vez (GPU y Wwise).
  - `GET /api/jobs/{id}/events`: SSE con los pasos 0–6, las líneas de log y el resultado o error final.
  - `GET /api/songs`: canciones generadas en `build/*/pkg` (portada, informe, versión desplegada en `dlc/`).
  - `POST /api/songs/{name}/deploy`: volver a copiarla a `dlc/` como `_vN` nuevo.
  - `GET /api/songs/{name}/cover` y `GET /api/songs/{name}/report`.
  - `GET /api/tones`: categorías y estado de la biblioteca. `POST /api/tones/rebuild` ejecuta `RsBuild tones`.
  - `GET /api/status`: comprobaciones de entorno (Wwise compatible, ffmpeg, GPU/CUDA, carpeta `dlc/`, biblioteca de tonos), para mostrar avisos claros.
- Escuchar solo en `127.0.0.1`. Subcomando `python -m cdlc web [--port 8000] [--no-browser]` que abre el navegador.

### 3. Frontend (`cdlc/web/static/index.html` + `app.js` + `style.css`)
- **Nueva canción**:
  1. Arrastrar el audio y la tab.
  2. Tabla de pistas (nombre, cuerdas, afinación, notas, acordes, cobertura, instrumento) con selectores Lead y Rhythm ya rellenos con la elección automática.
  3. Metadatos editables (artista, título, álbum, año) y vista previa de la portada de iTunes.
  4. Tono: automático, categoría o archivo.
  5. Casilla "copiar a dlc/" y botón **Generar**.
- **Progreso**: los 6 pasos con estado y tiempo, log desplegable, error de alineación y, al terminar, el aviso "vuelve a la pantalla de título del juego".
- **Mis canciones**: tarjetas con portada, artista y título, versión desplegada, botones "ver informe" y "volver a desplegar".
- **Estado del entorno**: avisos si falta Wwise 2019–2023, ffmpeg, CUDA o la biblioteca de tonos.
- Tema oscuro, sin dependencias externas (todo servido en local).

### 4. Verificación
- Seven Nation Army de principio a fin desde la web: aparece en el juego al volver a la pantalla de título.
- Errores: tab que no es de Guitar Pro, audio corrupto, falta de Wwise. Cada uno muestra un mensaje claro en la página.
- La CLI sigue funcionando igual.

## Después: preparar el repo público
- Configuración en un archivo (`config.toml` o variables de entorno) en lugar de rutas fijas: carpeta `dlc/` de Rocksmith, Wwise, ffmpeg.
- README en inglés o bilingüe, con capturas de la web, requisitos, instalación y aviso legal: usar audio propio, no compartir charts sin revisar y seguir las normas de CustomsForge.
- `LICENSE` (por ejemplo MIT) y revisar las licencias de las dependencias: Rocksmith2014.NET MIT, alphaTab MPL-2.0, y la licencia de los pesos de beat_this.
- Confirmar que no se sube nada con copyright: `samples/`, `build/tones` (tonos de otros CDLC), audio.
- Decidir con qué nombre y email firmar los commits antes de publicarlos (ahora aparece el email personal).
- Crear el repo en GitHub (lo confirma el usuario).

## Pendiente fuera de este plan
- Probar Master of Puppets `_v3` en el juego (alineación robusta) e informar de los tramos que se desfasen.
- Ideas para más adelante: tests automáticos, varios tonos por canción, juntar riff y acordes en un arrangement, agente "hazme X canción", transcripción sin tab.
