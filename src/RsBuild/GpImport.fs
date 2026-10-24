module RsBuild.GpImport

open System.IO
open AlphaTab
open AlphaTab.Importer
open AlphaTab.Model

let load (path: string) : Score =
    let bytes = File.ReadAllBytes path
    ScoreLoader.LoadScoreFromBytes(Core.EcmaScript.Uint8Array(bytes), Settings())

/// Orden de reproducción de los compases (índices de MasterBar) expandiendo repeticiones y finales alternativos.
let playbackOrder (s: Score) : int list =
    let bars = s.MasterBars
    let n = bars.Count
    let rec loop i repeatStart pass acc =
        if i >= n then List.rev acc
        else
            let mb = bars[i]
            let repeatStart = if mb.IsRepeatStart then i else repeatStart
            let alt = int mb.AlternateEndings
            let plays = alt = 0 || (alt &&& (1 <<< pass)) <> 0
            let acc = if plays then i :: acc else acc
            if plays && mb.RepeatCount > 0.0 && pass < int mb.RepeatCount - 1 then
                loop repeatStart repeatStart (pass + 1) acc
            elif plays && mb.RepeatCount > 0.0 then
                loop (i + 1) (i + 1) 0 acc
            else
                loop (i + 1) repeatStart pass acc
    loop 0 0 0 []

/// Duración estimada (s) siguiendo el orden de reproducción y el tempo escrito.
let estimatedSeconds (s: Score) =
    let mutable tempo = 120.0
    playbackOrder s
    |> List.sumBy (fun i ->
        let mb = s.MasterBars[i]
        for a in mb.TempoAutomations do tempo <- a.Value
        mb.CalculateDuration(false) / 960.0 * 60.0 / tempo)

let private noteName (midi: float) =
    let names = [| "C"; "C#"; "D"; "D#"; "E"; "F"; "F#"; "G"; "G#"; "A"; "A#"; "B" |]
    let m = int midi
    $"{names[m % 12]}{m / 12 - 1}"

/// Resumen legible de la partitura: metadatos, pistas, afinaciones, compases y tempos.
let inspect (path: string) =
    let s = load path
    printfn "Archivo:  %s" (Path.GetFileName path)
    printfn "Título:   %s | Artista: %s | Álbum: %s" s.Title s.Artist s.Album
    printfn "Compases: %d" s.MasterBars.Count

    let tempos =
        s.MasterBars
        |> Seq.collect (fun mb -> mb.TempoAutomations |> Seq.map (fun a -> int mb.Index + 1, a.Value))
        |> Seq.toList
    printfn "Tempos:   %s" (tempos |> List.map (fun (b, v) -> $"c{b}={v}") |> String.concat ", ")

    let sigs =
        s.MasterBars
        |> Seq.pairwise
        |> Seq.filter (fun (a, b) -> a.TimeSignatureNumerator <> b.TimeSignatureNumerator || a.TimeSignatureDenominator <> b.TimeSignatureDenominator)
        |> Seq.map (fun (_, b) -> $"c{int b.Index + 1}={b.TimeSignatureNumerator}/{b.TimeSignatureDenominator}")
        |> Seq.toList
    let first = s.MasterBars[0]
    printfn "Compás:   c1=%d/%d %s" (int first.TimeSignatureNumerator) (int first.TimeSignatureDenominator) (String.concat ", " sigs)

    let repeats = s.MasterBars |> Seq.filter (fun mb -> mb.IsRepeatStart || mb.RepeatCount > 0.0) |> Seq.length
    printfn "Repeticiones: %d marcas" repeats
    let order = playbackOrder s
    let secs = estimatedSeconds s
    printfn "Expandido: %d compases, %d:%02d (%.1f s) al tempo escrito" order.Length (int secs / 60) (int secs % 60) secs

    for t in s.Tracks do
        for st in t.Staves do
            let notes =
                st.Bars |> Seq.sumBy (fun b -> b.Voices |> Seq.sumBy (fun v -> v.Beats |> Seq.sumBy (fun bt -> bt.Notes.Count)))
            let tuning = st.StringTuning.Tunings |> Seq.map noteName |> String.concat " "
            printfn "  Pista %d: %-28s cuerdas=%d afinación=[%s] capo=%d notas=%d" (int t.Index) t.Name st.StringTuning.Tunings.Count tuning (int st.Capo) notes

/// Exporta a JSON la línea de tiempo nominal de la tab (tempo escrito):
/// inicio de cada compás en orden de reproducción y las notas [ms; duración; midi (-1 = batería); pista].
let exportTimeline (path: string) (outJson: string) =
    let s = load path
    let order = playbackOrder s
    let mutable tempo = 120.0
    let starts = ResizeArray<float>([ 0.0 ])
    let durs = ResizeArray<float>()
    for i in order do
        let mb = s.MasterBars[i]
        for a in mb.TempoAutomations do tempo <- a.Value
        let d = mb.CalculateDuration(false) / 960.0 * 60000.0 / tempo
        durs.Add d
        starts.Add(starts[starts.Count - 1] + d)
    let notes = ResizeArray<float[]>()
    for t in s.Tracks do
        for st in t.Staves do
            let isDrums = st.StringTuning.Tunings.Count = 0
            if true then
                order |> List.iteri (fun k mbIndex ->
                    let barTicks = s.MasterBars[mbIndex].CalculateDuration(false)
                    for v in st.Bars[mbIndex].Voices do
                        for b in v.Beats do
                            for n in b.Notes do
                                if not n.IsTieDestination && not n.IsDead then
                                    let tMs = starts[k] + durs[k] * b.PlaybackStart / barTicks
                                    let dMs = durs[k] * b.PlaybackDuration / barTicks
                                    let midi = if isDrums then -1.0 else float (n.CalculateRealValue(true, true))
                                    notes.Add [| tMs; dMs; float midi; float t.Index |])
    let json =
        System.Text.Json.JsonSerializer.Serialize(
            {| bar_starts_ms = starts
               bar_quarters = order |> List.map (fun i -> s.MasterBars[i].CalculateDuration(false) / 960.0)
               notes = notes
               masterbar_of_bar = order |})
    File.WriteAllText(outJson, json)
    printfn "Timeline: %d compases, %d notas, %.1f s -> %s" order.Length notes.Count (starts[starts.Count - 1] / 1000.0) outJson

/// Información de la partitura y sus pistas en JSON, para que la CLI elija pistas y metadatos.
let inspectJson (path: string) =
    let s = load path
    let order = playbackOrder s
    let tracks =
        [ for t in s.Tracks do
              let st = t.Staves[0]
              let beats =
                  [ for i in order do
                        for v in st.Bars[i].Voices do
                            for b in v.Beats do
                                let n = b.Notes |> Seq.filter (fun n -> not n.IsTieDestination) |> Seq.length
                                if n > 0 then yield b, n ]
              let frets = beats |> List.collect (fun (b, _) -> b.Notes |> Seq.map (fun n -> float n.Fret) |> List.ofSeq)
              {| index = int t.Index
                 name = t.Name
                 program = int t.PlaybackInfo.Program
                 strings = st.StringTuning.Tunings.Count
                 tuning = st.StringTuning.Tunings |> Seq.map int |> Seq.rev |> Seq.toArray
                 is_percussion = st.StringTuning.Tunings.Count = 0
                 single_notes = beats |> List.filter (fun (_, n) -> n = 1) |> List.length
                 chords = beats |> List.filter (fun (_, n) -> n > 1) |> List.length
                 mean_fret = (if frets.IsEmpty then 0.0 else List.average frets)
                 max_fret = (if frets.IsEmpty then 0.0 else List.max frets)
                 bars_with_notes =
                    order |> List.filter (fun i -> st.Bars[i].Voices |> Seq.exists (fun v -> v.Beats |> Seq.exists (fun b -> b.Notes.Count > 0))) |> List.length |} ]
    let info =
        {| title = s.Title
           artist = s.Artist
           album = s.Album
           bars = order.Length
           tracks = tracks |}
    System.Text.Json.JsonSerializer.Serialize(info)
