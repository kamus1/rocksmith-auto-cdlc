#r "../src/RsBuild/bin/Release/net10.0/Microsoft.IO.RecyclableMemoryStream.dll"
#r "../src/RsBuild/bin/Release/net10.0/Rocksmith2014.Common.dll"
#r "../src/RsBuild/bin/Release/net10.0/Rocksmith2014.XML.dll"
#r "../src/RsBuild/bin/Release/net10.0/Rocksmith2014.SNG.dll"
#r "../src/RsBuild/bin/Release/net10.0/Rocksmith2014.PSARC.dll"
#r "../src/RsBuild/bin/Release/net10.0/Rocksmith2014.Conversion.dll"
#r "nuget: FSharp.SystemTextJson"
open System.IO
open System.Text.Json
open Rocksmith2014.PSARC
open Rocksmith2014.Common
open Rocksmith2014.Common.Manifest
// Usage: survey.fsx <list.txt> <out.jsonl>  — one JSON line per song with metadata and the Lead ebeats
let paths = File.ReadAllLines fsi.CommandLineArgs[1]
use out = new StreamWriter(fsi.CommandLineArgs[2])
let tmp = Path.Combine(Path.GetTempPath(), "rsbench.sng")
for path in paths do
    try
        use p = PSARC.OpenFile path
        let leadManifest =
            p.Manifest |> List.tryFind (fun n -> n.EndsWith "_lead.json")
            |> Option.orElse (p.Manifest |> List.tryFind (fun n -> n.EndsWith "_combo.json" || n.EndsWith "_rhythm.json"))
        match leadManifest with
        | None -> ()
        | Some mname ->
            use ms = p.GetEntryStream(mname).Result
            let attr = Manifest.getSingletonAttributes ((Manifest.fromJsonStream ms).AsTask().Result)
            let sngName = p.Manifest |> List.find (fun n -> n.EndsWith ".sng" && Path.GetFileNameWithoutExtension(n) = Path.GetFileNameWithoutExtension(mname))
            p.InflateFile(sngName, tmp).Wait()
            let sng = Rocksmith2014.SNG.SNG.readPackedFile tmp PC |> Async.RunSynchronously
            let beats = sng.Beats |> Array.map (fun b -> float b.Time * 1000.0, (if b.Beat = 0s then int b.Measure else -1))
            let line =
                {| path = path; artist = attr.ArtistName; title = attr.SongName; arrangement = attr.ArrangementName
                   length = attr.SongLength; tempo = attr.SongAverageTempo
                   beats = beats |> Array.map fst; measures = beats |> Array.map snd |}
            out.WriteLine(JsonSerializer.Serialize line)
            out.Flush()
    with e -> eprintfn "ERR %s: %s" (Path.GetFileName path) e.Message
