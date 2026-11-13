import Foundation
import CoreGraphics
import ImageIO
import Testing
@testable import ITDBKit
@testable import RePodCore
@testable import ipodctl

/// `ipodctl` commands called as from the command line, on the fake iPod from the write
/// tests (`WriteTests.rig`), with backups in a temporary folder.
@Suite("ipodctl")
@MainActor
struct IpodctlCommandTests {

    private func call(_ arguments: String..., on rig: WriteTests.Rig) -> Invocation {
        Invocation(arguments: arguments + ["--volume", rig.paths.mountPoint.path], environment: rig.environment)
    }

    @Test("arguments: flag values and --volume are not files")
    func positionalArguments() {
        let call = Invocation(arguments: ["add", "a.mp3", "--album", "B", "--apply", "c.mp3", "--volume", "/Volumes/iPod"])
        #expect(call.positional(valueFlags: ["--album"]) == ["a.mp3", "c.mp3"])
        #expect(call.value(for: "--album") == "B")
        #expect(call.has("--apply"))
        #expect(Invocation(arguments: ["copy", "--album", "A", "--album", "B"]).values(for: "--album") == ["A", "B"])
        #expect(Invocation(arguments: ["tracks", "--album"]).value(for: "--album") == nil)
    }

    @Test("remove: a record pointing to a folder in Music disappears from the database, the folder and the files stay")
    func removeKeepsFolder() async throws {
        let rig = try await WriteTests.rig(WriteTests.Fixture(pointsAtFolder: 10)); defer { rig.cleanUp() }
        let filesBefore = try rig.musicFiles()

        try await RemoveCommand.run(call("remove", "--id", "10", "--apply", on: rig))

        #expect(!(try rig.onDisk().tracks.contains { $0.uniqueID == 10 }))
        #expect(try rig.musicFiles() == filesBefore)
        #expect(try rig.backups.list(deviceGUID: WriteTests.guid).count == 1, "a backup in the invocation environment")
    }

    @Test("add: the iPod path from --volume does not end up among the files to add")
    func addIgnoresVolumePath() async throws {
        // A WAV track in the database - the record template for the added WAV.
        let rig = try await WriteTests.rig(WriteTests.Fixture(hiRes: 14)); defer { rig.cleanUp() }
        // An audio file in Music without an entry - if the iPod path were a source, it would reach the database.
        try WriteTests.wave(bitDepth: 16, sampleRate: 44_100).write(to: rig.paths.musicDirectory.appendingPathComponent("F00/STRAY.wav"))
        let source = rig.root.appendingPathComponent("Only Shallow.wav")
        try WriteTests.wave(bitDepth: 16, sampleRate: 44_100).write(to: source)

        try await AddCommand.run(call("add", source.path, "--album", "Loveless", "--artist", "My Bloody Valentine", "--apply", on: rig))

        let tracks = try rig.onDisk().tracks
        #expect(tracks.count == 8, "\(tracks.map(\.title))")
        #expect(tracks.contains { $0.album == "Loveless" })
    }

    @Test("add: choices made before adding reach the database in the same write, and a file left out stays out")
    func addWithChoices() async throws {
        let rig = try await WriteTests.rig(WriteTests.Fixture(hiRes: 14)); defer { rig.cleanUp() }
        let folder = rig.root.appendingPathComponent("New songs", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let kept = folder.appendingPathComponent("01 Track.wav"), left = folder.appendingPathComponent("02 Track.wav")
        for file in [kept, left] { try WriteTests.wave(bitDepth: 16, sampleRate: 44_100).write(to: file) }
        let choices = rig.root.appendingPathComponent("choices.json")
        try Data("""
        {"version": 1, "files": [
          {"path": "\(kept.path)", "set": {"title": "When You Sleep", "artist": "My Bloody Valentine", "albumartist": "My Bloody Valentine",
                                            "album": "Loveless", "year": 1991, "track": 4, "tracks": 11}},
          {"path": "\(left.path)", "skip": true}]}
        """.utf8).write(to: choices)
        let before = try rig.onDisk().tracks.count

        try await AddCommand.run(call("add", folder.path, "--manifest", choices.path, "--apply", on: rig))

        let tracks = try rig.onDisk().tracks
        #expect(tracks.count == before + 1)
        let added = try #require(tracks.first { $0.title == "When You Sleep" })
        #expect((added.album, added.albumArtist, added.year, added.trackNumber, added.totalTracks) == ("Loveless", "My Bloody Valentine", 1991, 4, 11))
    }


    @Test("apply: a cover with replace overwrites the old image; without it the album is left alone")
    func applyReplacesACover() async throws {
        let rig = try await WriteTests.rig(WriteTests.Fixture(cover: true)); defer { rig.cleanUp() }
        let file = rig.root.appendingPathComponent("blue.png")
        let destination = try #require(CGImageDestinationCreateWithURL(file as CFURL, "public.png" as CFString, 1, nil))
        CGImageDestinationAddImage(destination, try WriteTests.image(red: 0, green: 0, blue: 1), nil)
        #expect(CGImageDestinationFinalize(destination))
        func pixel() throws -> [UInt8] {
            let variant = try #require(rig.artworkOnDisk().images.first?.variants.first)
            let image = try ITHMB.read(variant, in: rig.paths)
            return Array(image.rgba.prefix(4))
        }
        let before = try pixel()

        func manifest(_ replace: Bool) throws -> String {
            let url = rig.root.appendingPathComponent("covers-\(replace).json")
            try Data("""
            {"version": 1, "covers": [{"album": "Aftermath", "albumArtist": "Rolling Stones", "file": "\(file.path)", "replace": \(replace)}]}
            """.utf8).write(to: url)
            return url.path
        }
        try await ApplyCommand.run(call("apply", try manifest(false), "--apply", on: rig))
        #expect(try pixel() == before)

        try await ApplyCommand.run(call("apply", try manifest(true), "--apply", on: rig))
        #expect(try pixel() != before)
        #expect(try rig.artworkOnDisk().images.count == 1, "the same record, new pixels")
    }

    @Test("retag: a song moved into an album joins it on the iPod, in the table and not only in its text")
    func retagMovesIntoTheAlbum() async throws {
        let rig = try await WriteTests.rig(); defer { rig.cleanUp() }
        let manifest = rig.root.appendingPathComponent("group.json")
        try Data("""
        {"version": 1, "songs": [{"id": 10, "set": {"album": "Currents", "artist": "Tame Impala", "compilation": true}}]}
        """.utf8).write(to: manifest)

        try await RetagCommand.run(call("retag", manifest.path, "--apply", on: rig))

        let library = try rig.onDisk()
        let runs = library.albumOrder().filter { $0.album == "Currents" }.map(\.trackCount)
        let flags = library.tracks.filter { $0.album == "Currents" }.map(\.isCompilation)
        #expect(runs == [3])
        #expect(library.albumsOutOfPlace().isEmpty)
        #expect(flags == [true, true, true], "the album follows the songs that were told")
    }

    @Test("retag: different values for different songs, in one write; songs already as asked and unknown ids are left out")
    func retagWritesPerSongValues() async throws {
        let rig = try await WriteTests.rig(); defer { rig.cleanUp() }
        let before = try rig.onDisk().tracks
        let first = try #require(before.first), second = try #require(before.dropFirst().first)
        let manifest = rig.root.appendingPathComponent("retag.json")
        let json = """
        {"version": 1, "songs": [
          {"id": \(first.uniqueID), "set": {"title": "Retagged", "album": "New Album", "year": 2001, "track": 4, "tracks": 9}},
          {"id": \(second.uniqueID), "set": {"genre": "\(second.genre ?? "")"}},
          {"id": 999999, "set": {"title": "Nobody"}}]}
        """
        try json.write(to: manifest, atomically: true, encoding: .utf8)

        try await RetagCommand.run(call("retag", manifest.path, on: rig))   // a preview writes nothing
        #expect(try rig.onDisk().tracks.first { $0.uniqueID == first.uniqueID }?.title == first.title)

        try await RetagCommand.run(call("retag", manifest.path, "--apply", on: rig))
        let after = try rig.onDisk().tracks
        let changed = try #require(after.first { $0.uniqueID == first.uniqueID })
        #expect(changed.title == "Retagged" && changed.album == "New Album")
        #expect(changed.year == 2001 && changed.trackNumber == 4 && changed.totalTracks == 9)
        #expect(changed.artist == first.artist, "a field that was not given is left alone")
        #expect(after.count == before.count)
        #expect(try rig.backups.list(deviceGUID: WriteTests.guid).count == 1)
    }

    @Test("command errors are thrown, not ending the process")
    func errorsAreThrown() async throws {
        let rig = try await WriteTests.rig(); defer { rig.cleanUp() }
        await #expect { try await RemoveCommand.run(call("remove", on: rig)) }
            throws: { $0 is CommandError }
    }
}
