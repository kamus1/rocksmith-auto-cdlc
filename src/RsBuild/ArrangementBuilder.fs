module RsBuild.ArrangementBuilder

open System
open System.Collections.Generic
open AlphaTab.Model
open Rocksmith2014.XML

/// Start times (ms) of each bar in playback order, plus the end of the last one (length = bars + 1).
type BarTimes = float[]

/// Bar starts from the tab's written tempo, shifted by offsetMs.
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

/// Tuning offsets in semitones relative to E standard (low string first).
/// A 4-string (bass) track is played on the guitar's 4 low strings, one octave up.
let tuningOffsets (staff: Staff) =
    let t = staff.StringTuning.Tunings |> Seq.map int |> Seq.rev |> Seq.toArray
    let reference = if t.Length = 4 then bassStandardMidi else standardMidi
    Array.init 6 (fun i -> if i < t.Length && i < reference.Length then int16 (t[i] - reference[i]) else 0s)

let private pcNames = [| "C"; "C#"; "D"; "Eb"; "E"; "F"; "F#"; "G"; "Ab"; "A"; "Bb"; "B" |]

/// Suffix by the intervals over the root (relative pitch classes).
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

/// Chord name from the MIDI pitch of each note (low string first).
/// The lowest note is tried as the root and, if it does not fit, the others (inversions, e.g. "C/G").
let chordName (pitches: int list) =
    match pitches with
    | [] -> ""
    | _ ->
        let bass = List.min pitches % 12
        let pcs = pitches |> List.map (fun p -> p % 12) |> Set.ofList
        // Two pitch classes: only named if it is a power chord (fifth)
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

/// Simple fingering: lowest fret = finger 1 (barre if repeated), the rest by distance.
/// If two strings share a distance > 0, consecutive fingers are used (power chord 1-3-4).
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
    | Bass

/// Where the tab's strings go in Rocksmith (low string first). Guitar: the tab's 6 low strings as they are.
/// Bass (Rocksmith has 4 strings): the 4 consecutive tab strings closest to E standard; a note on another
/// string (low B of a 5-string, high C of a 6-string) moves to the nearest kept string at the same pitch
/// when the fret allows it, otherwise it is dropped.
/// Returns (tab string 1-based, fret → Rocksmith string 0-based and fret) and the open pitches of the
/// Rocksmith strings.
let private stringLayout (staff: Staff) (kind: ArrangementKind) =
    let t = staff.StringTuning.Tunings |> Seq.map int |> Seq.rev |> Seq.toArray
    match kind with
    | Bass when t.Length > 4 ->
        let first =
            [ 0 .. t.Length - 4 ]
            |> List.minBy (fun s -> Array.sumBy (fun i -> abs (t[s + i] - bassStandardMidi[i])) [| 0 .. 3 |])
        let place (str: int) (fret: int) =
            let s = str - 1
            if s >= first && s < first + 4 then Some(s - first, fret)
            else
                let pitch = t[s] + fret
                let target = if s < first then 0 else 3
                let f = pitch - t[first + target]
                if f >= 0 && f <= 24 then Some(target, f) else None
        place, t[first .. first + 3]
    | _ ->
        let place (str: int) (fret: int) = if str >= 1 && str <= 6 then Some(str - 1, fret) else None
        place, t

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

let private makeNote (ev: Ev) (n: AlphaTab.Model.Note) (str: int, fret: int) (tieExtra: int) =
    let rn = Rocksmith2014.XML.Note()
    rn.Time <- ev.Time
    rn.String <- sbyte str
    rn.Fret <- sbyte fret
    let shift = fret - int n.Fret                  // ≠ 0 when the note moved to another string
    rn.Sustain <- sustainFor ev n tieExtra
    if n.IsPalmMute then rn.IsPalmMute <- true
    if n.IsDead then rn.IsFretHandMute <- true
    if n.Accentuated <> AccentuationType.None then rn.IsAccent <- true
    if ev.Beat.TremoloPicking <> null then rn.IsTremolo <- true
    if ev.Beat.Slap then rn.IsSlap <- true
    if ev.Beat.Pop then rn.IsPluck <- true
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
            rn.SlideTo <- sbyte (max 0 (min 24 (int n.SlideTarget.Fret + shift)))
        | SlideOutType.OutDown -> rn.SlideUnpitchTo <- sbyte (max 1 (fret - 5))
        | SlideOutType.OutUp -> rn.SlideUnpitchTo <- sbyte (min 24 (fret + 5))
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

/// Result of building the arrangement, with warnings for the report.
type BuildResult =
    { Arrangement: InstrumentalArrangement
      Warnings: string list }

/// Builds the arrangement from a tab track and the time of each bar.
/// barEnds[k] is the actual end of bar k: equal to barTimes[k + 1] unless a gap follows (audio
/// not in the tab), which is left without notes and with beats at the same tempo.
let build (score: Score) (trackIndex: int) (kind: ArrangementKind) (order: int list) (barTimes: BarTimes) (barEnds: float[])
          (songLengthMs: int) : BuildResult =
    let staff = score.Tracks[trackIndex].Staves[0]
    let warnings = ResizeArray<string>()
    match kind, staff.StringTuning.Tunings.Count with
    | Bass, 4 | (Lead | Rhythm), 6 -> ()
    | Bass, n when n > 4 -> warnings.Add $"{n}-string bass: notes on the extra strings move to the nearest of the 4 kept ones."
    | Bass, n -> warnings.Add $"The track has {n} strings; a Rocksmith bass has 4."
    | _, 4 -> warnings.Add "4-string track (bass): played on the guitar's 4 low strings, one octave up."
    | _, n -> warnings.Add $"The track has {n} strings; only 6 are used."
    let place, openPitches = stringLayout staff kind
    let placed (n: AlphaTab.Model.Note) = place (int n.String) (int n.Fret)
    let mutable dropped = 0

    let timeAt (k: int) (tick: float) (barTicks: float) =
        let a, b = barTimes[k], barEnds[k]
        int (Math.Round(a + (b - a) * (tick / barTicks)))

    // 1. Events (beats with notes) in playback order
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
                      Notes =
                        beat.Notes
                        |> Seq.filter (fun n ->
                            let ok = (placed n).IsSome
                            if not ok && kind = Bass && not n.IsTieDestination then dropped <- dropped + 1
                            ok)
                        |> Seq.toList
                      Beat = beat })
    if dropped > 0 then
        warnings.Add $"{dropped} bass notes left out: they are below the lowest kept string."

    // 2. Ties: the tied note is not played, it extends the sustain of the origin note
    let tieExtra = Dictionary<int * int, int>()   // (event index, string) -> extra ms
    let lastOnString = Dictionary<int, int>()     // string -> index of the last event that played it
    for i in 0 .. events.Count - 1 do
        for n in events[i].Notes do
            let s = fst (placed n).Value
            if n.IsTieDestination then
                match lastOnString.TryGetValue s with
                | true, j ->
                    let key = (j, s)
                    tieExtra[key] <- (match tieExtra.TryGetValue key with | true, v -> v | _ -> 0) + events[i].DurMs
                | _ -> ()
            else
                lastOnString[s] <- i

    // 3. Notes, chords and handshapes
    let arr = InstrumentalArrangement()
    let level = Level(0y)
    let templates = Dictionary<string, int16>()
    for i in 0 .. events.Count - 1 do
        let ev = events[i]
        let played = ev.Notes |> List.filter (fun n -> not n.IsTieDestination)
        let extra (n: AlphaTab.Model.Note) = match tieExtra.TryGetValue((i, fst (placed n).Value)) with | true, v -> v | _ -> 0
        // Two notes moved onto the same string: keep the first
        let played = played |> List.distinctBy (fun n -> fst (placed n).Value)
        match played with
        | [] -> ()
        | [ n ] -> level.Notes.Add(makeNote ev n (placed n).Value (extra n))
        | many ->
            let frets = Array.create 6 -1y
            for n in many do
                let s, f = (placed n).Value
                frets[s] <- sbyte f
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
            chord.ChordNotes <- many |> List.map (fun n -> makeNote ev n (placed n).Value (extra n)) |> ResizeArray
            if many |> List.forall (fun n -> n.IsPalmMute) then chord.IsPalmMute <- true
            if many |> List.forall (fun n -> n.IsDead) then chord.IsFretHandMute <- true
            if many |> List.exists (fun n -> n.Accentuated <> AccentuationType.None) then chord.IsAccent <- true
            level.Chords.Add chord
            let sustain = chord.ChordNotes |> Seq.map (fun n -> n.Sustain) |> Seq.max
            level.HandShapes.Add(HandShape(id, ev.Time, ev.Time + max sustain (max 50 (ev.DurMs - 10))))

    // 4. Anchors: a 4-fret window that moves when a note falls outside it
    let fretted =
        Seq.append
            (level.Notes |> Seq.map (fun n -> n.Time, int n.Fret, int n.Fret))
            (level.Chords |> Seq.map (fun c ->
                let fs = arr.ChordTemplates[int c.ChordId].Frets |> Array.filter (fun f -> f > 0y) |> Array.map int
                if fs.Length = 0 then c.Time, 0, 0 else c.Time, Array.min fs, Array.max fs))
        |> Seq.sortBy (fun (t, _, _) -> t)
    //    The window cannot go past fret 24 (as in official charts: the last 4-fret one is 21–24);
    //    otherwise, with notes at 22–24 the camera goes off the neck.
    let lastFret = 24
    let mutable anchor = 0
    let mutable anchorWidth = 4
    for (t, lo, hi) in fretted do
        if lo > 0 then
            let width = min 6 (max 4 (hi - lo + 1))
            if anchor = 0 || lo < anchor || hi > anchor + anchorWidth - 1 then
                anchor <- max 1 (min lo (lastFret - width + 1))
                anchorWidth <- width
                level.Anchors.Add(Anchor(sbyte anchor, t, sbyte width))
    if level.Anchors.Count = 0 || (level.Anchors[0].Time > 0 && fretted |> Seq.exists (fun (t, _, _) -> t < level.Anchors[0].Time)) then
        let first = fretted |> Seq.tryHead |> Option.map (fun (t, _, _) -> t) |> Option.defaultValue 0
        level.Anchors.Insert(0, Anchor(1y, first, 4y))

    // 5. Beats (Ebeats): the bar's numerator spread evenly.
    //    Before the first bar, count-in bars are filled backwards down to t = 0.
    let beatTimes = ResizeArray<float * bool>()   // (time, is downbeat)
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
        // Gap: bars without notes at the previous bar's tempo up to the next tab bar
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
        warnings.Add $"Less than one bar of count-in ({barTimes[0] / 1000.0:F2} s before the first bar); add silence at the start of the audio."

    // 6. Metadata
    arr.Levels.Add level
    let md = arr.MetaData
    md.Arrangement <- (match kind with Lead -> "Lead" | Rhythm -> "Rhythm" | Bass -> "Bass")
    md.Title <- score.Title
    md.ArtistName <- score.Artist
    md.AlbumName <- score.Album
    md.SongLength <- songLengthMs
    md.Capo <- sbyte staff.Capo
    let offs =
        match kind with
        | Bass -> Array.init 6 (fun i -> if i < 4 && i < openPitches.Length then int16 (openPitches[i] - bassStandardMidi[i]) else 0s)
        | _ -> tuningOffsets staff
    md.Tuning.SetTuning(offs[0], offs[1], offs[2], offs[3], offs[4], offs[5])
    let barsMs = barTimes[barTimes.Length - 1] - barTimes[0]
    let beats = order |> List.sumBy (fun i -> float score.MasterBars[i].TimeSignatureNumerator)
    md.AverageTempo <- float32 (beats / (barsMs / 60000.0))
    match kind with
    | Lead -> md.ArrangementProperties.PathLead <- true
    | Rhythm -> md.ArrangementProperties.PathRhythm <- true
    | Bass ->
        md.ArrangementProperties.PathBass <- true
        // Picked when the tab marks pick strokes; slap and pop are their own techniques
        let beats = events |> Seq.map (fun e -> e.Beat)
        md.ArrangementProperties.BassPick <- beats |> Seq.exists (fun b -> b.PickStroke <> PickStroke.None)
        md.ArrangementProperties.SlapPop <- beats |> Seq.exists (fun b -> b.Slap || b.Pop)
    md.ArrangementProperties.StandardTuning <- (offs |> Array.forall ((=) 0s))

    { Arrangement = arr; Warnings = List.ofSeq warnings }
