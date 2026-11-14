import Foundation
import Testing
import ITDBKit
@testable import RePodCore

@Suite("Choices made before adding")
struct AdditionManifestTests {

    static func manifest(_ json: String) throws -> AdditionManifest {
        try JSONDecoder().decode(AdditionManifest.self, from: Data(json.utf8))
    }

    static func candidate(_ file: String, title: String = "01 Track", artwork: Data? = Data([1, 2, 3])) -> AudioImport.Candidate {
        let draft = TrackDraft(title: title, artist: "Unknown", albumArtist: "Unknown", album: "New folder", kind: .mp3, relativePath: "")
        var c = AudioImport.Candidate(url: URL(fileURLWithPath: "/tmp/\(file)"), draft: draft, verdict: .supported, formatLabel: "MP3", artwork: artwork)
        c.folderCover = URL(fileURLWithPath: "/tmp/cover.jpg")
        return c
    }

    @Test("each file gets its own values; what is not given stays as tagged")
    func perFileValues() throws {
        let m = try Self.manifest("""
        {"version": 1, "files": [
          {"path": "/tmp/a.mp3", "set": {"title": "Open Water", "album": "Tideline", "albumartist": "Mira Vale", "year": 2017, "track": 3, "tracks": 12}},
          {"path": "/tmp/b.mp3", "set": {"artist": "", "title": ""}}]}
        """)
        let out = try m.applied(to: [Self.candidate("a.mp3"), Self.candidate("b.mp3"), Self.candidate("c.mp3")])
        let a = out.candidates[0].draft, b = out.candidates[1].draft, c = out.candidates[2].draft
        #expect((a.title, a.album, a.albumArtist, a.year, a.trackNumber, a.totalTracks) == ("Open Water", "Tideline", "Mira Vale", 2017, 3, 12))
        #expect(a.artist == "Unknown", "a field that is not given is left alone")
        #expect(b.artist == nil, "an empty value clears the field")
        #expect(b.title == "01 Track", "a song always keeps a title")
        #expect(c.album == "New folder")
        #expect(out.unknown.isEmpty)
    }

    @Test("a file can be left out, and a path that is not among the files is reported")
    func skipAndUnknown() throws {
        let m = try Self.manifest("""
        {"version": 1, "files": [{"path": "/tmp/a.mp3", "skip": true}, {"path": "/tmp/nowhere.mp3", "set": {"title": "X"}}]}
        """)
        let out = try m.applied(to: [Self.candidate("a.mp3", title: "Gone"), Self.candidate("b.mp3")])
        #expect(out.candidates.map(\.url.lastPathComponent) == ["b.mp3"])
        #expect(out.skipped == ["Gone"])
        #expect(out.unknown == ["/tmp/nowhere.mp3"])
    }

    @Test("a cover: an empty one means none, a file is the chosen one, a missing file stops it all")
    func covers() throws {
        let image = FileManager.default.temporaryDirectory.appendingPathComponent("repod-cover-\(UUID().uuidString.prefix(6)).jpg")
        try Data([0xFF, 0xD8]).write(to: image)
        defer { try? FileManager.default.removeItem(at: image) }
        let m = try Self.manifest("""
        {"version": 1, "files": [{"path": "/tmp/a.mp3", "cover": ""}, {"path": "/tmp/b.mp3", "cover": "\(image.path)"}]}
        """)
        let out = try m.applied(to: [Self.candidate("a.mp3"), Self.candidate("b.mp3"), Self.candidate("c.mp3")])
        #expect(out.candidates[0].coverSource == nil)
        #expect(out.candidates[1].coverSource == .file(image))
        #expect(out.candidates[2].coverOrigin == .tags, "without a choice the file's own stays")

        let missing = try Self.manifest(#"{"version": 1, "files": [{"path": "/tmp/a.mp3", "cover": "/tmp/no-such-cover.jpg"}]}"#)
        #expect(throws: AdditionManifest.ManifestError.self) { try missing.applied(to: [Self.candidate("a.mp3")]) }
    }

    @Test("playlists: an id or new:Name; anything else stops it all")
    func playlists() throws {
        let m = try Self.manifest(#"{"version": 1, "files": [{"path": "/tmp/a.mp3", "playlists": ["6d10bd68cd10bd68", "new:Road trip"]}]}"#)
        let a = Self.candidate("a.mp3")
        let out = try m.applied(to: [a])
        #expect(out.playlists[a.id] == [.existing(0x6d10_bd68_cd10_bd68), .new("Road trip")])
        let bad = try Self.manifest(#"{"version": 1, "files": [{"path": "/tmp/a.mp3", "playlists": ["Road trip"]}]}"#)
        #expect(throws: AdditionManifest.ManifestError.self) { try bad.applied(to: [Self.candidate("a.mp3")]) }
    }

    @Test("another version is refused, not half read")
    func version() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("repod-choices-\(UUID().uuidString.prefix(6)).json")
        try Data(#"{"version": 2, "files": []}"#.utf8).write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }
        #expect(throws: AdditionManifest.ManifestError.self) { try AdditionManifest.read(url) }
    }
}
