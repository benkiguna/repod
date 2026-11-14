import Foundation
import ITDBKit

/// `playlist --list` and `playlist MANIFEST.json [--apply]` - the iPod's own playlists, changed in one write.
///
///     { "version": 1,
///       "changes": [ { "create": "Road trip", "add": [145, 146] },
///                    { "playlist": "6d10bd68cd10bd68", "add": [147] },
///                    { "playlist": "6d10bd68cd10bd68", "remove": [12] },
///                    { "playlist": "6d10bd68cd10bd68", "rename": "Evening" },
///                    { "playlist": "0f3c8a2219a4be01", "delete": true } ] }
///
/// A playlist is named by its id as `dump` writes it (16 hex digits). Changes go in the order given, so a
/// list can be made and then renamed. The list of all songs and smart lists are not changed by hand
/// (`PlaylistEditError.notEditable`). Songs taken out of a list stay on the iPod. Without `--apply` it only previews.
enum PlaylistCommand {
    struct Manifest: Decodable {
        let version: Int
        let changes: [Change]

        struct Change: Decodable {
            var create: String?
            var playlist: String?
            var add: [UInt32]?
            var remove: [UInt32]?
            var rename: String?
            var delete: Bool?
        }
    }

    static func songs(_ n: Int) -> String { n == 1 ? "1 song" : "\(n) songs" }

    static func run(_ call: Invocation) async throws {
        let device = try openDevice(call)
        let onDevice = try DeviceLibrary(reading: device, for: call)
        let library = onDevice.library
        let titles = Dictionary(library.tracks.map { ($0.uniqueID, $0.title ?? "?") }, uniquingKeysWith: { first, _ in first })

        if call.has("--list") {
            for list in library.playlists where !list.isMaster {
                print(String(format: "  %016llx  ", list.persistentID) + "\(list.name ?? "?")  \(songs(list.entries.count))" + (list.isSmart ? "  (smart)" : ""))
            }
            if !library.playlists.contains(where: { !$0.isMaster }) { print("  No playlists.") }
            return
        }

        guard let path = call.positional(valueFlags: []).first else { throw CommandError("give the manifest file, or --list") }
        let manifest: Manifest
        do {
            manifest = try JSONDecoder().decode(Manifest.self, from: Data(contentsOf: URL(fileURLWithPath: (path as NSString).expandingTildeInPath)))
        } catch {
            throw CommandError("cannot read the manifest \(path): \(error)")
        }
        guard manifest.version == 1 else { throw CommandError("manifest version \(manifest.version) is not supported (1 is)") }

        var updated = library
        var lines: [String] = []
        for change in manifest.changes {
            if let name = change.create {
                let ids = change.add ?? []
                updated = try updated.creatingPlaylist(named: name, trackIDs: ids)
                lines.append("new playlist \u{201C}\(name)\u{201D} with \(songs(ids.count))")
                continue
            }
            guard let hex = change.playlist, let id = UInt64(hex, radix: 16) else { throw CommandError("a change names no playlist") }
            let name = updated.playlists.first { $0.persistentID == id }?.name ?? hex
            if let ids = change.add {
                let before = updated.playlists.first { $0.persistentID == id }?.entries.count ?? 0
                updated = try updated.addingTracks(ids, toPlaylist: id)
                let after = updated.playlists.first { $0.persistentID == id }?.entries.count ?? 0
                lines.append("\u{201C}\(name)\u{201D}: \(songs(after - before)) added" + (after - before < ids.count ? ", \(ids.count - (after - before)) already in it" : ""))
                for added in ids.prefix(20) { lines.append("    + \(titles[added] ?? "\(added)")") }
            }
            if let ids = change.remove {
                updated = try updated.removingTracks(Set(ids), fromPlaylist: id)
                lines.append("\u{201C}\(name)\u{201D}: \(songs(ids.count)) taken out (they stay on the iPod)")
            }
            if let rename = change.rename {
                updated = try updated.renamingPlaylist(id, to: rename)
                lines.append("\u{201C}\(name)\u{201D} renamed \u{201C}\(rename)\u{201D}")
            }
            if change.delete == true {
                updated = try updated.deletingPlaylist(id)
                lines.append("\u{201C}\(name)\u{201D} removed (its songs stay on the iPod)")
            }
        }

        // What the iPod reads back must be what was meant: the bytes, parsed again, give the same bytes.
        let bytes = ITunesDBWriter.serialize(updated)
        guard ITunesDBWriter.serialize(try ITunesDBParser.parse(bytes)) == bytes else {
            throw CommandError("the new database does not read back the same - writing nothing")
        }
        report("Playlists", [
            ("iPod", "\(device.volume.name) - \(device.model.name)"),
            ("playlists", "\(library.playlists.filter { !$0.isMaster }.count) -> \(updated.playlists.filter { !$0.isMaster }.count)"),
            ("changes", "\(manifest.changes.count)"),
            ("writing", device.supportsWriting ? "allowed" : "BLOCKED: \(device.readOnlyReason?.description ?? "?")"),
        ])
        for line in lines { print("  \(line)") }
        guard !manifest.changes.isEmpty else { print("\n  Nothing to write."); return }
        guard call.has("--apply") else { print("\n  That was a preview. To write: add --apply"); return }

        print("")
        announce(try await onDevice.write(updated, note: "ipodctl playlist (\(manifest.changes.count) changes)"))
    }
}
