import Foundation
import ITDBKit

/// `edit --id N,N (--set FIELD=VALUE)... [--apply]` - setting text fields for chosen songs.
/// The same as `set-field`, but by song instead of by album, so a few songs can be renamed or
/// moved into another album (regrouped). An empty value clears the field.
enum EditCommand {
    static func run(_ call: Invocation) async throws {
        let device = try openDevice(call)
        guard let list = call.value(for: "--id") else { throw CommandError("give --id N,N,N") }
        let wanted = Set(list.split(separator: ",").compactMap { UInt32($0.trimmingCharacters(in: .whitespaces)) })
        guard !wanted.isEmpty else { throw CommandError("--id expects comma-separated identifiers") }
        let names: [String: MhodType] = [
            "albumartist": .albumArtist, "artist": .artist, "album": .album, "genre": .genre,
            "composer": .composer, "title": .title,
        ]
        var changes: [(MhodType, String?)] = []
        for pair in call.values(for: "--set") {
            guard let cut = pair.firstIndex(of: "=") else { throw CommandError("--set expects FIELD=VALUE, got '\(pair)'") }
            let name = String(pair[..<cut]).lowercased(), value = String(pair[pair.index(after: cut)...])
            guard let field = names[name] else {
                throw CommandError("unknown field '\(name)'. Available: \(names.keys.sorted().joined(separator: ", "))")
            }
            changes.append((field, value.isEmpty ? nil : value))
        }
        guard !changes.isEmpty else { throw CommandError("give at least one --set FIELD=VALUE") }

        let onDevice = try DeviceLibrary(reading: device, for: call)
        let library = onDevice.library
        let affected = library.tracks.filter { wanted.contains($0.uniqueID) }
        guard affected.count == wanted.count else {
            throw CommandError("\(wanted.count - affected.count) of the songs are not in the library")
        }
        report("Editing songs", [("songs", "\(affected.count)")] + changes.map { field, value in
            (field.label, value.map { "'\($0)'" } ?? "(empty)")
        })
        for track in affected.prefix(8) { print("  \(track.title ?? "?")  /  \(track.album ?? "?")") }
        if affected.count > 8 { print("  ... and \(affected.count - 8) more") }

        guard call.has("--apply") else {
            print("\n  That was a preview. To write: add --apply")
            return
        }
        let updated = try library.mappingTracks { track in
            guard wanted.contains(track.uniqueID) else { return track }
            return changes.reduce(track) { $0.setting($1.0, to: $1.1) }
        }.reconciled(after: library)
        announce(try await onDevice.write(updated, note: "ipodctl edit (\(affected.count) songs)"))
    }
}
