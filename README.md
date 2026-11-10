![Developed with an AI assistant](https://img.shields.io/badge/developed%20with-AI%20assistant-8A2BE2)

# rocksmith-auto-cdlc

Turns a song's audio and a Guitar Pro tab into a Rocksmith 2014 CDLC (`.psarc`) and copies it to the game. The tab is aligned to the recording automatically.

## Requirements

- Windows
- Rocksmith 2014 Remastered with CDLC enabled
- Python 3.12 (`winget install Python.Python.3.12`)
- .NET 10 SDK (`winget install Microsoft.DotNet.SDK.10`)
- ffmpeg (`winget install Gyan.FFmpeg`)
- Wwise 2023.1, Authoring package: install it with the [Audiokinetic Launcher](https://www.audiokinetic.com/download/) (free account). 2024+ does not work.

## Install

```
git clone https://github.com/kamus1/rocksmith-auto-cdlc
cd rocksmith-auto-cdlc
.\setup
```

`setup` lists anything still missing. Run `.\cdlc check` to check again.

## Use

Web app (http://127.0.0.1:8000):

```
.\cdlc web
```

Command line:

```
.\cdlc build --audio song.mp3 --tab song.gp
.\cdlc inspect --tab song.gp     # list the tab's tracks
.\cdlc tones                     # build a tone library from your installed CDLC
```

Then go back to the game's title screen to load the new song.

The game folder defaults to Steam's `Rocksmith2014/dlc`. Change it in the web app's Settings.

## Tones

Run `.\cdlc tones` once, or use Settings → Tone library in the web app. It copies the guitar tones from the CDLC you have installed.

Each song then gets tones from the same song if you have it, otherwise from the same artist, otherwise the closest sound. Sound changes marked in the tab ("Clean", "Dist."…) become tone changes in the game.

Without this library a default tone is used. To pick one yourself: `--tone distortion` (or `clean`, `crunch`, `highgain`, `acoustic`…) or `--tone file.json`.

## Limitations

- The tab must match the recording (same sections and repeats).
- Alignment can fail where the tab has almost no notes.
- Guitar only. No tuning changes within a track.

## Notes

- Use audio you own.
- Not affiliated with Ubisoft.
- How the alignment works: [docs/ALIGNMENT.md](docs/ALIGNMENT.md). Background research: [docs/RESEARCH.md](docs/RESEARCH.md). Similar projects and how they differ: [docs/RESEARCH.md § 4](docs/RESEARCH.md#4-related-projects).

## Credits

- [Rocksmith2014.NET](https://github.com/iminashi/Rocksmith2014.NET) (MIT): packaging, Wwise and dynamic difficulty. The default cover image comes from its tests.
- [alphaTab](https://github.com/CoderLine/alphaTab): Guitar Pro reading.
- [librosa](https://github.com/librosa/librosa): audio analysis.

## License

[MIT](LICENSE)
