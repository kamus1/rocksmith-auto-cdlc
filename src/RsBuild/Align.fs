module RsBuild.Align

open System.IO
open System.Text.Json

/// Reads the bar times (ms, playback order, bars + 1) computed by cdlc/align.py,
/// the actual end of each bar (before a gap of audio that is not in the tab), the suspicious bars,
/// the first bar (1-based) past the audio end, if the recording ends before the tab (fade-out), and
/// the semitones the recording sounds above the tab (0: same key) and above each track of it (a track
/// can be written in another key than the rest; a track not listed follows the tab).
let loadBarTimes (path: string) : float[] * float[] * int list * int option * int * Map<int, int> =
    use doc = JsonDocument.Parse(File.ReadAllText path)
    let root = doc.RootElement
    let times = root.GetProperty("bar_times_ms").EnumerateArray() |> Seq.map (fun e -> e.GetDouble()) |> Seq.toArray
    let suspicious =
        match root.TryGetProperty "suspicious_bars" with
        | true, v -> v.EnumerateArray() |> Seq.map (fun e -> e.GetInt32()) |> Seq.toList
        | _ -> []
    let ends =
        match root.TryGetProperty "bar_ends_ms" with
        | true, v -> v.EnumerateArray() |> Seq.map (fun e -> e.GetDouble()) |> Seq.toArray
        | _ -> times[1..]
    let cut =
        match root.TryGetProperty "tail_cut_bar" with
        | true, v when v.ValueKind = JsonValueKind.Number -> Some(v.GetInt32())
        | _ -> None
    let keyShift =
        match root.TryGetProperty "key_shift" with
        | true, v when v.ValueKind = JsonValueKind.Number -> v.GetInt32()
        | _ -> 0
    let trackShifts =
        match root.TryGetProperty "track_key_shifts" with
        | true, v when v.ValueKind = JsonValueKind.Object ->
            v.EnumerateObject() |> Seq.map (fun p -> int p.Name, p.Value.GetInt32()) |> Map.ofSeq
        | _ -> Map.empty
    times, ends, suspicious, cut, keyShift, trackShifts
