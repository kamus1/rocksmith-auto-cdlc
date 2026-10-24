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
      Year: int }

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

/// Construye el .psarc de PC a partir de un .rs2dlc (los .wem deben existir). Devuelve la ruta del .psarc.
let buildFromProject (projectPath: string) (outBase: string) =
    task {
        let! project = DLCProject.load projectPath
        let! _ = buildPackages (WithoutPlatformOrExtension outBase) (buildConfig "rsbuild") project |> Async.StartAsTask
        return outBase + "_p.psarc"
    }

/// Crea la preview (28 s desde previewStartMs) y convierte canción y preview a .wem con Wwise.
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

/// Un arrangement para el proyecto: XML, tipo (Lead/Rhythm) y afinación.
type ArrangementSpec =
    { Xml: string
      Kind: ArrangementBuilder.ArrangementKind
      Tuning: int16[] }

/// Escribe el proyecto .rs2dlc (formato de DLC Builder). Devuelve su ruta.
let writeProject (dir: string) (info: SongInfo) (arrangements: ArrangementSpec list) (wav: string) (previewWav: string)
                 (previewStartMs: float) (cover: string) (toneJsonPath: string) =
    let dlcKey = DLCKey.create "rsbuild" info.Artist info.Title
    let tone = JsonNode.Parse(File.ReadAllText toneJsonPath)
    tone["Key"] <- JsonValue.Create "guitar"   // debe coincidir con BaseTone de los arrangements
    let rel (p: string) = Path.GetRelativePath(dir, p)
    let arrNode (a: ArrangementSpec) =
        let name, route =
            match a.Kind with
            | ArrangementBuilder.Lead -> 0, 1
            | ArrangementBuilder.Rhythm -> 1, 2
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
                          BaseTone = "guitar"
                          Tones = Array.empty<string>
                          MasterID = Random.Shared.Next(100_000_000, Int32.MaxValue)
                          PersistentID = Guid.NewGuid() |} |] |}))
    let project = JsonObject()
    project["Version"] <- JsonValue.Create "1"
    project["DLCKey"] <- JsonValue.Create dlcKey
    project["ArtistName"] <- sortable info.Artist
    project["Title"] <- sortable info.Title
    project["AlbumName"] <- sortable info.Album
    project["Year"] <- JsonValue.Create info.Year
    project["AlbumArtFile"] <- JsonValue.Create(rel cover)
    project["AudioFile"] <- JsonObject([ Collections.Generic.KeyValuePair<string, JsonNode>("Path", JsonValue.Create(rel wav))
                                         Collections.Generic.KeyValuePair<string, JsonNode>("Volume", JsonValue.Create -7.0) ])
    project["AudioPreviewFile"] <- JsonObject([ Collections.Generic.KeyValuePair<string, JsonNode>("Path", JsonValue.Create(rel previewWav))
                                                Collections.Generic.KeyValuePair<string, JsonNode>("Volume", JsonValue.Create -7.0) ])
    project["AudioPreviewStartTime"] <- JsonValue.Create(previewStartMs / 1000.0)
    project["Arrangements"] <- JsonArray(arrangements |> List.map arrNode |> List.toArray)
    project["Tones"] <- JsonArray(tone)
    let path = Path.Combine(dir, dlcKey + ".rs2dlc")
    File.WriteAllText(path, project.ToJsonString(JsonSerializerOptions(WriteIndented = true)))
    path
