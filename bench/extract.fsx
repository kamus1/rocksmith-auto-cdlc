#r "../src/RsBuild/bin/Release/net10.0/Microsoft.IO.RecyclableMemoryStream.dll"
#r "../src/RsBuild/bin/Release/net10.0/Rocksmith2014.Common.dll"
#r "../src/RsBuild/bin/Release/net10.0/Rocksmith2014.XML.dll"
#r "../src/RsBuild/bin/Release/net10.0/Rocksmith2014.SNG.dll"
#r "../src/RsBuild/bin/Release/net10.0/Rocksmith2014.PSARC.dll"
#r "../src/RsBuild/bin/Release/net10.0/Rocksmith2014.Conversion.dll"
open System.IO
open Rocksmith2014.PSARC
open Rocksmith2014.Common
// Usage: extract.fsx <list of "psarc|folder"> — instrumental charts to XML and the main audio (largest .wem)
for line in File.ReadAllLines fsi.CommandLineArgs[1] do
    let parts = line.Split('|')
    let path, dir = parts[0], parts[1]
    Directory.CreateDirectory dir |> ignore
    try
        use p = PSARC.OpenFile path
        for e in p.Manifest |> List.filter (fun n -> n.EndsWith ".sng" && not (n.Contains "vocals")) do
            let sng = Path.Combine(dir, Path.GetFileName e)
            p.InflateFile(e, sng).Wait()
            Rocksmith2014.Conversion.ConvertInstrumental.sngFileToXml sng (Path.ChangeExtension(sng, ".xml")) PC
            |> Async.RunSynchronously
        let wems = p.Manifest |> List.filter (fun n -> n.EndsWith ".wem")
        let sizes = wems |> List.map (fun w -> let tmp = Path.Combine(dir, Path.GetFileName w) in p.InflateFile(w, tmp).Wait(); tmp, FileInfo(tmp).Length)
        let main, _ = sizes |> List.maxBy snd
        File.Move(main, Path.Combine(dir, "song.wem"), true)
        for (f, _) in sizes do if File.Exists f then File.Delete f
        printfn "OK %s" dir
    with e -> eprintfn "ERR %s: %s" path e.Message
