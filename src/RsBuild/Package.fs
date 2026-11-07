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
      /// Versión que se muestra en el título, " (vN)"; 0 = sin versión. La clave del DLC sale del título sin ella.
      Version: int
      /// Variante ("Lead en Guitar 1"…): va en el título y en la clave, para que conviva con la versión normal
      Variant: string }

/// Clave del DLC: la de DLC Builder (artista y título) más, si hay variante, sus primeras letras.
let dlcKeyOf (info: SongInfo) =
    let key = DLCKey.create "rsbuild" info.Artist info.Title
    let v: string = Rocksmith2014.DLCProject.StringValidator.dlcKey (if isNull info.Variant then "" else info.Variant)
    if v.Length = 0 then key else key + v.Substring(0, min 5 v.Length)

/// Título que se ve en el juego: título, [variante] y (vN).
let displayTitle (info: SongInfo) =
    let v = if String.IsNullOrWhiteSpace info.Variant then "" else $" [{info.Variant.Trim()}]"
    let n = if info.Version > 0 then $" (v{info.Version})" else ""
    info.Title + v + n

/// Siguiente versión de un DLC en la carpeta del juego: 1 + la mayor <clave>_vN_p.psarc que haya.
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

/// Un arrangement para el proyecto: XML, tipo (Lead/Rhythm), afinación y tonos (clave, archivo .json del
/// tono; el primero es el base). Con más de uno, el XML lleva los cambios de tono con esas claves.
type ArrangementSpec =
    { Xml: string
      Kind: ArrangementBuilder.ArrangementKind
      Tuning: int16[]
      Tones: (string * string) list }

/// Escribe el proyecto .rs2dlc (formato de DLC Builder). Devuelve su ruta.
let writeProject (dir: string) (info: SongInfo) (arrangements: ArrangementSpec list) (wav: string) (previewWav: string)
                 (previewStartMs: float) (cover: string) =
    let dlcKey = dlcKeyOf info
    // Un tono por clave distinta; clave y nombre deben coincidir con BaseTone/Tones de los arrangements
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
    // Volumen como DLC Builder: sonoridad integrada (BS.1770) llevada a -16 LUFS. Los tonos de los CDLC están
    // equilibrados contra esa referencia; con un volumen fijo, la música queda más fuerte o más floja que
    // el tono según lo alto que esté grabado el audio.
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
