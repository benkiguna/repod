import Foundation
import ImageIO
import ITDBKit
import RePodCore

/// `apply MANIFEST.json [--unverified-formats] [--apply]` - a batch of changes decided elsewhere,
/// written in one transaction (one backup, one database write).
///
/// The manifest is the seam between the tools that look things up and the one that writes:
///
///     { "version": 1,
///       "covers": [ { "album": "Title", "albumArtist": "Artist", "file": "cover.jpg", "replace": true } ] }
///
/// - `covers`: the first artwork for an album without any. `albumArtist` is optional but should
///   be given - without it an album title shared by two artists is refused as ambiguous.
///   Relative `file` paths are taken from the manifest's folder. With `replace`, an album that has
///   artwork gets the new image in place of the old one.
///
/// An entry that can't be applied (unknown album, already has artwork and no `replace`, unreadable
/// image) is reported and skipped; it never stops the others. Without `--apply` only a preview.
enum ApplyCommand {
    struct Manifest: Decodable {
        let version: Int
        let covers: [Cover]?

        struct Cover: Decodable {
            let album: String
            let albumArtist: String?
            let file: String
            var replace: Bool?
        }
    }

    static func run(_ call: Invocation) async throws {
        guard let path = call.positional(valueFlags: []).first else { throw CommandError("give the manifest file") }
        let manifestURL = URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
        let manifest: Manifest
        do {
            manifest = try JSONDecoder().decode(Manifest.self, from: Data(contentsOf: manifestURL))
        } catch {
            throw CommandError("cannot read the manifest \(manifestURL.path): \(error)")
        }
        guard manifest.version == 1 else { throw CommandError("manifest version \(manifest.version) is not supported (1 is)") }

        let device = try openDevice(call)
        let onDevice = try DeviceLibrary(reading: device, for: call)
        let library = onDevice.library
        let byTrack = try device.readArtwork()?.indexedByTrack() ?? [:]
        let unverified = call.has("--unverified-formats")

        var changes = ChangeSet()
        var queued: [String] = []
        var skipped: [String] = []
        let groups = Dictionary(grouping: library.tracks, by: \.albumKey)

        for cover in manifest.covers ?? [] {
            let label = cover.albumArtist.map { "\(cover.album) / \($0)" } ?? cover.album
            let matches = groups.filter { key, _ in
                key.album == cover.album && (cover.albumArtist.map { $0 == key.albumArtist } ?? true)
            }
            guard let tracks = matches.values.first else { skipped.append("\(label): no such album"); continue }
            guard matches.count == 1 else {
                skipped.append("\(label): \(matches.count) albums share this title - give albumArtist")
                continue
            }
            if cover.replace != true, let existing = tracks.first(where: { byTrack[$0.persistentID] != nil }) {
                skipped.append("\(label): already has artwork (on '\(existing.title ?? "?")')")
                continue
            }
            let file = URL(fileURLWithPath: (cover.file as NSString).expandingTildeInPath, relativeTo: manifestURL.deletingLastPathComponent())
            guard let source = CGImageSourceCreateWithURL(file as CFURL, nil),
                  let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
            else { skipped.append("\(label): cannot load the image \(file.path)"); continue }
            let bytes = (try? FileManager.default.attributesOfItem(atPath: file.path)[.size] as? Int) ?? 0
            let title = tracks[0].album ?? cover.album
            let slots = ArtworkReplacement.slots(for: tracks.compactMap { byTrack[$0.persistentID] })
            if slots.isEmpty {
                changes.queueFirstCover(image, sourceByteCount: ITTrack.imageSourceByteCount(pictureBytes: UInt32(clamping: bytes)),
                                        owner: tracks[0], tracks: tracks, album: title,
                                        for: AlbumID(albumOf: tracks), allowingUnverifiedFormats: unverified)
            } else {
                // The pixels go into the slices the album uses now; an album sharing them changes with it.
                changes.queueCoverReplacement(image, slots: slots, trackIDs: tracks.map(\.persistentID).filter { byTrack[$0] != nil },
                                              album: title, for: AlbumID(albumOf: tracks))
                let mine = Set(slots.map(\.variant))
                let sharing = groups.filter { key, others in
                    key != tracks[0].albumKey && others.contains { byTrack[$0.persistentID]?.variants.contains(where: mine.contains) == true }
                }
                for key in sharing.keys.map(\.album).sorted() { queued.append("\(label) - shares its image with '\(key)', which changes too") }
            }
            queued.append("\(label) - \(tracks.count) songs, \(image.width)x\(image.height) from \(file.lastPathComponent)")
        }

        report("Apply", [
            ("iPod", "\(device.volume.name) - \(device.model.name)"),
            ("covers to write", "\(changes.log.count)"),
            ("skipped", "\(skipped.count)"),
            ("writing", device.supportsWriting ? "allowed" : "BLOCKED: \(device.readOnlyReason?.description ?? "?")"),
        ])
        for line in queued { print("  + \(line)") }
        for line in skipped { print("  - SKIPPED \(line)") }
        guard !queued.isEmpty else { print("\n  Nothing to write."); return }
        guard call.has("--apply") else { print("\n  That was a preview. To write: add --apply"); return }

        print("")
        announce(try await onDevice.write(library, changes: changes, note: "ipodctl apply \(manifestURL.lastPathComponent)"))
    }
}
