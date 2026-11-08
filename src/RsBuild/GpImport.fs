module RsBuild.GpImport

open System.IO
open AlphaTab
open AlphaTab.Importer
open AlphaTab.Model

let load (path: string) : Score =
    let bytes = File.ReadAllBytes path
    ScoreLoader.LoadScoreFromBytes(Core.EcmaScript.Uint8Array(bytes), Settings())

/// Playback order of the bars (MasterBar indices), expanding repeats and alternate endings.
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

/// Estimated duration (s) following the playback order and the written tempo.
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

/// Human-readable summary of the score: metadata, tracks, tunings, bars and tempos.
let inspect (path: string) =
    let s = load path
    printfn "File:     %s" (Path.GetFileName path)
    printfn "Title:    %s | Artist: %s | Album: %s" s.Title s.Artist s.Album
    printfn "Bars:     %d" s.MasterBars.Count

    let tempos =
        s.MasterBars
        |> Seq.collect (fun mb -> mb.TempoAutomations |> Seq.map (fun a -> int mb.Index + 1, a.Value))
        |> Seq.toList
    printfn "Tempos:   %s" (tempos |> List.map (fun (b, v) -> $"b{b}={v}") |> String.concat ", ")

    let sigs =
        s.MasterBars
        |> Seq.pairwise
        |> Seq.filter (fun (a, b) -> a.TimeSignatureNumerator <> b.TimeSignatureNumerator || a.TimeSignatureDenominator <> b.TimeSignatureDenominator)
        |> Seq.map (fun (_, b) -> $"b{int b.Index + 1}={b.TimeSignatureNumerator}/{b.TimeSignatureDenominator}")
        |> Seq.toList
    let first = s.MasterBars[0]
    printfn "Time sig: b1=%d/%d %s" (int first.TimeSignatureNumerator) (int first.TimeSignatureDenominator) (String.concat ", " sigs)

    let repeats = s.MasterBars |> Seq.filter (fun mb -> mb.IsRepeatStart || mb.RepeatCount > 0.0) |> Seq.length
    printfn "Repeats:  %d marks" repeats
    let order = playbackOrder s
    let secs = estimatedSeconds s
    printfn "Expanded: %d bars, %d:%02d (%.1f s) at the written tempo" order.Length (int secs / 60) (int secs % 60) secs

    for t in s.Tracks do
        for st in t.Staves do
            let notes =
                st.Bars |> Seq.sumBy (fun b -> b.Voices |> Seq.sumBy (fun v -> v.Beats |> Seq.sumBy (fun bt -> bt.Notes.Count)))
            let tuning = st.StringTuning.Tunings |> Seq.map noteName |> String.concat " "
            printfn "  Track %d: %-28s strings=%d tuning=[%s] capo=%d notes=%d" (int t.Index) t.Name st.StringTuning.Tunings.Count tuning (int st.Capo) notes

/// Exports the tab's nominal timeline (written tempo) to JSON:
/// start of each bar in playback order and the notes [ms; duration; midi (-1 = drums); track].
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
    // Notes that sound without a new attack (only for the alignment chroma): the continuation of a
    // tied note and the bent part of a bend (BendPoint.Value in quarter tones, Offset 0-60)
    let held = ResizeArray<float[]>()
    for t in s.Tracks do
        for st in t.Staves do
            let isDrums = st.StringTuning.Tunings.Count = 0
            if true then
                order |> List.iteri (fun k mbIndex ->
                    let barTicks = s.MasterBars[mbIndex].CalculateDuration(false)
                    for v in st.Bars[mbIndex].Voices do
                        for b in v.Beats do
                            for n in b.Notes do
                                if n.IsTieDestination && not n.IsDead && not isDrums then
                                    let tMs = starts[k] + durs[k] * b.PlaybackStart / barTicks
                                    let dMs = durs[k] * b.PlaybackDuration / barTicks
                                    held.Add [| tMs; dMs; float (n.CalculateRealValue(true, true)) |]
                                if not n.IsTieDestination && not n.IsDead then
                                    let tMs = starts[k] + durs[k] * b.PlaybackStart / barTicks
                                    let dMs0 = durs[k] * b.PlaybackDuration / barTicks
                                    // With a bend: the attack lasts until the first bent point; the rest goes in held
                                    let dMs =
                                        if isDrums || not n.HasBend || n.BendPoints.Count = 0 then dMs0
                                        else
                                            let pts = n.BendPoints |> Seq.sortBy (fun p -> p.Offset) |> Array.ofSeq
                                            let baseMidi = float (n.CalculateRealValue(true, true))
                                            let at (off: float) = tMs + dMs0 * off / 60.0
                                            for i in 0 .. pts.Length - 1 do
                                                let a = at (float pts[i].Offset)
                                                let e = if i + 1 < pts.Length then at (float pts[i + 1].Offset) else tMs + dMs0
                                                if pts[i].Value > 0 && e > a then
                                                    held.Add [| a; e - a; baseMidi + System.Math.Round(float pts[i].Value / 2.0) |]
                                            match pts |> Array.tryFind (fun p -> p.Value > 0) with
                                            | Some p -> dMs0 * float p.Offset / 60.0
                                            | None -> dMs0
                                    // Drums: -(MIDI percussion note: 35/36 kick, 38/40 snare…); -1 if unknown
                                    let midi =
                                        if isDrums then
                                            let a = int n.PercussionArticulation
                                            let arts = t.PercussionArticulations
                                            let drum =
                                                if a >= 0 && a < arts.Count then int arts[a].OutputMidiNumber
                                                elif a > 0 then a
                                                else int n.Fret
                                            if drum > 0 then -(float drum) else -1.0
                                        else float (n.CalculateRealValue(true, true))
                                    notes.Add [| tMs; dMs; float midi; float t.Index |])
    let json =
        System.Text.Json.JsonSerializer.Serialize(
            {| bar_starts_ms = starts
               bar_quarters = order |> List.map (fun i -> s.MasterBars[i].CalculateDuration(false) / 960.0)
               notes = notes
               held = held
               masterbar_of_bar = order |})
    File.WriteAllText(outJson, json)
    printfn "Timeline: %d bars, %d notes (+%d without attack: ties and bends), %.1f s -> %s"
        order.Length notes.Count held.Count (starts[starts.Count - 1] / 1000.0) outJson

/// Tone category from the track's General MIDI instrument (like PROGRAM_CATEGORY in pipeline.py).
let programCategory (program: int) =
    match program with
    | 24 | 25 -> Some "acoustic"
    | 26 | 27 | 28 | 31 -> Some "clean"
    | 29 -> Some "overdrive"
    | 30 -> Some "distortion"
    | _ -> None

/// Tone category from a tab text ("Clean", "Dist.", "w/ overdrive"…), if it indicates one.
/// "limpi" and "ac[uú]st" also cover Spanish-language tabs ("limpio", "acústica").
let textCategory (text: string) =
    if System.String.IsNullOrWhiteSpace text then None
    else
        let t = text.ToLowerInvariant()
        let has (p: string) = System.Text.RegularExpressions.Regex.IsMatch(t, p)
        if has @"clean|limpi" then Some "clean"
        elif has @"acous|ac[uú]st" then Some "acoustic"
        elif has @"fuzz" then Some "fuzz"
        elif has @"\bdist|high ?gain" then Some "distortion"
        elif has @"overdrive|\bo\.?d\.|\bdrive|crunch" then Some "overdrive"
        else None

/// Sound changes of a track in playback order: (index in `order`, tick within the bar, category).
/// The first is the base tone, at (0, 0). Sources: the track's instrument, mid-track instrument changes
/// and the beat and section texts. "auto" = instrument with no known category (the tone is picked as
/// usual). At most 4 distinct categories (Rocksmith's limit).
let toneSegments (s: Score) (trackIndex: int) (order: int list) : (int * float * string) list =
    let t = s.Tracks[trackIndex]
    let st = t.Staves[0]
    let changes =
        [ for k, mbIndex in List.indexed order do
              match s.MasterBars[mbIndex].Section with
              | null -> ()
              | sec -> match textCategory sec.Text with Some c -> yield k, 0.0, c | None -> ()
              for v in st.Bars[mbIndex].Voices do
                  for b in v.Beats do
                      for a in b.Automations do
                          if a.Type = AutomationType.Instrument then
                              match programCategory (int a.Value) with Some c -> yield k, b.PlaybackStart, c | None -> ()
                      match textCategory b.Text with Some c -> yield k, b.PlaybackStart, c | None -> () ]
        |> List.sortBy (fun (k, tick, _) -> k, tick)
    // First note of the track: any change before it becomes the base tone
    let firstNote =
        order |> List.indexed |> List.tryPick (fun (k, mbIndex) ->
            st.Bars[mbIndex].Voices |> Seq.collect (fun v -> v.Beats) |> Seq.filter (fun b -> b.Notes.Count > 0)
            |> Seq.tryHead |> Option.map (fun b -> k, b.PlaybackStart))
        |> Option.defaultValue (0, 0.0)
    let baseCat =
        changes |> List.filter (fun (k, tick, _) -> (k, tick) <= firstNote) |> List.tryLast
        |> Option.map (fun (_, _, c) -> c)
        |> Option.orElse (programCategory (int t.PlaybackInfo.Program)) |> Option.defaultValue "auto"
    let segs =
        changes |> List.filter (fun (k, tick, _) -> (k, tick) > firstNote)
        |> List.fold (fun acc (k, tick, c) -> match acc with (_, _, cur) :: _ when cur = c -> acc | _ -> (k, tick, c) :: acc)
                     [ (0, 0.0, baseCat) ]
        |> List.rev
    let allowed = segs |> List.map (fun (_, _, c) -> c) |> List.distinct |> List.truncate 4 |> Set.ofList
    segs |> List.filter (fun (_, _, c) -> allowed.Contains c)
    |> List.fold (fun acc (k, tick, c) -> match acc with (_, _, cur) :: _ when cur = c -> acc | _ -> (k, tick, c) :: acc) []
    |> List.rev

/// Score and track info as JSON, so the CLI can pick tracks and metadata.
let inspectJson (path: string) =
    let s = load path
    let order = playbackOrder s
    let tracks =
        [ for t in s.Tracks do
              let st = t.Staves[0]
              let tones = if st.StringTuning.Tunings.Count = 0 then [] else toneSegments s (int t.Index) order
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
                 // Track sounds in order of appearance (the first is the base) and number of changes
                 tone_categories = tones |> List.map (fun (_, _, c) -> c) |> List.distinct
                 tone_changes = max 0 (tones.Length - 1)
                 tone_segments = tones |> List.map (fun (k, _, c) -> {| bar = k + 1; category = c |})
                 bars_with_notes =
                    order |> List.filter (fun i -> st.Bars[i].Voices |> Seq.exists (fun v -> v.Beats |> Seq.exists (fun b -> b.Notes.Count > 0))) |> List.length |} ]
    let info =
        {| title = s.Title
           artist = s.Artist
           album = s.Album
           bars = order.Length
           tracks = tracks |}
    System.Text.Json.JsonSerializer.Serialize(info)
