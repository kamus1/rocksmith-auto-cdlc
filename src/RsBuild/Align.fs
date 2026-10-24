module RsBuild.Align

open System.IO
open System.Text.Json

/// Lee los tiempos de compás (ms, orden de reproducción, compases + 1) calculados por cdlc/align.py,
/// el fin real de cada compás (antes de un hueco de audio que no está en la tab) y los compases dudosos.
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
