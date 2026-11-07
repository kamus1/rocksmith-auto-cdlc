module RsBuild.Program

open System
open System.IO

/// Tab → XML de Rocksmith con los tiempos de compás dados. toneKeys: categoría de sonido → clave del tono;
/// si la pista cambia de sonido y hay tonos para ello, escribe los cambios de tono. Devuelve el arrangement,
/// las líneas del informe y las claves de tono usadas (la primera es la base).
let tabToXml (tab: string) (track: int) (kind: ArrangementBuilder.ArrangementKind) (barTimes: AlphaTab.Model.Score -> int list -> float[] * float[] * string list)
             (songLengthMs: int) (outXml: string) (toneKeys: Map<string, string>) =
    let score = GpImport.load tab
    let order = GpImport.playbackOrder score
    let times, ends, timingNotes = barTimes score order
    let r = ArrangementBuilder.build score track kind order times ends songLengthMs
    let arr = r.Arrangement
    let segs =
        GpImport.toneSegments score track order
        |> List.choose (fun (k, tick, c) -> toneKeys.TryFind c |> Option.map (fun key -> k, tick, c, key))
        // Dos sonidos con el mismo tono comparten clave: entre ellos no hay cambio
        |> List.fold (fun acc (k, tick, c, key) -> match acc with (_, _, _, cur) :: _ when cur = key -> acc | _ -> (k, tick, c, key) :: acc) []
        |> List.rev
    let keysUsed = segs |> List.map (fun (_, _, _, key) -> key) |> List.distinct
    let toneNotes =
        if keysUsed.Length > 1 then
            arr.Tones.BaseToneName <- keysUsed.Head
            keysUsed |> List.iteri (fun i key -> arr.Tones.Names[i] <- key)
            [ for k, tick, c, key in segs.Tail do
                  let barTicks = score.MasterBars[order[k]].CalculateDuration(false)
                  let t = int (Math.Round(times[k] + (ends[k] - times[k]) * tick / barTicks))
                  arr.Tones.Changes.Add(Rocksmith2014.XML.ToneChange(key, t, byte (List.findIndex ((=) key) keysUsed)))
                  yield $"Cambio de tono en el compás {k + 1} ({t / 60000}:{float (t % 60000) / 1000.0:F1}): {key} (la tab marca {c})" ]
        else []
    Rocksmith2014.XML.Processing.PhraseGenerator.generate arr
    let issues = Rocksmith2014.XML.Processing.ArrangementChecker.checkInstrumental arr
    arr.Save outXml
    let lvl = arr.Levels[0]
    let report =
        [ $"{kind} = pista {track} ({score.Tracks[track].Name}): {lvl.Notes.Count} notas, {lvl.Chords.Count} acordes ({arr.ChordTemplates.Count} plantillas), {lvl.Anchors.Count} anchors, {arr.Ebeats.Count} beats, {arr.PhraseIterations.Count} phrases"
          $"Compás 1 en {times[0] / 1000.0:F2} s, último compás termina en {times[times.Length - 1] / 1000.0:F2} s"
          yield! timingNotes
          yield! toneNotes
          yield! r.Warnings |> List.map (fun w -> "AVISO: " + w)
          yield! issues |> Seq.countBy (fun i -> i.IssueType.ToString()) |> Seq.map (fun (k, c) -> $"Checker: {k} x{c}") ]
    score, arr, report, keysUsed

/// Argumentos --clave valor
let private parseArgs (args: string[]) =
    args
    |> Array.chunkBySize 2
    |> Array.choose (function
        | [| k; v |] when k.StartsWith "--" -> Some(k.Substring 2, v)
        | _ -> None)
    |> Map.ofArray

let private usage () =
    eprintfn "Uso:"
    eprintfn "  RsBuild inspect <tab>"
    eprintfn "  RsBuild timeline <tab> <salida.json>"
    eprintfn "  RsBuild xml <tab> <pista> <offset_ms> <duracion_ms> <salida.xml>      (tempo escrito)"
    eprintfn "  RsBuild build --tab T --lead N [--rhythm M] --bar-times J --wav W --out-dir D"
    eprintfn "                --artist A --title T [--album A] [--year Y] [--cover C] [--preview-ms P] [--tone J]"
    eprintfn "                [--tones-lead \"clean=J|distortion=J\"] [--tones-rhythm ...]   (un tono por sonido de la pista)"
    eprintfn "                [--dlc-dir D]   (versión siguiente a las de D, en el título y en el nombre del archivo)"
    eprintfn "  RsBuild tones <carpeta_dlc> <salida>     (extrae tonos de guitarra de los .psarc)"
    eprintfn "  RsBuild pack <proyecto.rs2dlc> <salida_sin_extension>"
    2

[<EntryPoint>]
let main argv =
    Console.OutputEncoding <- Text.Encoding.UTF8
    match argv with
    | [| "inspect-json"; tab |] ->
        printfn "%s" (GpImport.inspectJson (Path.GetFullPath tab))
        0
    | [| "inspect"; tab |] ->
        GpImport.inspect (Path.GetFullPath tab)
        0
    | [| "timeline"; tab; outJson |] ->
        GpImport.exportTimeline (Path.GetFullPath tab) (Path.GetFullPath outJson)
        0
    | [| "xml"; tab; track; offsetMs; songLengthMs; outXml |] ->
        let timing score order =
            let t = ArrangementBuilder.barTimesFromTempo score order (float offsetMs)
            t, t[1..], []
        let _, _, report, _ = tabToXml (Path.GetFullPath tab) (int track) ArrangementBuilder.Lead timing (int songLengthMs) (Path.GetFullPath outXml) Map.empty
        report |> List.iter (printfn "%s")
        0
    | [| "tones"; dlcDir; outDir |] ->
        let psarcs = Directory.EnumerateFiles(Path.GetFullPath dlcDir, "*_p.psarc", SearchOption.AllDirectories)
        let entries = Tones.extract psarcs (Path.GetFullPath outDir)
        printfn "%d tonos extraídos en %s" entries.Count outDir
        0
    | [| "pack"; project; outBase |] ->
        let psarc = (Package.buildFromProject (Path.GetFullPath project) (Path.GetFullPath outBase)).GetAwaiter().GetResult()
        printfn "OK %s" psarc
        0
    | args when args.Length > 0 && args[0] = "build" ->
        let a = parseArgs args[1..]
        let req k = match a.TryFind k with Some v -> v | None -> failwith $"Falta --{k}"
        let opt k d = a.TryFind k |> Option.defaultValue d
        let outDir = Path.GetFullPath(req "out-dir")
        Directory.CreateDirectory outDir |> ignore
        let wav = Path.GetFullPath(req "wav")
        let songLengthMs =
            use r = new NAudio.Wave.WaveFileReader(wav)
            int r.TotalTime.TotalMilliseconds
        let barTimesJson = Path.GetFullPath(req "bar-times")
        let timing _ _ =
            let times, ends, suspicious = Align.loadBarTimes barTimesJson
            let gaps =
                [ for k in 0 .. ends.Length - 1 do
                      if times[k + 1] - ends[k] > 1.0 then
                          yield $"ALINEACIÓN: {(times[k + 1] - ends[k]) / 1000.0:F1} s de audio que no está en la tab antes del compás {k + 2} (queda sin notas)" ]
            let notes =
                gaps @
                (if suspicious.IsEmpty then []
                 else [ $"ALINEACIÓN: {suspicious.Length} compases con tempo >10%% distinto de la mediana: " +
                        (suspicious |> List.truncate 30 |> List.map (sprintf "c%d") |> String.concat ", ") ])
            times, ends, notes
        let tab = Path.GetFullPath(req "tab")
        let parts =
            [ yield ArrangementBuilder.Lead, int (req "lead")
              match a.TryFind "rhythm" with
              | Some m -> yield ArrangementBuilder.Rhythm, int m
              | None -> () ]
        let toolRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", ".."))
        let defaultTone = Path.GetFullPath(opt "tone" (Path.Combine(toolRoot, "assets", "tones", "lead_default.json")))
        // --tones-lead / --tones-rhythm "categoría=archivo|categoría=archivo": un tono por sonido de la pista
        let toneFiles (kind: ArrangementBuilder.ArrangementKind) =
            match a.TryFind $"tones-{(string kind).ToLowerInvariant()}" with
            | None | Some "" -> Map.empty
            | Some spec ->
                spec.Split('|') |> Array.choose (fun e -> match e.Split('=', 2) with [| c; f |] -> Some(c, Path.GetFullPath f) | _ -> None)
                |> Map.ofArray
        let built =
            parts |> List.map (fun (kind, track) ->
                let name = (string kind).ToLowerInvariant()
                let xml = Path.Combine(outDir, $"arr_{name}_RS2.xml")
                let files = toneFiles kind
                // La clave es la del primer sonido que usa cada archivo: mismo archivo, misma clave
                let keyOf (c: string) =
                    let first = files |> Map.toList |> List.find (fun (_, f) -> f = files[c]) |> fst
                    $"{name}_{first}"
                let score, arr, report, keysUsed = tabToXml tab track kind timing songLengthMs xml (files |> Map.map (fun c _ -> keyOf c))
                // Un solo sonido: el tono de --tone (o el de su categoría, si se dio) con la clave de siempre
                let tones =
                    if keysUsed.Length > 1 then
                        keysUsed |> List.map (fun key -> key, files[key.Substring(name.Length + 1)])
                    else
                        let single = keysUsed |> List.tryHead |> Option.bind (fun key -> files.TryFind(key.Substring(name.Length + 1)))
                        [ "guitar", Option.defaultValue defaultTone single ]
                score, { Package.Xml = xml; Package.Kind = kind; Package.Tuning = arr.MetaData.Tuning.Strings; Package.Tones = tones }, report)
        let score, _, _ = List.head built
        let report = built |> List.collect (fun (_, _, r) -> r)
        let info =
            { Package.Artist = opt "artist" score.Artist
              Package.Title = opt "title" score.Title
              Package.Album = opt "album" score.Album
              Package.Year = int (opt "year" "0")
              Package.Version = 0 }
        // Versión: la siguiente a las que haya en la carpeta del juego (--dlc-dir); va en el título y en el archivo
        let dlcKey = Rocksmith2014.DLCProject.DLCKey.create "rsbuild" info.Artist info.Title
        let version = Package.nextVersion (opt "dlc-dir" "") dlcKey
        let info = { info with Version = version }
        let previewMs = float (opt "preview-ms" (string (min 60000 (songLengthMs / 3))))
        let previewWav = Package.convertAudio wav previewMs
        let cover = Path.GetFullPath(opt "cover" (Path.Combine(toolRoot, "assets", "cover_default.png")))
        let specs = built |> List.map (fun (_, spec, _) -> spec)
        let project = Package.writeProject outDir info specs wav previewWav previewMs cover
        let outBase = Path.Combine(outDir, Path.GetFileNameWithoutExtension project + $"_v{version}")
        let psarc = (Package.buildFromProject project outBase).GetAwaiter().GetResult()
        let reportPath = Path.Combine(outDir, "report.md")
        File.WriteAllLines(reportPath, [ $"# {info.Artist} - {info.Title}"; "" ] @ (report |> List.map (fun l -> "- " + l)))
        report |> List.iter (printfn "%s")
        printfn "Proyecto: %s" project
        printfn "PSARC:    %s" psarc
        printfn "Informe:  %s" reportPath
        0
    | _ -> usage ()
