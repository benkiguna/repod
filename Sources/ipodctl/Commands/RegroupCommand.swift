import Foundation
import ITDBKit

/// `regroup [--apply]` - albums that older edits left behind: a song still in the place of its old
/// album in the table, an album under a stale `mhia` record or with mixed sort fields. Each is
/// rebuilt from its tracks (`ITLibrary.regroupingAlbums`).
enum RegroupCommand {
    static func run(_ call: Invocation) async throws {
        let device = try openDevice(call)
        let onDevice = try DeviceLibrary(reading: device, for: call)
        let library = onDevice.library
        let albums = library.albumsOutOfPlace()
        guard !albums.isEmpty else {
            print("Every album matches its songs. Nothing to do.")
            return
        }
        let updated = try library.regroupingAlbums(Set(albums))
        report("Regroup", [
            ("iPod", "\(device.volume.name) - \(device.model.name)"),
            ("albums to rebuild", "\(albums.count)"),
            ("album records", "\(library.albums.count) -> \(updated.albums.count)"),
            ("writing", device.supportsWriting ? "allowed" : "BLOCKED: \(device.readOnlyReason?.description ?? "?")"),
        ])
        for album in albums.prefix(40) { print("  + \(album.album) / \(album.albumArtist)") }
        if albums.count > 40 { print("  ... and \(albums.count - 40) more") }

        guard call.has("--apply") else {
            print("\n  That was a preview. To write: add --apply")
            return
        }
        print("")
        announce(try await onDevice.write(updated, note: "ipodctl regroup (\(albums.count) albums)"))
    }
}
