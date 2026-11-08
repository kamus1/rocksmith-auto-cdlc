module RsBuild.Package

open System
open System.IO
open System.Text.Json
open System.Text.Json.Nodes
open Rocksmith2014.Audio
open Rocksmith2014.Common
open Rocksmith2014.DLCProject
open Rocksmith2014.DLCProject.PackageBuilder
open Rocksmith2014.DD

type SongInfo =
    { Artist: string
      Title: string
      Album: string
      Year: int
      /// Version shown in the title, " (vN)"; 0 = no version. The DLC key comes from the title without it.
      Version: int
      /// Variant ("Lead on Guitar 1"…): goes in the title and key, so it coexists with the normal version
      Variant: string }

/// DLC key: DLC Builder's (artist and title) plus, if there is a variant, its first letters.
let dlcKeyOf (info: SongInfo) =
    let key = DLCKey.create "rsbuild" info.Artist info.Title
    let v: string = Rocksmith2014.DLCProject.StringValidator.dlcKey (if isNull info.Variant then "" else info.Variant)
    if v.Length = 0 then key else key + v.Substring(0, min 5 v.Length)

/// Title shown in game: title, [variant] and (vN).
let displayTitle (info: SongInfo) =
    let v = if String.IsNullOrWhiteSpace info.Variant then "" else $" [{info.Variant.Trim()}]"
    let n = if info.Version > 0 then $" (v{info.Version})" else ""
    info.Title + v + n

/// Next version of a DLC in the game folder: 1 + the highest existing <key>_vN_p.psarc.
let nextVersion (dlcDir: string) (dlcKey: string) =
    if String.IsNullOrEmpty dlcDir || not (Directory.Exists dlcDir) then 1
    else
        let pattern = Text.RegularExpressions.Regex($"^{Text.RegularExpressions.Regex.Escape dlcKey}_v(\d+)_p\.psarc$")
        Directory.EnumerateFiles(dlcDir, $"{dlcKey}_v*_p.psarc")
        |> Seq.choose (fun f -> let m = pattern.Match(Path.GetFileName f) in if m.Success then Some(int m.Groups[1].Value) else None)
        |> Seq.fold max 0
        |> (+) 1

let buildConfig author =
    { Platforms = [ PC ]
      BuilderVersion = "rsbuild-0.1"
      Author = author
      AppId = AppId.CherubRock
      GenerateDD = true
      ForcePhraseCreation = false
      DDConfig = { PhraseSearchThreshold = Some 80; LevelCountGeneration = LevelCountGeneration.Simple }
      ApplyImprovements = true
      SaveDebugFiles = false
      AudioConversionTask = async { return () }
      IdResetConfig = None
      ProgressReporter = None }

/// Builds the PC .psarc from a .rs2dlc (the .wem files must exist). Returns the .psarc path.
let buildFromProject (projectPath: string) (outBase: string) =
    task {
        let! project = DLCProject.load projectPath
        let! _ = buildPackages (WithoutPlatformOrExtension outBase) (buildConfig "rsbuild") project |> Async.StartAsTask
        return outBase + "_p.psarc"
    }

/// Creates the preview (28 s from previewStartMs) and converts song and preview to .wem with Wwise.
let convertAudio (wav: string) (previewStartMs: float) =
    let previewWav = Path.Combine(Path.GetDirectoryName wav, Path.GetFileNameWithoutExtension wav + "_preview.wav")
    Preview.create wav previewWav (TimeSpan.FromMilliseconds previewStartMs)
    for f in [ wav; previewWav ] do
        printfn "Wwise: %s" (Path.GetFileName f)
        Wwise.convertToWem None f |> Async.RunSynchronously
    previewWav

let private sortable (s: string) =
    let sortValue = if s.StartsWith("The ", StringComparison.OrdinalIgnoreCase) then s.Substring 4 else s
    JsonObject([ Collections.Generic.KeyValuePair<string, JsonNode>("Value", JsonValue.Create s)
                 Collections.Generic.KeyValuePair<string, JsonNode>("SortValue", JsonValue.Create sortValue) ])

/// One arrangement for the project: XML, kind (Lead/Rhythm), tuning and tones (key, tone .json file;
/// the first is the base). With more than one, the XML carries the tone changes with those keys.
type ArrangementSpec =
    { Xml: string
      Kind: ArrangementBuilder.ArrangementKind
      Tuning: int16[]
      Tones: (string * string) list }

/// Writes the .rs2dlc project (DLC Builder format). Returns its path.
let writeProject (dir: string) (info: SongInfo) (arrangements: ArrangementSpec list) (wav: string) (previewWav: string)
                 (previewStartMs: float) (cover: string) =
    let dlcKey = dlcKeyOf info
    // One tone per distinct key; key and name must match the arrangements' BaseTone/Tones
    let tones =
        arrangements |> List.collect (fun a -> a.Tones) |> List.distinctBy fst
        |> List.map (fun (key, path) ->
            let tone = JsonNode.Parse(File.ReadAllText path)
            tone["Key"] <- JsonValue.Create key
            tone["Name"] <- JsonValue.Create key
            tone)
    let rel (p: string) = Path.GetRelativePath(dir, p)
    let arrNode (a: ArrangementSpec) =
        let name, route =
            match a.Kind with
            | ArrangementBuilder.Lead -> 0, 1
            | ArrangementBuilder.Rhythm -> 2, 2
        JsonNode.Parse(
            JsonSerializer.Serialize(
                {| Case = "Instrumental"
                   Fields =
                    [| {| XML = rel a.Xml
                          Name = name
                          RouteMask = route
                          Priority = 0
                          ScrollSpeed = 1.3
                          BassPicked = false
                          Tuning = a.Tuning
                          TuningPitch = 440.0
                          BaseTone = fst a.Tones.Head
                          Tones = (if a.Tones.Length > 1 then a.Tones |> List.map fst |> List.toArray else Array.empty<string>)
                          MasterID = Random.Shared.Next(100_000_000, Int32.MaxValue)
                          PersistentID = Guid.NewGuid() |} |] |}))
    let project = JsonObject()
    project["Version"] <- JsonValue.Create "1"
    project["DLCKey"] <- JsonValue.Create dlcKey
    project["ArtistName"] <- sortable info.Artist
    project["Title"] <- sortable (displayTitle info)
    project["AlbumName"] <- sortable info.Album
    project["Year"] <- JsonValue.Create info.Year
    project["AlbumArtFile"] <- JsonValue.Create(rel cover)
    // Volume like DLC Builder: integrated loudness (BS.1770) brought to -16 LUFS. CDLC tones are
    // balanced against that reference; with a fixed volume, the music ends up louder or quieter than
    // the tone depending on how loud the audio was recorded.
    let volume (path: string) = Rocksmith2014.Audio.Volume.calculate path
    project["AudioFile"] <- JsonObject([ Collections.Generic.KeyValuePair<string, JsonNode>("Path", JsonValue.Create(rel wav))
                                         Collections.Generic.KeyValuePair<string, JsonNode>("Volume", JsonValue.Create(volume wav)) ])
    project["AudioPreviewFile"] <- JsonObject([ Collections.Generic.KeyValuePair<string, JsonNode>("Path", JsonValue.Create(rel previewWav))
                                                Collections.Generic.KeyValuePair<string, JsonNode>("Volume", JsonValue.Create(volume previewWav)) ])
    project["AudioPreviewStartTime"] <- JsonValue.Create(previewStartMs / 1000.0)
    project["Arrangements"] <- JsonArray(arrangements |> List.map arrNode |> List.toArray)
    project["Tones"] <- JsonArray(tones |> List.toArray)
    let path = Path.Combine(dir, dlcKey + ".rs2dlc")
    File.WriteAllText(path, project.ToJsonString(JsonSerializerOptions(WriteIndented = true)))
    path
