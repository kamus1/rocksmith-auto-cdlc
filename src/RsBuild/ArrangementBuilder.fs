module RsBuild.ArrangementBuilder

open System
open System.Collections.Generic
open AlphaTab.Model
open Rocksmith2014.XML

/// Tiempos (ms) de inicio de cada compás en orden de reproducción, más el final del último (longitud = compases + 1).
type BarTimes = float[]

/// Inicio de compases a partir del tempo escrito en la tab, desplazado offsetMs.
let barTimesFromTempo (score: Score) (order: int list) (offsetMs: float) : BarTimes =
    let mutable tempo = 120.0
    let mutable t = offsetMs
    [| yield t
       for i in order do
           let mb = score.MasterBars[i]
           for a in mb.TempoAutomations do tempo <- a.Value
           t <- t + mb.CalculateDuration(false) / 960.0 * 60000.0 / tempo
           yield t |]

let private standardMidi = [| 40; 45; 50; 55; 59; 64 |]
let private bassStandardMidi = [| 28; 33; 38; 43 |]

/// Offsets de afinación en semitonos respecto a E estándar (cuerda grave primero).
/// Una pista de 4 cuerdas (bajo) se toca en las 4 cuerdas graves de la guitarra, una octava más arriba.
let tuningOffsets (staff: Staff) =
    let t = staff.StringTuning.Tunings |> Seq.map int |> Seq.rev |> Seq.toArray
    let reference = if t.Length = 4 then bassStandardMidi else standardMidi
    Array.init 6 (fun i -> if i < t.Length && i < reference.Length then int16 (t[i] - reference[i]) else 0s)

let private pcNames = [| "C"; "C#"; "D"; "Eb"; "E"; "F"; "F#"; "G"; "Ab"; "A"; "Bb"; "B" |]

/// Sufijo según los intervalos sobre la fundamental (clases de altura relativas).
let private chordSuffixes =
    [ set [ 0; 7 ], "5"
      set [ 0; 4; 7 ], ""
      set [ 0; 3; 7 ], "m"
      set [ 0; 4; 7; 10 ], "7"
      set [ 0; 3; 7; 10 ], "m7"
      set [ 0; 4; 7; 11 ], "maj7"
      set [ 0; 2; 7 ], "sus2"
      set [ 0; 5; 7 ], "sus4"
      set [ 0; 3; 6 ], "dim"
      set [ 0; 4; 8 ], "aug"
      set [ 0; 2; 4; 7 ], "add9"
      set [ 0; 7; 10 ], "7"
      set [ 0; 4; 10 ], "7" ]
    |> Map.ofList

/// Nombre del acorde a partir de la altura MIDI de cada nota (cuerda grave primero).
/// Se prueba como fundamental la nota más grave y, si no encaja, el resto (inversiones, p. ej. "C/G").
let chordName (pitches: int list) =
    match pitches with
    | [] -> ""
    | _ ->
        let bass = List.min pitches % 12
        let pcs = pitches |> List.map (fun p -> p % 12) |> Set.ofList
        // Dos clases de altura: solo se nombra si es un power chord (quinta)
        if pcs.Count = 1 then ""
        elif pcs.Count = 2 && not (pcs.Contains((bass + 7) % 12)) then ""
        else
            let tryRoot r =
                let rel = pcs |> Set.map (fun pc -> (pc - r + 12) % 12)
                chordSuffixes.TryFind rel |> Option.map (fun suffix -> pcNames[r] + suffix)
            match tryRoot bass with
            | Some n -> n
            | None ->
                pcs
                |> Seq.tryPick (fun r -> tryRoot r |> Option.map (fun n -> $"{n}/{pcNames[bass]}"))
                |> Option.defaultValue ""

/// Digitación simple: traste mínimo = dedo 1 (cejilla si se repite), el resto según la distancia.
/// Si dos cuerdas comparten distancia > 0, se reparten dedos consecutivos (power chord 1-3-4).
let chordFingers (frets: sbyte[]) =
    let fingers = Array.create frets.Length -1y
    let fretted = frets |> Array.indexed |> Array.filter (fun (_, f) -> f > 0y)
    if fretted.Length > 0 then
        let minFret = fretted |> Array.map snd |> Array.min
        let mutable used = Set.empty
        for (i, f) in fretted |> Array.sortBy (fun (i, f) -> f, i) do
            let d = int (f - minFret)
            let ideal = if d = 0 then 1 else min 4 (d + 1)
            let finger =
                if d = 0 then 1
                elif used.Contains ideal && ideal < 4 then ideal + 1
                else ideal
            used <- used.Add finger
            fingers[i] <- sbyte finger
    fingers

type ArrangementKind =
    | Lead
    | Rhythm

type private Ev =
    { Time: int
      DurMs: int
      DurTicks: float
      Notes: AlphaTab.Model.Note list
      Beat: Beat }

let private sustainFor (ev: Ev) (n: AlphaTab.Model.Note) (tieExtra: int) =
    let hasTech =
        n.SlideOutType <> SlideOutType.None || n.HasBend || n.Vibrato <> VibratoType.None
    let dur = ev.DurMs + tieExtra
    if (ev.DurTicks >= 960.0 || hasTech || tieExtra > 0) && not n.IsPalmMute && not n.IsDead then
        max 0 (dur - 10)
    else 0

let private makeNote (ev: Ev) (n: AlphaTab.Model.Note) (tieExtra: int) =
    let rn = Rocksmith2014.XML.Note()
    rn.Time <- ev.Time
    rn.String <- sbyte (int n.String - 1)
    rn.Fret <- sbyte n.Fret
    rn.Sustain <- sustainFor ev n tieExtra
    if n.IsPalmMute then rn.IsPalmMute <- true
    if n.IsDead then rn.IsFretHandMute <- true
    if n.Accentuated <> AccentuationType.None then rn.IsAccent <- true
    if ev.Beat.TremoloPicking <> null then rn.IsTremolo <- true
    if n.Vibrato <> VibratoType.None then rn.Vibrato <- 80uy
    match n.HarmonicType with
    | HarmonicType.Natural -> rn.IsHarmonic <- true
    | HarmonicType.Artificial | HarmonicType.Pinch | HarmonicType.Semi -> rn.IsPinchHarmonic <- true
    | _ -> ()
    match n.HammerPullOrigin with
    | null -> ()
    | o when n.Fret > o.Fret -> rn.IsHammerOn <- true
    | _ -> rn.IsPullOff <- true
    if rn.Sustain > 0 then
        match n.SlideOutType with
        | SlideOutType.Shift | SlideOutType.Legato when n.SlideTarget <> null && n.SlideTarget.Fret <> n.Fret ->
            rn.SlideTo <- sbyte n.SlideTarget.Fret
        | SlideOutType.OutDown -> rn.SlideUnpitchTo <- sbyte (max 1 (int n.Fret - 5))
        | SlideOutType.OutUp -> rn.SlideUnpitchTo <- sbyte (min 24 (int n.Fret + 5))
        | _ -> ()
        if n.HasBend then
            let bends =
                n.BendPoints
                |> Seq.map (fun bp -> BendValue(ev.Time + int (bp.Offset / 60.0 * float rn.Sustain), float32 (bp.Value / 4.0)))
                |> Seq.distinctBy (fun b -> b.Time)
                |> ResizeArray
            rn.BendValues <- bends
            rn.MaxBend <- bends |> Seq.map (fun b -> b.Step) |> Seq.max
    rn

/// Resultado de construir el arrangement, con avisos para el informe.
type BuildResult =
    { Arrangement: InstrumentalArrangement
      Warnings: string list }

/// Construye el arrangement Lead desde una pista de la tab y los tiempos de cada compás.
/// barEnds[k] es el fin real del compás k: igual a barTimes[k + 1] salvo si después hay un hueco (audio
/// que no está en la tab), que queda sin notas y con beats al mismo tempo.
let build (score: Score) (trackIndex: int) (kind: ArrangementKind) (order: int list) (barTimes: BarTimes) (barEnds: float[])
          (songLengthMs: int) : BuildResult =
    let staff = score.Tracks[trackIndex].Staves[0]
    let warnings = ResizeArray<string>()
    match staff.StringTuning.Tunings.Count with
    | 6 -> ()
    | 4 -> warnings.Add "Pista de 4 cuerdas (bajo): se toca en las 4 cuerdas graves de la guitarra, una octava más arriba."
    | n -> warnings.Add $"La pista tiene {n} cuerdas; solo se usan 6."

    let timeAt (k: int) (tick: float) (barTicks: float) =
        let a, b = barTimes[k], barEnds[k]
        int (Math.Round(a + (b - a) * (tick / barTicks)))

    // 1. Eventos (beats con notas) en orden de reproducción
    let events = ResizeArray<Ev>()
    order |> List.iteri (fun k mbIndex ->
        let mb = score.MasterBars[mbIndex]
        let barTicks = mb.CalculateDuration(false)
        let bar = staff.Bars[mbIndex]
        for beat in bar.Voices[0].Beats do
            if not beat.IsEmpty && beat.Notes.Count > 0 && beat.GraceType = GraceType.None then
                let t0 = timeAt k beat.PlaybackStart barTicks
                let t1 = timeAt k (min barTicks (beat.PlaybackStart + beat.PlaybackDuration)) barTicks
                events.Add
                    { Time = t0
                      DurMs = t1 - t0
                      DurTicks = beat.PlaybackDuration
                      Notes = beat.Notes |> Seq.filter (fun n -> int n.String >= 1 && int n.String <= 6) |> Seq.toList
                      Beat = beat })

    // 2. Ligaduras: la nota ligada no se toca, alarga el sustain de la de origen
    let tieExtra = Dictionary<int * int, int>()   // (índice de evento, cuerda) -> ms extra
    let lastOnString = Dictionary<int, int>()     // cuerda -> índice del último evento que la tocó
    for i in 0 .. events.Count - 1 do
        for n in events[i].Notes do
            let s = int n.String
            if n.IsTieDestination then
                match lastOnString.TryGetValue s with
                | true, j ->
                    let key = (j, s)
                    tieExtra[key] <- (match tieExtra.TryGetValue key with | true, v -> v | _ -> 0) + events[i].DurMs
                | _ -> ()
            else
                lastOnString[s] <- i

    // Altura MIDI de cada cuerda al aire (grave primero), para nombrar acordes
    let openPitches = staff.StringTuning.Tunings |> Seq.map int |> Seq.rev |> Seq.toArray

    // 3. Notas, acordes y handshapes
    let arr = InstrumentalArrangement()
    let level = Level(0y)
    let templates = Dictionary<string, int16>()
    for i in 0 .. events.Count - 1 do
        let ev = events[i]
        let played = ev.Notes |> List.filter (fun n -> not n.IsTieDestination)
        let extra (n: AlphaTab.Model.Note) = match tieExtra.TryGetValue((i, int n.String)) with | true, v -> v | _ -> 0
        match played with
        | [] -> ()
        | [ n ] -> level.Notes.Add(makeNote ev n (extra n))
        | many ->
            let frets = Array.create 6 -1y
            for n in many do frets[int n.String - 1] <- sbyte n.Fret
            let key = String.Join(",", frets)
            let id =
                match templates.TryGetValue key with
                | true, id -> id
                | _ ->
                    let id = int16 arr.ChordTemplates.Count
                    let pitches =
                        frets |> Array.indexed |> Array.choose (fun (i, f) ->
                            if f >= 0y && i < openPitches.Length then Some(openPitches[i] + int f) else None)
                        |> List.ofArray
                    let name = chordName pitches
                    arr.ChordTemplates.Add(ChordTemplate(name, name, chordFingers frets, frets))
                    templates[key] <- id
                    id
            let chord = Chord()
            chord.Time <- ev.Time
            chord.ChordId <- id
            chord.ChordNotes <- many |> List.map (fun n -> makeNote ev n (extra n)) |> ResizeArray
            if many |> List.forall (fun n -> n.IsPalmMute) then chord.IsPalmMute <- true
            if many |> List.forall (fun n -> n.IsDead) then chord.IsFretHandMute <- true
            if many |> List.exists (fun n -> n.Accentuated <> AccentuationType.None) then chord.IsAccent <- true
            level.Chords.Add chord
            let sustain = chord.ChordNotes |> Seq.map (fun n -> n.Sustain) |> Seq.max
            level.HandShapes.Add(HandShape(id, ev.Time, ev.Time + max sustain (max 50 (ev.DurMs - 10))))

    // 4. Anchors: ventana de 4 trastes que se mueve cuando una nota cae fuera
    let fretted =
        Seq.append
            (level.Notes |> Seq.map (fun n -> n.Time, int n.Fret, int n.Fret))
            (level.Chords |> Seq.map (fun c ->
                let fs = arr.ChordTemplates[int c.ChordId].Frets |> Array.filter (fun f -> f > 0y) |> Array.map int
                if fs.Length = 0 then c.Time, 0, 0 else c.Time, Array.min fs, Array.max fs))
        |> Seq.sortBy (fun (t, _, _) -> t)
    let mutable anchor = 0
    for (t, lo, hi) in fretted do
        if lo > 0 then
            let width = max 4 (hi - lo + 1)
            if anchor = 0 || lo < anchor || hi > anchor + width - 1 then
                anchor <- lo
                level.Anchors.Add(Anchor(sbyte anchor, t, sbyte (min 6 width)))
    if level.Anchors.Count = 0 || (level.Anchors[0].Time > 0 && fretted |> Seq.exists (fun (t, _, _) -> t < level.Anchors[0].Time)) then
        let first = fretted |> Seq.tryHead |> Option.map (fun (t, _, _) -> t) |> Option.defaultValue 0
        level.Anchors.Insert(0, Anchor(1y, first, 4y))

    // 5. Beats (Ebeats): numerador del compás repartido uniformemente.
    //    Antes del primer compás se rellenan compases de count-in hacia atrás hasta t = 0.
    let beatTimes = ResizeArray<float * bool>()   // (tiempo, es downbeat)
    let firstNum = int score.MasterBars[List.head order].TimeSignatureNumerator
    let firstBeatLen = (barTimes[1] - barTimes[0]) / float firstNum
    let countIn = ResizeArray<float * bool>()
    let mutable t = barTimes[0] - firstBeatLen
    let mutable b = firstNum - 1
    while t >= 0.0 do
        countIn.Insert(0, (t, b = 0))
        b <- if b = 0 then firstNum - 1 else b - 1
        t <- t - firstBeatLen
    beatTimes.AddRange countIn
    order |> List.iteri (fun k mbIndex ->
        let num = int score.MasterBars[mbIndex].TimeSignatureNumerator
        for b in 0 .. num - 1 do
            beatTimes.Add(barTimes[k] + (barEnds[k] - barTimes[k]) * float b / float num, (b = 0))
        // Hueco: compases sin notas al tempo del compás anterior hasta el siguiente compás de la tab
        let beatLen = (barEnds[k] - barTimes[k]) / float num
        let mutable t = barEnds[k]
        let mutable b = 0
        while t < barTimes[k + 1] - beatLen * 0.5 do
            beatTimes.Add(t, (b % num = 0))
            t <- t + beatLen
            b <- b + 1)
    let mutable measure = 0s
    for (t, isDown) in beatTimes do
        if isDown then measure <- measure + 1s
        arr.Ebeats.Add(Ebeat(int (Math.Round t), (if isDown then measure else -1s)))
    if countIn.Count < firstNum then
        warnings.Add $"Menos de un compás de count-in ({barTimes[0] / 1000.0:F2} s antes del primer compás); añade silencio al inicio del audio."

    // 6. Metadatos
    arr.Levels.Add level
    let md = arr.MetaData
    md.Arrangement <- (match kind with Lead -> "Lead" | Rhythm -> "Rhythm")
    md.Title <- score.Title
    md.ArtistName <- score.Artist
    md.AlbumName <- score.Album
    md.SongLength <- songLengthMs
    md.Capo <- sbyte staff.Capo
    let offs = tuningOffsets staff
    md.Tuning.SetTuning(offs[0], offs[1], offs[2], offs[3], offs[4], offs[5])
    let barsMs = barTimes[barTimes.Length - 1] - barTimes[0]
    let beats = order |> List.sumBy (fun i -> float score.MasterBars[i].TimeSignatureNumerator)
    md.AverageTempo <- float32 (beats / (barsMs / 60000.0))
    match kind with
    | Lead -> md.ArrangementProperties.PathLead <- true
    | Rhythm -> md.ArrangementProperties.PathRhythm <- true
    md.ArrangementProperties.StandardTuning <- (offs |> Array.forall ((=) 0s))

    { Arrangement = arr; Warnings = List.ofSeq warnings }
