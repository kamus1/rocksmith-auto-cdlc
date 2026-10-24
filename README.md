# Rocksmith CDLC: investigación

Investigación del 22/10/2026 sobre qué son los CDLC de Rocksmith, cómo se crean a mano y cómo se podría automatizar el proceso. **Estado: solo investigación, no hay nada construido.**

---

## 1. Qué es un CDLC

**CDLC** significa *Custom DLC*: canciones hechas por fans para **Rocksmith 2014 Remastered** (PC/Mac). Funcionan como el DLC oficial. Cada una es un archivo `.psarc` que contiene:

- El audio en formato Wwise (`.wem`), más una vista previa de unos 30 s
- Los charts (arrangements) de Lead, Rhythm, Bass y voces
- La dificultad dinámica (DD)
- Los tonos del amplificador y los pedales
- La portada y los metadatos

**Comunidad:** CustomsForge. Su base de datos [Ignition](https://ignition4.customsforge.com) tiene decenas de miles de canciones. **Antes de crear una canción, conviene buscarla ahí.**

**Puntos a tener en cuenta:**
- Solo funciona en **Rocksmith 2014 en PC/Mac**. No hay soporte en consolas ni en Rocksmith+.
- El juego necesita el **CDLC Enabler**, que se instala con el instalador de CustomsForge. Los `.psarc` van en `Rocksmith2014/dlc/`.
- Rocksmith 2014 salió de las tiendas en octubre de 2023 y volvió a Steam en diciembre de 2024 como edición "Learn & Play".
- Es una zona gris legal, porque el audio tiene copyright. La norma de la comunidad es usar audio que hayas comprado.

## 2. Proceso manual

### Herramientas

| Herramienta | Para qué sirve |
|---|---|
| **EOF (Editor on Fire)** | Editor de charts, el núcleo del proceso. Hace falta la versión completa más el *hotfix*. |
| **DLC Builder** (iminashi, v3.5.0 de julio de 2025) | Empaqueta en `.psarc`, genera la DD, gestiona los tonos y valida. Funciona en Windows, macOS y Linux. |
| **Wwise** (2019/2021/2022/2023) | Convierte el audio a `.wem`. DLC Builder lo invoca. |
| **Guitar Pro / TuxGuitar** | Tab de partida que EOF importa. |
| **Audacity** | Prepara el audio. |
| **UltraStar Creator** (opcional) | Letras. |

### Flujo
1. Conseguir el audio de buena calidad. Opcionalmente, añadir unos 10 s de silencio al principio.
2. Conseguir la tab en Guitar Pro y **añadir un compás vacío al inicio**. Si no, EOF da error por falta de *count-in*.
3. **En EOF:** importar el audio y la tab, y **sincronizar el beat map con la grabación**. Es lo más laborioso. Después revisar las técnicas, las secciones, las fret hand positions y los acordes, y exportar el XML.
4. **En DLC Builder:** crear el proyecto, añadir los XML, el audio, la portada, los tonos y la afinación, generar la DD y pulsar **Build** para obtener el `.psarc`.
5. Copiar el archivo a `dlc/`, probarlo en el juego y repetir los pasos 3 y 4 hasta que quede bien.

Consejo de la comunidad: aprender a escribir y corregir tabs antes de lanzarse.

### Linux
- **Rocksmith con Proton.** Para tener baja latencia hace falta la cadena PipeWire-JACK → wineASIO → RS_ASIO → Rocksmith. La guía de referencia es [theNizo/linux_rocksmith](https://github.com/theNizo/linux_rocksmith). Su mirror avisa de problemas con wine 10.2 o superior; hay que comprobar el estado actual antes de empezar.
- **CDLC Enabler en Linux:** hay reportes contradictorios.
  - La [guía CachyOS/Arch (dic. 2025)](https://www.devroom.io/2025/12/02/rocksmith-2014-on-cachyos-arch-linux/) dice que el enabler reciente crashea el juego y usa como workaround un `D3DX9_42.dll` antiguo.
  - Un [hilo de CustomsForge (ene. 2025)](https://customsforge.com/topic/67471-cdlc-on-linux-instructions/) dice que funciona ejecutando el instalador con wine dentro de `compatdata/221680/pfx`.
- **Herramientas:** DLC Builder tiene versión nativa para Linux. EOF y Wwise son de Windows y habría que usarlos con wine (no comprobado). Rocksmith2014.NET busca Wwise en `~/.wine/drive_c/Program Files (x86)/Audiokinetic/`.

## 3. Automatización

### Estado del arte
- **No hay ningún generador automático de CDLC** a partir de audio. No se encontró ninguno.
- **El empaquetado se puede programar.** [Rocksmith2014.NET](https://github.com/iminashi/Rocksmith2014.NET) es el motor de DLC Builder: F# (con algo de C#), licencia MIT y activo (último push el 12/10/2026). Módulos relevantes en `src/`:
  - `Rocksmith2014.XML` / `.XML.Processing`: leer, escribir y validar arrangements. También está en [NuGet](https://www.nuget.org/packages/Rocksmith2014.XML).
  - `Rocksmith2014.DD`: generador de dificultad dinámica.
  - `Rocksmith2014.Audio`: conversión con Wwise (`Wwise/`, `WwiseFinder.fs`) y vista previa.
  - `Rocksmith2014.DLCProject`: `PackageBuilder.buildPackages` genera el `.psarc`. También `ShowLightGenerator`, `ToolkitImporter` y `PsarcImport`.
  - `Rocksmith2014.EOF`: exportación a proyecto de EOF.
  - `Rocksmith2014.PSARC` y `Rocksmith2014.SNG`: formatos binarios.
  - Alternativa en Python para PSARC: [rs-utils](https://github.com/0x0L/rs-utils).
- **Transcripción de audio a tab:** sigue en fase de investigación. Funciona en casos simples y falla con polifonía y técnicas.
  - [TART](https://arxiv.org/pdf/2609.11904): detecta técnicas (bends, slides, armónicos).
  - [Fretting-Transformer](https://papers.cool/arxiv/2506.14223): pasa de MIDI a cuerda y traste con T5.
  - [AutoTab](https://sol.sbc.org.br/index.php/semish/article/view/36816): CNN, 87,8 % de precisión en notas sueltas.
  - Herramientas conocidas (sin verificar en esta investigación): Demucs (separación de stems, `htdemucs_6s` incluye guitarra), Basic Pitch de Spotify (audio a MIDI), beat_this y madmom (detección de beats y compases).

### Reglas de CustomsForge sobre IA
Según el [CDLC Agreement](https://ignition4.customsforge.com/kb/article/Rules%20and%20Guidelines/cdlc-agreement), se permite usar IA para tareas de audio, como crear stems. Se **prohíben las tabs generadas solo por IA**, y todo CDLC hecho con ayuda de IA debe cumplir las guías de charting y probarse a fondo. Para uso personal no hay restricción.

### Sistema propuesto

**Entrada:** el audio de la canción y una tab en Guitar Pro (`.gp`/`.gp5`/`.gpx`). Opcionalmente, los metadatos (artista, título, álbum, año).
**Salida:** un `.psarc` listo para `dlc/`, opcionalmente un proyecto de EOF y un informe de validación.

```
Audio + tab Guitar Pro
   │
   ├─ 1. Separar stems (Demucs)                                   ✅ automatizable
   ├─ 2. Detectar beats y compases → beat map                     ✅ automatizable (es lo más tedioso a mano)
   ├─ 3. Detectar secciones (verso, estribillo…) → phrases         ✅ automatizable
   ├─ 4. Notas:
   │      A) Alinear la tab con el audio (estirar cada compás)     ✅ bastante fiable
   │      B) Transcribir del audio (Basic Pitch + asignar traste)  ⚠️ solo borrador
   ├─ 5. Generar XML de Rocksmith + validar                        ✅ Rocksmith2014.XML
   ├─ 6. DD, tonos según el género, portada y metadatos            ✅ Rocksmith2014.DD
   ├─ 7. Audio a .wem (Wwise vía wine) + empaquetar .psarc         ✅ Rocksmith2014.Audio / DLCProject
   └─ 8. Jugarlo y revisarlo                                       👤 humano
```

**Papel del agente (LLM):** orquestar el pipeline. Buscar los metadatos y la portada, deducir la afinación, ejecutar las herramientas, leer los errores del validador y corregirlos (count-in, posiciones de mano imposibles), y elegir los tonos. **No debe transcribir**; eso lo hacen los modelos de audio.

**MVP sugerido:** un CLI en Python (análisis de audio y alineación) que llame a un script F# (`dotnet fsi`) con Rocksmith2014.NET para el XML, la DD, el audio y el empaquetado. La opción A va primero. La opción B y la capa de agente ("hazme X canción") quedan para después.

**Limitaciones:**
- Si la tab tiene errores, el CDLC también.
- Si la tab es de otra versión de la canción (en directo, remaster), la alineación falla en algunas partes. El informe debería señalarlo.
- La prueba final en el juego es manual.
- Hay que usar audio propio. No conviene sacar tabs de Songsterr con scraping porque sus términos de uso lo prohíben.

### Pendiente antes de construir
- [ ] ¿Rocksmith 2014 está instalado en este PC con Proton?
- [ ] Elegir una canción de prueba (audio y tab en Guitar Pro), idealmente de estructura sencilla.
- [ ] ¿Guitarra, bajo o los dos?
- [ ] Comprobar que Wwise se instala y funciona con wine.
- [ ] Revisar `samples/` y `PackageBuilder.fs` en Rocksmith2014.NET para ver cómo construir un `DLCProject` desde un script.

## 4. Fuentes

**Proceso manual (CustomsForge):**
- [Which software to use for creating CDLC](https://www.editoronfire.customsforge.com/kb/article/Charting/which-software-to-use)
- [arrov's CDLC creation process step by step](https://customsforge.com/topic/66003-arrovs-cdlc-creation-process-step-by-step/)
- [CDLC creation for Rocksmith 2014 Remastered](https://customsforge.com/topic/35318-cdlc-creation-for-rocksmith-2014-remastered/)
- [Advice regarding CDLC creation](https://customsforge.com/topic/67553-advice-and-question-regarding-cdlc-creation/)
- [Best way to unpack and edit a CDLC](https://customsforge.com/topic/80257-best-way-to-unpack-and-edit-a-cdlc/)
- [DLC Builder](https://cfmanager.customsforge.com/tools/DLCBuilder)
- [CDLC Agreement](https://ignition4.customsforge.com/kb/article/Rules%20and%20Guidelines/cdlc-agreement)

**Rocksmith en Linux:**
- [theNizo/linux_rocksmith](https://github.com/theNizo/linux_rocksmith)
- [Rocksmith 2014 on CachyOS/Arch Linux](https://www.devroom.io/2025/12/02/rocksmith-2014-on-cachyos-arch-linux/)
- [CDLC on Linux instructions (CustomsForge)](https://customsforge.com/topic/67471-cdlc-on-linux-instructions/)
- [ProtonDB – Rocksmith 2014](https://protondb.com/app/221680)

**Librerías y transcripción:**
- [Rocksmith2014.NET](https://github.com/iminashi/Rocksmith2014.NET)
- [Rocksmith2014.XML (NuGet)](https://www.nuget.org/packages/Rocksmith2014.XML)
- [rs-utils](https://github.com/0x0L/rs-utils)
- [TART](https://arxiv.org/pdf/2609.11904)
- [Fretting-Transformer](https://papers.cool/arxiv/2506.14223)
- [AutoTab](https://sol.sbc.org.br/index.php/semish/article/view/36816)

**General:**
- [Rocksmith 2014 – Wikipedia](https://en.wikipedia.org/wiki/Rocksmith_2014)
