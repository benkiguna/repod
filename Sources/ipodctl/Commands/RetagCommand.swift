import Foundation
import ITDBKit

/// `retag MANIFEST.json [--apply]` - different values for different songs, in one write.
///
/// `edit` gives the same values to every song it is pointed at; this takes a manifest made by a
/// tool that looked the songs up (the MusicBrainz scan), one entry per song:
///
///     { "version": 1,
///       "songs": [ { "id": 145, "set": { "title": "Open Water", "artist": "Mira Vale",
///                                         "album": "Tideline", "albumartist": "Mira Vale",
///                                         "year": 2017, "track": 3, "tracks": 12 } } ] }
///
/// Text fields: title, artist, albumartist, album, genre, composer (an empty value clears it).
/// Numbers: year, track, tracks, disc, discs. And compilation, true or false. A field that is not
/// given is left alone, a song whose values already match is reported as unchanged, and an id that
/// is not on the iPod is skipped without stopping the others. What the iPod keeps about an album
/// follows the new values (`ITLibrary.reconciled`). Without `--apply` it only previews.
enum RetagCommand {
    struct Manifest: Decodable {
        let version: Int
        let songs: [Song]

        struct Song: Decodable {
            let id: UInt32
            let set: Fields
        }

        struct Fields: Decodable {
            var title, artist, albumartist, album, genre, composer: String?
            var year, track, tracks, disc, discs: UInt32?
            var compilation: Bool?
        }
    }

    // Computed, not stored: key paths are not Sendable, and a static must be in Swift 6.
    private static var text: [(String, MhodType, KeyPath<Manifest.Fields, String?>)] {
        [("title", .title, \.title), ("artist", .artist, \.artist), ("album artist", .albumArtist, \.albumartist),
         ("album", .album, \.album), ("genre", .genre, \.genre), ("composer", .composer, \.composer)]
    }
    private static var numbers: [(String, WritableKeyPath<ITTrack, UInt32>, KeyPath<Manifest.Fields, UInt32?>)] {
        [("year", \.year, \.year), ("track", \.trackNumber, \.track), ("of tracks", \.totalTracks, \.tracks),
         ("disc", \.discNumber, \.disc), ("of discs", \.totalDiscs, \.discs)]
    }

    /// What the fields would change on a track: label, old value, new value. Empty when nothing differs.
    static func differences(_ fields: Manifest.Fields, on track: ITTrack) -> [(String, String, String)] {
        var found: [(String, String, String)] = []
        for (label, type, path) in text {
            guard let value = fields[keyPath: path] else { continue }
            let old = track.fields[type] ?? ""
            if old != value { found.append((label, old, value)) }
        }
        for (label, path, field) in numbers {
            guard let value = fields[keyPath: field] else { continue }
            if track[keyPath: path] != value { found.append((label, "\(track[keyPath: path])", "\(value)")) }
        }
        if let value = fields.compilation, track.isCompilation != value {
            found.append(("compilation", "\(track.isCompilation)", "\(value)"))
        }
        return found
    }

    static func applying(_ fields: Manifest.Fields, to track: ITTrack) -> ITTrack {
        var result = track
        for (_, type, path) in text {
            if let value = fields[keyPath: path] { result = result.setting(type, to: value.isEmpty ? nil : value) }
        }
        for (_, path, field) in numbers {
            if let value = fields[keyPath: field] { result[keyPath: path] = value }
        }
        if let value = fields.compilation { result.isCompilation = value }
        return result
    }

    static func run(_ call: Invocation) async throws {
        guard let path = call.positional(valueFlags: []).first else { throw CommandError("give the manifest file") }
        let url = URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
        let manifest: Manifest
        do {
            manifest = try JSONDecoder().decode(Manifest.self, from: Data(contentsOf: url))
        } catch {
            throw CommandError("cannot read the manifest \(url.path): \(error)")
        }
        guard manifest.version == 1 else { throw CommandError("manifest version \(manifest.version) is not supported (1 is)") }

        let device = try openDevice(call)
        let onDevice = try DeviceLibrary(reading: device, for: call)
        let library = onDevice.library
        let byID = Dictionary(library.tracks.map { ($0.uniqueID, $0) }, uniquingKeysWith: { first, _ in first })

        var wanted: [UInt32: Manifest.Fields] = [:]
        var lines: [String] = [], skipped: [String] = []
        var unchanged = 0
        for song in manifest.songs {
            guard let track = byID[song.id] else { skipped.append("song \(song.id): not on the iPod"); continue }
            guard wanted[song.id] == nil else { skipped.append("song \(song.id): listed twice"); continue }
            let changes = differences(song.set, on: track)
            if changes.isEmpty { unchanged += 1; continue }
            wanted[song.id] = song.set
            let what = changes.map { "\($0.0): '\($0.1)' -> '\($0.2)'" }.joined(separator: "; ")
            lines.append("\(track.title ?? "?") - \(what)")
        }

        let updated = try library.mappingTracks { track in
            wanted[track.uniqueID].map { applying($0, to: track) } ?? track
        }.reconciled(after: library)

        report("Retag", [
            ("iPod", "\(device.volume.name) - \(device.model.name)"),
            ("songs to change", "\(wanted.count)"),
            ("albums", "\(library.albumGroups().count) -> \(updated.albumGroups().count)"),
            ("already as asked", "\(unchanged)"),
            ("skipped", "\(skipped.count)"),
            ("writing", device.supportsWriting ? "allowed" : "BLOCKED: \(device.readOnlyReason?.description ?? "?")"),
        ])
        for line in lines.prefix(40) { print("  + \(line)") }
        if lines.count > 40 { print("  ... and \(lines.count - 40) more") }
        for line in skipped { print("  - SKIPPED \(line)") }
        guard !wanted.isEmpty else { print("\n  Nothing to write."); return }
        guard call.has("--apply") else { print("\n  That was a preview. To write: add --apply"); return }

        print("")
        announce(try await onDevice.write(updated, note: "ipodctl retag (\(wanted.count) songs)"))
    }
}
