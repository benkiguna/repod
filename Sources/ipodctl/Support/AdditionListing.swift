import Foundation
import RePodCore

/// `add --json`: what the files to add say, for a program to read (the web page's MusicBrainz review).
/// Everything the human preview shows and more: the length, the ids a lookup can start from, and where
/// the album's cover is, as a file the page can show.
struct AdditionListing: Encodable {
    struct File: Encodable {
        let path: String
        let title: String
        let artist, albumartist, album, genre, composer: String
        let year, track, tracks, disc, discs: UInt32
        let compilation: Bool
        let seconds: Double
        let format: String
        /// What it becomes on the iPod when it has to be converted, e.g. "Apple Lossless 16-bit 44.1 kHz".
        let convert: String?
        /// Why the iPod cannot play it and it cannot be converted either.
        let problem: String?
        /// tags, folder or none.
        let cover: String
        /// The cover as an image file: the folder's own, or the one in the tags written to `--art-dir`.
        let art: String?
        let isrc, mbRecording, mbRelease: String?
    }
    let files: [File]
    let problems: [String]

    init(_ candidates: [AudioImport.Candidate], problems: [any Error], artDirectory: URL?) throws {
        var written: [Data: String] = [:]   // the songs of an album usually carry the same picture
        if let artDirectory { try FileManager.default.createDirectory(at: artDirectory, withIntermediateDirectories: true) }
        files = try candidates.map { c in
            let d = c.draft
            var art = c.folderCover?.path
            if let data = c.artwork, let artDirectory {
                if let known = written[data] {
                    art = known
                } else {
                    let png = data.starts(with: [0x89, 0x50, 0x4E, 0x47])
                    let url = artDirectory.appendingPathComponent("\(written.count + 1).\(png ? "png" : "jpg")")
                    try data.write(to: url)
                    written[data] = url.path
                    art = url.path
                }
            }
            let cover: String = switch c.coverOrigin { case .tags: "tags"; case .folder: "folder"; case .chosen: "chosen"; case .none: "none" }
            let problem: String? = if case .rejected(let reason) = c.verdict { reason } else { nil }
            let convert: String? = if case .convertible(_, let target) = c.verdict { target.label } else { nil }
            return File(path: c.url.path, title: d.title, artist: d.artist ?? "", albumartist: d.albumArtist ?? "", album: d.album ?? "",
                        genre: d.genre ?? "", composer: d.composer ?? "", year: d.year, track: d.trackNumber, tracks: d.totalTracks,
                        disc: d.discNumber, discs: d.totalDiscs, compilation: d.isCompilation,
                        seconds: Double(d.durationMilliseconds) / 1000, format: c.formatLabel, convert: convert, problem: problem,
                        cover: cover, art: art, isrc: c.identifiers.isrc, mbRecording: c.identifiers.musicBrainzRecording,
                        mbRelease: c.identifiers.musicBrainzRelease)
        }
        self.problems = problems.map { "\($0)" }
    }

    func print() throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        Swift.print(String(decoding: try encoder.encode(self), as: UTF8.self))
    }
}
