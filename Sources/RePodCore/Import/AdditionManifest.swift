import Foundation
import ITDBKit

/// Choices made for files before they are added: other values for their fields, another cover for
/// their album, or leaving a file out. The web page writes it after the songs were looked up on
/// MusicBrainz and reviewed one by one, so the songs go in with those values in one write, instead
/// of going in as tagged and being retagged afterwards.
///
///     { "version": 1,
///       "files": [ { "path": "/Users/you/Music/New/01 Open Water.flac",
///                    "set": { "title": "Open Water", "album": "Tideline", "albumartist": "Mira Vale",
///                             "year": 2017, "track": 1, "tracks": 12 },
///                    "cover": "/Users/you/.repod/add-covers/3f2a.jpg" },
///                  { "path": "/Users/you/Music/New/02 Interlude.flac", "skip": true } ] }
///
/// The fields are those of `retag`: title, artist, albumartist, album, genre, composer (an empty
/// value clears it; a song always keeps a title), and year, track, tracks, disc, discs, compilation.
/// `cover` is an image for the album, or "" for none; without it the album takes what the files
/// have. As always when adding, a cover is only given to an album that is not on the iPod yet.
/// A file the manifest does not name goes in as its tags say.
public struct AdditionManifest: Decodable, Sendable {
    public let version: Int
    public let files: [File]

    public struct File: Decodable, Sendable {
        public let path: String
        public var skip: Bool?
        public var set: Fields?
        public var cover: String?
    }

    public struct Fields: Decodable, Sendable {
        public var title, artist, albumartist, album, genre, composer: String?
        public var year, track, tracks, disc, discs: UInt32?
        public var compilation: Bool?
    }

    public enum ManifestError: Error, CustomStringConvertible {
        case unreadable(String)
        case version(Int)
        case missingCover(String)

        public var description: String {
            switch self {
            case .unreadable(let why): "the choices file cannot be read: \(why)"
            case .version(let v): "the choices file is version \(v); this ipodctl reads version 1"
            case .missingCover(let path): "the cover \(path) is not there"
            }
        }
    }

    public static func read(_ url: URL) throws -> AdditionManifest {
        let manifest: AdditionManifest
        do { manifest = try JSONDecoder().decode(AdditionManifest.self, from: Data(contentsOf: url)) } catch {
            throw ManifestError.unreadable("\(error)")
        }
        guard manifest.version == 1 else { throw ManifestError.version(manifest.version) }
        return manifest
    }

    /// What was done with the manifest's choices: the candidates to add, how many were left out on purpose,
    /// and the paths it names that are not among the files.
    public struct Outcome: Sendable {
        public var candidates: [AudioImport.Candidate]
        public var skipped: [String] = []
        public var unknown: [String] = []
    }

    public func applied(to candidates: [AudioImport.Candidate]) throws -> Outcome {
        // The page and the command may spell one file differently (/tmp and /private/tmp), so both sides are resolved.
        func key(_ path: String) -> String { URL(fileURLWithPath: path).resolvingSymlinksInPath().standardizedFileURL.path }
        var choices: [String: File] = [:]
        for file in files { choices[key(file.path)] = file }
        var outcome = Outcome(candidates: [])
        var used = Set<String>()
        for var candidate in candidates {
            let path = key(candidate.url.path)
            guard let choice = choices[path] else { outcome.candidates.append(candidate); continue }
            used.insert(path)
            if choice.skip == true { outcome.skipped.append(candidate.draft.title); continue }
            if let fields = choice.set { candidate.draft = Self.applying(fields, to: candidate.draft) }
            switch choice.cover {
            case nil: break
            case ""?:
                candidate.artwork = nil
                candidate.folderCover = nil
                candidate.chosenCover = nil
            case let image?:
                let url = URL(fileURLWithPath: (image as NSString).expandingTildeInPath)
                guard FileManager.default.fileExists(atPath: url.path) else { throw ManifestError.missingCover(image) }
                candidate.chosenCover = url
            }
            outcome.candidates.append(candidate)
        }
        outcome.unknown = files.map(\.path).filter { !used.contains(key($0)) }
        return outcome
    }

    static func applying(_ fields: Fields, to draft: TrackDraft) -> TrackDraft {
        var result = draft
        func text(_ value: String?) -> String?? { value.map { $0.isEmpty ? nil : $0 } }
        if let title = fields.title, !title.isEmpty { result.title = title }
        if let value = text(fields.artist) { result.artist = value }
        if let value = text(fields.albumartist) { result.albumArtist = value }
        if let value = text(fields.album) { result.album = value }
        if let value = text(fields.genre) { result.genre = value }
        if let value = text(fields.composer) { result.composer = value }
        if let value = fields.year { result.year = value }
        if let value = fields.track { result.trackNumber = value }
        if let value = fields.tracks { result.totalTracks = value }
        if let value = fields.disc { result.discNumber = value }
        if let value = fields.discs { result.totalDiscs = value }
        if let value = fields.compilation { result.isCompilation = value }
        return result
    }
}
