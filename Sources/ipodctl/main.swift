import Foundation
import ITDBKit

// Developer tool for ITDBKit. Deliberately without external dependencies -
// the core must not have any, so the CLI does not either.
//
// Each command in its own file in Commands/ (a `...Command` type with `run()`),
// shared helpers in Support/. Here only the help text and command dispatch.

let usage = """
ipodctl - reads and writes an iPod's library. The web page runs it; it can be used on its own.

  usage: ipodctl COMMAND [options] - the command always comes first

  --volume IPOD         which iPod: nano, classic, GUID or path (required when several are connected)
  --developer           for models not yet confirmed and for disk images: lifts the list of
                        confirmed unsigned families, the USB confirmation and the list of measured
                        artwork formats. Never lifts the signature, identity conflicts or backups.

  info                  information about the connected iPod
  diagnose [--rockbox]  every diagnostic check, read-only; --rockbox also reads every song file
                        and lists albums that look different in Rockbox
  roundtrip             whether the writer reproduces this iPod's database byte for byte, read-only
  dump [--out FILE]     the whole library as JSON
  check [--remove-orphans [--apply]]
                        database consistency with files on disk; --remove-orphans deletes files without an entry
  artwork [--extract ID --out FILE.png]
                        artwork: summary or extracting one image
  artwork --check-counts [--apply]
                        albums whose artwork the iPod does not see: the image record declares
                        fewer tracks than the album has
  artwork --add ALBUM --file IMAGE [--unverified-formats] [--apply]
                        first artwork for an album without artwork; --unverified-formats is a
                        hardware trial of a format outside the confirmed ones
  artwork --export DIR  every album's cover as a PNG plus index.json
  artwork --missing [--out FILE]
                        albums without artwork, as JSON
  artwork --per-track ALBUM [--apply]
                        album artwork in a separate record for each track; the nano 2G needs it
  apply MANIFEST.json [--unverified-formats] [--apply]
                        a batch of decided changes (first covers), one backup and one write
  audio [--fix ID[,ID] | --fix-all] (--keep-originals FOLDER | --discard-originals) [--apply]
                        file formats: what the iPod plays and what it does not; --fix transcodes
  fix-sort [--apply]    repairing sort fields that split albums into several covers
  add FILE|FOLDER ... [--title T] [--artist A] [--album B] [--track N] [--manifest CHOICES.json] [--apply]
                        adding tracks to the library. Field overrides work only with a single file;
                        --manifest gives each file its own values, cover or none, or leaves it out
  add FILE|FOLDER ... --json [--art-dir DIR]
                        what the files say, as JSON, with covers from tags written to DIR; no iPod needed
  edit --id N,N (--set FIELD=VALUE)... [--apply]
                        text fields for chosen songs (title, artist, albumartist, album, genre, composer);
                        setting album and albumartist moves songs into another album
  retag MANIFEST.json [--apply]
                        different values for different songs, in one write
  regroup [--apply]     albums whose records or table place fell behind their songs put back together
  remove (--dead | --id N,N | --orphan-albums) [--apply]
                        removing tracks from the database with remapping of the tables
  backup [--note TEXT]  backup of the database and artwork
  snapshots             list of backups
  verify <id>           checking a backup's checksums
  restore <id>          restoring a backup to the device

  Without --apply a command only previews. Every write checks that Music is closed, merges pending
  plays and saves a backup first.
  --allow-music         write even with Music running (changes to the tables will not survive)
  --keep-backups        delete no backup after a write; without it, backups older than 7 days go, the newest 3 always stay
"""

/// The command named by the first argument. An error the command did not
/// handle itself ends the program with the message `error: ...` and exit code 1 - this
/// is the only place that ends the process.
let command: ((Invocation) async throws -> Void)? = switch CommandLine.arguments.dropFirst().first {
case "info": InfoCommand.run
case "diagnose": DiagnoseCommand.run
case "roundtrip": RoundtripCommand.run
case "audio": AudioCommand.run
case "fix-sort": FixSortCommand.run
case "check": CheckCommand.run
case "artwork": ArtworkCommand.run
case "apply": ApplyCommand.run
case "dump": DumpCommand.run
case "edit": EditCommand.run
case "retag": RetagCommand.run
case "regroup": RegroupCommand.run
case "add": AddCommand.run
case "remove": RemoveCommand.run
case "backup": BackupCommand.run
case "snapshots": SnapshotsCommand.run
case "verify": VerifyCommand.run
case "restore": RestoreCommand.run
default: nil
}

let name = CommandLine.arguments.dropFirst().first
if let command {
    do {
        try await command(Invocation(arguments: Array(CommandLine.arguments.dropFirst())))
    } catch {
        FileHandle.standardError.write(Data("error: \(error)\n".utf8))
        exit(1)
    }
} else if let name {
    // A typo in the command name must not look like success in a script.
    let problem = name.hasPrefix("--") ? "the command always comes first, before \(name)" : "unknown command '\(name)'"
    FileHandle.standardError.write(Data("error: \(problem)\n".utf8))
    print(usage)
    exit(1)
} else {
    print(usage)
}
