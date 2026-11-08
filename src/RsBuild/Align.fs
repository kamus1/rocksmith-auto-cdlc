module RsBuild.Align

open System.IO
open System.Text.Json

/// Reads the bar times (ms, playback order, bars + 1) computed by cdlc/align.py,
/// the actual end of each bar (before a gap of audio that is not in the tab) and the suspicious bars.
let loadBarTimes (path: string) : float[] * float[] * int list =
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
    times, ends, suspicious
