module RsBuild.Program

open System
open System.IO

/// Tab → XML de Rocksmith con los tiempos de compás dados. Devuelve el arrangement y las líneas del informe.
let tabToXml (tab: string) (track: int) (kind: ArrangementBuilder.ArrangementKind) (barTimes: AlphaTab.Model.Score -> int list -> float[] * string list)
             (songLengthMs: int) (outXml: string) =
    let score = GpImport.load tab
    let order = GpImport.playbackOrder score
    let times, timingNotes = barTimes score order
    let r = ArrangementBuilder.build score track kind order times songLengthMs
    let arr = r.Arrangement
    Rocksmith2014.XML.Processing.PhraseGenerator.generate arr
    let issues = Rocksmith2014.XML.Processing.ArrangementChecker.checkInstrumental arr
    arr.Save outXml
    let lvl = arr.Levels[0]
    let report =
        [ $"{kind} = pista {track} ({score.Tracks[track].Name}): {lvl.Notes.Count} notas, {lvl.Chords.Count} acordes ({arr.ChordTemplates.Count} plantillas), {lvl.Anchors.Count} anchors, {arr.Ebeats.Count} beats, {arr.PhraseIterations.Count} phrases"
          $"Compás 1 en {times[0] / 1000.0:F2} s, último compás termina en {times[times.Length - 1] / 1000.0:F2} s"
          yield! timingNotes
          yield! r.Warnings |> List.map (fun w -> "AVISO: " + w)
          yield! issues |> Seq.countBy (fun i -> i.IssueType.ToString()) |> Seq.map (fun (k, c) -> $"Checker: {k} x{c}") ]
    score, arr, report

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
    eprintfn "  RsBuild pack <proyecto.rs2dlc> <salida_sin_extension>"
    2

[<EntryPoint>]
let main argv =
    Console.OutputEncoding <- Text.Encoding.UTF8
    match argv with
    | [| "inspect"; tab |] ->
        GpImport.inspect (Path.GetFullPath tab)
        0
    | [| "timeline"; tab; outJson |] ->
        GpImport.exportTimeline (Path.GetFullPath tab) (Path.GetFullPath outJson)
        0
    | [| "xml"; tab; track; offsetMs; songLengthMs; outXml |] ->
        let timing score order = ArrangementBuilder.barTimesFromTempo score order (float offsetMs), []
        let _, _, report = tabToXml (Path.GetFullPath tab) (int track) ArrangementBuilder.Lead timing (int songLengthMs) (Path.GetFullPath outXml)
        report |> List.iter (printfn "%s")
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
            let times, suspicious = Align.loadBarTimes barTimesJson
            let notes =
                if suspicious.IsEmpty then []
                else [ $"ALINEACIÓN: {suspicious.Length} compases con tempo >10%% distinto de la mediana: " +
                       (suspicious |> List.truncate 30 |> List.map (sprintf "c%d") |> String.concat ", ") ]
            times, notes
        let tab = Path.GetFullPath(req "tab")
        let parts =
            [ yield ArrangementBuilder.Lead, int (req "lead")
              match a.TryFind "rhythm" with
              | Some m -> yield ArrangementBuilder.Rhythm, int m
              | None -> () ]
        let built =
            parts |> List.map (fun (kind, track) ->
                let xml = Path.Combine(outDir, $"arr_{(string kind).ToLowerInvariant()}_RS2.xml")
                let score, arr, report = tabToXml tab track kind timing songLengthMs xml
                score, { Package.Xml = xml; Package.Kind = kind; Package.Tuning = arr.MetaData.Tuning.Strings }, report)
        let score, _, _ = List.head built
        let report = built |> List.collect (fun (_, _, r) -> r)
        let info =
            { Package.Artist = opt "artist" score.Artist
              Package.Title = opt "title" score.Title
              Package.Album = opt "album" score.Album
              Package.Year = int (opt "year" "0") }
        let previewMs = float (opt "preview-ms" (string (min 60000 (songLengthMs / 3))))
        let previewWav = Package.convertAudio wav previewMs
        let toolRoot = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", ".."))
        let cover = Path.GetFullPath(opt "cover" (Path.Combine(toolRoot, "assets", "cover_default.png")))
        let tone = Path.GetFullPath(opt "tone" (Path.Combine(toolRoot, "assets", "tones", "lead_default.json")))
        let specs = built |> List.map (fun (_, spec, _) -> spec)
        let project = Package.writeProject outDir info specs wav previewWav previewMs cover tone
        let outBase = Path.Combine(outDir, Path.GetFileNameWithoutExtension project + "_v1")
        let psarc = (Package.buildFromProject project outBase).GetAwaiter().GetResult()
        let reportPath = Path.Combine(outDir, "report.md")
        File.WriteAllLines(reportPath, [ $"# {info.Artist} - {info.Title}"; "" ] @ (report |> List.map (fun l -> "- " + l)))
        report |> List.iter (printfn "%s")
        printfn "Proyecto: %s" project
        printfn "PSARC:    %s" psarc
        printfn "Informe:  %s" reportPath
        0
    | _ -> usage ()
